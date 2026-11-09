# -*- coding: utf-8 -*-
"""2026-11-06 平台视角统一（用户拍板）的回归用例。

口径：
1. 收回某平台授权 = 该平台停发（与停用账号同效，但仅限该平台）——调度层 + 直调层双闸；
2. 账号管理卡片统计按当前平台过滤（角色/成员/模板/今日实例；未开通且无数据的管理员全 0）；
3. 成员池按当前平台聚合：张三案例（同一人双平台、部门各不相同）在平台视角下不再误报
   「部门不一致」；姓名不一致提示保留（同平台内多行不同名仍提示）；
4. 池操作（启停/改名/删除）platform 传入时只作用于该平台名册行（跨管理员），
   绑定清理与 delete_role_member 同口径（仅清该平台、且该平台再无归属时）。
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
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402


def bind_activated_at(store, phone: str, external_id: str, activated_at: str = "2000-01-01 00:00:00", platform: str = "zdxlz") -> None:
    """建立平台绑定并**指定首次激活时刻**。

    2026-11-09 新口径「当天激活的只创建激活时刻之后的任务，第二天才是刷新完整一天」：
    实例生成会拿 delivery_target.activated_at 与 period_key 比 ——
      · 激活时刻早于 period_key 那天 → 建实例
      · 激活时刻晚于 period_key 那天 → 不建（那天他还没激活）

    历史 period_key 的测试场景必须把激活时刻设在那天之前，
    否则语义上「那天他还没激活」→ 不该有实例，断言就会对不上。
    """
    store.upsert_delivery_target(phone, external_id, platform=platform, activated_at=activated_at)
PHONE_X = "13700001001"
PHONE_Y = "13700001002"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_plat_")) / "im.db")


def _app(store: StateStore) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _dt(day: int, hour: int = 17, minute: int = 16) -> datetime:
    return datetime(2026, 11, day, hour, minute, 0)


def _notice(rid, name: str) -> dict:
    rids = [rid] if isinstance(rid, str) else list(rid)
    return {"name": name, "kind": "notice", "role_ids": rids,
            "text_tpl": f"{name}正文", "notice_times": ["17:15"], "period": "day"}


# ── 1. 收回平台授权 = 该平台停发（multi-tick 幂等 + 直调双闸）──
def test_revoked_platform_stops_scheduling_multi_tick() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        sub_id = int(store.create_admin_account(
            username="sa", display_name="sa",
            allowed_platforms=["zdxlz", "wecom"], actor="admin")["id"])
        rz = store.create_role("密信组", actor="sa", platform="zdxlz", owner_id=sub_id)["role_id"]
        rw = store.create_role("企微组", actor="sa", platform="wecom", owner_id=sub_id)["role_id"]
        store.add_role_member(rz, PHONE_X, "张三", "市场部", actor="sa", owner_id=sub_id)
        store.add_role_member(rw, PHONE_X, "张三", "市场部", actor="sa", owner_id=sub_id)
        store.upsert_delivery_target(PHONE_X, "uid_z", platform="zdxlz")
        store.upsert_delivery_target(PHONE_X, "uid_w", platform="wecom")
        tz = store.create_template(_notice(rz, "密信通知"), actor="sa", owner_id=sub_id)
        tw = store.create_template(_notice(rw, "企微通知"), actor="sa", owner_id=sub_id)

        # 第 1 天：双平台都正常发（注入双平台 notifiers —— 单 notifier 会退回旧逻辑忽略平台）
        nz, nw = FakeNotifier(), FakeNotifier()
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=nz,
                          now_fn=lambda: _dt(6),
                          notifiers={"zdxlz": nz, "wecom": nw})
        sched.run_once()
        assert any(m.receive_id == "uid_z" for m in nz.sent_messages)
        assert any(m.receive_id == "uid_w" for m in nw.sent_messages)

        # 收回企微授权 → 只应停发企微模板（密信照常）
        store.update_admin_account(sub_id, allowed_platforms=["zdxlz"], actor="admin")
        assert tw["template_id"] not in {t["template_id"] for t in store.list_active_templates()}
        assert tz["template_id"] in {t["template_id"] for t in store.list_active_templates()}
        # 直调闸：企微模板不再生成实例
        assert store.ensure_template_instances(tw["template_id"], "2026-11-07", "2026-11-07 23:59:00") == []

        # 第 2 天连跑 3 轮 tick（幂等验证）：企微零实例零发送，密信照常
        nz2, nw2 = FakeNotifier(), FakeNotifier()
        sched2 = Scheduler(state=store, public_base_url="http://e.com", notifier=nz2,
                          now_fn=lambda: datetime(2026, 11, 7, 17, 15, 30),
                          notifiers={"zdxlz": nz2, "wecom": nw2})
        for _ in range(3):
            sched2.run_once()
            assert store.list_instances_for_period(tw["template_id"], "2026-11-07") == []
        assert nw2.sent_messages == []
        assert any(m.receive_id == "uid_z" for m in nz2.sent_messages)

        # 重新授权企微（可逆）→ 恢复发送
        store.update_admin_account(sub_id, allowed_platforms=["zdxlz", "wecom"], actor="admin")
        nz3, nw3 = FakeNotifier(), FakeNotifier()
        Scheduler(state=store, public_base_url="http://e.com", notifier=nz3,
                  now_fn=lambda: datetime(2026, 11, 8, 17, 16, 0),
                  notifiers={"zdxlz": nz3, "wecom": nw3}).run_once()
        assert any(m.receive_id == "uid_w" for m in nw3.sent_messages)
        assert any(m.receive_id == "uid_z" for m in nz3.sent_messages)
    finally:
        store.close()


# ── 2. 账号卡片统计按平台过滤；实例只看今天 ──
def test_accounts_stats_platform_scoped() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        s1 = int(admin.post("/api/admin/accounts", json={
            "username": "sa", "display_name": "sa",
            "allowed_platforms": ["zdxlz", "wecom"]}).json()["account"]["id"])
        # 未开通企微、也没建过任何数据的第二位分级管理员
        admin.post("/api/admin/accounts", json={
            "username": "sb", "display_name": "sb", "allowed_platforms": ["zdxlz"]})

        rz1 = store.create_role("密信组1", actor="sa", platform="zdxlz", owner_id=s1)["role_id"]
        rz2 = store.create_role("密信组2", actor="sa", platform="zdxlz", owner_id=s1)["role_id"]
        rw = store.create_role("企微组", actor="sa", platform="wecom", owner_id=s1)["role_id"]
        store.add_role_member(rz1, PHONE_X, "张三", "市场部", actor="sa", owner_id=s1)
        bind_activated_at(store, PHONE_X, "uid_phone_x")
        store.add_role_member(rz1, PHONE_Y, "李四", "渠道部", actor="sa", owner_id=s1)
        bind_activated_at(store, PHONE_Y, "uid_phone_y")
        store.add_role_member(rz2, PHONE_X, "张三", "市场部", actor="sa", owner_id=s1)  # X 双角色，去重只算 1 人
        store.add_role_member(rw, PHONE_X, "张三", "市场部", actor="sa", owner_id=s1)
        # PHONE_X 在企微组也要有企微绑定（2026-11-09：激活按**角色所属平台**判定）
        bind_activated_at(store, PHONE_X, "uid_phone_x_w", platform="wecom")
        tz = store.create_template(_notice([rz1, rz2], "密信通知"), actor="sa", owner_id=s1)
        tw = store.create_template(_notice(rw, "企微通知"), actor="sa", owner_id=s1)
        today = store.now().strftime("%Y-%m-%d")
        assert len(store.ensure_template_instances(tz["template_id"], today, f"{today} 23:59:00")) == 2
        assert len(store.ensure_template_instances(tw["template_id"], today, f"{today} 23:59:00")) == 1

        def card(items: list, username: str) -> dict:
            return next(a for a in items if a["username"] == username)

        # 密信视角：sa = 角色2 / 成员去重2 / 模板1 / 今日2；sb 全 0；超管卡 = 该平台全局汇总
        z = admin.get("/api/admin/accounts?platform=zdxlz").json()["items"]
        assert card(z, "sa")["role_count"] == 2
        assert card(z, "sa")["member_count"] == 2
        assert card(z, "sa")["template_count"] == 1
        assert card(z, "sa")["instance_count_today"] == 2
        for key in ("role_count", "member_count", "template_count", "instance_count_today"):
            assert card(z, "sb")[key] == 0
        assert card(z, "admin")["role_count"] == 2 and card(z, "admin")["instance_count_today"] == 2

        # 企微视角：sa = 角色1 / 成员1 / 模板1 / 今日1（46/47 之争的根因即此差 1 条）
        w = admin.get("/api/admin/accounts?platform=wecom").json()["items"]
        assert card(w, "sa")["role_count"] == 1
        assert card(w, "sa")["member_count"] == 1
        assert card(w, "sa")["template_count"] == 1
        assert card(w, "sa")["instance_count_today"] == 1
        assert card(w, "admin")["instance_count_today"] == 1
        for key in ("role_count", "member_count", "template_count", "instance_count_today"):
            assert card(w, "sb")[key] == 0

        # 不传 platform → 兼容旧口径（全平台）
        a = admin.get("/api/admin/accounts").json()["items"]
        assert card(a, "sa")["role_count"] == 3
        assert card(a, "sa")["member_count"] == 2
    finally:
        store.close()


# ── 3+4. 成员池平台聚合与平台内操作 ──
def test_member_pool_platform_scoped_and_ops() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        s1 = int(admin.post("/api/admin/accounts", json={
            "username": "sa", "display_name": "sa",
            "allowed_platforms": ["zdxlz", "wecom"]}).json()["account"]["id"])
        rz = store.create_role("密信组", actor="sa", platform="zdxlz", owner_id=s1)["role_id"]
        rw = store.create_role("企微组", actor="sa", platform="wecom", owner_id=s1)["role_id"]
        # 张三案例：同一人双平台、部门各不同；密信侧再加一行不同姓名（验证姓名提醒保留）
        store.add_role_member(rz, PHONE_X, "张三", "云中台", actor="sa", owner_id=s1)
        store.add_role_member(rw, PHONE_X, "张三", "市场部", actor="sa", owner_id=s1)
        store.upsert_delivery_target(PHONE_X, "uid_z", platform="zdxlz")
        store.upsert_delivery_target(PHONE_X, "uid_w", platform="wecom")

        # 密信视角：只见 1 行 1 角色、部门=云中台、无不一致提示（跨平台分叉不再误报）
        pz = store.list_member_pool(platform="zdxlz")
        assert len(pz) == 1 and pz[0]["phone"] == PHONE_X
        assert pz[0]["row_count"] == 1 and pz[0]["role_count"] == 1
        assert pz[0]["display_dept"] == "云中台"
        assert pz[0]["dept_inconsistent"] is False and pz[0]["name_inconsistent"] is False
        # 企微视角：部门=市场部
        pw = store.list_member_pool(platform="wecom")
        assert pw[0]["display_dept"] == "市场部" and pw[0]["row_count"] == 1
        # 不传平台（兼容口径）：2 行、部门不一致为真（跨平台真相仍在）
        pa = store.list_member_pool()
        assert pa[0]["row_count"] == 2 and pa[0]["dept_inconsistent"] is True

        # 同平台内多行不同姓名 → 姓名不一致提醒保留
        rz2 = store.create_role("密信组2", actor="sa", platform="zdxlz", owner_id=s1)["role_id"]
        store.add_role_member(rz2, PHONE_X, "张三丰", "云中台", actor="sa", owner_id=s1)
        bind_activated_at(store, PHONE_X, "uid_phone_x")
        pz2 = store.list_member_pool(platform="zdxlz")
        assert pz2[0]["row_count"] == 2 and pz2[0]["role_count"] == 2
        assert pz2[0]["name_inconsistent"] is True

        # 平台内启停：只停密信两行，企微行不受影响
        res = store.member_pool_set_status(PHONE_X, enabled=False, actor="admin", platform="zdxlz")
        assert res["rows"] == 2
        states = {
            (str(r["role_id"]), int(r["enabled"]))
            for r in store.conn.execute("SELECT role_id, enabled FROM member WHERE phone = ?", (PHONE_X,)).fetchall()
        }
        assert (rz, 0) in states and (rz2, 0) in states and (rw, 1) in states

        # 平台内改名：只同步密信两行，企微行姓名不动
        store.member_pool_update_profile(PHONE_X, name="张三", dept="渠道部", actor="admin", platform="zdxlz")
        row_w = store.conn.execute(
            "SELECT name, dept FROM member WHERE phone = ? AND role_id = ?", (PHONE_X, rw)).fetchone()
        assert row_w["name"] == "张三" and row_w["dept"] == "市场部"
        depts_z = {r["dept"] for r in store.conn.execute(
            "SELECT dept FROM member WHERE phone = ? AND role_id IN (?, ?)", (PHONE_X, rz, rz2)).fetchall()}
        assert depts_z == {"渠道部"}

        # 平台内删除：删密信两行；企微行保留、企微绑定保留；密信绑定清除（该平台再无归属）
        res = store.member_pool_delete(PHONE_X, actor="admin", platform="zdxlz")
        assert res["rows"] == 2 and res["bindings_removed"] == ["zdxlz"]
        assert store.conn.execute(
            "SELECT COUNT(1) FROM member WHERE phone = ? AND role_id IN (?, ?)", (PHONE_X, rz, rz2)
        ).fetchone()[0] == 0
        assert store.conn.execute(
            "SELECT COUNT(1) FROM member WHERE phone = ? AND role_id = ?", (PHONE_X, rw)).fetchone()[0] == 1
        assert store.is_phone_activated(PHONE_X, "wecom") is True
        assert store.is_phone_activated(PHONE_X, "zdxlz") is False

        # API 端平台参数（超管）：企微视角池中仍可见 X
        r = admin.get("/api/admin/member-pool?platform=wecom")
        assert r.status_code == 200
        assert [i["phone"] for i in r.json()["items"]] == [PHONE_X]
        # 密信视角已无 X
        r2 = admin.get("/api/admin/member-pool?platform=zdxlz")
        assert r2.json()["items"] == []
    finally:
        store.close()
