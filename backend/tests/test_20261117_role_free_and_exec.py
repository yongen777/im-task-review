# -*- coding: utf-8 -*-
"""2026-11-17 · 用户四条指令（2026-11-16 17:23 下达、当晚解析成待办、次日执行）的专项用例：

  ① 角色名册展示全部可达，兼容 `size=5000` 全量调用；管理页按后续要求服务端每页 20 人。
     翻页和搜索均以完整名册为范围，独立于单页渲染数量。
  ② CSV 批量导入 loading —— 纯前端（静态契约用例守护：spinner + 进行中提示）。
  ③ 统计面板任务清单带出执行时间 + 执行周期（每天 / 每周几 / 每月几号）——
     `/stats-panel/tasks` 返回 remind_at / exec_weekdays / exec_dom_days。
  ④ 删除角色不再排查任务绑定（旧 409 ROLE_IN_USE 废除）：模板 role_ids 同步移除该角色，
     唯一角色被删则模板放空（"[]"）继续存在（平台过滤列表也必须可见，否则后台无从再绑）；
     任务模板允许 role_ids=[]（放空），放空模板零实例、零派发。

全部临时库（mysql_testbank），时刻走模块级 CLOCK；不碰 data/im_task.db。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import BizError, StateStore, parse_role_ids  # noqa: E402

CLOCK = {"now": datetime(2026, 11, 17, 10, 0, 0)}


def _store() -> StateStore:
    return StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_rf_")),
                      now_fn=lambda: CLOCK["now"])


def _app(store: StateStore) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t"))
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _seed_role(store: StateStore, name: str, *phones: str) -> str:
    rid = store.create_role(name, actor="t")["role_id"]
    for ph in phones:
        store.add_role_member(rid, ph, f"用户{ph[-4:]}", "测试部", actor="t")
        store.upsert_delivery_target(ph, f"uid_zdxlz_{ph[-4:]}", platform="zdxlz")
    return rid


def _tpl(store: StateStore, rids, name: str = "执行周期验证") -> dict:
    return store.create_template(
        {
            "name": name, "kind": "form", "role_ids": list(rids), "period": "day",
            "remind_at": "09:00", "deadline": "18:00", "has_deadline": True,
            "deadline_type": "same_day", "deadline_time": "18:00",
            "text_tpl": "请提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        },
        actor="t",
    )


def _role_ids_of(store: StateStore, tid: str) -> list:
    with store.lock:
        row = store.conn.execute("SELECT role_ids FROM task_template WHERE template_id = ?", (tid,)).fetchone()
    return parse_role_ids(str((row["role_ids"] if row else "") or ""))


def _inst_status(store: StateStore, tid: str) -> dict:
    with store.lock:
        rows = store.conn.execute(
            "SELECT phone, status FROM task_instance WHERE template_id = ? ORDER BY phone", (tid,)).fetchall()
    return {str(r["phone"]): str(r["status"]) for r in rows}


def _audit_rows(store: StateStore, action: str) -> list:
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            "SELECT username, action, target_type, target_id, detail FROM admin_audit WHERE action = ?",
            (action,)).fetchall()]


# ════════════════ ① 成员上限：size=5000 必须真返回全量（旧钳 200） ════════════════

def test_role_member_full_fetch_not_clamped_to_200() -> None:
    """用户实锤「角色分组不要设置成员上限，不然显示不完整」：
    前端已按 size=5000 请求全量，后端却把 size 钳到 200 → 2600 人的组只能看到前 200。"""
    store = _store()
    rid = store.create_role("大组", actor="t")["role_id"]
    n = 210
    with store.lock, store.conn:
        for i in range(n):
            store.conn.execute(
                "INSERT INTO member(role_id, phone, name, dept, enabled, source, owner_id, created_at) "
                "VALUES (?, ?, ?, '测试部', 1, 'manual', 1, '2026-11-01 08:00:00')",
                (rid, f"1390001{i:04d}", f"成员{i:03d}"))
    res = store.list_role_members(rid, page=1, size=5000)
    assert res["total"] == n
    assert len(res["items"]) == n, "size=5000 被后端截断：前端「显示不完整」的根因"
    assert len(store.list_role_members(rid, size=0)["items"]) == n
    assert len(store.list_role_members(rid, page=2, size=200)["items"]) == 10
    [role] = store.list_roles()
    assert role["member_count"] == role["active_count"] == n
    assert role["activated_count"] == 0


def test_role_members_server_pagination_search_and_lookup_membership() -> None:
    store, _, owner, rid = _seed_stats_ctx()
    try:
        owner_id = store.get_role(rid)["owner_id"]
        ids = [f"1390003{i:04d}" for i in range(43)]
        with store.conn:
            store.conn.executemany(
                "INSERT INTO member(role_id, phone, name, source, owner_id, created_at) "
                "VALUES (?, ?, ?, 'manual', ?, '2026-11-17 08:00:00')",
                [(rid, phone, f"分页成员{i:02d}", owner_id) for i, phone in enumerate(ids)],
            )
        store.upsert_delivery_target(ids[-1], "u_paginated")
        path = f"/api/admin/roles/{rid}/members"
        for page, expected in enumerate((ids[:20], ids[20:40], ids[40:], []), start=1):
            response = owner.get(path, params={"page": page, "size": 20})
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 43
            assert [item["phone"] for item in response.json()["items"]] == expected
        search = owner.get(path, params={"page": 1, "size": 20, "q": ids[-1]})
        assert search.json()["total"] == 1 and search.json()["items"][0]["phone"] == ids[-1]
        search_by_name = owner.get(path, params={"page": 1, "size": 20, "q": "分页成员42"})
        assert search_by_name.json()["total"] == 1
        global_search = "/api/admin/global-activated-members"
        query = {"q": ids[-1], "platform": "zdxlz", "role_id": rid}
        result = owner.get(global_search, params=query)
        assert result.status_code == 200 and result.json()["items"][0]["in_role"] is True
        second = owner.post("/api/admin/roles", json={"name": "独立角色"}).json()["role_id"]
        assert owner.get(global_search, params={**query, "role_id": second}).json()["items"][0]["in_role"] is False
        foreign = store.create_role("其他管理员", owner_id=1)["role_id"]
        assert owner.get(global_search, params={**query, "role_id": foreign}).status_code == 403
    finally:
        store.close()


# ════════════════ ④ 删除角色：不再 409，模板同步移除角色 ════════════════

def test_delete_role_no_409_and_sync_prunes_template() -> None:
    """2026-11-16 指令④原文：「删除角色的时候不用再排查是否绑定了任务。
    任务绑定角色及时同步移除该角色/放空。」"""
    store = _store()
    r1 = _seed_role(store, "一组", "13900001001")
    r2 = _seed_role(store, "二组", "13900002002")
    tpl = _tpl(store, [r1, r2])
    made = store.ensure_template_instances(tpl["template_id"], "2026-11-17", "2026-11-17 18:00:00")
    assert {m["phone"] for m in made} == {"13900001001", "13900002002"}

    res = store.delete_role(r1, actor="t")            # 旧实现此处 409 ROLE_IN_USE
    assert res["ok"] is True
    assert _role_ids_of(store, tpl["template_id"]) == [r2], \
        "模板必须同步移除被删角色、保留剩余角色（不删模板、不 409）"
    st = _inst_status(store, tpl["template_id"])
    assert st["13900001001"] == "cancelled", "被删角色成员的未完成实例走 11-12 留痕口径"
    assert st["13900002002"] == "pending", "剩余角色成员实例不受影响"
    assert [r for r in store.list_roles() if r["role_id"] == r1] == [], "角色行必须删除"
    audits = _audit_rows(store, "role.purge")
    assert audits, "删除角色必须记 role.purge 审计"
    assert tpl["template_id"] in str(audits[-1]["detail"]), "审计应带出被剪枝的模板"


def test_delete_role_last_role_empties_template_but_keeps_it() -> None:
    """唯一角色被删 → 模板放空（"[]"）继续存在；放空模板零实例、零派发；
    平台过滤的模板列表也必须看到它（否则后台根本无从再绑角色）。"""
    store = _store()
    r1 = _seed_role(store, "唯一组", "13900003003")
    tpl = _tpl(store, [r1], "放空验证")
    store.ensure_template_instances(tpl["template_id"], "2026-11-17", "2026-11-17 18:00:00")
    store.delete_role(r1, actor="t")
    assert _role_ids_of(store, tpl["template_id"]) == [], "唯一角色被删后模板应放空而不是消失"
    item = store.get_template(tpl["template_id"])
    assert item is not None and item["enabled"] is True
    listed = store.list_templates(page=1, size=50, platform="zdxlz")["items"]
    assert any(t["template_id"] == tpl["template_id"] for t in listed), \
        "放空模板在平台过滤列表里不可见（后台将无从再绑角色）"
    sched = Scheduler(state=store, public_base_url="http://t.example.com", now_fn=lambda: CLOCK["now"])
    assert sched.ensure_instances_for_template(item, CLOCK["now"]) == [], "放空模板不得生成实例"
    assert sched.push_now_prepare(tpl["template_id"])["instance_count"] == 0
    assert _inst_status(store, tpl["template_id"]) == {"13900003003": "cancelled"}


def test_delete_role_keeps_multi_role_member_instances() -> None:
    """一人多角色：被删角色之外仍挂在模板剩余角色 → 实例与成员行都必须保留。"""
    store = _store()
    r1 = _seed_role(store, "甲组", "13900001001")
    r2 = _seed_role(store, "乙组", "13900002002")
    store.add_role_member(r1, "13900003003", "用户3003", "测试部", actor="t")
    store.upsert_delivery_target("13900003003", "uid_zdxlz_3003", platform="zdxlz")
    store.add_role_member(r2, "13900003003", "用户3003", "测试部", actor="t")
    tpl = _tpl(store, [r1, r2])
    store.ensure_template_instances(tpl["template_id"], "2026-11-17", "2026-11-17 18:00:00")
    store.delete_role(r1, actor="t")
    st = _inst_status(store, tpl["template_id"])
    assert st["13900003003"] == "pending", "一人多角色：仍挂在剩余角色，实例必须保留"
    assert st["13900001001"] == "cancelled"
    with store.lock:
        rows = store.conn.execute("SELECT role_id FROM member WHERE phone = '13900003003'").fetchall()
    assert [str(r["role_id"]) for r in rows] == [r2]


# ════════════════ ④ 模板放空：role_ids=[] 可建可存、零派发、可恢复 ════════════════

def test_removing_last_role_keeps_three_tick_delivery_idempotent() -> None:
    store = _store()
    try:
        role_id = _seed_role(store, "三轮派发", "13900006006")
        tpl = _tpl(store, [role_id])
        store.conn.execute(
            "UPDATE delivery_target SET activated_at = ? WHERE phone = ?",
            ("2026-11-16 08:00:00", "13900006006"),
        )
        notifier = FakeNotifier()
        sched = Scheduler(state=store, notifier=notifier, public_base_url="http://t.example.com")
        trigger = CLOCK["now"].replace(hour=9)
        for i in range(3):
            sched.run_once(trigger + timedelta(seconds=30 * i))
        assert len(notifier.sent_messages) == 1
        store.delete_role(role_id, actor="t")
        for i in range(3):
            sched.run_once(trigger + timedelta(days=1, seconds=30 * i))
        assert len(notifier.sent_messages) == 1
        assert store.list_instances_for_period(tpl["template_id"], "2026-11-18") == []
    finally:
        store.close()


def test_template_zero_roles_allowed_and_never_dispatches() -> None:
    """用户原文「现在任务可以放空，就是不需要必须绑定角色」——
    旧「必须至少选择 1 个适用角色」校验废除，放空模板不建实例、不发送，后续可恢复绑定。"""
    store = _store()
    tpl = _tpl(store, [], "无角色模板")
    assert parse_role_ids(tpl["role_ids"]) == []
    item = store.get_template(tpl["template_id"])
    assert item is not None and item["role_ids"] == []
    sched = Scheduler(state=store, public_base_url="http://t.example.com", now_fn=lambda: CLOCK["now"])
    assert sched.ensure_instances_for_template(item, CLOCK["now"]) == []
    # 放空后可恢复绑定（编辑模板挂角色）
    r = _seed_role(store, "恢复组", "13900004004")
    store.update_template(tpl["template_id"], {"role_ids": [r]}, actor="t")
    assert _role_ids_of(store, tpl["template_id"]) == [r]
    # 再放空回去（模板编辑清空角色）
    store.update_template(tpl["template_id"], {"role_ids": []}, actor="t")
    assert _role_ids_of(store, tpl["template_id"]) == []


# ════════════════ ③ 统计面板任务清单：执行时间 + 执行周期字段 ════════════════

def _seed_stats_ctx():
    store = _store()
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    app = _app(store)
    admin = _login(app, "admin", "Admin@2026")
    admin.post("/api/admin/accounts", json={"username": "rd1", "display_name": "rd1"})
    c = _login(app, "rd1", StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
    c.post("/api/admin/change-password",
           json={"old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    sa = _login(app, "rd1", "NewPass@2026")
    rid = sa.post("/api/admin/roles", json={"name": "统计组"}).json()["role_id"]
    return store, admin, sa, rid


def _stats_body(name: str, rid: str, **extra) -> dict:
    body = {
        "name": name, "kind": "form", "period": "day", "role_ids": [rid],
        "remind_at": "08:30", "deadline": "18:00", "has_deadline": True,
        "deadline_type": "same_day", "deadline_time": "18:00",
        "text_tpl": "请提交", "need_review": False, "allow_note": True, "enabled": True,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": True}],
        "spec": {"upload_modules": ["fields"], "fields": [{"key": "qty", "label": "数量", "type": "number", "required": True}]},
    }
    body.update(extra)
    return body


def test_stats_panel_tasks_exposes_exec_time_and_cycle() -> None:
    """用户原文「执行时间加入，执行周期是每天还是每周的周几还是每月的几号」——
    任务清单接口必须带出三个字段，前端据此渲染「执行时间 + 执行周期」。"""
    store, admin, sa, rid = _seed_stats_ctx()
    r = sa.post("/api/admin/templates", json=_stats_body("每周任务", rid, exec_weekdays=[1, 3]))
    assert r.status_code == 200, r.text
    r = sa.post("/api/admin/templates", json=_stats_body("月度任务", rid, exec_dom_days=[5, 31], exec_weekdays=None))
    assert r.status_code == 200, r.text
    r = sa.post("/api/admin/templates", json=_stats_body(
        "不定期任务", rid, is_adhoc=True, remind_at="", has_deadline=False,
        deadline="", deadline_type=None, deadline_time=None, exec_weekdays=None, exec_dom_days=[]))
    assert r.status_code == 200, r.text

    res = sa.get("/api/admin/stats-panel/tasks")
    assert res.status_code == 200, res.text
    items = {i["name"]: i for i in res.json()["items"]}
    w = items["每周任务"]
    assert w["remind_at"] == "08:30"
    assert w["exec_weekdays"] == [1, 3]
    assert w["exec_dom_days"] is None
    m = items["月度任务"]
    assert m["exec_dom_days"] == [5, 31]
    a = items["不定期任务"]
    assert a["is_adhoc"] is True
    # 超管只读口径不变
    assert admin.get("/api/admin/stats-panel/tasks").status_code == 403


# ════════════════ ①②④ 前端静态契约（无浏览器环境下守住关键交互红线） ════════════════

def _read_ui(name: str):
    p = Path(__file__).resolve().parents[1].parent / "im-task-admin" / "src" / "components" / "imtask" / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def _strip_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"^\s*//.*$", "", s, flags=re.M)


def test_frontend_static_contract() -> None:
    rmv = _read_ui("RolesMembersView.tsx")
    stats = _read_ui("StatisticsView.tsx")
    tpls = _read_ui("TemplatesView.tsx")
    if rmv is None or stats is None or tpls is None:
        pytest.skip("前端源码目录不在本机，跳过静态契约检查")
    rmv_c = _strip_comments(rmv)
    # 穿透后端分页，避免为渲染 20 条记录预先拉全量。
    assert "const MEMBER_PAGE_SIZE = 20" in rmv_c
    assert "String(MEMBER_PAGE_SIZE)" in rmv_c and "String(scope.page)" in rmv_c
    assert "size=0" not in rmv_c and "filteredMembers.slice(" not in rmv_c
    assert "全选/取消全选当前页成员" in rmv_c
    # ② CSV 导入 loading：必须有 spinner 与「进行中」提示
    assert "Loader2" in rmv_c and "animate-spin" in rmv_c, "CSV 批量导入必须有 loading 动画"
    assert "正在导入" in rmv_c, "导入中必须有明确文案提示，避免管理员以为卡住"
    # ③ 统计面板执行时间/执行周期
    stats_c = _strip_comments(stats)
    assert "execCycleText" in stats_c, "执行周期必须由统一函数渲染（每天/每周几/每月几号）"
    assert "exec_weekdays" in stats_c and "exec_dom_days" in stats_c
    assert "执行时间" in stats_c and "执行周期" in stats_c
    # ④ 模板允许放空角色
    tpls_c = _strip_comments(tpls)
    assert "请至少选择 1 个所属业务角色" not in tpls_c, "模板不得再强制绑定角色（可放空）"
    assert "放空" in tpls_c or "未绑定角色" in tpls_c, "模板表单应提示可放空"