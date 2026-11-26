import json

import pytest

from test_20261117_manual_mark_submitted import ctx
from test_20261119_manual_repush_batches import advance, make_manual_form, make_manual_link_notice, make_periodic_form
from test_20261124_variable_notice import BODY, add_second, payload, preflight


@pytest.mark.parametrize("notice", [False, True])
def test_note_on_repush_preserves_original_body_and_template(ctx, notice):
    state, scheduler, notifier, _, client, _, existing, _ = ctx
    factory = make_manual_link_notice if notice else make_manual_form
    task = factory(client, existing["role_ids"][0])
    original = state.get_template(task)
    url = f"/api/admin/templates/{task}/push-now"
    assert client.post(url, json={"push_note": " \n "}).json()["queued"]
    before = notifier.sent_messages[0].text
    if notice:
        assert before == "请测试成员查阅本条通知正文。"
    else:
        assert before.startswith(f"【任务提醒】{original['name']}\n请测试成员提交今日数据\n\n")
    advance(state, scheduler)
    note = "截止临近，{姓名}请及时提交！"
    assert client.post(url, json={"push_note": note}).json()["needs_confirm"]
    count = len(notifier.sent_messages)
    response = client.post(url, params={"confirmed": 1}, json={"push_note": "  " + note + " \n"})
    assert response.status_code == 200, response.text
    after = notifier.sent_messages[count].text
    if notice:
        assert after == f"【通知提醒】{original['name']}\n【补充提醒】{note}\n{before}"
    else:
        heading, _, body = before.partition("\n")
        assert after.split("#t=")[0] == f"{heading}\n【补充提醒】{note}\n{body.split('#t=')[0]}"
    assert state.get_template(task)["text_tpl"] == original["text_tpl"]
    logs = state.conn.execute(
        "SELECT detail FROM admin_audit WHERE target_id=? AND action='template.push_now' ORDER BY id", (task,),
    ).fetchall()
    assert [json.loads(row["detail"]).get("push_note") for row in logs] == [None, None, note, note]


def test_variable_notice_note_keeps_recipient_rendering_and_dedupe(ctx):
    state, scheduler, notifier, _, client, _, existing, _ = ctx
    state.update_template(existing["template_id"], {"enabled": False})
    add_second(ctx)
    config = preflight(client).json()["config"]
    created = client.post("/api/admin/templates", json=payload(config, "none"))
    assert created.status_code == 200, created.text
    task = created.json()["template_id"]
    url = f"/api/admin/templates/{task}/push-now"
    assert client.post(url, json={"push_note": "请核对{得分}"}).json()["queued"]
    for _ in range(3):
        scheduler.run_once()
        scheduler.push_now(task)
    assert len(notifier.sent_messages) == 2
    expected = {
        "test_recipient": "测试成员您好，得分 95，再次核对 95，金额 1.25 元",
        "second-union": "第二成员您好，得分 88，再次核对 88，金额 0 元",
    }
    for message in notifier.sent_messages:
        assert message.text == f"【通知提醒】考核通知\n【补充提醒】请核对{{得分}}\n{expected[message.receive_id]}"
    assert state.get_template(task)["text_tpl"] == BODY


def test_note_over_limit_rejected_before_push(ctx):
    state, _, notifier, _, client, _, existing, _ = ctx
    task = make_manual_form(client, existing["role_ids"][0])
    url = f"/api/admin/templates/{task}/push-now"
    for note, message in [("  " + "催" * 201 + " ", "附加提醒不能超过 200 字"), (123, "附加提醒必须为字符串")]:
        response = client.post(url, json={"push_note": note})
        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert response.json()["message"] == message
    assert client.post(url, content="{").status_code == 400
    assert not notifier.sent_messages
    assert not state.list_instances_for_period(task, "once")
    assert client.post(url, json={"push_note": "  " + "催" * 200 + " "}).json()["queued"]


def test_manual_upload_note_does_not_leak_into_scheduled_ticks(ctx):
    state, scheduler, notifier, _, client, _, existing, _ = ctx
    state.update_template(existing["template_id"], {"enabled": False})
    task = make_periodic_form(client, existing["role_ids"][0], remind_at="10:00")
    response = client.post(f"/api/admin/templates/{task}/push-now", json={"push_note": "本次催办"})
    assert response.status_code == 200 and response.json()["queued"]
    for _ in range(3):
        scheduler.run_once()
    texts = [message.text.split("#t=")[0] for message in notifier.sent_messages]
    assert len(texts) == 2
    assert texts[0] == texts[1].replace("\n", "\n【补充提醒】本次催办\n", 1)
