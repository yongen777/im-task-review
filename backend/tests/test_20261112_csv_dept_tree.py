# -*- coding: utf-8 -*-
"""2026-11-12 A1 + A3 专项：两列 CSV 导入 与 超管部门分级树。

A1（后端侧，前端 parseCsvRows 已改两列自动识别列序）：
1. CSV 只给「姓名,手机号」两列（dept 缺省）→ 导入成功；不自动建部门选项；
   部门/路径由通讯录反查回填 member.dept / member.dept_path，并写 union_id 绑定。
2. resolver 未装配时（测试/降级环境）两列导入同样成功（fail-open，不阻塞导入）。

A3：build_dept_tree()
3. 按 dept_path 的 "/" 分段聚合成树；count = 人数（含全部下级）。
4. 同一手机号多角色/多管理员行不重复计数；同一人跨 dept_path 时公共祖先只计 1 人。
5. enabled=0（停用）不计入；dept_path 为空不计入。
6. GET /api/admin/dept-tree：超管 200、分级管理员 403、匿名 401。

全部相对时刻（AGENTS.md 第六节：时间炸弹教训），不依赖真实网络。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.state import StateStore  # noqa: E402

PWD = StateStore.DEFAULT_SUB_ADMIN_PASSWORD
PHONE_A = "13600001111"
PHONE_B = "13600002222"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_csv_tree_")) / "im.db")


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


def _sub(app: FastAPI, username: str, platforms: Optional[List[str]] = None) -> TestClient:
    """建分级管理员 → 登录 → 强制改密 → 返回可用会话。"""
    admin = TestClient(app)
    r = admin.post("/api/admin/login", json={"username": "admin", "password": "Admin@2026"})
    admin.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    body: Dict[str, Any] = {"username": username, "display_name": username}
    if platforms is not None:
        body["allowed_platforms"] = platforms
    r = admin.post("/api/admin/accounts", json=body)
    assert r.status_code == 200, r.text
    c = _login(app, username, PWD)
    c.post("/api/admin/change-password", json={"old_password": PWD, "new_password": "NewPass@2026"})
    return c


def _role(c: TestClient, name: str, platform: str = "zdxlz") -> str:
    r = c.post("/api/admin/roles", json={"name": name, "platform": platform})
    assert r.status_code == 200, r.text
    return r.json()["role_id"]


class FakeContacts:
    """内存版通讯录：只映射显式登记过的手机号（绝不发起真实网络请求）。"""

    def __init__(self, users: Optional[List[Dict[str, Any]]] = None):
        self.users = list(users or [])
        self.by_phone = {(u.get("phone") or "").strip(): u for u in self.users}
        self.calls = 0

    def search_by_phone(self, phone: str):
        self.calls += 1
        return self.by_phone.get((phone or "").strip())

    def search_by_name(self, name: str):
        return [u for u in self.users if u.get("name") == name]

    def get_by_union_id(self, union_id: str):
        for u in self.users:
            if u.get("union_id") == union_id:
                return u
        return None


def _contact_user(union_id: str, phone: str, name: str, department: str, dept_path: str) -> Dict[str, Any]:
    return {"union_id": union_id, "phone": phone, "name": name,
            "department": department, "dept_path": dept_path}


def _add_member_row(store: StateStore, role_id: str, phone: str, name: str,
                    dept_path: Optional[str], enabled: int = 1, owner_id: int = 1) -> None:
    """直接造名册行（dept_path 是通讯录反查产物；树聚合只认这两列）。"""
    leaf = (dept_path or "").split("/")[-1] or None
    with store.lock, store.conn:
        store.conn.execute(
            "INSERT INTO member(role_id, phone, name, dept, dept_path, enabled, source, created_at, owner_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'import', ?, ?)",
            (role_id, phone, name, leaf, dept_path, enabled, store.now_str(), owner_id),
        )


# ── A1：两列 CSV（dept 缺省）导入 ────────────────────────────────────
def test_csv_two_columns_import_backfills_dept_from_contacts() -> None:
    """A1：CSV 两列 → 导入成功、不建部门选项、反查回填 dept/dept_path 并激活。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        store.contacts = FakeContacts([
            _contact_user("union_A", PHONE_A, "张三", "市公司本部", "四川/德阳/市公司本部"),
            _contact_user("union_B", PHONE_B, "李四", "产数支撑中心", "四川/德阳/产数支撑中心"),
        ])
        app = _app(store)
        a = _sub(app, "a1")
        rid = _role(a, "测试组")
        res = a.post(f"/api/admin/roles/{rid}/members/import", json={
            "csv_rows": [
                {"row": 1, "phone": PHONE_A, "name": "张三"},
                {"row": 2, "phone": PHONE_B, "name": "李四"},
            ],
            "dry_run": False,
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["blocked"] is False and body["will_add"] == 2, res.text
        # dept 留空 → 不自动建部门选项（前端 A1 改造后不再有部门列）
        depts = a.get("/api/admin/departments").json()["items"]
        assert depts == [], depts
        # 反查回填：官方末级部门 + 官方全路径（A3 树的数据源）
        rows = store.conn.execute(
            "SELECT phone, dept, dept_path FROM member WHERE role_id = ? ORDER BY phone", (rid,)).fetchall()
        assert [(r["phone"], r["dept"], r["dept_path"]) for r in rows] == [
            (PHONE_A, "市公司本部", "四川/德阳/市公司本部"),
            (PHONE_B, "产数支撑中心", "四川/德阳/产数支撑中心"),
        ], rows
        # 导入即激活：union_id 绑定落库
        dt = store.get_delivery_target(PHONE_A, platform="zdxlz")
        assert dt and dt.get("external_type") == "union_id", dt
    finally:
        store.close()


def test_csv_two_columns_import_without_resolver_still_ok() -> None:
    """A1：resolver 未装配（降级环境）时两列导入照常成功，dept_path 留给后续反查。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        assert getattr(store, "contacts", None) is None
        app = _app(store)
        a = _sub(app, "a1")
        rid = _role(a, "测试组")
        res = a.post(f"/api/admin/roles/{rid}/members/import", json={
            "csv_rows": [{"row": 1, "phone": PHONE_A, "name": "张三"}],
            "dry_run": False,
        })
        assert res.status_code == 200, res.text
        assert res.json()["will_add"] == 1 and res.json()["blocked"] is False, res.text
        row = store.conn.execute(
            "SELECT phone, dept, dept_path FROM member WHERE role_id = ?", (rid,)).fetchone()
        assert row["phone"] == PHONE_A and row["dept"] is None and row["dept_path"] is None
    finally:
        store.close()


# ── A3：部门分级树聚合 ───────────────────────────────────────────────
def test_build_dept_tree_aggregation() -> None:
    """A3：树按 dept_path 分段聚合；count 含下级；跨行/跨路径按人去重；停用/空路径不计。"""
    store = _store()
    try:
        rid_a = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        rid_b = store.create_role("乙组", actor="t", platform="zdxlz")["role_id"]
        # 张三：同一人两条名册行（两个角色/管理员），同一路径 → 四川只计 1 人
        _add_member_row(store, rid_a, "13600001111", "张三", "四川/德阳/市公司本部")
        _add_member_row(store, rid_b, "13600001111", "张三", "四川/德阳/市公司本部", owner_id=2)
        # 李四：同一条路径的另一末级
        _add_member_row(store, rid_a, "13600002222", "李四", "四川/德阳/产数支撑中心")
        # 王五：另一分支
        _add_member_row(store, rid_a, "13600003333", "王五", "四川/绵阳")
        # 赵六：dept_path 空（尚未反查）→ 不计入
        _add_member_row(store, rid_a, "13600004444", "赵六", None)
        # 孙七：停用 → 不计入（若计入，德阳会变成 4）
        _add_member_row(store, rid_a, "13600005555", "孙七", "四川/德阳", enabled=0)
        # 周八：另一个根
        _add_member_row(store, rid_a, "13600006666", "周八", "总部/云中台")
        # 钱九：跨角色两条不同 dept_path → 公共祖先「四川/德阳」只计 1 人
        _add_member_row(store, rid_a, "13600007777", "钱九", "四川/德阳/市公司本部")
        _add_member_row(store, rid_b, "13600007777", "钱九", "四川/德阳/产数支撑中心", owner_id=2)

        tree = store.build_dept_tree()
        assert {n["name"] for n in tree} == {"四川", "总部"}, tree
        assert tree[0]["path"] == "四川" and tree[1]["path"] == "总部"  # 字典序输出
        sc = tree[0]
        assert sc["count"] == 4  # 张三/李四/王五/钱九（张三、钱九跨行只各计 1 次）
        assert {c["name"]: c["count"] for c in sc["children"]} == {"德阳": 3, "绵阳": 1}
        dy = next(c for c in sc["children"] if c["name"] == "德阳")
        assert dy["path"] == "四川/德阳"
        # 钱九跨两条路径：两个末级都在（各含钱九），但德阳/四川只计 1 次（上面已锁定）
        assert {c["name"]: c["count"] for c in dy["children"]} == {
            "产数支撑中心": 2,  # 李四 + 钱九
            "市公司本部": 2,    # 张三 + 钱九
        }
        assert tree[1]["count"] == 1
        assert [c["name"] for c in tree[1]["children"]] == ["云中台"]
        assert tree[1]["children"][0]["count"] == 1
    finally:
        store.close()


# ── A3：接口权限 ─────────────────────────────────────────────────────
def test_dept_tree_endpoint_permissions() -> None:
    """A3：GET /api/admin/dept-tree 仅超管可读（分级 403 / 匿名 401）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        # 匿名
        anon = TestClient(app).get("/api/admin/dept-tree")
        assert anon.status_code == 401, anon.text
        # 超管：200 且返回结构完整
        super_c = _login(app, "admin", "Admin@2026")
        r = super_c.get("/api/admin/dept-tree")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and isinstance(body["items"], list), body
        # 分级管理员：403 FORBIDDEN（A2 同口径：部门全局库仅超管）
        sub_c = _sub(app, "a1")
        r2 = sub_c.get("/api/admin/dept-tree")
        assert r2.status_code == 403, r2.text
        assert r2.json().get("code") == "FORBIDDEN", r2.text
    finally:
        store.close()