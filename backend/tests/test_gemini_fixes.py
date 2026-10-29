# -*- coding: utf-8 -*-
"""2026-10-29 外部代码评审（Gemini）18 项修复的回归用例。

覆盖批次：
  第一批 P0-2 CSV 导入格式 / P0-1 H5 逾期补交 / P0-3 移出成员回池 / P1-1 事务原子性
  第二批 P0-4 移除待归纳清绑定 / P1-2 审核状态校验 / P1-3 调度不阻塞事件循环
  第三批 P1-6 删除模板清实例 / P2-1 路径穿越 / P2-2 回调防重放 / P2-3 登录锁定 / P2-4 CSV 注入

约定：全部用例自建临时库，不碰 data/im_task.db。
"""

from __future__ import annotations

import asyncio
import time
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import SHANGHAI_TZ
from app.state import BizError, StateStore


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp()
    return StateStore(Path(tmp) / "im.db")


def _dt(h: int, m: int, day: int = 28) -> datetime:
    return datetime(2026, 10, day, h, m, 0, tzinfo=SHANGHAI_TZ)


def _form_tpl(store: StateStore, *, need_review: bool = True, deadline: str = "18:00") -> dict:
    rid = store.create_role("测试组", actor="t")["role_id"]
    store.add_role_member(rid, "13900001001", "甲", "一部", actor="t")
    store.upsert_delivery_target("13900001001", "uid_jia")
    return store.create_template(
        {
            "name": "日报", "kind": "form", "role_ids": [rid],
            "schema_json": [{"key": "summary", "label": "小结", "type": "text", "required": True}],
            "text_tpl": "{姓名}请交{任务名}", "remind_at": "17:30", "deadline": deadline,
            "period": "day", "need_review": need_review,
        },
        actor="t",
    )


# ── 第一批 ──────────────────────────────────────────────────────────


def test_csv_import_dict_format():
    """P0-2：CSV 导入必须是 dict 行；二维数组会被判成格式错误（前端已改为 dict）。"""
    store = _mk_store()
    try:
        rid = store.create_role("导入组", actor="t")["role_id"]
        rows = [
            {"row": 1, "phone": "13900002001", "name": "甲", "dept": "一部"},
            {"row": 2, "phone": "13900002002", "name": "乙", "dept": "二部"},
            {"row": 3, "phone": "13900002003", "name": "丙", "dept": ""},
        ]
        res = store.import_role_members(role_id=rid, csv_rows=rows, dry_run=False, actor="t")
        assert res["will_add"] == 3
        assert res["invalid"] == []
        assert store.list_role_members(rid)["total"] == 3

        # 旧格式（二维数组）应全部判为格式错误，而不是 500
        bad = store.import_role_members(
            role_id=rid,
            csv_rows=[["13900002004", "丁", "三部"]],  # type: ignore[list-item]
            dry_run=True,
            actor="t",
        )
        assert bad["will_add"] == 0
        assert len(bad["invalid"]) == 1
    finally:
        store.close()


def test_h5_overdue_window_can_submit():
    """P0-1：逾期实例在 72 小时补交窗口内仍可提交（前端不再锁死页面）。"""
    store = _mk_store()
    try:
        tpl = _form_tpl(store)
        pkey = "2026-10-28"
        store.ensure_template_instances(tpl["template_id"], pkey, "2026-10-28 18:00:00")
        inst = store.get_instance_by_key(tpl["template_id"], "13900001001", pkey)
        assert inst is not None

        # 推进到逾期
        adv = store.advance_instance_statuses(_dt(19, 0))
        assert adv["to_overdue"] >= 1
        assert store.get_instance(inst["id"])["status"] == "overdue"

        # 逾期后 3 小时（仍在 72h 内）应允许提交
        tok = store.issue_h5_token("13900001001", tpl["template_id"], pkey, inst["id"])
        res = store.submit_h5_task(raw_token=tok, fields={"summary": "补交"})
        assert res["ok"] is True
        assert res["late_submit"] is True

        # 超过 72 小时则拒绝
        store.conn.execute("UPDATE task_instance SET status='overdue' WHERE id=?", (inst["id"],))
        store.conn.commit()
        tok2 = store.issue_h5_token("13900001001", tpl["template_id"], pkey, inst["id"])
        _orig_now = store._now_fn
        store._now_fn = lambda: _dt(19, 0, day=31)  # 截止后 73 小时
        try:
            with pytest.raises(BizError) as err:
                store.submit_h5_task(raw_token=tok2, fields={"summary": "超期"})
        finally:
            store._now_fn = _orig_now
        assert err.value.code == "WINDOW_CLOSED"
    finally:
        store.close()


def test_delete_member_moves_to_pending():
    """P0-3：移出成员 → 回到待归纳池，且推送绑定置为不可用。"""
    store = _mk_store()
    try:
        tpl = _form_tpl(store)
        rid = tpl["role_ids"][0]
        store.delete_role_member(rid, "13900001001", actor="t")

        pending = store.get_pending_member("uid_jia")
        assert pending is not None, "移出成员后应回到待归纳池"
        assert pending["phone"] == "13900001001"

        target = store.get_delivery_target("13900001001", include_unusable=True)
        assert target is not None and int(target["usable"]) == 0
    finally:
        store.close()


def test_transaction_atomicity(monkeypatch):
    """P1-1：多步写操作中途失败必须整体回滚（DEFERRED 事务）。"""
    store = _mk_store()
    try:
        tpl = _form_tpl(store)
        rid = tpl["role_ids"][0]

        def _boom(*args, **kwargs):
            raise RuntimeError("审计写入失败")

        monkeypatch.setattr(store, "_insert_audit_unlocked", _boom)
        with pytest.raises(RuntimeError):
            store.delete_role_member(rid, "13900001001", actor="t")

        # DELETE 应被回滚，成员仍在
        assert store.list_role_members(rid)["total"] == 1
    finally:
        store.close()


# ── 第二批 ──────────────────────────────────────────────────────────


def test_remove_pending_clears_delivery_target():
    """P0-4：移除待归纳人员时，其推送绑定必须置为不可用。"""
    store = _mk_store()
    try:
        store.upsert_pending_member("uid_pending", phone="13900003001", name="待定甲")
        store.upsert_delivery_target("13900003001", "uid_pending")
        assert store.get_delivery_target("13900003001") is not None

        store.remove_pending_member("uid_pending", actor="t")
        assert store.get_pending_member("uid_pending") is None
        target = store.get_delivery_target("13900003001", include_unusable=True)
        assert target is not None and int(target["usable"]) == 0
    finally:
        store.close()


def test_review_instance_rejects_wrong_status():
    """P1-2：只有 submitted 状态可审核。"""
    store = _mk_store()
    try:
        tpl = _form_tpl(store)
        pkey = "2026-10-28"
        store.ensure_template_instances(tpl["template_id"], pkey, "2026-10-28 18:00:00")
        inst = store.get_instance_by_key(tpl["template_id"], "13900001001", pkey)
        assert inst is not None and inst["status"] == "pending"

        with pytest.raises(BizError) as err:
            store.review_instance(inst["id"], action="approve", reviewer="admin")
        assert err.value.code == "VALIDATION_FAILED"

        # 提交后可审核
        tok = store.issue_h5_token("13900001001", tpl["template_id"], pkey, inst["id"])
        store.submit_h5_task(raw_token=tok, fields={"summary": "初稿"})
        res = store.review_instance(inst["id"], action="approve", reviewer="admin")
        assert res["status"] == "approved"

        # 已通过后不能再审
        with pytest.raises(BizError):
            store.review_instance(inst["id"], action="reject", reviewer="admin", reason="再审一次")
    finally:
        store.close()


def test_scheduler_async_doesnt_block():
    """P1-3：调度跑在 to_thread 里，期间事件循环仍能推进其它协程。"""
    from app.main import _scheduler_loop

    class _SlowScheduler:
        def __init__(self) -> None:
            self.calls = 0

        def run_once(self):
            self.calls += 1
            time.sleep(0.30)  # 模拟同步 HTTP 投递
            return {}

    async def _main() -> float:
        sched = _SlowScheduler()
        stop = asyncio.Event()
        task = asyncio.create_task(_scheduler_loop(sched, stop))  # type: ignore[arg-type]
        t0 = time.monotonic()
        await asyncio.sleep(0.10)
        elapsed = time.monotonic() - t0
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        return elapsed

    elapsed = asyncio.run(_main())
    # 若调度占住事件循环，这个 sleep 会被拖到 300ms 以上
    assert elapsed < 0.20, f"事件循环被调度阻塞：{elapsed:.3f}s"


# ── 第三批 ──────────────────────────────────────────────────────────


def test_delete_template_expires_open_instances():
    """P1-6：删除模板 → 其未完成实例置 expired，已完成的不动。"""
    store = _mk_store()
    try:
        tpl = _form_tpl(store, need_review=False)
        pkey = "2026-10-28"
        store.ensure_template_instances(tpl["template_id"], pkey, "2026-10-28 18:00:00")
        inst = store.get_instance_by_key(tpl["template_id"], "13900001001", pkey)
        assert inst is not None

        store.delete_template(tpl["template_id"], actor="t")
        assert store.get_instance(inst["id"])["status"] == "expired"
    finally:
        store.close()


def test_attach_image_path_traversal_blocked():
    """P2-1：attach_image_path 不允许 .. / 绝对路径 / 指到 data 目录之外。"""
    store = _mk_store()
    try:
        rid = store.create_role("图组", actor="t")["role_id"]
        base = {
            "name": "带图通知", "kind": "notice", "role_ids": [rid],
            "text_tpl": "看今日数据", "remind_at": "09:00", "notice_times": ["09:00"], "period": "day",
        }
        for bad in ("../secret.png", "/etc/passwd", "..\\..\\win.ini"):
            with pytest.raises(BizError) as err:
                store.create_template({**base, "attach_image_path": bad}, actor="t")
            assert err.value.code == "VALIDATION_FAILED"

        # 正常相对路径可保存
        ok = store.create_template({**base, "attach_image_path": "notice_attach/a.png"}, actor="t")
        assert ok["attach_image_path"] == "notice_attach/a.png"
    finally:
        store.close()


def test_event_timestamp_replay_blocked():
    """P2-2：时间戳超出 ±300 秒视为重放。"""
    from app.channels.zdxlz import is_event_timestamp_fresh

    now_s = int(time.time())
    assert is_event_timestamp_fresh(str(now_s), 300) is True
    assert is_event_timestamp_fresh(str(now_s - 400), 300) is False
    assert is_event_timestamp_fresh(str(now_s * 1000), 300) is True          # 毫秒也认
    assert is_event_timestamp_fresh(str((now_s - 400) * 1000), 300) is False
    assert is_event_timestamp_fresh("", 300) is False
    assert is_event_timestamp_fresh("not-a-number", 300) is False
    assert is_event_timestamp_fresh(str(now_s - 100000), 0) is True          # 0 = 关闭


def test_login_lockout():
    """P2-3：连续 5 次密码错误 → 第 6 次 429。"""
    from app.api.admin import create_admin_router, register_admin_guard

    store = _mk_store()
    try:
        store.upsert_admin_user("lockuser", "Correct@123", must_change=False, actor="t")
        app = FastAPI()
        app.state.store = store
        register_admin_guard(app, store)
        app.include_router(create_admin_router(store))
        client = TestClient(app)

        for i in range(5):
            r = client.post("/api/admin/login", json={"username": "lockuser", "password": "wrong-pass"})
            assert r.status_code == 401, f"第 {i + 1} 次应仍是 401"

        r = client.post("/api/admin/login", json={"username": "lockuser", "password": "wrong-pass"})
        assert r.status_code == 429, "第 6 次应被锁定"
        assert r.json()["code"] == "RATE_LIMITED"

        # 锁定期间即使密码正确也进不来
        r = client.post("/api/admin/login", json={"username": "lockuser", "password": "Correct@123"})
        assert r.status_code == 429
    finally:
        store.close()


def test_csv_export_formula_injection_escaped():
    """P2-4：以 = + - @ 开头的单元格前置单引号，防止 Excel 当公式执行。"""
    from app.api.admin import _csv_row, _csv_safe

    assert _csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _csv_safe("+1") == "'+1"
    assert _csv_safe("-1") == "'-1"
    assert _csv_safe("@cmd") == "'@cmd"
    assert _csv_safe("正常文本") == "正常文本"
    assert _csv_safe(123) == 123
    assert _csv_row([1, "=x"]) == [1, "'=x"]
