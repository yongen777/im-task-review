from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router, register_admin_guard
from app.channels.wecom_contacts import WecomContacts
from app.channels.zdxlz_contacts import ZdxlzContacts
from app.config import format_dt
from app.state import BizError, StateStore
from admin_test_helpers import post_verified_admin
from mysql_testbank import mysql_test_cfg


NOW = datetime(2026, 11, 17, 3)
P1, P2, P3, P4 = ("13900008801", "13900008802", "13900008803", "13900008804")


@pytest.fixture
def store(tmp_path):
    state = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp_path, now_fn=lambda: NOW)
    try:
        yield state
    finally:
        state.close()


def upload(store, ref, age, *, consumed=False, rel_path=None, create_file=True):
    rel_path = rel_path or f"2026-11-16/t_demo/{ref}.png"
    store.create_upload_ref(
        ref=ref, phone=P1, template_id="t_demo", file_name=f"{ref}.png",
        mime="image/png", size=1, rel_path=rel_path,
    )
    store.conn.execute(
        "UPDATE upload_ref SET created_at = ?, consumed = ? WHERE ref = ?",
        (format_dt(NOW - age), int(consumed), ref),
    )
    file = store.data_dir / "uploads" / rel_path
    if create_file:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"x")
    return file


def test_cleanup_removes_expired_orphan_and_empty_parents(store):
    file = upload(store, "expired", timedelta(hours=25))
    upload(store, "already_missing", timedelta(hours=25), create_file=False)
    assert store.cleanup_orphan_uploads() == 2
    assert not file.exists() and not file.parent.exists() and not file.parent.parent.exists()
    assert (store.data_dir / "uploads").is_dir()
    assert store.get_upload_ref("expired") is None
    assert store.get_upload_ref("already_missing") is None
    assert store.cleanup_orphan_uploads() == 0


def test_cleanup_preserves_consumed_recent_and_exact_cutoff(store):
    consumed = upload(store, "consumed", timedelta(days=3), consumed=True)
    fresh = upload(store, "recent", timedelta(hours=23))
    boundary = upload(store, "exact", timedelta(hours=24))
    assert store.cleanup_orphan_uploads() == 0
    assert all(file.exists() for file in (consumed, fresh, boundary))
    assert all(store.get_upload_ref(ref) for ref in ("consumed", "recent", "exact"))


def test_cleanup_refuses_absolute_and_parent_traversal(store):
    outside = store.data_dir / "outside.png"
    outside.write_bytes(b"x")
    upload(store, "outside", timedelta(days=3), rel_path="../outside.png", create_file=False)
    upload(store, "absolute", timedelta(days=3), rel_path=str(outside), create_file=False)
    assert store.cleanup_orphan_uploads() == 0
    assert outside.read_bytes() == b"x"
    assert store.get_upload_ref("outside") and store.get_upload_ref("absolute")


def test_replaced_cleanup_retries_from_audit_and_preserves_shared_attachments(store, monkeypatch):
    uploads_dir = store.data_dir / 'uploads'
    age = timedelta()
    retired = upload(store, 'retired', age, consumed=True)
    current = upload(store, 'current', age, consumed=True)
    for ref in ('shared_legacy', 'shared_json', 'unrelated'):
        upload(store, ref, age, consumed=True)
    upload(store, 'outside', age, consumed=True, rel_path='../outside.png', create_file=False)
    outside = store.data_dir / 'outside.png'
    outside.write_bytes(b'keep')
    store.conn.execute("INSERT INTO submission(instance_id,file_ref,refs_json,source,status,created_at) VALUES (1,?,?, 'user','approved',?)",
                       ('shared_legacy', json.dumps(['shared_json', 'current']), store.now_str()))
    store.write_audit('admin', 'instance.edit_submission', 'task_instance', '1', {
        'previous_refs': ['retired', 'shared_legacy', 'shared_json', 'outside'], 'refs': ['current'],
    })
    with monkeypatch.context() as patch:
        def blocked(path, *, missing_ok=False):
            raise PermissionError('file busy')
        patch.setattr(Path, 'unlink', blocked)
        assert store.cleanup_replaced_uploads(uploads_dir) == 0
    assert retired.exists() and store.get_upload_ref('retired')
    assert store.cleanup_replaced_uploads(uploads_dir) == 1
    assert not retired.exists() and store.get_upload_ref('retired') is None
    assert current.exists() and outside.read_bytes() == b'keep'
    assert all(store.get_upload_ref(ref) for ref in ('shared_legacy', 'shared_json', 'unrelated', 'outside'))
    assert store.cleanup_replaced_uploads(uploads_dir) == 0


def test_zdxlz_department_priority(monkeypatch):
    monkeypatch.delenv("IMT_ORG_PREFER_DEPT_KEYWORD", raising=False)
    primary = {"name": "主部门", "full_name": "省公司|主部门", "is_primary": True}
    preferred = {"name": "本地组", "full_name": "省公司|中国电信德阳分公司|本地组"}
    preferred_primary = {
        "name": "本地主组", "full_name": "省公司|中国电信德阳分公司|本地主组", "is_primary": True,
    }
    first = {"name": "第一部门", "full_name": "省公司|第一部门"}

    def pick(*departments):
        return ZdxlzContacts._pick_user({"union_id": "u_demo", "user_departments": list(departments)})

    assert pick(first, primary, preferred)["department"] == "本地组"
    assert pick(preferred, primary, preferred_primary)["dept_path"] == "省公司/中国电信德阳分公司/本地主组"
    assert pick(first, primary)["department"] == "主部门"
    assert pick(first, preferred | {"full_name": "异地组"})["department"] == "第一部门"
    monkeypatch.setenv("IMT_ORG_PREFER_DEPT_KEYWORD", "第一部门")
    assert pick(primary, preferred_primary, first)["department"] == "第一部门"


class Directory:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.cached = []
        self.fresh = []

    def search_by_phone_strict(self, phone):
        self.cached.append(phone)
        return self.outcomes.get(phone, ("ok", None))

    def refresh_by_phone_strict(self, phone):
        self.fresh.append(phone)
        return self.outcomes.get(phone, ("ok", None))


def person(name, external_id, department):
    return {"name": name, "union_id": external_id, "department": department, "dept_path": f"总部/{department}"}


def role(store, name, platform="zdxlz", owner_id=1):
    return store.create_role(name, platform=platform, owner_id=owner_id)["role_id"]


def seed(store, role_id, phone, name, platform="zdxlz", owner_id=1):
    store.add_role_member(role_id, phone, name, "旧部门", owner_id=owner_id)
    store.upsert_delivery_target(phone, f"old_{phone}", platform=platform, activated_at="2026-10-01 08:00:00")
    store.conn.execute("UPDATE member SET dept_path = ? WHERE role_id = ? AND phone = ?",
                       ("总部/旧部门", role_id, phone))


def test_local_pool_quick_add_and_csv_reuse_without_external_calls(store):
    origin = role(store, "原组")
    seed(store, origin, P1, "员工甲")
    target = role(store, "目标组")
    batch = role(store, "批量组")
    store.contacts = fake = Directory({})
    with pytest.raises(BizError, match="姓名不正确"):
        store.add_role_member(target, P1, "姓名错误")
    added = store.add_role_member(target, P1, "员工甲", "应忽略的输入部门")
    assert added["dept"] == "旧部门" and added["activated"] and added["reused_binding"]
    result = store.import_role_members(batch, [{"name": "员工甲", "phone": P1}], dry_run=False)
    assert result["global_reused_count"] == 1 and result["auto_activation"]["skipped"] == 1
    rows = store.conn.execute("SELECT dept_path FROM member WHERE phone = ?", (P1,)).fetchall()
    assert [row[0] for row in rows] == ["总部/旧部门"] * 3
    assert store.get_delivery_target(P1, "zdxlz")["external_id"] == f"old_{P1}"
    assert fake.cached == [] and fake.fresh == []


def test_csv_pool_is_platform_isolated_and_rejects_unusable_or_left(store):
    wx = role(store, "已在企微", "wecom")
    seed(store, wx, P1, "企微姓名", "wecom")
    src = role(store, "旧密信")
    seed(store, src, P2, "已离职")
    seed(store, src, P3, "旧绑定")
    store.conn.execute("UPDATE member SET is_left_org = 1 WHERE role_id = ? AND phone = ?", (src, P2))
    store.conn.execute("UPDATE delivery_target SET usable = 0 WHERE platform = 'zdxlz' AND phone = ?", (P3,))
    store.contacts = fake = Directory({
        P1: ("ok", person("密信姓名", "zd_1", "新部门")),
        P3: ("ok", person("当前绑定", "zd_3", "新部门")),
    })
    dest = role(store, "新密信")
    result = store.import_role_members(dest, [
        {"name": "密信姓名", "phone": P1},
        {"name": "已离职", "phone": P2},
        {"name": "当前绑定", "phone": P3},
    ], dry_run=False)
    assert result["blocked"] and result["invalid"][0]["row"] == 2
    assert set(fake.cached) == {P1, P2, P3}
    assert store.list_role_members(dest)["total"] == 0
    assert store.get_delivery_target(P1, "zdxlz") is None
    row = store.conn.execute(
        "SELECT usable FROM delivery_target WHERE platform = 'zdxlz' AND phone = ?", (P3,),
    ).fetchone()
    assert row["usable"] == 0


def test_csv_pool_name_mismatch_and_binding_failure_write_nothing(store, monkeypatch):
    src = role(store, "旧组")
    seed(store, src, P1, "员工甲")
    dest = role(store, "新组")
    store.contacts = fake = Directory({P2: ("ok", person("员工乙", "zd_2", "新部门"))})
    bad = store.import_role_members(dest, [{"phone": P1, "name": "错名"}], dry_run=False)
    assert bad["blocked"] and "姓名不正确" in bad["invalid"][0]["reason"]
    assert fake.cached == []

    def fail_binding(*args, **kwargs):
        raise BizError(503, "TEST_BINDING_FAILURE", "模拟绑定失败")

    monkeypatch.setattr(store, "upsert_delivery_target", fail_binding)
    with pytest.raises(BizError, match="模拟绑定失败"):
        store.import_role_members(dest, [
            {"phone": P1, "name": "员工甲"}, {"phone": P2, "name": "员工乙"},
        ], dry_run=False)
    assert store.list_role_members(dest)["total"] == 0
    assert store.get_delivery_target(P2, "zdxlz") is None


def test_directory_sync_updates_current_roster_and_continues_on_missing_or_outage(store):
    first, second = role(store, "甲组"), role(store, "乙组")
    other_platform = role(store, "企微组", "wecom")
    other_owner = role(store, "其他管理员", owner_id=42)
    for rid, phone in ((first, P1), (second, P1), (first, P2), (first, P3)):
        seed(store, rid, phone, "旧姓名")
    seed(store, other_platform, P1, "企微姓名", "wecom")
    seed(store, other_owner, P4, "其他姓名", owner_id=42)
    store.contacts = fake = Directory({
        P1: ("ok", person("最新姓名", "fresh_uid", "最新部门")),
        P2: ("ok", None),
        P3: ("error", None),
    })
    res = store.sync_directory_profiles("zdxlz", 1, "tester")
    assert res == {"ok": True, "total": 3, "updated": 1, "left": 1, "failed": 1}
    assert sorted(fake.fresh) == [P1, P2, P3] and fake.cached == []
    assert store.get_delivery_target(P1, "zdxlz")["external_id"] == "fresh_uid"
    assert store.get_delivery_target(P1, "zdxlz")["activated_at"] == "2026-10-01 08:00:00"
    assert store.get_delivery_target(P1, "wecom")["external_id"] == f"old_{P1}"
    first_by_phone = {item["phone"]: item for item in store.list_role_members(first)["items"]}
    assert first_by_phone[P1]["name"] == store.list_role_members(second)["items"][0]["name"] == "最新姓名"
    assert first_by_phone[P1]["dept"] == "最新部门"
    assert first_by_phone[P2]["is_left_org"] and first_by_phone[P2]["name"] == "旧姓名"
    assert not first_by_phone[P3]["is_left_org"]
    assert store.build_dept_tree(platform="zdxlz", owner_id=1) == [{
        "name": "总部", "path": "总部", "count": 2,
        "children": [
            {"name": "旧部门", "path": "总部/旧部门", "count": 1, "children": []},
            {"name": "最新部门", "path": "总部/最新部门", "count": 1, "children": []},
        ],
    }]
    fake.outcomes[P2] = ("ok", person("恢复成员", "return_uid", "返回部门"))
    assert store.sync_directory_profiles("zdxlz", 1, "tester")["updated"] == 2
    recovered = next(item for item in store.list_role_members(first)["items"] if item["phone"] == P2)
    assert recovered["name"] == "恢复成员" and not recovered["is_left_org"]


def test_read_only_department_and_sync_endpoints_are_owner_and_platform_scoped(store):
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t", require_directory=True))

    def login(username, password):
        c = TestClient(app)
        response = c.post("/api/admin/login", json={"username": username, "password": password})
        assert response.status_code == 200
        c.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
        return c

    admin = login("admin", "Admin@2026")
    response = post_verified_admin(admin, {
        "username": "branch", "display_name": "分部", "allowed_platforms": ["zdxlz"],
    })
    assert response.status_code == 200, response.text
    sub = login("branch", StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
    change = sub.post("/api/admin/change-password", json={
        "old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "Branch@2026",
    })
    assert change.status_code == 200, change.text
    sub = login("branch", "Branch@2026")
    owner_id = store.get_admin_by_username("branch")["id"]
    rid = role(store, "分部", owner_id=owner_id)
    seed(store, rid, P1, "所属成员", owner_id=owner_id)
    member_path = f"/api/admin/roles/{rid}/members"
    missing = {"name": "通讯录不可用", "phone": P2}
    assert sub.post(member_path, json=missing).status_code == 503
    assert sub.post(f"{member_path}/import", json={"csv_rows": [missing], "dry_run": False}).status_code == 503
    assert store.list_role_members(rid)["total"] == 1
    store.contacts = Directory({P1: ("ok", person("新档案", "uid_new", "官方部门"))})
    assert admin.post("/api/admin/directory/sync", json={"platform": "zdxlz"}).status_code == 403
    assert sub.post("/api/admin/directory/sync", json={"platform": "wecom"}).status_code == 403
    sync = sub.post("/api/admin/directory/sync", json={"platform": "zdxlz"})
    assert sync.status_code == 200 and sync.json()["updated"] == 1, sync.text
    for client in (admin, sub):
        tree = client.get("/api/admin/dept-tree?platform=zdxlz").json()
        assert tree["items"][0]["children"][0]["name"] == "官方部门"
        assert client.post("/api/admin/departments", json={"name": "手动部门"}).status_code == 403
    path = f"/api/admin/roles/{rid}/members/{P1}"
    assert sub.put(path, json={"phone": P2}).status_code == 403
    assert sub.put(path, json={"enabled": False}).json()["member"]["enabled"] is False


def test_mysql_round_trips_literal_percents_with_and_without_parameters(store):
    sql = "SELECT '100%' AS literal, '%%' AS doubles"
    assert tuple(store.conn.execute(sql).fetchone()) == ("100%", "%%")
    assert tuple(store.conn.execute(sql + ", ? AS param", ("20%",)).fetchone()) == ("100%", "%%", "20%")


def test_wecom_sync_refreshes_department_map_and_reuses_it_across_phones(store):
    class Config:
        wecom_corpid = "corp"
        wecom_secret = "secret"

    class Client:
        department_reads = 0

        def get_userid_by_mobile_strict(self, phone):
            return ("ok", f"user_{phone}")

        def directory_user(self, userid):
            return {"name": userid, "main_department": 10, "department": [20, 10]}

        def department_list(self):
            self.department_reads += 1
            return [
                {"dept_id": "1", "name": "总部", "parent_id": "0"},
                {"dept_id": "10", "name": f"部门{self.department_reads}", "parent_id": "1"},
            ]

    rid = role(store, "企微同步", "wecom")
    seed(store, rid, P1, "旧姓名", "wecom")
    seed(store, rid, P2, "旧姓名", "wecom")
    client = Client()
    resolver = WecomContacts(Config(), client=client)
    store.wecom_contacts = resolver
    assert resolver.resolve_user_strict(P1)[1]["department"] == "部门1"
    assert store.sync_directory_profiles("wecom", 1, "test")["updated"] == 2
    assert client.department_reads == 2
    assert [m["dept"] for m in store.list_role_members(rid, platform="wecom")["items"]] == ["部门2", "部门2"]
    assert store.get_delivery_target(P1, "wecom")["external_id"] == f"user_{P1}"


def test_frontend_directory_tree_and_read_only_profile_contract():
    ui = Path(__file__).resolve().parents[2] / "im-task-admin" / "src" / "components" / "imtask" / "RolesMembersView.tsx"
    if not ui.exists():
        pytest.skip("此环境没有前端工作区")
    source = ui.read_text(encoding="utf-8")
    tree = ui.with_name("DepartmentTree.tsx").read_text(encoding="utf-8")
    assert "部门架构" in source and "deptModalOpen &&" in source and "<DepartmentTree nodes={deptTree}" in source
    assert "useState(() => new Set(" in tree
    assert "aria-expanded={isExpanded}" in tree and "isExpanded && hasChildren" in tree
    assert "同步最新部门" in source and "已移出通讯录（疑似离职）" in source
    assert "停用派单" in source and "恢复派单" in source and "加入其他角色" in source and "批量移除" in source
    assert "加入部门选项" not in source and "新建下级" not in source
    assert "openEditMemberModal" not in source and "setPoolEditing" not in source
