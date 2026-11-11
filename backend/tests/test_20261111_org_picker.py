# -*- coding: utf-8 -*-
"""2026-11-11 任务二专项：组织架构选择器（Org Picker）后端链路。

口径（用户 2026-11-11 任务书）：
1. GET /org/departments：密信走官方 dept 接口（信封解密）；根部门 = IMT_ORG_ROOT_DEPT_ID；
   has_children ≤30 个子部门时并行探一层；企微无自建应用凭证 → configured=False；
2. GET /org/department-users：部门直属成员 + already_in_role 判定；手机号全量下发（导入要用）、
   masked_phone 供前端脱敏展示；
3. POST /roles/{id}/import-from-org：方式A（勾选人员）/ 方式B（整部门）→ 成员入册 + 绑定
   在**单事务**内完成；已在册 skip；部门自动建档；external_type 按平台（union_id/user）；
   超管只读（4.2）→ 业务写走分级管理员；
4. ⛔ 全程 Mock，测试绝不发起真实网络请求，绝不触碰企微长连接。
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

P_A = "13800003001"
P_B = "13800003002"
P_C = "13800003003"
DEPT_CS = "产数支撑中心"


class FakeZdxlzContacts:
    """内存版密信部门树（镜像官方 dept 接口语义）。"""

    def __init__(self) -> None:
        self.tree = {  # dept_id -> {"name", "count", "children":[id], "users":[...]}
            "root_1": {"name": "四川省分公司", "count": 111, "children": ["d_yy"], "users": []},
            "d_yy": {"name": "德阳市公司本部", "count": 111, "children": ["d_yzt"], "users": []},
            "d_yzt": {"name": "云中台", "count": 111, "children": ["d_cs"], "users": [
                {"union_id": "u_shilei", "phone": P_C, "name": "石磊", "title": "组长",
                 "customer_id": "", "departments": []},
            ]},
            "d_cs": {"name": DEPT_CS, "count": 92, "children": [], "users": [
                {"union_id": "u_a", "phone": P_A, "name": "测试甲", "title": "",
                 "customer_id": "", "departments": []},
                {"union_id": "u_b", "phone": P_B, "name": "测试乙", "title": "",
                 "customer_id": "", "departments": []},
            ]},
        }
        self.users_all_calls: list[str] = []

    def get_department(self, dept_id: str):
        node = self.tree.get((dept_id or "").strip())
        if not node:
            return None
        return {"dept_id": dept_id, "parent_id": "", "name": node["name"],
                "full_name": node["name"], "user_count": node["count"]}

    def department_children(self, dept_id: str):
        node = self.tree.get((dept_id or "").strip())
        if not node:
            return []
        return [{"dept_id": c, "name": self.tree[c]["name"], "user_count": self.tree[c]["count"], "index": i}
                for i, c in enumerate(node["children"])]

    def dept_users_all(self, dept_id: str, **_):
        self.users_all_calls.append(str(dept_id))
        node = self.tree.get((dept_id or "").strip())
        return list((node or {}).get("users") or [])

    # 加人路径兜底（add_role_member 会走 _auto_activate_imported → search_by_phone）
    def search_by_phone(self, phone: str):
        for node in self.tree.values():
            for u in node["users"]:
                if u["phone"] == (phone or "").strip():
                    return u
        return None


class FakeWecomNoCreds:
    """企微无凭证形态：org_available() = False。"""

    def org_available(self):
        return False

    def org_departments(self, parent_id: str = ""):
        return []

    def org_department_users(self, dept_id: str):
        return []


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_org_")) / "im.db")


def _app(store: StateStore) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t"))
    return app


def _login(app: FastAPI, username: str = "admin", password: str = "Admin@2026") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _sa_ready(app: FastAPI, username: str = "sa") -> TestClient:
    """超管建号 + 分级改密 + 登录（业务写归分级管理员，超管只读 4.2）。"""
    admin = _login(app)
    rr = admin.post("/api/admin/accounts", json={"username": username, "display_name": username})
    assert rr.status_code == 200, rr.text
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    cp = c.post("/api/admin/change-password", json={"old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD,
                                                    "new_password": "NewPass@2026"})
    assert cp.status_code == 200, cp.text
    return _login(app, username, "NewPass@2026")


def _sa_role(client: TestClient, name: str = "A组", platform: str = "zdxlz") -> str:
    r = client.post("/api/admin/roles", json={"name": name, "platform": platform})
    assert r.status_code == 200, r.text
    return str(r.json()["role_id"])


# ── 1. org/departments（只读：超管也可查）─────────────────────────────
def test_org_departments_root_and_children(monkeypatch) -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.contacts = FakeZdxlzContacts()
        app = _app(store)
        c = _login(app)
        monkeypatch.setenv("IMT_ORG_ROOT_DEPT_ID", "root_1")

        r = c.get("/api/admin/org/departments?platform=zdxlz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] is True
        assert [i["dept_id"] for i in body["items"]] == ["root_1"], body
        assert body["items"][0]["user_count"] == 111 and body["items"][0]["has_children"] is True

        r2 = c.get("/api/admin/org/departments?platform=zdxlz&parent_id=d_yy")
        assert r2.status_code == 200
        items = r2.json()["items"]
        assert [i["name"] for i in items] == ["云中台"]
        assert items[0]["has_children"] is True  # 云中台还有子部门（d_cs）
        r3 = c.get("/api/admin/org/departments?platform=zdxlz&parent_id=d_cs")
        assert r3.json()["items"] == [], "末级部门 children 为空列表"
    finally:
        store.close()


def test_org_departments_no_root_config(monkeypatch) -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.contacts = FakeZdxlzContacts()
        app = _app(store)
        c = _login(app)
        monkeypatch.delenv("IMT_ORG_ROOT_DEPT_ID", raising=False)
        r = c.get("/api/admin/org/departments?platform=zdxlz")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True and body["items"] == []
        assert "IMT_ORG_ROOT_DEPT_ID" in body["message"], body["message"]
    finally:
        store.close()


def test_org_departments_wecom_unconfigured() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.wecom_contacts = FakeWecomNoCreds()
        app = _app(store)
        c = _login(app)
        r = c.get("/api/admin/org/departments?platform=wecom")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is False and body["items"] == []
        assert "corpid" in body["message"]
    finally:
        store.close()


def test_org_departments_platform_not_authorized_forbidden() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        # 分级管理员默认双平台授权 → 现场改库收掉 wecom 后应 403
        sa = _sa_ready(app)
        with store.lock:
            store.conn.execute("UPDATE admin_user SET allowed_platforms = '[\"zdxlz\"]' WHERE username='sa'")
        r = sa.get("/api/admin/org/departments?platform=wecom")
        assert r.status_code == 403, r.text
        r2 = sa.get("/api/admin/org/departments?platform=zdxlz")
        assert r2.status_code == 200
    finally:
        store.close()


# ── 2. org/department-users ─────────────────────────────────────────
def test_org_department_users_with_already_in_role() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.contacts = FakeZdxlzContacts()
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa)
        rr = sa.post(f"/api/admin/roles/{rid}/members", json={"phone": P_A, "name": "测试甲", "dept": DEPT_CS})
        assert rr.status_code == 200, rr.text

        r = sa.get(f"/api/admin/org/department-users?platform=zdxlz&dept_id=d_cs&role_id={rid}&dept_name={DEPT_CS}")
        assert r.status_code == 200, r.text
        body = r.json()
        by_phone = {i["phone"]: i for i in body["items"]}
        assert by_phone[P_A]["already_in_role"] is True
        assert by_phone[P_B]["already_in_role"] is False
        assert by_phone[P_A]["external_id"] == "u_a"
        assert by_phone[P_B]["masked_phone"].startswith("138****"), by_phone[P_B]["masked_phone"]
        assert by_phone[P_A]["department"] == DEPT_CS
        assert body["total"] == 2
    finally:
        store.close()


# ── 3. import-from-org（方式 A：勾选）────────────────────────────────
def test_import_from_org_selected_users() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa)

        payload = {
            "selected_users": [
                {"name": "测试甲", "phone": P_A, "external_id": "u_a", "department": DEPT_CS},
                {"name": "测试乙", "phone": P_B, "external_id": "u_b", "department": DEPT_CS},
                {"name": "坏手机号", "phone": "123", "external_id": "u_x", "department": DEPT_CS},
            ]
        }
        r = sa.post(f"/api/admin/roles/{rid}/import-from-org", json=payload)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["added"] == 2 and res["activated"] == 2, res
        assert len(res["invalid"]) == 1 and "手机号" in res["invalid"][0]["reason"], res
        row_a = store.get_delivery_target(P_A, platform="zdxlz")
        assert row_a["external_id"] == "u_a" and row_a["external_type"] == "union_id", row_a
        row_b = store.get_delivery_target(P_B, platform="zdxlz")
        assert row_b["external_id"] == "u_b"
        assert store.get_delivery_target("123", platform="zdxlz") is None, "⛔ 无效行不得绑定"

        # 幂等：重复导入 → 已在册 skip，不覆盖
        r2 = sa.post(f"/api/admin/roles/{rid}/import-from-org", json=payload)
        res2 = r2.json()
        assert res2["added"] == 0 and res2["skipped"] == 2, res2

        # 部门自动建档
        assert store.department_exists(DEPT_CS) is True
    finally:
        store.close()


def test_import_from_org_super_admin_rejected() -> None:
    """4.2 超管只读：组织导入也属业务写 → 403。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa)
        admin = _login(app)
        r = admin.post(f"/api/admin/roles/{rid}/import-from-org", json={"selected_users": [
            {"name": "测试甲", "phone": P_A, "external_id": "u_a", "department": DEPT_CS}]})
        assert r.status_code == 403, r.text
    finally:
        store.close()


def test_import_from_org_keeps_activated_at_on_rebind() -> None:
    """存量绑定（含 usable=0）被组织导入覆盖时：复活 usable=1、保留首次激活时刻。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa)
        store.upsert_delivery_target(P_A, "im_old", platform="zdxlz",
                                     activated_at="2026-10-01 09:00:00")
        store.mark_delivery_target_unusable(P_A, platform="zdxlz")
        r = sa.post(f"/api/admin/roles/{rid}/import-from-org", json={"selected_users": [
            {"name": "测试甲", "phone": P_A, "external_id": "u_a", "department": DEPT_CS}]})
        assert r.status_code == 200, r.text
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "u_a" and row["external_type"] == "union_id"
        assert row["usable"] == 1 and row["activated_at"] == "2026-10-01 09:00:00"
    finally:
        store.close()


# ── 4. import-from-org（方式 B：整部门）──────────────────────────────
def test_import_from_org_whole_department() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.contacts = FakeZdxlzContacts()
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa)
        r = sa.post(f"/api/admin/roles/{rid}/import-from-org",
                    json={"dept_id": "d_cs", "dept_name": DEPT_CS})
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["added"] == 2 and res["activated"] == 2, res
        assert store.contacts.users_all_calls == ["d_cs"], "方式 B 只按部门取一次直属成员"
        assert store.get_delivery_target(P_A, platform="zdxlz")["external_id"] == "u_a"
        with store.lock:
            audit = store.conn.execute(
                "SELECT detail FROM admin_audit WHERE action='member.org_import'").fetchall()
        assert len(audit) == 1
    finally:
        store.close()


def test_import_from_org_wecom_whole_dept_unconfigured() -> None:
    """企微整部门导入但无凭证 → 明确报错，不误写任何数据。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.wecom_contacts = FakeWecomNoCreds()
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa, "W组", platform="wecom")
        r = sa.post(f"/api/admin/roles/{rid}/import-from-org", json={"dept_id": "100"})
        assert r.status_code == 400, r.text
        assert "corpid" in r.json()["message"]
    finally:
        store.close()


# ── 5. 平台隔离 ─────────────────────────────────────────────────────
def test_import_wecom_role_binds_userid_type() -> None:
    """企微角色 → external_type='user'，绝不写 union_id。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        sa = _sa_ready(app)
        rid = _sa_role(sa, "W组", platform="wecom")
        r = sa.post(f"/api/admin/roles/{rid}/import-from-org", json={"selected_users": [
            {"name": "企微甲", "phone": P_A, "external_id": "wx_uid_1", "department": "市场部"}]})
        assert r.status_code == 200, r.text
        row = store.get_delivery_target(P_A, platform="wecom")
        assert row["external_id"] == "wx_uid_1" and row["external_type"] == "user", row
    finally:
        store.close()