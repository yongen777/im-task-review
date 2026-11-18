from unittest.mock import patch

from app.state import StateStore


def post_verified_admin(client, payload):
    """创建测试管理员时用单次通讯录替身模拟已核验的姓名与手机号。"""
    data = {"display_name": payload["username"], "phone": "13900009971", **payload}

    def search(phone, name, dept):
        if phone == data["phone"]:
            return "ok", {"name": data["display_name"]}
        return "ok", None

    with patch.object(StateStore, "_strict_contacts_lookup", return_value=search):
        return client.post("/api/admin/accounts", json=data)
