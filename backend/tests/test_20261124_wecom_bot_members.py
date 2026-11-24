from datetime import timedelta
from types import SimpleNamespace

from app.api.events import handle_inbound_text
from app.channels.wecom_aibot import WecomAibotNotifier
from app.channels.wecom_contacts import WecomContacts
from app.scheduler import Scheduler
from test_20261110_wecom_mcp_contacts import _mcp
from test_20261117_manual_mark_submitted import NOW, PHONE, ctx
from test_wecom_aibot import FakeAibotClient, _make_ready_bridge, _text_frame, _wait_until

DEPARTMENT = "四川分公司/德阳分公司/综合部"
USERID = "wecom_member_01"
WECHAT = {"userid": USERID, "name": "测试成员", "departments": [DEPARTMENT.replace("/", "|")]}
CONFIG = SimpleNamespace(wecom_mcp_apikey="test_key", wecom_bot_id="test_bot", wecom_bot_secret="test_secret",
                         wecom_corpid="unused_app_corp", wecom_secret="unused_app_secret", wecom_agentid="123")


def _setup(ctx, users):
    store, _, _, super_admin, owner, new_admin, template, _ = ctx
    owner_id = template["owner_id"]
    role_id = store.create_role("企微演示角色", platform="wecom", owner_id=owner_id)["role_id"]
    store.wecom_contacts = WecomContacts(CONFIG, mcp=_mcp(users), mcp_only=True)
    assert not store.wecom_contacts.supports_strict_phone_verify() and not store.wecom_contacts.org_available()
    endpoint = f"/api/admin/roles/{role_id}/members/wecom-bot"
    body = {"name": "测试成员", "phone": PHONE, "dept_path": DEPARTMENT}
    return store, super_admin, owner, new_admin, owner_id, role_id, endpoint, body


def test_bot_member_binding_and_interactive_delivery(ctx):
    store, _, owner, _, owner_id, role_id, endpoint, body = _setup(ctx, [WECHAT])
    preview = owner.post(endpoint, json=body)
    assert preview.status_code == 200, preview.text
    assert [c["userid"] for c in preview.json()["candidates"]] == [USERID]
    assert store.get_delivery_target(PHONE, "wecom") is None
    bound = owner.post(endpoint, json={**body, "userid": USERID})
    assert bound.status_code == 200, bound.text
    assert store.get_delivery_target(PHONE, "wecom")["external_id"] == USERID
    assert store.get_delivery_target(PHONE, "zdxlz")["external_id"] == "test_recipient"
    member = store.conn.execute("SELECT dept_path, source FROM member WHERE role_id=? AND phone=?",
                                (role_id, PHONE)).fetchone()
    assert member["dept_path"] == DEPARTMENT and member["source"] == "wecom_mcp"

    class WeComTransport(FakeAibotClient):
        opened = False

        async def send_message(self, chatid, message):
            if not self.opened:
                return {"errcode": 1, "errmsg": "会话尚未开通"}
            return await super().send_message(chatid, message)

    client = WeComTransport()
    bridge = _make_ready_bridge(client)
    try:
        notifier = WecomAibotNotifier(CONFIG, bridge=bridge, on_text=lambda userid, msg, send: handle_inbound_text(
            state=store, user_id=userid, msg_text=msg, send=send, platform="wecom", base_url="http://test"))
        assert not notifier.send_text(USERID, "首条测试", "not-open").ok
        client.opened = True
        bridge._on_message_text(_text_frame(USERID, "你好"))
        assert _wait_until(lambda: bool(client.calls))
        assert "/h5/portal#t=" in client.calls[0][1]["markdown"]["content"]
        notice = store.create_template({
            "name": "企微测试任务", "kind": "notice", "period": "day", "is_manual": True,
            "role_ids": [role_id], "text_tpl": "测试任务已送达",
        }, owner_id=owner_id)
        scheduler = Scheduler(store, public_base_url="http://test", notifier=notifier,
                              notifiers={"wecom": notifier}, now_fn=lambda: NOW + timedelta(minutes=1))
        scheduler.push_now(notice["template_id"])
        assert client.calls[-1][0] == USERID
        assert "测试任务已送达" in client.calls[-1][1]["markdown"]["content"]
    finally:
        bridge._loop.call_soon_threadsafe(bridge._loop.stop)


def test_bot_member_collision_requires_validated_wecom_account(ctx):
    other = {**WECHAT, "userid": "wecom_member_02", "position": "值班经理"}
    mismatched = {**WECHAT, "userid": "wecom_member_03", "departments": ["四川分公司/其他分公司/综合部"]}
    store, admin, owner, new_admin, owner_id, role_id, endpoint, body = _setup(ctx, [WECHAT, other, mismatched])
    result = owner.post(endpoint, json=body)
    assert result.status_code == 200, result.text
    assert {c["userid"] for c in result.json()["candidates"]} == {USERID, other["userid"]}
    assert store.list_role_members(role_id)["total"] == 0
    assert admin.post(endpoint, json=body).status_code == 403
    _, outsider = new_admin("bot_binding_other")
    assert outsider.post(endpoint, json=body).status_code == 403
    assert owner.post(endpoint, json={**body, "userid": mismatched["userid"]}).status_code == 409
    assert owner.post(endpoint, json={**body, "userid": other["userid"]}).status_code == 200
    second_role = store.create_role("企微演示另一角色", platform="wecom", owner_id=owner_id)["role_id"]
    second = f"/api/admin/roles/{second_role}/members/wecom-bot"
    conflict = owner.post(second, json={**body, "phone": "13900009402", "userid": other["userid"]})
    assert conflict.status_code == 409 and conflict.json()["code"] == "WECOM_BINDING_CONFLICT"
    assert store.list_role_members(second_role)["total"] == 0
