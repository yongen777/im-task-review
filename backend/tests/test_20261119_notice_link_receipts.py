from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook
from PIL import Image

from app.api.events import handle_inbound_text
from app.config import format_dt
from app.portal import PortalService
from app.state import NO_DEADLINE_SENTINEL
from test_20261117_manual_mark_submitted import NOW, ctx
from test_20261119_notice_media import movie, upload


API = "/api/h5/notice-receipt"
SECOND = "13900009402"


def auth(token):
    return {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("manual", [True, False])
def test_link_notice_media_auth_and_receipt_lifecycle(ctx, movie, manual):
    state, scheduler, notifier, _, client, _, existing, first = ctx
    state.update_template(existing["template_id"], {"enabled": False})
    state.add_role_member(existing["role_ids"][0], SECOND, "第二成员", owner_id=existing["owner_id"])
    state.upsert_delivery_target(SECOND, "second-union", external_type="union_id",
                                 activated_at="2026-11-16 08:00:00")
    photo = BytesIO()
    Image.new("RGB", (32, 16), "#244c6a").save(photo, format="PNG")
    attachments = [
        ("video", "巡检视频.mp4", movie.read_bytes(), "video/mp4"),
        ("file", "交接明细.txt", "检查记录".encode(), "text/plain"),
        ("image", "巡检照片.png", photo.getvalue(), "image/png"),
    ]
    assets = [upload(client, *item) for item in attachments]
    spec = {"notice_receipt": {"enabled": True, "mode": "link", "button_label": "确认已读"},
            "notice_items": [{"format": asset["kind"], "asset_id": asset["asset_id"]} for asset in assets]}
    created = client.post("/api/admin/templates", json={
        "name": "机柜巡检通知", "kind": "notice", "period": "day", "is_manual": manual,
        "role_ids": existing["role_ids"], "notice_times": [] if manual else ["10:00"],
        "text_tpl": "请{姓名}查阅通知。\n\n" + "原始正文。" * 35, "spec": spec,
    })
    assert created.status_code == 200, created.text
    template_id = created.json()["template_id"]
    period = "once" if manual else NOW.date().isoformat()
    instances = state.ensure_template_instances(template_id, period, format_dt(NOW + timedelta(hours=8)))
    instance = next(row for row in instances if row["phone"] == first["phone"])
    early = state.issue_h5_token(first["phone"], template_id, period, instance["id"], purpose="notice_receipt")
    assert client.post(API + "/info", headers=auth(early)).status_code == 404

    for tick in range(3):
        if manual:
            scheduler.push_now(template_id)
        else:
            scheduler.run_once(NOW + timedelta(seconds=tick * 30))
    # 2026-11-19 需求四（link 模式完整投递）：每人 = 1 正文 + 3 媒体（FakeNotifier 无原生
    # 附件能力 → 走 H5 链接兜底，每附件 1 条文本）+ 1 条确认链接 = 5 条，两人共 10 条；
    # 重复 tick 命中幂等键，不会追加（所以 3 轮后仍是 10）。
    assert len(notifier.sent_messages) == 10
    links = {message.receive_id: message for message in notifier.sent_messages}
    assert links["second-union"].receive_type == "union_id"
    tokens = {receiver: message.text.splitlines()[-1].partition("#t=")[2] for receiver, message in links.items()}
    assert len(set(tokens.values())) == 2 and all(tokens.values())
    token = tokens["test_recipient"]
    credentials = auth(token)
    assert state.get_h5_token(token)["expires_at"] == NO_DEADLINE_SENTINEL
    assert "点击「确认已读」" in links["test_recipient"].text and "…" in links["test_recipient"].text
    assert client.get("/h5/notice-receipt").status_code == 200
    assert client.post("/api/h5/task/info", json={"token": token}).status_code == 401
    assert client.post("/api/h5/portal/info", json={}, headers=credentials).status_code == 401
    upload_token = state.issue_h5_token(first["phone"], existing["template_id"], first["period_key"], first["id"])
    assert client.post(API + "/info", headers=auth(upload_token)).status_code == 401
    forged = state.issue_h5_token(SECOND, template_id, period, instance["id"], purpose="notice_receipt")
    assert client.post(API + "/info", headers=auth(forged)).status_code == 404

    info = client.post(API + "/info", headers=credentials)
    assert info.status_code == 200, info.text
    detail = info.json()
    assert detail["title"] == "机柜巡检通知" and detail["button_label"] == "确认已读"
    assert detail["body"].startswith("请测试成员查阅通知。") and detail["body"].endswith("原始正文。" * 35)
    assert detail["published_at"] and detail["delivered_at"] and detail["confirmed_at"] is None
    assert [item["kind"] for item in detail["media"]] == ["image", "file", "video"]
    assert all(item["name"] and item["size"] for item in detail["media"])
    for item, original in zip(detail["media"], [attachments[2], attachments[1], attachments[0]]):
        params = {"token": token, "id": item["id"]}
        response = client.get(API + "/media", params=params)
        assert response.content == original[2] and response.status_code == 200
        assert response.headers["referrer-policy"] == "no-referrer"
        downloaded = client.get(API + "/media", params={**params, "download": 1})
        assert downloaded.headers["content-disposition"].startswith("attachment;") and downloaded.content == original[2]
        if item["kind"] == "video":
            part = client.get(API + "/media", params=params, headers={"Range": "bytes=0-15"})
            assert part.status_code == 206 and part.content == original[2][:16]
    assert client.get(API + "/media", params={"token": token, "id": 99}).status_code == 404

    portal = PortalService(state)
    portal_auth = auth(portal.issue(first["phone"], "zdxlz"))
    second_auth = auth(portal.issue(SECOND, "zdxlz"))
    open_data = {"instance_id": instance["id"]}
    assert client.post("/api/h5/portal/notice-open", json=open_data, headers=second_auth).status_code == 404
    assert client.post("/api/h5/portal/open", json=open_data, headers=portal_auth).status_code == 403
    opened = client.post("/api/h5/portal/notice-open", json=open_data, headers=portal_auth)
    assert opened.status_code == 200 and opened.json()["url"].startswith("/h5/notice-receipt#t=")
    todo = client.post("/api/h5/portal/info", json={}, headers=portal_auth).json()["todos"]
    assert any(row["id"] == instance["id"] and row["receipt_mode"] == "link" for row in todo)

    state._now_fn = lambda: NOW + timedelta(days=400)
    state.advance_instance_statuses(state.now())
    reply = []
    handle_inbound_text(state=state, user_id="test_recipient", msg_text="确认已读", platform="zdxlz",
                        event_id="link-is-not-chat-receipt", base_url="http://test",
                        send=lambda body, tag: reply.append((body, tag)))
    assert reply[0][1] == "portal" and "/h5/portal" in reply[0][0]
    state.update_template(template_id, {"enabled": False})
    assert not client.post(API + "/info", headers=credentials).json()["confirmation_open"]
    assert client.post(API + "/confirm", headers=credentials).status_code == 403
    state.update_template(template_id, {"enabled": True, "name": "后来改名", "text_tpl": "后来改文",
                                        "spec": {"notice_receipt": {"enabled": True, "mode": "text"}}})

    def submit():
        return client.post(API + "/confirm", headers=credentials)

    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmations = [future.result() for future in (pool.submit(submit), pool.submit(submit))]
    assert [result.status_code for result in confirmations] == [200, 200]
    at = confirmations[0].json()["receipt_at"]
    assert at == confirmations[1].json()["receipt_at"]
    state._now_fn = lambda: NOW + timedelta(days=401)
    assert submit().json()["receipt_at"] == at
    replay = client.post(API + "/info", headers=credentials).json()
    assert replay["confirmed_at"] == at and replay["title"] == detail["title"] and replay["body"] == detail["body"]
    assert replay["media"] == detail["media"]
    saved = state.get_instance(instance["id"])
    assert saved["receipt_text"] == "确认已读" and saved["receipt_mode"] == "link" and at > saved["deadline_at"]
    portal_auth = auth(portal.issue(first["phone"], "zdxlz"))
    assert all(row["id"] != instance["id"] for row in client.post(
        "/api/h5/portal/info", json={}, headers=portal_auth).json()["todos"])
    assert client.post("/api/h5/portal/history", json={}, headers=portal_auth).json()["items"] == []

    state._now_fn = lambda: NOW
    params = {"template_id": template_id, "period_key": period}
    overview = client.get("/api/admin/stats-panel/overview", params=params).json()
    assert overview["kpis"]["done"] == 1 and overview["kpis"]["not_done"] == 1 and overview["kpis"]["rate"] == 50
    roster = client.get("/api/admin/stats-panel/instances", params=params).json()["items"]
    assert {row["receipt_mode"] for row in roster} == {"link"}
    assert any(row["receipt_at"] == at and row["receipt_text"] == "确认已读" for row in roster)
    exported = client.get("/api/admin/stats-panel/export", params=params)
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    rows = list(workbook.active.values)
    assert "回执方式" in rows[0] and any("H5 链接确认" in row and "确认已读" in row for row in rows[1:])
    state.delete_template(template_id)
    assert client.post(API + "/info", headers=credentials).status_code == 404
    assert state.conn.execute("SELECT 1 FROM notice_receipt_view WHERE instance_id=?", (instance["id"],)).fetchone() is None


def test_link_default_button_and_legacy_text_default(ctx):
    state, scheduler, _, _, client, _, existing, member = ctx
    for config, expected in [({"enabled": True}, "link"),
                             ({"enabled": True, "phrase": "旧版知晓"}, "text")]:
        created = client.post("/api/admin/templates", json={
            "name": f"回执配置 {expected}", "kind": "notice", "period": "day", "is_manual": True,
            "role_ids": existing["role_ids"], "text_tpl": "通知",
            "spec": {"notice_receipt": config},
        })
        assert created.status_code == 200, created.text
        template_id = created.json()["template_id"]
        scheduler.push_now(template_id)
        instance = state.get_instance_by_key(template_id, member["phone"], "once")
        assert instance["receipt_mode"] == expected
        assert instance["receipt_phrase"] == ("我已知晓" if expected == "link" else "旧版知晓")
        result = client.get("/api/admin/stats-panel/export", params={"template_id": template_id, "period_key": "once"})
        rows = list(load_workbook(BytesIO(result.content), read_only=True).active.values)
        assert rows[1][-1] == ("H5 链接确认" if expected == "link" else "文本回复确认")
