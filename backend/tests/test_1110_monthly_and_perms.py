# -*- coding: utf-8 -*-
"""2026-11-10 三需求回归：月度执行日 + 执行日保存链 + 成员修改权限矩阵。

① 执行日保存链（bug 根因在前端 realApi 白名单丢字段，后端本就能存）：
   PUT 带 exec_weekdays=[1..5] → 读回即工作日（此前前端丢字段导致后端保留旧值）。
② 月度执行日：exec_dom_days 优先于 exec_weekdays；31 号 = 大月 31 / 小月 30 / 2 月 28-29。
③ 成员修改权限矩阵（用户 11-10 口径，取代 11-09 的分级一律 403）：
   创建者可改姓名部门并同步该平台全量池；非创建者改名部门 403、改派单状态 200。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler, is_template_anchor_day  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

PHONE_A = "13800001001"
PHONE_B = "13800001002"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_1110_")) / "im.db")


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


def _sub_ready(app: FastAPI, admin: TestClient, username: str) -> tuple:
    aid = int(admin.post("/api/admin/accounts", json={"username": username, "display_name": username}).json()["account"]["id"])
    c = _login(app, username, StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
    c.post("/api/admin/change-password", json={"old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    return aid, _login(app, username, "NewPass@2026")


def _notice(rid: str, name: str, **extra) -> dict:
    p = {"name": name, "kind": "notice", "role_ids": [rid],
         "text_tpl": "x", "notice_times": ["09:00"], "period": "day"}
    p.update(extra)
    return p


def test_exec_weekdays_save_roundtrip() -> None:
    """① 保存「工作日」→ 读回即工作日（后端链路；前端丢字段的 bug 已在 realApi 修复）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        aid, sa = _sub_ready(app, admin, "sa")
        rid = sa.post("/api/admin/roles", json={"name": "甲组"}).json()["role_id"]
        sa.post(f"/api/admin/roles/{rid}/members", json={"phone": PHONE_A, "name": "张三", "dept": "市场部"})
        r = sa.post("/api/admin/templates", json=_notice(rid, "T", exec_weekdays=[1, 2, 3, 4, 5, 6, 7]))
        assert r.status_code == 200, r.text
        tid = r.json()["template_id"]
        # 编辑：改成工作日（部分更新语义，payload 显式带 exec_weekdays）
        r2 = sa.put(f"/api/admin/templates/{tid}", json={"exec_weekdays": [1, 2, 3, 4, 5]})
        assert r2.status_code == 200, r2.text
        got = store.get_template(tid)
        assert got["exec_weekdays"] == [1, 2, 3, 4, 5], got["exec_weekdays"]
        # 切回每天 = 七天全选：后端存 [1..7]（序列化保留数组，仅 null/空 = 全周；
        #   「全选压成 null」是前端的等价压缩，后端两种形态语义相同，调度 fail-open）
        r3 = sa.put(f"/api/admin/templates/{tid}", json={"exec_weekdays": [1, 2, 3, 4, 5, 6, 7]})
        assert r3.status_code == 200
        assert store.get_template(tid)["exec_weekdays"] in ([1, 2, 3, 4, 5, 6, 7], None)
    finally:
        store.close()


def test_monthly_exec_dom_days_anchor() -> None:
    """② 月度锚点：选中日执行；31=大月31/小月30/闰平年2月末；未选日不执行；周内不看周几。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        # 造一个 exec_dom_days=[1, 15, 31] 的模板（不经 API，直捣锚点函数）
        rid = store.create_role("甲组", actor="t", platform="zdxlz", owner_id=1)["role_id"]
        store.upsert_delivery_target(PHONE_A, "uid_a", platform="zdxlz")
        store.add_role_member(rid, PHONE_A, "张三", "市场部", actor="t", owner_id=1)
        tpl = store.create_template(
            _notice(rid, "月度T", exec_dom_days=[1, 15, 31], exec_weekdays=[1, 2, 3, 4, 5, 6, 7]),
            actor="t", owner_id=1,
        )
        # 锚点判定（直调纯函数）
        assert is_template_anchor_day(tpl, datetime(2026, 11, 1, 9, 0)) is True    # 1 号
        assert is_template_anchor_day(tpl, datetime(2026, 11, 15, 9, 0)) is True  # 15 号
        assert is_template_anchor_day(tpl, datetime(2026, 11, 10, 9, 0)) is False # 10 号不执行
        assert is_template_anchor_day(tpl, datetime(2026, 11, 30, 9, 0)) is True  # 小月最后一天（31 适配）
        assert is_template_anchor_day(tpl, datetime(2026, 12, 31, 9, 0)) is True  # 大月 31
        assert is_template_anchor_day(tpl, datetime(2026, 2, 28, 9, 0)) is True   # 2026 平年 2 月末
        assert is_template_anchor_day(tpl, datetime(2028, 2, 29, 9, 0)) is True   # 2028 闰年 2 月末
        assert is_template_anchor_day(tpl, datetime(2026, 2, 27, 9, 0)) is False  # 2 月 27 不是月末
        # 调度行为：11-10（未选日）零实例；11-15 生成
        ntf = FakeNotifier()
        sched_none = Scheduler(state=store, public_base_url="http://e.com", notifier=ntf,
                               now_fn=lambda: datetime(2026, 11, 10, 9, 30))
        sched_none.run_once()
        assert store.list_instances_for_period(tpl["template_id"], "2026-11-10") == []
        sched_run = Scheduler(state=store, public_base_url="http://e.com", notifier=ntf,
                              now_fn=lambda: datetime(2026, 11, 15, 9, 30))
        sched_run.run_once()
        assert len(store.list_instances_for_period(tpl["template_id"], "2026-11-15")) == 1
        # 清空月度（显式 []）→ 回落每周模式
        store.update_template(tpl["template_id"], {"exec_dom_days": []}, actor="t")
        got = store.get_template(tpl["template_id"])
        assert got["exec_dom_days"] is None
        assert is_template_anchor_day(got, datetime(2026, 11, 10, 9, 0)) is True  # 回到每周每天
    finally:
        store.close()


def test_member_profile_permission_matrix() -> None:
    """③ 权限矩阵：创建者改姓名部门 200 且同步平台全量池；非创建者 403；派单全员可改。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        sa_id, sa = _sub_ready(app, admin, "sa")
        sb_id, sb = _sub_ready(app, admin, "sb")
        # sa 的角色是 zdxlz 平台；把同一手机号也放进 sb 的角色（模拟该平台下多管理员名册）
        ra = sa.post("/api/admin/roles", json={"name": "甲组", "platform": "zdxlz"}).json()["role_id"]
        rb = sb.post("/api/admin/roles", json={"name": "乙组", "platform": "zdxlz"}).json()["role_id"]
        sa.post(f"/api/admin/roles/{ra}/members", json={"phone": PHONE_A, "name": "张三", "dept": "市场部"})
        sb.post(f"/api/admin/roles/{rb}/members", json={"phone": PHONE_A, "name": "张三", "dept": "市场部"})
        # 两条行的 owner 分别是 sa / sb

        # ① 创建者 sa 改自己建的行（ra 行 owner=sa）→ 200，且同步该平台全量池（sb 的行也改）
        r = sa.put(f"/api/admin/roles/{ra}/members/{PHONE_A}", json={"name": "张三丰", "dept": "渠道部"})
        assert r.status_code == 200, r.text
        rows = {(str(x["role_id"]), str(x["name"]), str(x["dept"]))
                for x in store.conn.execute("SELECT role_id, name, dept FROM member WHERE phone = ?", (PHONE_A,))}
        assert rows == {(ra, "张三丰", "渠道部"), (rb, "张三丰", "渠道部")}, rows

        # ② 非创建者 sb 改「sa 的角色里那条行」→ 403。
        #    两道防线（顺序）：角色归属护栏 FORBIDDEN_OWNER（sb 别人的角色一律拦）
        #    → 若同角色混行，则 MEMBER_PROFILE_FORBIDDEN（场景⑤）。此处 sb 访问 ra
        #    先被角色归属拦下，两个错误码任一即视为正确拒绝。
        r2 = sb.put(f"/api/admin/roles/{ra}/members/{PHONE_A}", json={"name": "李四"})
        assert r2.status_code == 403, r2.text
        assert r2.json().get("code") in ("FORBIDDEN_OWNER", "MEMBER_PROFILE_FORBIDDEN")

        # ⑤ 同角色混行场景（直改 DB 模拟成员池同步把别人的行带进自己角色）：
        #    sb 的角色 rb 里有一行 owner=sa → sb 改名部门应被新护栏 MEMBER_PROFILE_FORBIDDEN 拦下
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE member SET owner_id = ? WHERE role_id = ? AND phone = ?",
                (sa_id, rb, PHONE_A),
            )
        r5 = sb.put(f"/api/admin/roles/{rb}/members/{PHONE_A}", json={"name": "李四"})
        assert r5.status_code == 403, r5.text
        assert r5.json().get("code") == "MEMBER_PROFILE_FORBIDDEN"
        # 派单状态不受影响（混行也放行，仅作用本行）
        r6 = sb.put(f"/api/admin/roles/{rb}/members/{PHONE_A}", json={"enabled": False})
        assert r6.status_code == 200, r6.text
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE member SET owner_id = ?, enabled = 1 WHERE role_id = ? AND phone = ?",
                (sb_id, rb, PHONE_A),
            )

        # ③ 非创建者 sb 改自己行的派单状态（不改姓名部门）→ 200（仅作用于自己名下行）
        r3 = sb.put(f"/api/admin/roles/{rb}/members/{PHONE_A}", json={"enabled": False})
        assert r3.status_code == 200, r3.text
        states = {str(x["role_id"]): int(x["enabled"])
                  for x in store.conn.execute("SELECT role_id, enabled FROM member WHERE phone = ?", (PHONE_A,))}
        assert states[rb] == 0 and states[ra] == 1, states  # 创建者那条不受影响

        # ④ 停/启可逆 + sb 改回
        r4 = sb.put(f"/api/admin/roles/{rb}/members/{PHONE_A}", json={"enabled": True})
        assert r4.status_code == 200
    finally:
        store.close()
