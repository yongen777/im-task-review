# -*- coding: utf-8 -*-
"""2026-11-04 多管理员分级：越权边界专项。

覆盖：
1. 分级管理员建模板时挂别人的角色 → 403 FORBIDDEN_OWNER（不给别人的成员派单）
2. 分级管理员改模板时换成别人的角色 → 403 FORBIDDEN_OWNER
3. 授权「仅密信」的账号，把企微角色挂进模板 → 403 PLATFORM_NOT_ALLOWED
4. 分级管理员建自己的角色 + 模板 → 正常
5. 超管建模板 → 403 SUPER_ADMIN_READONLY（超管只管账号）
6. 超管能改分级管理员的平台授权；授权收回后该账号建企微角色被拒
7. 跨管理员免二次激活：别人已激活的手机号，导入到我的分组直接是已激活
8. 分级管理员看不到别人的角色/模板/实例
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.state import StateStore  # noqa: E402

PWD = StateStore.DEFAULT_SUB_ADMIN_PASSWORD
PHONE_A = "13600001111"
PHONE_B = "13600002222"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_scope_")) / "im.db")


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


def _mk_admin(c: TestClient, username: str, platforms=None, display="") -> int:
    body = {"username": username, "display_name": display or username}
    if platforms is not None:
        body["allowed_platforms"] = platforms
    r = c.post("/api/admin/accounts", json=body)
    assert r.status_code == 200, r.text
    return int(r.json()["account"]["id"])


def _sub(app: FastAPI, username: str, platforms=None) -> TestClient:
    """建号 → 登录 → 强制改密 → 返回可用会话。"""
    admin = TestClient(app)
    r = admin.post("/api/admin/login", json={"username": "admin", "password": "Admin@2026"})
    admin.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    _mk_admin(admin, username, platforms)
    c = _login(app, username, PWD)
    c.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
    return c


def _role(c: TestClient, name: str, platform: str = "zdxlz") -> str:
    r = c.post("/api/admin/roles", json={"name": name, "platform": platform})
    assert r.status_code == 200, r.text
    return r.json()["role_id"]


def test_template_cannot_target_other_admin_role() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        a = _sub(app, "a1")
        b = _sub(app, "b1")
        role_b = _role(b, "乙组")

        # 甲把自己的角色挂进自己的模板 → 允许
        role_a = _role(a, "甲组")
        ok = a.post("/api/admin/templates", json={
            "name": "甲的任务", "kind": "notice", "role_ids": [role_a],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"})
        assert ok.status_code == 200, ok.text

        # 甲把乙的角色挂进自己的模板 → 拒绝（否则会给乙的成员派单）
        bad = a.post("/api/admin/templates", json={
            "name": "越权任务", "kind": "notice", "role_ids": [role_b],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"})
        assert bad.status_code == 403, bad.text
        assert bad.json().get("code") == "FORBIDDEN_OWNER", bad.text

        # 甲改自己的模板，把目标换成乙的角色 → 同样拒绝
        tpl_id = ok.json()["template_id"]
        bad2 = a.put(f"/api/admin/templates/{tpl_id}", json={"role_ids": [role_b]})
        assert bad2.status_code == 403, bad2.text
        assert bad2.json().get("code") == "FORBIDDEN_OWNER", bad2.text
    finally:
        store.close()


def test_template_cannot_cross_platform_authorization() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        # 甲一开始授权双平台，建好一个企微角色
        a = _sub(app, "a1", platforms=["zdxlz", "wecom"])
        role_wecom = _role(a, "甲的企微组", platform="wecom")
        assert a.post("/api/admin/templates", json={
            "name": "企微任务", "kind": "notice", "role_ids": [role_wecom],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"}).status_code == 200

        # 超管把甲的企微授权收回 → 甲再也不能建企微角色，也不能用企微角色建模板
        admin = _login(app, "admin", "Admin@2026")
        aid = [a2["id"] for a2 in admin.get("/api/admin/accounts").json()["items"]
               if a2["username"] == "a1"][0]
        upd = admin.put(f"/api/admin/accounts/{aid}", json={"allowed_platforms": ["zdxlz"]})
        assert upd.status_code == 200, upd.text

        # 授权收回后：建企微角色被拒
        bad_role = a.post("/api/admin/roles", json={"name": "再来个企微组", "platform": "wecom"})
        assert bad_role.status_code == 403, bad_role.text
        assert bad_role.json().get("code") == "PLATFORM_NOT_ALLOWED", bad_role.text

        # 授权收回后：拿已有的企微角色建模板 → 也被平台校验拦下（否则等于绕过平台隔离）
        bad = a.post("/api/admin/templates", json={
            "name": "跨平台", "kind": "notice", "role_ids": [role_wecom],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"})
        assert bad.status_code == 403, bad.text
        assert bad.json().get("code") == "PLATFORM_NOT_ALLOWED", bad.text
    finally:
        store.close()


def test_super_admin_cannot_create_business_data() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        c = _login(app, "admin", "Admin@2026")
        r = c.post("/api/admin/roles", json={"name": "超管组", "platform": "zdxlz"})
        assert r.status_code == 403, r.text
        assert r.json().get("code") == "SUPER_ADMIN_READONLY", r.text
        # 但超管能改分级管理员的平台授权
        aid = _mk_admin(c, "s1", ["zdxlz"])
        upd = c.put(f"/api/admin/accounts/{aid}", json={"allowed_platforms": ["wecom"]})
        assert upd.status_code == 200, upd.text
        # 授权改成「仅企微」后：该账号建企微角色允许、建密信角色被拒（授权收回即时生效）
        s = _login(app, "s1", PWD)
        s.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        assert s.post("/api/admin/roles", json={"name": "企微组", "platform": "wecom"}).status_code == 200
        denied = s.post("/api/admin/roles", json={"name": "密信组", "platform": "zdxlz"})
        assert denied.status_code == 403, denied.text
        assert denied.json().get("code") == "PLATFORM_NOT_ALLOWED", denied.text
    finally:
        store.close()


def test_global_activation_is_shared_across_admins() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        a = _sub(app, "a1")
        b = _sub(app, "b1")

        # 甲添加成员 → 用户在密信激活（模拟机器人回调写入绑定）
        role_a = _role(a, "甲组")
        r = a.post(f"/api/admin/roles/{role_a}/members",
                   json={"phone": PHONE_A, "name": "张三", "dept": "市场部"})
        assert r.status_code == 200, r.text
        store.upsert_delivery_target(PHONE_A, "uid_a", platform="zdxlz")

        # 乙导入同一个手机号 → 免二次激活（注意：导入接口默认 dry_run=true，需显式关掉）
        role_b = _role(b, "乙组")
        imp = b.post(f"/api/admin/roles/{role_b}/members/import", json={
            "dry_run": False,
            "csv_rows": [{"phone": PHONE_A, "name": "张三", "dept": "市场部"}]})
        assert imp.status_code == 200, imp.text
        assert imp.json().get("global_reused") is True, imp.text
        assert imp.json().get("global_reused_count") == 1, imp.text
        assert imp.json().get("will_add") == 1, imp.text

        # 未激活的手机号 → global_reused 为 False
        imp2 = b.post(f"/api/admin/roles/{role_b}/members/import", json={
            "dry_run": False,
            "csv_rows": [{"phone": PHONE_B, "name": "李四", "dept": "市场部"}]})
        assert imp2.status_code == 200, imp2.text
        assert imp2.json().get("global_reused") is False, imp2.text
        assert imp2.json().get("global_reused_count") == 0, imp2.text

        # 乙能看到全局已激活成员并搜到甲激活的那个人
        g = b.get(f"/api/admin/global-activated-members?q={PHONE_A}&platform=zdxlz")
        assert g.status_code == 200, g.text
        items = g.json()["items"]
        assert any(x["phone"] == PHONE_A for x in items), items
    finally:
        store.close()


def test_data_isolation_between_sub_admins() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        a = _sub(app, "a1")
        b = _sub(app, "b1")
        role_a = _role(a, "甲组")
        tpl_a = a.post("/api/admin/templates", json={
            "name": "甲的任务", "kind": "notice", "role_ids": [role_a],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"}).json()["template_id"]

        assert len(b.get("/api/admin/roles").json()["items"]) == 0
        assert len(b.get("/api/admin/templates").json()["items"]) == 0
        # 直接访问甲的资源 → 403
        assert b.get(f"/api/admin/roles/{role_a}").status_code in (403, 404)
        assert b.put(f"/api/admin/templates/{tpl_a}", json={"name": "改名"}).status_code == 403
        # 甲自己看得到
        assert len(a.get("/api/admin/roles").json()["items"]) == 1
        assert len(a.get("/api/admin/templates").json()["items"]) == 1
    finally:
        store.close()


def test_sub_admin_cannot_download_other_admins_upload() -> None:
    """2026-11-05：附件下载按归属校验 —— 分级管理员拿别人的 ref 只能得到 404。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        a = _sub(app, "a1")
        b = _sub(app, "b1")
        role_a = _role(a, "甲组")
        tpl_a = a.post("/api/admin/templates", json={
            "name": "甲的任务", "kind": "notice", "role_ids": [role_a],
            "text_tpl": "干活", "notice_times": ["09:00"], "period": "day"}).json()["template_id"]

        uploads = store.db.db_path.parent / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        (uploads / "a-report.txt").write_text("甲的上报", encoding="utf-8")
        store.create_upload_ref(
            ref="ref-a-report", phone=PHONE_A, template_id=tpl_a, file_name="a-report.txt",
            mime="text/plain", size=12, rel_path="a-report.txt",
        )

        assert a.get("/api/admin/uploads/ref-a-report").status_code == 200
        assert b.get("/api/admin/uploads/ref-a-report").status_code == 404
    finally:
        store.close()


def test_no_password_hash_in_any_account_response() -> None:
    """账号列表/创建/重置 都不能把口令哈希带出去（2026-11-04 生产实测发现过）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        c = _login(app, "admin", "Admin@2026")
        aid = _mk_admin(c, "s1", ["zdxlz"])
        for url in ("/api/admin/accounts",):
            body = c.get(url).text
            assert "pwd_hash" not in body, body[:300]
            assert "pbkdf2" not in body, body[:300]
        created = c.post("/api/admin/accounts", json={"username": "s2"}).text
        assert "pwd_hash" not in created, created[:300]
        reset = c.post(f"/api/admin/accounts/{aid}/reset-password").text
        assert "pwd_hash" not in reset, reset[:300]
        # 单个账号（me）同样不能带
        me = c.get("/api/admin/me").text
        assert "pwd_hash" not in me and "pbkdf2" not in me, me[:300]
    finally:
        store.close()
