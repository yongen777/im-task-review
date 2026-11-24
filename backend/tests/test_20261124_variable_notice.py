from datetime import timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from openpyxl import load_workbook
from PIL import Image

from app.api.events import handle_inbound_text
from app.scheduler import FakeNotifier
from test_20261117_manual_mark_submitted import NOW, ctx
from test_20261119_notice_media import movie, upload


TABLE = "/api/admin/variable-notices"
TEMPLATES = "/api/admin/templates"
RECEIPT = "/api/h5/notice-receipt"
FIRST = "13900009401"
SECOND = "13900009402"
BODY = "{姓名}您好，得分 {得分}，再次核对 {得分}，金额 {金额} 元"
HEADER = "手机号,姓名,得分,金额\n"
VALID = HEADER + f"{FIRST},测试成员,95,1.25\n{SECOND},第二成员,88,0\n"


def preflight(client, csv=VALID, body=BODY):
    return client.post(TABLE + "/validate", data={"body": body, "platform": "zdxlz"},
                       files={"file": ("通知.csv", csv.encode("utf-8-sig"), "text/csv")})


def payload(config, mode="link", **extra_spec):
    return {
        "name": "考核通知", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": [], "text_tpl": BODY,
        "spec": {
            "variable_notice": config,
            "notice_receipt": {"enabled": mode != "none", "mode": mode if mode != "none" else "link",
                               "button_label": "确认考核", "phrase": "确认考核"},
            **extra_spec,
        },
    }


def add_second(ctx):
    state, _, _, _, _, _, existing, _ = ctx
    state.add_role_member(existing["role_ids"][0], SECOND, "第二成员", owner_id=existing["owner_id"])
    state.upsert_delivery_target(SECOND, "second-union", external_type="union_id")


def test_download_csv_and_xlsx_headers_and_round_trip(ctx):
    _, _, _, _, client, *_ = ctx
    csv = client.post(TABLE + "/template", json={"body": BODY, "format": "csv"})
    assert csv.status_code == 200
    assert csv.content.decode("utf-8-sig").splitlines() == ["手机号,姓名,得分,金额"]
    xlsx = client.post(TABLE + "/template", json={"body": BODY, "format": "xlsx"})
    assert xlsx.status_code == 200
    book = load_workbook(BytesIO(xlsx.content))
    assert list(book.active.values) == [("手机号", "姓名", "得分", "金额")]
    book.active.append([int(FIRST), "测试成员", 0, 1.25])
    stream = BytesIO()
    book.save(stream)
    result = client.post(TABLE + "/validate", data={"body": BODY, "platform": "zdxlz"},
                         files={"file": ("考核.xlsx", stream.getvalue())})
    assert result.status_code == 200, result.text
    assert "得分 0" in result.json()["samples"][0]["body"]
    only_name = client.post(TABLE + "/template", json={"body": "{姓名}", "format": "csv"})
    assert only_name.content.decode("utf-8-sig").splitlines() == ["手机号,姓名"]


@pytest.mark.parametrize(("table", "row", "reason"), [
    ("姓名,得分,金额\n甲,1,2\n", 1, "手机号"),
    ("手机号,得分,金额\n13900009401,1,2\n", 1, "姓名"),
    ("手机号,姓名,得分\n13900009401,甲,1\n", 1, "金额"),
    (HEADER, 2, "没有有效数据行"),
    (HEADER + f"{FIRST},测试成员,,0\n", 2, "得分"),
    (HEADER + f"{FIRST},,95,0\n", 2, "姓名不能为空"),
    (HEADER + f"{FIRST},张冠李戴,95,0\n", 2, "姓名与名册不一致"),
    (HEADER + f"{SECOND},第二成员,95,0\n", 2, "未入册"),
    (HEADER + f"{FIRST},测试成员,95,0\n{FIRST},测试成员,98,0\n", 3, "第 2 行"),
    (HEADER + ",测试成员,95,0\n", 2, "手机号不能为空"),
    (HEADER + "123,测试成员,95,0\n", 2, "手机号格式"),
])
def test_invalid_table_is_read_only(ctx, table, row, reason):
    state, _, notifier, _, client, *_ = ctx
    audit_before = state.conn.execute("SELECT COUNT(*) AS n FROM admin_audit").fetchone()["n"]
    result = preflight(client, table)
    assert result.status_code == 400, result.text
    assert any(issue["row"] == row and reason in issue["reason"] for issue in result.json()["issues"])
    assert state.conn.execute("SELECT COUNT(*) AS n FROM admin_audit").fetchone()["n"] == audit_before
    assert not notifier.sent_messages


def test_collect_all_row_errors_and_reject_forged_save(ctx):
    state, _, notifier, _, client, *_ = ctx
    result = preflight(client, HEADER + f"{FIRST},,95,0\n\n{SECOND},第二成员,88,\n")
    assert result.status_code == 400
    assert [issue["row"] for issue in result.json()["issues"]] == [2, 4, 4]
    assert result.json()["issues"][-1]["phone"] == SECOND
    config = {"platform": "zdxlz", "rows": [
        {"row": 2, "cells": {"手机号": FIRST, "姓名": "测试成员", "得分": "95", "金额": ""}},
    ]}
    saved = client.post(TEMPLATES, json=payload(config))
    assert saved.status_code == 400 and saved.json()["issues"][0]["row"] == 2
    scheduled = client.post(TEMPLATES, json={**payload(config), "is_manual": False, "notice_times": ["08:15"]})
    assert scheduled.status_code == 400
    assert state.conn.execute("SELECT COUNT(*) AS n FROM task_template").fetchone()["n"] == 1
    assert not notifier.sent_messages


@pytest.mark.parametrize(("state_change", "reason"), [
    ("disabled", "已停用"), ("left", "已离职"), ("unbound", "未激活或不可投递"),
])
def test_push_revalidates_every_member_before_writing(ctx, state_change, reason):
    state, _, notifier, _, client, _, existing, _ = ctx
    add_second(ctx)
    config = preflight(client).json()["config"]
    saved = client.post(TEMPLATES, json=payload(config))
    assert saved.status_code == 200, saved.text
    template_id = saved.json()["template_id"]
    if state_change == "disabled":
        state.update_role_member(existing["role_ids"][0], SECOND, enabled=False)
    elif state_change == "left":
        state.conn.execute("UPDATE member SET is_left_org=1 WHERE phone=?", (SECOND,))
    else:
        state.mark_delivery_target_unusable(SECOND, platform="zdxlz")
    pushed = client.post(f"{TEMPLATES}/{template_id}/push-now")
    assert pushed.status_code == 400, pushed.text
    assert pushed.json()["issues"] == [{"row": 3, "phone": SECOND, "reason": reason}]
    assert not state.list_instances_for_period(template_id, "once")
    assert not state.list_reminder_logs(template_id=template_id)
    assert not notifier.sent_messages


@pytest.mark.parametrize("mode", ["link", "text", "none"])
def test_personal_body_snapshot_delivery_and_receipts(ctx, mode, request):
    state, scheduler, notifier, admin, client, sub, original, _ = ctx
    state.update_template(original["template_id"], {"enabled": False})
    add_second(ctx)
    other_owner, other = sub("variable_other")
    result = preflight(client)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["count"] == 2
    bodies = [item["body"] for item in data["samples"]]
    assert bodies == ["测试成员您好，得分 95，再次核对 95，金额 1.25 元",
                      "第二成员您好，得分 88，再次核对 88，金额 0 元"]
    asset = upload(client, "file", "通用说明.txt", b"common", "text/plain")
    assets = [asset]
    if mode == "link":
        png = BytesIO()
        Image.new("RGB", (32, 16), "#244c6a").save(png, format="PNG")
        video = request.getfixturevalue("movie")
        assets.extend([
            upload(client, "video", "通用视频.mp4", video.read_bytes(), "video/mp4"),
            upload(client, "image", "通用图片.png", png.getvalue(), "image/png"),
        ])
    draft = payload(data["config"], mode, notice_items=[
        {"format": asset["kind"], "asset_id": asset["asset_id"]} for asset in assets
    ])
    if mode == "link":
        draft.update(push_on_create=True, request_id="0" * 32)
    created = client.post(TEMPLATES, json=draft)
    assert created.status_code == 200, created.text
    template_id = created.json()["template_id"]
    if mode == "link":
        assert created.json()["push"]["instance_count"] == 2
        replay = client.post(TEMPLATES, json=draft)
        assert replay.json()["replayed"] and replay.json()["template_id"] == template_id
    else:
        pushed = client.post(f"{TEMPLATES}/{template_id}/push-now")
        assert pushed.status_code == 200 and pushed.json()["instance_count"] == 2
    for _ in range(3):
        scheduler.run_once()
        scheduler.push_now(template_id)
    instances = state.list_instances_for_period(template_id, "once")
    for instance, receive_id, body in zip(instances, ["test_recipient", "second-union"], bodies):
        messages = [msg.text for msg in notifier.sent_messages if msg.receive_id == receive_id]
        assert len(messages) == 1 + len(assets) + (mode != "none")
        assert messages[0] == body
        if mode == "link":
            assert "通用图片.png" in messages[1] and "通用视频.mp4" in messages[3]
        file_msg = messages[2 if mode == "link" else 1]
        assert "通用说明.txt" in file_msg
        media_url = file_msg.splitlines()[-1]
        media_token = parse_qs(urlsplit(media_url).query)["token"][0]
        assert client.get("/h5/notice-media", params={"token": media_token, "raw": 1}).content == b"common"
        detail_path = f"/api/admin/instances/{instance['id']}"
        assert other.get(detail_path).status_code == 403
        for reader in [client, admin]:
            detail = reader.get(detail_path).json()
            view = detail["notice_view"]
            assert view["body"] == body and view["source"] == "snapshot"
            assert "rows" not in detail["template"]["spec_json"]["variable_notice"]
            assert [item["kind"] for item in view["media"]] == (["image", "file", "video"] if mode == "link" else ["file"])
        if mode == "link":
            token = messages[-1].split("#t=")[1]
            auth = {"Authorization": "Bearer " + token}
            received = client.post(RECEIPT + "/info", headers=auth)
            assert received.status_code == 200 and received.json()["body"] == body
            assert [item["kind"] for item in received.json()["media"]] == ["image", "file", "video"]
            wrong_phone = SECOND if instance["phone"] == FIRST else FIRST
            forged = state.issue_h5_token(wrong_phone, template_id, "once", instance["id"], purpose="notice_receipt")
            assert client.post(RECEIPT + "/info", headers={"Authorization": "Bearer " + forged}).status_code == 404
            if instance["phone"] == FIRST:
                assert client.post(RECEIPT + "/confirm", headers=auth).status_code == 200
        elif mode == "text" and instance["phone"] == FIRST:
            handle_inbound_text(state=state, user_id=receive_id, msg_text="确认考核", platform="zdxlz",
                                event_id="variable-text-receipt", base_url="http://test", send=lambda *_: None)

    if mode != "none":
        params = {"template_id": template_id, "period_key": "once"}
        path = "/api/admin/stats-panel/"
        for reader in [client, admin]:
            overview = reader.get(path + "overview", params=params)
            assert overview.status_code == 200, overview.text
            assert {k: overview.json()["kpis"][k] for k in
                    ("expected_replies", "replied", "pending_replies", "reply_rate")} == {
                        "expected_replies": 2, "replied": 1, "pending_replies": 1, "reply_rate": 50.0,
                    }
            export = reader.get(path + "export", params=params)
            assert export.status_code == 200
            assert "测试成员" in [cell for row in load_workbook(BytesIO(export.content)).active.values for cell in row]
        assert other.get(path + "overview", params=params).status_code == 403
        for reader, extra in [(client, {}), (admin, {"variable_only": 1})]:
            catalog = reader.get(path + "tasks", params={"platform": "zdxlz", **extra})
            assert template_id in [task["template_id"] for task in catalog.json()["items"]]
            catalog = reader.get(path + "tasks", params={"platform": "wecom", **extra})
            assert not [task for task in catalog.json()["items"] if task["template_id"] == template_id]
    assert admin.post(f"{TEMPLATES}/{template_id}/push-now").status_code == 403
    assert other_owner != instances[0]["owner_id"]


def test_table_uses_only_owners_roster_and_current_platform(ctx):
    state, _, _, _, client, sub, original, _ = ctx
    other_id, other = sub("foreign_owner")
    owner_id = original["owner_id"]
    foreign_role = state.create_role("另一管理员名册", owner_id=other_id)["role_id"]
    state.add_role_member(foreign_role, SECOND, "第二成员", owner_id=other_id)
    wecom_role = state.create_role("企微同一成员名册", owner_id=owner_id, platform="wecom")["role_id"]
    state.add_role_member(wecom_role, SECOND, "第二成员", owner_id=owner_id)
    for platform in ["zdxlz", "wecom"]:
        state.upsert_delivery_target(SECOND, f"test-{platform}", platform=platform)

    second_only = HEADER + f"{SECOND},第二成员,95,0\n"
    assert preflight(other, second_only).status_code == 200
    result = preflight(client, second_only)
    assert result.status_code == 400
    assert result.json()["issues"] == [{"row": 2, "phone": SECOND, "reason": "未入册"}]


def test_reimport_and_repush_keep_original_instance_snapshot(ctx):
    state, scheduler, notifier, _, client, *_ = ctx
    initial = HEADER + f"{FIRST},测试成员,95,24\n"
    config = preflight(client, initial).json()["config"]
    draft = payload(config)
    saved = client.post(TEMPLATES, json=draft).json()
    template_id = saved["template_id"]
    endpoint = f"{TEMPLATES}/{template_id}/push-now"
    assert client.post(endpoint).status_code == 200
    original = state.list_instances_for_period(template_id, "once")[0]
    original_text = notifier.sent_messages[0].text
    original_token = notifier.sent_messages[-1].text.split("#t=")[1]
    add_second(ctx)
    draft = payload(preflight(client, VALID.replace(",95,1.25", ",56,23")).json()["config"])
    assert client.put(f"{TEMPLATES}/{template_id}", json=draft).status_code == 200
    state._now_fn = scheduler._now_fn = lambda: NOW + timedelta(seconds=5)
    assert client.post(endpoint).json()["target_count"] == 2
    repush = client.post(endpoint + "?confirmed=1")
    assert repush.status_code == 200, repush.text
    assert repush.json()["period_key"].startswith("once#")
    detail = client.get(f"/api/admin/instances/{original['id']}").json()["notice_view"]
    assert detail["body"] == original_text
    auth = {"Authorization": "Bearer " + original_token}
    assert client.post(RECEIPT + "/info", headers=auth).json()["body"] == original_text
    assert any("得分 56" in message.text and message.receive_id == "test_recipient" for message in notifier.sent_messages)


def test_ambiguous_delivery_binding_is_rejected(ctx):
    state, _, notifier, _, client, *_ = ctx
    state.upsert_delivery_target(SECOND, "test_recipient")
    result = preflight(client, HEADER + f"{FIRST},测试成员,95,0\n")
    assert result.status_code == 400, result.text
    assert "投递账号绑定了多个手机号" in result.json()["issues"][0]["reason"]
    assert not notifier.sent_messages


def test_create_and_push_rolls_back_if_preparation_fails(ctx, monkeypatch):
    state, scheduler, notifier, _, client, *_ = ctx
    monkeypatch.setattr(scheduler, "_notice_snapshot_media", lambda *args, **kwargs: None)
    data = preflight(client, HEADER + f"{FIRST},测试成员,95,0\n").json()
    draft = {**payload(data["config"]), "push_on_create": True, "request_id": "a" * 32}
    audit_before = state.conn.execute("SELECT COUNT(*) AS n FROM admin_audit").fetchone()["n"]
    result = client.post(TEMPLATES, json=draft)
    assert result.status_code == 400 and result.json()["code"] == "NOTICE_MEDIA_UNAVAILABLE"
    assert state.conn.execute("SELECT COUNT(*) AS n FROM task_template").fetchone()["n"] == 1
    assert state.conn.execute("SELECT COUNT(*) AS n FROM task_instance").fetchone()["n"] == 1
    assert state.conn.execute("SELECT COUNT(*) AS n FROM admin_audit").fetchone()["n"] == audit_before
    assert not notifier.sent_messages


def test_wecom_variable_notice_does_not_send_to_the_other_platform(ctx):
    state, scheduler, notifier, _, client, _, original, _ = ctx
    owner_id = original["owner_id"]
    role = state.create_role("企微同一手机号", owner_id=owner_id, platform="wecom")["role_id"]
    state.add_role_member(role, FIRST, "测试成员", owner_id=owner_id)
    state.upsert_delivery_target(FIRST, "wecom-recipient", platform="wecom")
    wecom = FakeNotifier(now_fn=lambda: NOW)
    scheduler.notifiers = {"zdxlz": notifier, "wecom": wecom}
    csv = HEADER + f"{FIRST},测试成员,95,0\n"
    verified = client.post(TABLE + "/validate", data={"body": BODY, "platform": "wecom"},
                           files={"file": ("考核.csv", csv.encode("utf-8-sig"))})
    assert verified.status_code == 200, verified.text
    draft = payload(verified.json()["config"], "none")
    created = client.post(TEMPLATES, json=draft)
    assert created.status_code == 200, created.text
    template_id = created.json()["template_id"]
    pushed = client.post(f"{TEMPLATES}/{template_id}/push-now")
    assert pushed.status_code == 200, pushed.text
    [message] = wecom.sent_messages
    assert message.receive_id == "wecom-recipient" and "得分 95" in message.text
    assert not notifier.sent_messages
    for platform, count in [("zdxlz", 0), ("wecom", 1)]:
        response = client.get("/api/admin/instances", params={"platform": platform, "template_id": template_id})
        assert response.json()["total"] == count, response.text
