# -*- coding: utf-8 -*-
"""给 2026-11-09 新增的后端护栏加用例：分级管理员不能改姓名/部门。

覆盖：
① 分级管理员改姓名 → 403 MEMBER_PROFILE_FORBIDDEN
② 分级管理员改部门 → 403
③ 分级管理员只改 enabled（派单状态）→ 允许
④ 分级管理员传与现值相同的 name（不改）→ 允许
⑤ 超管改姓名/部门 → 允许
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.admin import create_admin_router  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13700000011"
PWD = "Hds22999!"


def _app():
    store = StateStore(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    app = FastAPI()
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://x"))
    return store, app


def _login(app, username, password):
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _setup():
    store, app = _app()
    admin = _login(app, "admin", "Admin@2026")
    r = admin.post("/api/admin/accounts", json={"username": "sub1", "display_name": "分级甲"})
    assert r.status_code == 200, r.text
    sub = _login(app, "sub1", PWD)
    sub.post("/api/admin/change-password",
             json={"old_password": PWD, "new_password": "NewPass@2026"})
    sub = _login(app, "sub1", "NewPass@2026")
    role = sub.post("/api/admin/roles", json={"name": "组", "platform": "zdxlz"}).json()["role_id"]
    sub.post(f"/api/admin/roles/{role}/members",
             json={"phone": PHONE, "name": "张三", "dept": "市场部"})
    return store, app, admin, sub, role


def test_sub_admin_cannot_change_member_name():
    store, app, admin, sub, role = _setup()
    try:
        r = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"name": "李四"})
        assert r.status_code == 403, r.text
        assert r.json().get("code") == "MEMBER_PROFILE_FORBIDDEN", r.text
    finally:
        store.close()


def test_sub_admin_cannot_change_member_dept():
    store, app, admin, sub, role = _setup()
    try:
        r = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"dept": "渠道部"})
        assert r.status_code == 403, r.text
        assert r.json().get("code") == "MEMBER_PROFILE_FORBIDDEN", r.text
    finally:
        store.close()


def test_sub_admin_can_toggle_enabled_only():
    store, app, admin, sub, role = _setup()
    try:
        r = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"enabled": False})
        assert r.status_code == 200, r.text
        assert r.json()["member"]["enabled"] is False, r.text
    finally:
        store.close()


def test_sub_admin_same_name_is_allowed():
    """值没变就不算「修改」→ 放行（前端禁用输入框后回传原值也不该报错）。"""
    store, app, admin, sub, role = _setup()
    try:
        r = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"name": "张三", "enabled": True})
        assert r.status_code == 200, r.text
    finally:
        store.close()


def test_super_admin_can_change_member_profile():
    store, app, admin, sub, role = _setup()
    try:
        r = admin.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"name": "李四", "dept": "渠道部"})
        assert r.status_code == 200, r.text
        m = r.json()["member"]
        assert m["name"] == "李四" and m["dept"] == "渠道部", m
    finally:
        store.close()
