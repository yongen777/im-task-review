# -*- coding: utf-8 -*-
"""成员姓名/部门修改护栏用例。

2026-11-09 口径：分级管理员一律不能改姓名/部门。
2026-11-10 用户口径（取代上者）：**超管或该成员行的创建者**可改，改后同步
该办公软件全量池；非创建者 → 403。本文件已按新口径适配（sub1 是自己角色里
成员行的创建者 → 改名改部门应 200），跨场景的非创建者拦截见
Test_1110_monthly_and_perms.py 的权限矩阵用例。

覆盖：
① 创建者改姓名 → 允许（且同步该平台全量池）
② 创建者改部门 → 允许
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
    """同角色混行场景：行 owner 不是当前管理员也不是超管 → 改姓名 403。"""
    store, app, admin, sub, role = _setup()
    try:
        # 把这一行的 owner 改成超管以外的账号 id（模拟成员池同步把别人的行带进本角色）
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE member SET owner_id = 999 WHERE role_id = ? AND phone = ?", (role, PHONE))
        r = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"name": "李四"})
        assert r.status_code == 403, r.text
        assert r.json().get("code") == "MEMBER_PROFILE_FORBIDDEN", r.text
    finally:
        store.close()


def test_sub_admin_cannot_change_member_dept():
    """同角色混行场景：非创建者改部门 → 403（同 test_sub_admin_cannot_change_member_name）。"""
    store, app, admin, sub, role = _setup()
    try:
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE member SET owner_id = 999 WHERE role_id = ? AND phone = ?", (role, PHONE))
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
    """创建者传与现值相同的 name（不算修改）→ 放行（前端禁用输入框后回传原值也不报错）。"""
    store, app, admin, sub, role = _setup()
    try:
        r = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"name": "张三", "enabled": True})
        assert r.status_code == 200, r.text
    finally:
        store.close()


def test_creator_change_syncs_platform_pool():
    """2026-11-10 口径①②：创建者（sub1）改名部门 → 200，且同步该平台全量池。"""
    store, app, admin, sub, role = _setup()
    try:
        # 再造一个同平台超管外管理员的名册行，验证同步
        r = admin.post("/api/admin/accounts", json={"username": "sub2", "display_name": "分级乙"})
        assert r.status_code == 200, r.text
        sub2 = _login(app, "sub2", PWD)
        sub2.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
        sub2 = _login(app, "sub2", "NewPass@2026")
        role2 = sub2.post("/api/admin/roles", json={"name": "乙组", "platform": "zdxlz"}).json()["role_id"]
        sub2.post(f"/api/admin/roles/{role2}/members", json={"phone": PHONE, "name": "张三", "dept": "市场部"})

        r1 = sub.put(f"/api/admin/roles/{role}/members/{PHONE}", json={"name": "张三丰", "dept": "渠道部"})
        assert r1.status_code == 200, r1.text
        rows = {(str(x[0]), str(x[1])) for x in store.conn.execute(
            "SELECT role_id, name FROM member WHERE phone = ?", (PHONE,))}
        assert rows == {(role, "张三丰"), (role2, "张三丰")}, rows  # 两行都已同步
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
