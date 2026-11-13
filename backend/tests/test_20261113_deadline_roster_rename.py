# -*- coding: utf-8 -*-
"""2026-11-13 修复指令第二部分专项（用户拍板口径）。

一、截止时间三端统一：废除「本月月底/本周日/今天」抽象规则词，统一换算显示
    当前周期对应的具体日期「M月D日 HH:MM」；无截止显示「不限」。
    · 后端纯函数 state.format_deadline_rule（底部已由 test_20261112_livesync_close_push 覆盖换算）；
    · H5 任务页徽章优先用**实例自身** deadline_display（旧周期链接不随当前月份漂移）；
    · 机器人消息 / 后台模板列表 / 编辑预览 / 实例详情同口径（前端由同一函数换算）。

二、机器人全局更名「德阳量密助手」：全部提示文案（ack / 未录入 / 待办空态 / 菜单空态）；
    无待办文案精确为「【德阳量密助手】当前暂无待提交的任务事项。」。

三、H5 提交成功：绿色「✅ 提交成功！」反馈 + 900ms 后自动关窗（微信 WebView 四条路径）
    + 宿主拦截时切换全屏完成视图「✅ 提交成功，可安全关闭页面」。

四、统计看板重设计数据源：GET /stats/roster 单任务当期全量「已交/未交」名单
    （已交排前；owner 隔离与 /stats/dashboard 同口径），供 15 秒自动刷新实时进度。

绝对时刻 fake clock + 内存假件，零真实网络。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.api.h5 import process_h5_task_info  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402

DEFAULT_SUB_ADMIN_PASSWORD = StateStore.DEFAULT_SUB_ADMIN_PASSWORD

PHONE_A = "13900004001"
PHONE_B = "13900004002"
PHONE_C = "13900004003"

CLOCK = {"now": datetime(2026, 11, 13, 8, 0, 0)}


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_p2_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _app(store: StateStore, scheduler: Scheduler) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    app.state.scheduler = scheduler
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _new_sub_admin(app: FastAPI, super_admin: TestClient, username: str) -> TestClient:
    r = super_admin.post("/api/admin/accounts", json={"username": username, "display_name": username})
    assert r.status_code == 200, r.text
    c = _login(app, username, DEFAULT_SUB_ADMIN_PASSWORD)
    r = c.post("/api/admin/change-password",
                json={"old_password": DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    assert r.status_code == 200, r.text
    return c


def _setup(need_review: bool = False) -> Tuple[StateStore, FastAPI, TestClient, TestClient, Scheduler, FakeNotifier, str]:
    """超管 + 属主分级 sa + 三名成员（已激活）+ 月度模板（每月 1 号 / 月底 17:00 截止）。"""
    CLOCK["now"] = datetime(2026, 11, 13, 8, 0, 0)
    store = _store()
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
    sched = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                      now_fn=lambda: CLOCK["now"])
    app = _app(store, sched)
    sa = _login(app, "admin", "Admin@2026")
    sub = _new_sub_admin(app, sa, "sa")
    rid = store.create_role("月报组", actor="t", platform="zdxlz", owner_id=2)["role_id"]
    for i, p in enumerate((PHONE_A, PHONE_B, PHONE_C)):
        store.add_role_member(rid, p, f"成员{i}", "一部", owner_id=2, actor="sub")
        store.upsert_delivery_target(p, f"uid_{p[-4:]}", platform="zdxlz", external_type="union_id")
    r = sub.post("/api/admin/templates", json={
        "name": "月度数据上报", "kind": "upload", "role_ids": [rid], "period": "day",
        "remind_at": "15:00", "has_deadline": True, "deadline_type": "month_end",
        "deadline_time": "17:00", "text_tpl": "请提交本月数据", "enabled": True,
        "need_review": need_review, "exec_dom_days": [1],
        "spec": {"upload_modules": ["fields"],
                 "fields": [{"key": "n", "label": "数量", "type": "number", "required": False}]},
    })
    assert r.status_code == 200, r.text
    return store, app, sa, sub, sched, ntf, r.json()["template_id"]


def _push(store: StateStore, sched: Scheduler, tpl_id: str, at: datetime) -> None:
    CLOCK["now"] = at
    prep = sched.push_now_prepare(tpl_id, actor="sub")
    sched.push_now_deliver(tpl_id, prep, actor="sub")


# ═══════════════════ 一、截止时间具体日期（H5 实例级优先） ═══════════════════

def test_h5_badge_uses_instance_concrete_deadline() -> None:
    """月末任务：H5 徽章显示实例自身截止「11月30日 17:00」（deadline_display 优先于模板级规则）；
    纯函数层验证：即便到 12 月，11 月实例的截止显示仍是 11月30日，而模板规则会漂成 12月31日
    ——这正是 H5 徽章必须优先用实例级截止的原因（旧周期链接不漂移）。"""
    from app.scheduler import format_deadline_display
    from app.state import format_deadline_rule

    store, app, sa, sub, sched, ntf, tpl_id = _setup()
    try:
        _push(store, sched, tpl_id, datetime(2026, 11, 13, 15, 0, 0))
        inst = next(i for i in store.list_instances_for_period(tpl_id, "2026-11-13")
                    if i["phone"] == PHONE_A)
        assert inst["deadline_at"] == "2026-11-30 17:00:00"
        raw = store.issue_h5_token(PHONE_A, tpl_id, "2026-11-13", inst["id"])
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200, info
        tpl_payload = info["template"]
        assert tpl_payload["deadline_display"] == "11月30日 17:00", tpl_payload
        # 实例级显示是纯函数（不随当前日期漂移）；模板级规则在 12 月会漂——故 H5 优先前者
        assert format_deadline_display(inst["deadline_at"]) == "11月30日 17:00"
        assert format_deadline_rule(
            {"has_deadline": 1, "deadline_type": "month_end", "deadline_time": "17:00"},
            ref_dt=datetime(2026, 12, 5)) == "12月31日 17:00"
        # task.html 徽章必须优先取 deadline_display（静态契约）
        html = (Path(__file__).resolve().parents[1] / "app" / "h5" / "task.html").read_text(encoding="utf-8")
        assert "deadline_display || data.template.deadline_rule" in html, html[html.find("deadline_display") - 80:][:200]
    finally:
        store.close()


def test_h5_badge_no_deadline_via_api() -> None:
    """无截止模板（has_deadline=False）→ 实例截止存哨兵，H5 徽章「不限」。"""
    store, app, sa, sub, sched, ntf, tpl_id = _setup()
    try:
        rid = store.create_role("无截止组", actor="sa", platform="zdxlz", owner_id=2)["role_id"]
        store.add_role_member(rid, PHONE_A, "甲一", "一部", owner_id=2, actor="sub")
        store.upsert_delivery_target(PHONE_A, "uid_4001", platform="zdxlz", external_type="union_id")
        r = sub.post("/api/admin/templates", json={
            "name": "随到随报", "kind": "upload", "role_ids": [rid], "period": "day",
            "remind_at": "15:00", "has_deadline": False, "text_tpl": "随到随报",
            "enabled": True, "need_review": False,
            "spec": {"upload_modules": ["fields"], "fields": []},
        })
        assert r.status_code == 200, r.text
        tid = r.json()["template_id"]
        _push(store, sched, tid, datetime(2026, 11, 14, 15, 0, 0))
        inst = next(i for i in store.list_instances_for_period(tid, "2026-11-14")
                    if i["phone"] == PHONE_A)
        raw = store.issue_h5_token(PHONE_A, tid, "2026-11-14", inst["id"])
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200, info
        assert info["template"]["deadline_display"] == "不限", info["template"]
    finally:
        store.close()


# ═══════════════════ 二、机器人更名「德阳量密助手」 ═══════════════════

def _inbound(store: StateStore, user_id: str, text: str) -> Tuple[int, Dict[str, Any], List[str]]:
    from app.api.events import handle_inbound_text
    sent: List[Tuple[str, str]] = []
    code, body = handle_inbound_text(
        state=store, user_id=user_id, msg_text=text,
        send=lambda t, tag: sent.append((t, tag)),
        platform="zdxlz", base_url="http://e.com",
    )
    return code, body, [t for t, _ in sent]


def test_bot_rename_all_texts() -> None:
    """机器人全部提示文案更名「德阳量密助手」：未录入提示 / 闲聊 ack / 待办空态 / 菜单空态。"""
    store = _store()
    try:
        rid = store.create_role("小组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, PHONE_A, "甲一", "一部", actor="t")
        store.upsert_delivery_target(PHONE_A, "uid_zdxlz_9001", platform="zdxlz", external_type="union_id")

        # ① 未录入（未绑定）→ 固定提醒
        code, body, msgs = _inbound(store, "uid_unknown", "你好")
        assert code == 200 and body["bound"] is False, body
        assert msgs[-1].startswith("【德阳量密助手】"), msgs[-1]
        assert "尚未录入您的业务权限" in msgs[-1], msgs[-1]

        # ② 已绑定闲聊 → ack 自我介绍
        code, body, msgs = _inbound(store, "uid_zdxlz_9001", "你好")
        assert code == 200 and body["bound"] is True, body
        assert msgs[-1].startswith("【德阳量密助手】"), msgs[-1]
        assert "我是德阳量密助手" in msgs[-1], msgs[-1]
        assert "OPS任务调度助手" not in msgs[-1], msgs[-1]

        # ③ 【待办】无未完成任务 → 精确文案（用户拍板逐字）
        code, body, msgs = _inbound(store, "uid_zdxlz_9001", "【待办】")
        assert code == 200 and body["path"] == "todo" and body["count"] == 0, body
        assert msgs[-1] == "【德阳量密助手】当前暂无待提交的任务事项。", msgs[-1]

        # ④ 【菜单】无自主上报事项 → 空态文案
        code, body, msgs = _inbound(store, "uid_zdxlz_9001", "【菜单】")
        assert code == 200 and body["path"] == "menu" and body["count"] == 0, body
        assert msgs[-1] == "【德阳量密助手】当前暂无可自主上报的任务事项。", msgs[-1]

        # ⑤ 菜单编号不存在 → 未找到文案
        code, body, msgs = _inbound(store, "uid_zdxlz_9001", "99")
        assert code == 200 and body.get("found") is False, body
        assert msgs[-1] == "【德阳量密助手】未找到编号为 99 的事项，请回复【菜单】查看最新列表。", msgs[-1]
    finally:
        store.close()


# ═══════════════════ 三、H5 提交成功自动关窗（静态契约） ═══════════════════

def test_task_html_success_autoclose_contract() -> None:
    """H5 提交成功链路契约：绿色成功反馈 + 四条关窗路径 + 900ms 延迟 + 全屏兜底完成视图。"""
    src = (Path(__file__).resolve().parents[1] / "app" / "h5" / "task.html").read_text(encoding="utf-8")
    for marker in (
        "submit-success-banner",
        "✅ 提交成功！",
        "WeixinJSBridge.call(\"closeWindow\")",
        "window.wx.closeWindow",
        "window.history.back()",
        "submit-done-mask",
        "✅ 提交成功，可安全关闭页面",
        "}, 900);",
    ):
        assert marker in src, f"task.html 缺少自动关窗链路标记: {marker}"


# ═══════════════════ 四、统计看板 · 实时名单接口 ═══════════════════

def test_stats_roster_reported_and_missing() -> None:
    """单任务当期实时名单：已交（提交时间）在前、未交（状态/截止）在后；owner 隔离口径生效。"""
    store, app, sa, sub, sched, ntf, tpl_id = _setup()
    try:
        _push(store, sched, tpl_id, datetime(2026, 11, 13, 15, 0, 0))
        inst_a = next(i for i in store.list_instances_for_period(tpl_id, "2026-11-13")
                      if i["phone"] == PHONE_A)
        raw = store.issue_h5_token(PHONE_A, tpl_id, "2026-11-13", inst_a["id"])
        res = store.submit_h5_task(raw_token=raw, fields={"n": 5})
        assert res["status"] in ("submitted", "approved"), res

        r = sub.get(f"/api/admin/stats/roster?template_id={tpl_id}&period_key=2026-11-13")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3, body
        assert body["scope_owner_id"] == 2, body
        items = body["items"]
        assert len(items) == 3
        # 已交排前
        assert items[0]["phone"] == PHONE_A, items
        assert items[0]["status"] in ("submitted", "approved"), items[0]
        assert items[0]["submitted_at"], items[0]
        assert items[0]["late_submit"] is False, items[0]
        by = {i["phone"]: i for i in items}
        for p in (PHONE_B, PHONE_C):
            assert by[p]["status"] == "pending", by[p]
            assert by[p]["deadline_at"] == "2026-11-30 17:00:00", by[p]
            assert by[p]["submitted_at"] is None, by[p]
    finally:
        store.close()


def test_stats_roster_status_roundtrip() -> None:
    """状态原样回传：approved/overdue/cancelled 各自呈现（前端据此分「已交/未交/其他」三栏）。"""
    store, app, sa, sub, sched, ntf, tpl_id = _setup()
    try:
        _push(store, sched, tpl_id, datetime(2026, 11, 13, 15, 0, 0))
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE task_instance SET status='approved', submitted_at=? "
                "WHERE template_id=? AND phone=?", ("2026-11-13 15:05:00", tpl_id, PHONE_A))
            store.conn.execute(
                "UPDATE task_instance SET status='overdue' WHERE template_id=? AND phone=?",
                (tpl_id, PHONE_B))
            store.conn.execute(
                "UPDATE task_instance SET status='cancelled' WHERE template_id=? AND phone=?",
                (tpl_id, PHONE_C))
        r = sub.get(f"/api/admin/stats/roster?template_id={tpl_id}&period_key=2026-11-13")
        body = r.json()
        by = {i["phone"]: i for i in body["items"]}
        assert by[PHONE_A]["status"] == "approved" and by[PHONE_A]["submitted_at"] == "2026-11-13 15:05:00"
        assert by[PHONE_B]["status"] == "overdue"
        assert by[PHONE_C]["status"] == "cancelled"
        assert body["items"][0]["phone"] == PHONE_A, "已交（approved）必须排最前"
    finally:
        store.close()


def test_stats_roster_requires_template_and_period_filters() -> None:
    """未选任务 → 空名单；按 period_key 过滤只回当期实例。"""
    store, app, sa, sub, sched, ntf, tpl_id = _setup()
    try:
        _push(store, sched, tpl_id, datetime(2026, 11, 13, 15, 0, 0))
        _push(store, sched, tpl_id, datetime(2026, 11, 14, 15, 0, 0))  # 第二期手推（11-14 建新周期实例）
        r0 = sub.get("/api/admin/stats/roster")
        assert r0.status_code == 200 and r0.json()["total"] == 0, r0.text
        r1 = sub.get(f"/api/admin/stats/roster?template_id={tpl_id}&period_key=2026-11-13")
        assert r1.json()["total"] == 3, r1.text
        r2 = sub.get(f"/api/admin/stats/roster?template_id={tpl_id}")
        assert r2.json()["total"] == 6, r2.text
    finally:
        store.close()