import pytest

from test_20261117_directory_and_upload_cleanup import Directory, P1, P2, person, role, seed, store


@pytest.fixture
def roster(store):
    origin = role(store, "其他管理员的名册", owner_id=42)
    seed(store, origin, P1, "员工甲", owner_id=42)
    destination = role(store, "CSV 导入目标组")
    directory = Directory({
        P1: ("ok", person("不应查询的远端姓名", "zd_1", "远端部门")),
        P2: ("ok", person("员工乙（新部门）", "zd_2", "新部门")),
    })
    store.contacts = directory
    return store, destination, directory


def test_csv_fills_missing_names_from_local_pool_then_official_directory(roster):
    store, destination, directory = roster
    rows = [{"phone": P1}, {"phone": P2, "name": "  "}]
    result = store.import_role_members(destination, rows, dry_run=False, require_directory=True)
    assert not result["blocked"] and result["will_add"] == 2
    assert directory.cached == [P2]
    assert result["global_reused_count"] == 1
    inserted = store.conn.execute("SELECT name,dept,dept_path FROM member WHERE role_id=? ORDER BY phone",
                                  (destination,)).fetchall()
    assert [(row["name"], row["dept"], row["dept_path"]) for row in inserted] == [
        ("员工甲", "旧部门", "总部/旧部门"),
        ("员工乙（新部门）", "新部门", "总部/新部门"),
    ]
    assert store.get_delivery_target(P1)["external_id"] == f"old_{P1}"
    assert store.get_delivery_target(P2)["external_id"] == "zd_2"


@pytest.mark.parametrize("names, failing_phone", [
    (("员工甲", "员工乙"), None),
    (("错误姓名", "员工乙"), P1),
    (("员工甲", "错误姓名"), P2),
])
def test_csv_checks_supplied_names_on_both_sources(roster, names, failing_phone):
    store, destination, directory = roster
    rows = [{"phone": phone, "name": name} for phone, name in zip((P1, P2), names)]
    result = store.import_role_members(destination, rows, dry_run=False, require_directory=True)
    assert directory.cached == [P2]
    if failing_phone is None:
        assert not result["blocked"] and result["will_add"] == 2
        assert store.list_role_members(destination)["total"] == 2
    else:
        assert result["blocked"] and len(result["invalid"]) == 1
        assert f"错误姓名（{failing_phone}）姓名不正确" in result["invalid"][0]["reason"]
        assert store.list_role_members(destination)["total"] == 0
        assert store.get_delivery_target(P2) is None


def test_csv_phone_only_requires_a_real_directory_match(roster):
    store, destination, directory = roster
    directory.outcomes[P2] = ("ok", None)
    result = store.import_role_members(destination, [{"phone": P1}, {"phone": P2}], dry_run=False,
                                       require_directory=True)
    assert result["blocked"] and result["invalid"][0]["row"] == 2
    assert "不存在" in result["invalid"][0]["reason"]
    assert store.list_role_members(destination)["total"] == 0
    assert store.get_delivery_target(P2) is None


def test_csv_phone_only_does_not_write_empty_name_without_directory(roster):
    store, destination, _ = roster
    store.contacts = None
    result = store.import_role_members(destination, [{"phone": P2}], dry_run=False)
    assert result["blocked"] and store.list_role_members(destination)["total"] == 0
