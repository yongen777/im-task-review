# -*- coding: utf-8 -*-
"""2026-11-04 多管理员分级（超级管理员 + 分级管理员）回归。

业务口径（用户 2026-11-04 定）：
1. admin = 超级管理员：全局只读 + 管理员账号管理；业务写接口一律 403；
2. 分级管理员：只读写自己名下（owner_id）的角色/成员/模板/实例；账号级限制可用办公软件平台；
3. 成员的平台会话激活（delivery_target）**全局共享** —— 谁先激活过都算已激活，无需二次激活；
4. 新建/重置分级管理员：初始密码 Hds22999!（兼容全角！），强制首登改密。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
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
PWD = StateStore.DEFAULT_SUB_ADMIN_PASSWORD
PHONE = "13600001234"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_admin_")) / "im.db")


def _client(store: StateStore, username: str, password: str) -> TestClient:
    """起一个带守卫中间件的后台应用并登录（中间件负责 must_change 强制改密与超管只读拦截）。"""
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _login2(app, username: str, password: str) -> TestClient:
    """同一 app 上另起一个登录态（TestClient 之间 cookie 不共享）。"""
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    c.login_response = r  # 供断言 must_change / role_level 用
    return c


def test_admin_is_super_admin_and_can_create_sub_admin() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        me = c.get("/api/admin/me").json()
        assert me["role_level"] == "super_admin", me
        assert me["is_super_admin"] is True
        assert me["allowed_platforms"] == ["zdxlz", "wecom"], me

        r = c.post("/api/admin/accounts", json={
            "username": "zhangsan", "display_name": "张三", "allowed_platforms": ["zdxlz"]})
        assert r.status_code == 200, r.text
        acct = r.json()["account"]
        assert acct["role_level"] == "sub_admin" and acct["allowed_platforms"] == ["zdxlz"]
        assert r.json()["must_change_password"] is True

        items = c.get("/api/admin/accounts").json()["items"]
        assert any(a["username"] == "zhangsan" for a in items), items
    finally:
        store.close()


def test_sub_admin_login_with_fullwidth_bang_and_must_change() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        c.post("/api/admin/accounts", json={"username": "lisi", "display_name": "李四",
                                           "allowed_platforms": ["zdxlz", "wecom"]})
        # 全角感叹号也要能登录
        c2 = TestClient(c.app)
        r = c2.post("/api/admin/login", json={"username": "lisi", "password": "Hds22999！"})
        assert r.status_code == 200, r.text
        assert r.json()["must_change"] is True
        assert r.json()["role_level"] == "sub_admin"
        # 强制改密：未改密前业务接口被拦
        c2.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
        assert c2.get("/api/admin/roles").status_code == 403
    finally:
        store.close()


def test_data_isolation_between_sub_admins() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        c.post("/api/admin/accounts", json={"username": "a1", "display_name": "甲"})
        c.post("/api/admin/accounts", json={"username": "b1", "display_name": "乙"})
        # 两人各建一个角色
        for user, name in (("a1", "甲组"), ("b1", "乙组")):
            cc = _login2(c.app, user, PWD)
            # 首次登录强制改密 → 先改密
            cc.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
            rr = cc.post("/api/admin/roles", json={"name": name, "platform": "zdxlz"})
            assert rr.status_code == 200, rr.text
        # 甲只看得到自己的角色
        cc = _login2(c.app, "a1", "NewPass@2026")
        roles = cc.get("/api/admin/roles").json()["items"]
        assert [x["name"] for x in roles] == ["甲组"], roles
        # 超管看得到全部
        all_roles = c.get("/api/admin/roles").json()["items"]
        assert {x["name"] for x in all_roles} == {"甲组", "乙组"}, all_roles
        # 超管按管理员筛选
        b_id = [a["id"] for a in c.get("/api/admin/accounts").json()["items"] if a["username"] == "b1"][0]
        filtered = c.get(f"/api/admin/roles?owner_id={b_id}").json()["items"]
        assert [x["name"] for x in filtered] == ["乙组"], filtered
    finally:
        store.close()


def test_sub_admin_cannot_touch_others_resources() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        c.post("/api/admin/accounts", json={"username": "a1", "display_name": "甲"})
        c.post("/api/admin/accounts", json={"username": "b1", "display_name": "乙"})
        cc = _login2(c.app, "b1", PWD)
        cc.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        role_b = cc.post("/api/admin/roles", json={"name": "乙组", "platform": "zdxlz"}).json()["role_id"]
        # 甲账号登录后访问乙的角色 → 403
        ca = _login2(c.app, "a1", PWD)
        ca.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        assert ca.get(f"/api/admin/roles/{role_b}/members").status_code == 403
        assert ca.post(f"/api/admin/roles/{role_b}/members",
                       json={"phone": PHONE, "name": "张三"}).status_code == 403
        # 分级管理员不能管理账号
        assert ca.get("/api/admin/accounts").status_code == 403
        # 分级管理员不能访问通道配置
        assert ca.get("/api/admin/channel-config").status_code == 403
    finally:
        store.close()


def test_super_admin_cannot_write_business_data() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        assert c.post("/api/admin/templates", json={"name": "T", "kind": "notice",
                                                    "role_ids": [], "period": "day"}).status_code == 403
        assert c.post("/api/admin/roles", json={"name": "超管建的组", "platform": "zdxlz"}).status_code == 403
    finally:
        store.close()


def test_platform_authorization_limits_sub_admin() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        c.post("/api/admin/accounts", json={"username": "only_zd", "display_name": "只密信",
                                           "allowed_platforms": ["zdxlz"]})
        cc = _login2(c.app, "only_zd", PWD)
        cc.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        assert cc.post("/api/admin/roles", json={"name": "密信组", "platform": "zdxlz"}).status_code == 200
        assert cc.post("/api/admin/roles", json={"name": "企微组", "platform": "wecom"}).status_code == 403
        me = cc.get("/api/admin/me").json()
        assert me["allowed_platforms"] == ["zdxlz"], me
    finally:
        store.close()


def test_global_activation_is_shared_across_admins() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        c.post("/api/admin/accounts", json={"username": "a1", "display_name": "甲"})
        # 模拟"别的管理员/机器人已激活过该手机号"
        store.upsert_delivery_target(PHONE, "zdx_uid_1", platform="zdxlz")
        cc = _login2(c.app, "a1", PWD)
        cc.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        role = cc.post("/api/admin/roles", json={"name": "甲组", "platform": "zdxlz"}).json()["role_id"]
        # 加成员 → 已激活（全局复用），无需二次激活
        resp = cc.post(f"/api/admin/roles/{role}/members", json={"phone": PHONE, "name": "张三"}).json()
        store.upsert_delivery_target(PHONE, "zdxlz_uid_multi_admin", platform="zdxlz")
        assert resp["member"]["activated"] is True, resp
        assert resp["global_reused"] is True, resp
        # 全局搜索能搜到
        hits = cc.get(f"/api/admin/global-activated-members?q={PHONE}").json()["items"]
        assert hits and hits[0]["phone"] == PHONE, hits
    finally:
        store.close()


def test_account_reset_password_and_delete_rules() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        created = c.post("/api/admin/accounts", json={"username": "tmp1", "display_name": "临时"}).json()
        aid = created["account"]["id"]
        # 重置密码 → 回到初始密码 + 强制改密
        r = c.post(f"/api/admin/accounts/{aid}/reset-password")
        assert r.status_code == 200, r.text
        # ⚠️ 子账号登录要用独立 client：同一 client 登录会覆盖 admin 的会话 cookie
        lr = _login2(c.app, "tmp1", PWD)
        assert lr.login_response.json()["must_change"] is True
        # 停用后不能登录
        pu = c.put(f"/api/admin/accounts/{aid}", json={"enabled": False})
        assert pu.status_code == 200, pu.text
        lr2 = TestClient(c.app).post("/api/admin/login", json={"username": "tmp1", "password": PWD})
        assert lr2.status_code == 403, lr2.text
        # 停用后可重新启用；有名单的账号不能删
        c.put(f"/api/admin/accounts/{aid}", json={"enabled": True})
        assert c.delete(f"/api/admin/accounts/{aid}").status_code == 200
        # 超管账号不能删
        admin_id = [a["id"] for a in c.get("/api/admin/accounts").json()["items"] if a["username"] == "admin"][0]
        assert c.delete(f"/api/admin/accounts/{admin_id}").status_code == 400
    finally:
        store.close()


def test_owner_filter_applies_to_templates_and_instances() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        c.post("/api/admin/accounts", json={"username": "a1", "display_name": "甲"})
        cc = _login2(c.app, "a1", PWD)
        cc.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        role = cc.post("/api/admin/roles", json={"name": "甲组", "platform": "zdxlz"}).json()["role_id"]
        tpl = cc.post("/api/admin/templates", json={
            "name": "甲的任务", "kind": "notice", "role_ids": [role],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"}).json()
        tpl_id = tpl["template_id"]
        assert len(cc.get("/api/admin/templates").json()["items"]) == 1
        # 超管看得到，且能按 owner 筛
        assert len(c.get("/api/admin/templates").json()["items"]) == 1
        a_id = [a["id"] for a in c.get("/api/admin/accounts").json()["items"] if a["username"] == "a1"][0]
        assert len(c.get(f"/api/admin/templates?owner_id={a_id}").json()["items"]) == 1
        assert len(c.get("/api/admin/templates?owner_id=999").json()["items"]) == 0
        # 实例归属继承模板（先生成实例：模板要有成员才会建实例）
        cc.post(f"/api/admin/roles/{role}/members", json={"phone": PHONE, "name": "张三", "dept": "市场部"})
        store.upsert_delivery_target(PHONE, "zdxlz_uid_multi_admin", platform="zdxlz")
        inst = store.ensure_template_instances(tpl_id, "2026-11-04", "2026-11-04 23:59:00")
        assert inst and int(inst[0]["owner_id"]) == a_id, inst
    finally:
        store.close()


def test_transfer_owner_moves_history() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        c = _client(store, "admin", "Admin@2026")
        acct = c.post("/api/admin/accounts", json={"username": "zhengjia", "display_name": "张三"}).json()["account"]
        # 历史数据用 state 直建（模拟"多管理员体系之前就有的数据"），归属默认落在 admin(id=1)
        role = store.create_role("历史组", platform="zdxlz")["role_id"]
        store.add_role_member(role, PHONE, "老成员", "市场部")
        bind_activated_at(store, PHONE, "uid_phone")
        assert c.post(f"/api/admin/roles/{role}/members", json={"phone": PHONE, "name": "老成员"}).status_code == 403  # 超管不能写
        res = store.transfer_owner(acct["id"], actor="admin")
        assert res["ok"] is True
        roles = c.get("/api/admin/roles").json()["items"]
        assert all(x["owner_id"] == acct["id"] for x in roles), roles
        # 转移后子账号看得到，超管按 owner 筛选也看得到
        cc = _login2(c.app, "zhengjia", PWD)
        cc.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        assert [x["name"] for x in cc.get("/api/admin/roles").json()["items"]] == ["历史组"]
    finally:
        store.close()
