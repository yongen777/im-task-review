# -*- coding: utf-8 -*-
"""2026-10-29 逾期补交审核制：逾期后旧链接不放行 → 回复【申请补交】→ 管理员审核 → 30 分钟新链接。

业务口径（用户拍板，⛔ 不再改）：
1. 逾期后 H5 **旧链接不放行**；rejected 的驳回重交窗口（calc_resubmit_deadline）完全不动
2. 申请窗口 = 截止后 24 小时，超窗不受理
3. 每实例**一次**申请机会（pending / approved / rejected 之后都不能再申请）
4. 通过 → 30 分钟 resubmit 链接；拒绝 → 通知用户
5. 一次申请覆盖该用户所有窗口内逾期实例，管理员逐实例审核

约定：全部用例自建临时库，不碰 data/im_task.db。
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from app.config import SHANGHAI_TZ, format_dt
from app.state import BizError, StateStore

PHONE = "13900009001"
UID = "uid_rs_1"


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None, microsecond=0)


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp()
    return StateStore(Path(tmp) / "im.db")


def _mk_overdue(store: StateStore, *, hours_late: float = 2.0) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """建一个已逾期 hours_late 小时的 form 实例，并推进到 overdue。"""
    rid = store.create_role("执行组", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    store.upsert_delivery_target(PHONE, UID)
    tpl = store.create_template(
        {
            "name": "每日上报", "kind": "form", "role_ids": [rid], "text_tpl": "上报今日数据",
            "remind_at": "09:00", "deadline": "18:00", "period": "day",
            # need_review=True 才会进入 submitted（否则提交即 approved，无法演示驳回）
            "need_review": True,
            "schema_json": [{"key": "summary", "label": "摘要", "type": "text"}],
        },
        actor="t",
    )
    base = store.now().replace(tzinfo=None, microsecond=0)
    deadline = base - timedelta(hours=hours_late)
    pkey = format_dt(deadline)[:10]
    inst = store.ensure_template_instances(tpl["template_id"], pkey, format_dt(deadline))[0]
    store.advance_instance_statuses(base)
    return tpl, store.get_instance(inst["id"])


# ── 1. 24h 内申请 → pending ────────────────────────────────────────────
def test_apply_within_24h_sets_pending() -> None:
    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store, hours_late=2)
        assert inst["status"] == "overdue"
        assert not inst.get("resubmit_status")

        res = store.request_resubmit(PHONE)
        assert res["result"] == "applied"
        assert len(res["items"]) == 1

        got = store.get_instance(inst["id"])
        assert got["resubmit_status"] == "pending"
        assert got["resubmit_req_at"], "申请时间必须落库（后台要显示）"
    finally:
        store.close()


# ── 2. 重复申请一律拒绝（一次机会）──────────────────────────────────────
def test_second_apply_is_rejected() -> None:
    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store)
        assert store.request_resubmit(PHONE)["result"] == "applied"
        # 无论 pending / approved / rejected，只要申请过就不能再来一次
        assert store.request_resubmit(PHONE)["result"] == "already"

        store.approve_resubmit(int(inst["id"]), actor="admin")
        assert store.request_resubmit(PHONE)["result"] == "already"
    finally:
        store.close()


# ── 3. 超 24h 申请窗口 → 不受理 ────────────────────────────────────────
def test_apply_after_24h_window_is_refused() -> None:
    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store, hours_late=30)
        assert inst["status"] == "overdue"  # 72h 后才 expired，此处仍是 overdue

        res = store.request_resubmit(PHONE)
        assert res["result"] == "window_expired"
        assert store.get_instance(inst["id"])["resubmit_status"] is None
    finally:
        store.close()


# ── 4. 通过 → 30 分钟 resubmit token → H5 放行 ──────────────────────────
def test_approve_issues_30min_resubmit_token_and_allows_submit() -> None:
    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store)
        store.request_resubmit(PHONE)

        res = store.approve_resubmit(int(inst["id"]), actor="admin")
        assert store.get_instance(inst["id"])["resubmit_status"] == "approved"
        assert store.get_instance(inst["id"])["resubmit_decided_by"] == "admin"

        tok = store.get_h5_token(res["raw_token"])
        assert tok is not None and tok["purpose"] == "resubmit", "必须复用 issue_h5_token，purpose=resubmit"
        expected = format_dt(store.now().replace(tzinfo=None) + timedelta(minutes=30))
        assert tok["expires_at"] == expected, "有效期必须正好 30 分钟"

        ok = store.submit_h5_task(raw_token=res["raw_token"], fields={"summary": "补交数据"})
        assert ok["ok"] is True
        assert ok["late_submit"] is True
    finally:
        store.close()


# ── 5. 补交链接过期 → 拦截且提示「机会已使用」─────────────────────────
def test_expired_resubmit_token_is_blocked() -> None:
    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store)
        store.request_resubmit(PHONE)
        res = store.approve_resubmit(int(inst["id"]), actor="admin")

        _orig = store._now_fn
        base = store.now()
        store._now_fn = lambda: base + timedelta(minutes=31)
        try:
            with pytest.raises(BizError) as err:
                store.submit_h5_task(raw_token=res["raw_token"], fields={"summary": "超时"})
        finally:
            store._now_fn = _orig
        assert "补交链接已过期" in err.value.message
        assert "本次补交机会已使用" in err.value.message
    finally:
        store.close()


# ── 6. 拒绝 → 通知文案由 admin 层发；用户不能再申请 ────────────────────
def test_reject_marks_rejected_and_blocks_further_apply() -> None:
    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store)
        store.request_resubmit(PHONE)

        got = store.reject_resubmit(int(inst["id"]), actor="admin", reason="材料不符")
        assert got["instance"]["resubmit_status"] == "rejected"
        assert store.request_resubmit(PHONE)["result"] == "already"
    finally:
        store.close()


# ── 7. 回归红线：逾期实例用普通 submit token 不放行 ────────────────────
def test_overdue_instance_rejects_plain_submit_token() -> None:
    store = _mk_store()
    try:
        tpl, inst = _mk_overdue(store)
        plain = store.issue_h5_token(PHONE, tpl["template_id"], inst["period_key"], int(inst["id"]))
        assert store.get_h5_token(plain)["purpose"] == "submit"

        with pytest.raises(BizError) as err:
            store.submit_h5_task(raw_token=plain, fields={"summary": "想蒙混过关"})
        assert err.value.code == "WINDOW_CLOSED"
        assert "申请补交" in err.value.message
    finally:
        store.close()


# ── 8. 回归红线：rejected 的驳回重交窗口完全不受影响 ───────────────────
def test_rejected_instance_keeps_original_resubmit_window() -> None:
    store = _mk_store()
    try:
        tpl, inst = _mk_overdue(store, hours_late=1)
        # 先补交成功 → 进入待审核 → 驳回，形成 rejected
        store.request_resubmit(PHONE)
        approved = store.approve_resubmit(int(inst["id"]), actor="admin")
        store.submit_h5_task(raw_token=approved["raw_token"], fields={"summary": "第一稿"})
        reviewed = store.review_instance(int(inst["id"]), action="reject", reviewer="admin", reason="重做")
        assert reviewed["status"] == "rejected"

        # 驳回重交窗口内：普通 submit token 仍可提交（原行为不许变）
        tok = store.issue_h5_token(PHONE, tpl["template_id"], inst["period_key"], int(inst["id"]))
        res = store.submit_h5_task(raw_token=tok, fields={"summary": "改好的"})
        assert res["status"] == "submitted"

        # 超过驳回重交窗口后关闭
        from app.state import calc_resubmit_deadline

        late = calc_resubmit_deadline(
            store.get_instance(inst["id"])["deadline_at"], store.get_instance(inst["id"]).get("reviewed_at")
        )
        _orig = store._now_fn
        store._now_fn = lambda: datetime.strptime(late, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=10)
        try:
            store.conn.execute("UPDATE task_instance SET status='rejected' WHERE id=?", (inst["id"],))
            store.conn.commit()
            tok2 = store.issue_h5_token(PHONE, tpl["template_id"], inst["period_key"], int(inst["id"]))
            with pytest.raises(BizError) as err:
                store.submit_h5_task(raw_token=tok2, fields={"summary": "超窗"})
            assert err.value.code == "WINDOW_CLOSED"
        finally:
            store._now_fn = _orig
    finally:
        store.close()


# ── 9. 机器人回执：三种结果 + ⛔ 不入待归纳池 ───────────────────────────
def _event_env():
    """建一套「已绑定用户 + 逾期实例 + 加密事件」的最小环境。"""
    import hashlib
    import hmac
    import time

    from app.channels.zdxlz import ZdxlzChannel, encrypt_event_payload
    from app.config import Config
    from app.scheduler import FakeNotifier

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(
        app_id="test_app_id", app_secret="test_app_secret",
        api_base="https://open.work.example.com",
        verify_token="test_verify_token_123", encrypt_key="test_encrypt_key_456",
        public_base_url="https://task.example.com", public_port=21001, admin_port=21002,
        data_dir=tmp / "data",
    )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(cfg.db_path)
    channel = ZdxlzChannel(cfg)
    notifier = FakeNotifier()
    return store, channel, notifier, cfg


def _send_text_event(store, channel, notifier, cfg, text: str) -> Tuple[int, Dict[str, Any]]:
    import hashlib
    import hmac
    import time

    from app.api.events import process_app_event
    from app.channels.zdxlz import encrypt_event_payload

    ts, nonce = str(int(time.time())), "nonce_rs"
    body = encrypt_event_payload(cfg.encrypt_key, ts, nonce, {"userId": UID, "text": text})
    sig = hmac.new(cfg.verify_token.encode(), (ts + nonce + body).encode(), hashlib.sha256).hexdigest()
    return process_app_event(
        state=store, channel=channel, public_base_url=cfg.public_base_url,
        timestamp=ts, nonce=nonce, signature=sig, body_text=body, notifier=notifier,
    )


def test_apply_message_reply_and_not_into_pending_pool() -> None:
    store, channel, notifier, cfg = _event_env()
    try:
        _tpl, inst = _mk_overdue(store)
        code, body = _send_text_event(store, channel, notifier, cfg, "申请补交")
        assert code == 200
        assert body["path"] == "resubmit"
        assert body["result"] == "applied"

        # 回执文案
        texts = [m.text for m in notifier.sent_messages]
        assert any("已收到补交申请" in t for t in texts), texts
        # ⛔ 不能落进待归纳池
        assert store.get_pending_member(UID) is None
        # 实例进入 pending
        assert store.get_instance(inst["id"])["resubmit_status"] == "pending"
    finally:
        channel.close()
        store.close()


def test_apply_message_replies_none_when_no_overdue() -> None:
    store, channel, notifier, cfg = _event_env()
    try:
        # 已绑定，但没有逾期实例
        rid = store.create_role("执行组", actor="t")["role_id"]
        store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
        store.upsert_delivery_target(PHONE, UID)

        code, body = _send_text_event(store, channel, notifier, cfg, "申请补交")
        assert (code, body["result"]) == (200, "none")
        assert any("当前没有可申请补交" in m.text for m in notifier.sent_messages)
    finally:
        channel.close()
        store.close()


def test_apply_message_replies_already_after_first_apply() -> None:
    store, channel, notifier, cfg = _event_env()
    try:
        _mk_overdue(store)
        assert _send_text_event(store, channel, notifier, cfg, "申请补交")[1]["result"] == "applied"
        notifier.sent_messages.clear()

        code, body = _send_text_event(store, channel, notifier, cfg, "申请补交")
        assert (code, body["result"]) == (200, "already")
        assert any("已申请过补交" in m.text for m in notifier.sent_messages)
    finally:
        channel.close()
        store.close()


def test_apply_message_over_window_reply() -> None:
    store, channel, notifier, cfg = _event_env()
    try:
        _mk_overdue(store, hours_late=30)
        code, body = _send_text_event(store, channel, notifier, cfg, "申请补交")
        assert (code, body["result"]) == (200, "window_expired")
        assert any("已超补交申请窗口" in m.text for m in notifier.sent_messages)
    finally:
        channel.close()
        store.close()


# ── 10. 审核接口：必须先登录 + 必须带 CSRF ──────────────────────────────
def test_admin_resubmit_endpoints_require_session_and_csrf() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.admin import create_admin_router

    store = _mk_store()
    try:
        _tpl, inst = _mk_overdue(store)
        store.request_resubmit(PHONE)
        uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
        sess = store.create_admin_session(uid)

        app = FastAPI()
        app.include_router(
            create_admin_router(store, notifier=None, public_base_url="http://task.example.com")
        )
        client = TestClient(app)
        approve_url = f"/api/admin/instances/{inst['id']}/resubmit/approve"

        # 未登录
        assert client.post(approve_url).status_code == 401
        # 已登录但缺 CSRF
        r2 = client.post(approve_url, cookies={"sid": sess["sid"]})
        assert (r2.status_code, r2.json().get("code")) == (403, "CSRF_INVALID")
        # 带 CSRF 才放行
        r3 = client.post(approve_url, cookies={"sid": sess["sid"]}, headers={"X-CSRF-Token": sess["csrf"]})
        assert r3.status_code == 200, r3.text
        assert r3.json()["ok"] is True
        assert r3.json()["h5_url"].startswith("http://task.example.com/h5/task#t=")
        # notifier 未注入时 sent=False（不抛异常）
        assert r3.json()["sent"] is False

        reject_url = f"/api/admin/instances/{inst['id']}/resubmit/reject"
        r4 = client.post(reject_url, cookies={"sid": sess["sid"]})
        assert (r4.status_code, r4.json().get("code")) == (403, "CSRF_INVALID")
    finally:
        store.close()
