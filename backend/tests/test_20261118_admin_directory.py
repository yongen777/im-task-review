from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import state as state_module
from test_multi_admin import _client, _store


PHONE = "13900009971"


@pytest.mark.parametrize(("fields", "directory_result", "status", "code"), [
    ({"display_name": ""}, None, 400, "VALIDATION_FAILED"),
    ({"phone": ""}, None, 400, "VALIDATION_FAILED"),
    ({}, None, 503, "CONTACTS_QUERY_FAILED"),
    ({}, ("error", None), 503, "CONTACTS_QUERY_FAILED"),
    ({}, ("ok", None), 400, "PHONE_NOT_IN_CONTACTS"),
    ({}, ("ok", {"name": "另一个人"}), 400, "NAME_MISMATCH"),
    ({}, ("ok", {"name": "测试成员（演示部门）"}), 200, None),
])
def test_admin_create_requires_verified_phone_and_real_name(fields, directory_result, status, code):
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026")
        client = _client(store, "admin", "Admin@2026")
        calls = []

        class Directory:
            def refresh_by_phone_strict(self, phone):
                calls.append(phone)
                return directory_result

            def search_by_phone_strict(self, phone):
                pytest.fail("新建管理员需要实时查询官方通讯录")

        if directory_result is not None:
            store.contacts = Directory()

        response = client.post("/api/admin/accounts", json={
            "username": "verified_owner", "display_name": "测试成员", "phone": PHONE,
            "allowed_platforms": ["zdxlz"], **fields,
        })
        assert response.status_code == status, response.text
        if code:
            assert response.json()["code"] == code
            assert store.get_admin_by_username("verified_owner") is None
        else:
            assert response.json()["account"]["phone"] == PHONE
            assert store.get_admin_by_username("verified_owner")["display_name"] == "测试成员"
        assert calls == ([PHONE] if directory_result is not None else [])
    finally:
        store.close()


def test_admin_create_uses_only_authorized_platform_directories():
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026")
        client = _client(store, "admin", "Admin@2026")

        class Zdxlz:
            def search_by_phone_strict(self, phone):
                pytest.fail("仅授权企微时不能查密信通讯录")

        class Wecom:
            def resolve_user_strict(self, phone, name, dept):
                assert phone == PHONE
                return "ok", {"name": "测试成员"}

        store.contacts, store.wecom_contacts = Zdxlz(), Wecom()
        response = client.post("/api/admin/accounts", json={
            "username": "wecom_verified", "display_name": "测试成员", "phone": PHONE,
            "allowed_platforms": ["wecom"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["account"]["allowed_platforms"] == ["wecom"]
    finally:
        store.close()


def test_admin_create_duplicate_concurrent_requests_return_conflict(monkeypatch):
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026")
        client = _client(store, "admin", "Admin@2026")
        store.contacts = type("Directory", (), {
            "search_by_phone_strict": lambda self, phone: ("ok", {"name": "测试成员"}),
        })()
        both_validated = Barrier(2)
        hash_password = state_module.hash_password

        def hash_when_both_requests_passed_checks(password):
            both_validated.wait(timeout=10)
            return hash_password(password)

        monkeypatch.setattr(state_module, "hash_password", hash_when_both_requests_passed_checks)
        payload = {"username": "duplicate_owner", "display_name": "测试成员", "phone": PHONE,
                   "allowed_platforms": ["zdxlz"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: client.post("/api/admin/accounts", json=payload), range(2)))
        assert sorted(r.status_code for r in outcomes) == [200, 409]
        assert next(r for r in outcomes if r.status_code == 409).json()["code"] == "USERNAME_TAKEN"
    finally:
        store.close()
