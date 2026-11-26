from datetime import timedelta
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import format_dt, parse_dt
from app.portal import PortalService, run_portal_tasks
from app.state import NO_DEADLINE_SENTINEL
from test_20261117_manual_mark_submitted import NOW, ctx
from test_20261123_portal_notice_and_local_tree import MEMBER_B, leader, notice_payload


TASK_EXPIRED = "当前任务已过期，无法提交。"
REJECT_EXPIRED = "已过重交截止时间，无法提交。"
LINK_EXPIRED = "链接已过期，请返回聊天框向德阳量密助手发送任意消息获取最新工作台入口。"
NOTICE_EXPIRED = "链接已过期，请返回聊天框给德阳量密助手发送任意消息进入个人工作台继续查看确认。"
ATTACHMENT_EXPIRED = "附件链接已过期或不存在。"
NOTICE_API = "/api/h5/notice-receipt"
PORTAL_API = "/api/h5/portal"


def at(state, scheduler, when):
    state._now_fn = scheduler._now_fn = lambda: when


def auth(raw):
    return {"Authorization": "Bearer " + raw}


def task_link(scheduler, instance):
    return scheduler.build_h5_submit_url(
        phone=instance["phone"], template_id=instance["template_id"],
        period_key_val=instance["period_key"], instance_id=instance["id"],
    )[1]


def task_info(client, raw):
    return client.post("/api/h5/task/info", json={"token": raw})


def submit(client, raw, **payload):
    return client.post("/api/h5/task/submit", json={"token": raw, **payload})


def change_deadline(state, template, hhmm):
    return state.update_template(template["template_id"], {
        "has_deadline": bool(hhmm), "deadline_type": "same_day" if hhmm else None,
        "deadline_time": hhmm, "deadline": hhmm or "",
    })


def stored_expiry(state, raw):
    token = state.get_h5_token(raw)
    return state.conn.execute("SELECT expires_at FROM h5_token WHERE token_id=?", (token["token_id"],)).fetchone()["expires_at"]


def test_template_deadline_recalculates_active_tokens_both_ways(ctx):
    state, scheduler, _, _, client, _, template, instance = ctx
    raw = task_link(scheduler, instance)
    consumed = task_link(scheduler, instance)
    state.consume_h5_token(state.get_h5_token(consumed)["token_id"])
    assert state.get_h5_token(raw)["expires_at"] == "2026-11-18 18:00:00"
    change_deadline(state, template, "15:00")
    assert state.get_h5_token(raw)["expires_at"] == "2026-11-18 15:00:00"
    change_deadline(state, template, "22:00")
    assert state.get_h5_token(raw)["expires_at"] == "2026-11-18 22:00:00"
    assert state.get_h5_token(consumed)["expires_at"] == "2026-11-18 18:00:00"

    now = NOW + timedelta(days=2)
    at(state, scheduler, now)
    change_deadline(state, template, "16:00")
    assert state.get_h5_token(raw)["expires_at"] == format_dt(now + timedelta(hours=1))
    response = task_info(client, raw)
    assert response.status_code == 200 and response.json()["window_reason"] == TASK_EXPIRED
    assert not response.json()["window_open"]
    assert submit(client, raw).json()["message"] == TASK_EXPIRED

    change_deadline(state, template, None)
    expires = format_dt(now + timedelta(days=30))
    assert state.get_h5_token(raw)["expires_at"] == expires
    assert stored_expiry(state, raw) == expires
    assert state.get_instance(instance["id"])["deadline_at"] == NO_DEADLINE_SENTINEL
    assert task_info(client, raw).json()["window_open"]
    at(state, scheduler, now + timedelta(hours=1))
    state.update_template(template["template_id"], {"name": "只改标题"})
    assert state.get_h5_token(raw)["expires_at"] == expires


@pytest.mark.parametrize("old_expiry", [NO_DEADLINE_SENTINEL, "2027-01-31 00:00:00"])
def test_legacy_long_upload_tokens_obey_cap_and_use_link_message(ctx, old_expiry):
    state, scheduler, _, _, client, _, template, instance = ctx
    change_deadline(state, template, None)
    raw = task_link(scheduler, instance)
    state.conn.execute("UPDATE h5_token SET expires_at=? WHERE token_id=?",
                       (old_expiry, state.get_h5_token(raw)["token_id"]))
    at(state, scheduler, NOW + timedelta(days=30))
    for response in [task_info(client, raw), submit(client, raw)]:
        assert response.status_code == 401 and response.json()["message"] == LINK_EXPIRED


@pytest.mark.parametrize("has_deadline", [True, False])
def test_reject_link_preserves_previous_submission_after_business_cutoff(ctx, has_deadline):
    state, scheduler, notifier, _, client, _, template, instance = ctx
    tid = template["template_id"]
    state.update_template(tid, {"need_review": True, "spec": {"upload_modules": ["fields", "file"]}})
    if not has_deadline:
        change_deadline(state, template, None)
    original_token = task_link(scheduler, instance)
    pdf = b"%PDF-1.4\n"
    uploaded = client.post("/api/h5/task/upload", data={"token": original_token},
                           files={"file": ("原始附件.pdf", pdf, "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    ref = uploaded.json()["ref"]
    result = submit(client, original_token, fields={"qty": "7", "summary": "上次提交"}, refs=[ref])
    assert result.status_code == 200 and result.json()["status"] == "submitted", result.text
    response = client.post(f'/api/admin/instances/{instance["id"]}/review',
                           json={"action": "reject", "reason": "请补充说明"})
    assert response.status_code == 200 and response.json()["instance"]["status"] == "rejected", response.text
    assert not notifier.sent_messages
    portal = PortalService(state)
    portal_token = portal.issue(instance["phone"], "zdxlz")
    opened = client.post(PORTAL_API + "/open", headers=auth(portal_token), json={"instance_id": instance["id"]})
    assert opened.status_code == 200, opened.text
    raw = opened.json()["url"].split("#t=")[-1]
    expires = NOW + (timedelta(days=2) if has_deadline else timedelta(days=30))
    assert state.get_h5_token(raw)["expires_at"] == format_dt(expires)
    if has_deadline:
        at(state, scheduler, NOW + timedelta(hours=25))
        state.advance_instance_statuses()
        assert state.get_instance(instance["id"])["status"] == "expired"
        info = task_info(client, raw)
        assert info.status_code == 200, info.text
        assert info.json()["window_reason"] == REJECT_EXPIRED and not info.json()["window_open"]
        assert info.json()["instance"]["reject_reason"] == "请补充说明"
        assert info.json()["instance"]["resubmit_deadline"] == format_dt(NOW + timedelta(days=1))
        assert info.json()["submission"]["fields"]["summary"] == "上次提交"
        attachment = client.post("/api/h5/task/attachment", headers=auth(raw), json={"ref": ref})
        assert attachment.status_code == 200 and attachment.content == pdf
        assert submit(client, raw).json()["message"] == REJECT_EXPIRED
    at(state, scheduler, expires)
    info = task_info(client, raw)
    assert info.status_code == 401 and info.json()["message"] == LINK_EXPIRED


@pytest.mark.parametrize("kind", ["normal", "variable", "leader"])
def test_notice_expires_after_thirty_days_but_portal_can_reissue(leader, kind):
    portal, identity, _, (state, scheduler, notifier, _, client, _, template, _) = leader
    receipt = {"enabled": True, "mode": "link", "button_label": "确认已读"}
    if kind == "leader":
        tid = portal.create_task(identity, notice_payload(notice_receipt=receipt))["task_id"]
        dispatch = lambda: run_portal_tasks(scheduler, state.now())
    else:
        spec = {"notice_receipt": receipt}
        if kind == "variable":
            spec["variable_notice"] = {"platform": "zdxlz", "rows": [
                {"row": 2, "cells": {"手机号": MEMBER_B, "姓名": "范围甲"}},
            ]}
        created = client.post("/api/admin/templates", json={
            "kind": "notice", "name": "回执期限", "is_manual": True, "period": "day",
            "role_ids": [] if kind == "variable" else template["role_ids"],
            "text_tpl": "{姓名}请查看通知", "spec": spec,
        })
        assert created.status_code == 200, created.text
        tid = created.json()["template_id"]
        dispatch = lambda: scheduler.push_now(tid)
    for _ in range(3):
        dispatch()
    [message] = [msg for msg in notifier.sent_messages
                 if msg.receive_id == MEMBER_B and "/h5/notice-receipt#t=" in msg.text]
    raw = message.text.split("#t=")[-1]
    portal_raw = portal.issue(MEMBER_B, "zdxlz")
    assert state.get_h5_token(raw)["expires_at"] == format_dt(NOW + timedelta(days=30))
    assert stored_expiry(state, raw) == format_dt(NOW + timedelta(days=30))
    at(state, scheduler, NOW + timedelta(days=30))
    state.advance_instance_statuses()
    expired = client.post(NOTICE_API + "/info", headers=auth(raw))
    assert expired.status_code == 401 and expired.json()["message"] == NOTICE_EXPIRED
    expired_portal = client.post(PORTAL_API + "/info", headers=auth(portal_raw), json={})
    assert expired_portal.status_code == 401 and expired_portal.json()["message"] == LINK_EXPIRED

    headers = auth(portal.issue(MEMBER_B, "zdxlz"))
    todos = client.post(PORTAL_API + "/info", headers=headers, json={}).json()["todos"]
    [item] = [item for item in todos if item["template_id"] == tid]
    opened = client.post(PORTAL_API + "/notice-open", headers=headers, json={"instance_id": item["id"]})
    assert opened.status_code == 200, opened.text
    fresh = opened.json()["url"].split("#t=")[-1]
    assert fresh != raw and state.get_h5_token(fresh)["expires_at"] == format_dt(NOW + timedelta(days=60))
    confirm = client.post(NOTICE_API + "/confirm", headers=auth(fresh))
    assert confirm.status_code == 200, confirm.text
    replay = client.post(NOTICE_API + "/info", headers=auth(fresh)).json()
    assert replay["body"] and replay["confirmed_at"] == confirm.json()["receipt_at"]
    assert not replay["confirmation_open"]


def test_adhoc_portal_history_and_attachment_token_lifetimes(ctx):
    state, scheduler, _, _, client, _, template, instance = ctx
    phone = instance["phone"]
    portal = PortalService(state)
    adhoc_template = state.create_template({
        "name": "自主上报期限", "kind": "form", "period": "day", "is_adhoc": True, "text_tpl": "自主填写",
        "role_ids": template["role_ids"], "schema_json": template["schema_json"],
    }, owner_id=template["owner_id"])
    adhoc = state.issue_adhoc_report(adhoc_template["template_id"], phone, "zdxlz")
    portal_raw = portal.issue(phone, "zdxlz")
    assert state.get_h5_token(adhoc)["expires_at"] == format_dt(NOW + timedelta(days=30))
    assert state.get_h5_token(portal_raw)["expires_at"] == format_dt(NOW + timedelta(days=7))
    asset_query = urlsplit(scheduler._notice_asset_url(instance, "fake")).query
    asset = parse_qs(asset_query)["token"][0]
    assert state.get_h5_token(asset)["expires_at"] == format_dt(NOW + timedelta(days=7))

    state.update_template(template["template_id"], {"spec": {"upload_modules": ["fields"]}})
    raw = task_link(scheduler, instance)
    assert submit(client, raw, fields={"qty": "2", "summary": "初次提交"}).status_code == 200
    at(state, scheduler, NOW + timedelta(hours=9))  # 已过 18:00，已提交内容可读、可从工作台更正。
    assert task_info(client, raw).json()["submission"]["fields"]["summary"] == "初次提交"
    history_url = portal.submission_history_link({"phone": phone, "platform": "zdxlz"}, instance["id"], edit=True)["url"]
    history = parse_qs(urlsplit(history_url).fragment)["t"][0]
    assert task_info(client, history).json()["can_edit"]
    assert state.get_h5_token(history)["expires_at"] == format_dt(NOW + timedelta(hours=10))
    at(state, scheduler, NOW + timedelta(hours=10))
    assert task_info(client, history).json()["message"] == LINK_EXPIRED
    at(state, scheduler, NOW + timedelta(days=7))
    response = client.get("/h5/notice-media", params={"token": asset})
    assert response.status_code == 401 and response.text == ATTACHMENT_EXPIRED


@pytest.mark.parametrize("deadline_days", [None, 45, 1])
def test_leader_upload_uses_shared_expiry_and_readonly_window(leader, deadline_days):
    portal, identity, _, (state, scheduler, notifier, _, client, _, _, _) = leader
    deadline = NOW + timedelta(days=deadline_days) if deadline_days is not None else None
    tid = portal.create_task(identity, {
        "name": "领导上传有效期", "description": "填写现场情况", "request_id": "leader-upload-ttl",
        "phones": [MEMBER_B], "mode": "once", "deadline_at": format_dt(deadline) if deadline else None,
    })["task_id"]
    for _ in range(3):
        run_portal_tasks(scheduler, state.now())
    [message] = [msg for msg in notifier.sent_messages
                 if msg.receive_id == MEMBER_B and "领导上传有效期" in msg.text]
    raw = message.text.split("#t=")[-1]
    [instance] = state.list_instances_for_period(tid, "once")
    portal_link = portal.task_link({"phone": MEMBER_B, "platform": "zdxlz"}, instance["id"])["url"].split("#t=")[-1]
    expires = NOW + timedelta(days=2 if deadline_days == 1 else 30)
    for token in (raw, portal_link):
        assert stored_expiry(state, token) == format_dt(expires)
    if deadline_days == 1:
        at(state, scheduler, deadline + timedelta(hours=1))
        info = task_info(client, raw)
        assert info.status_code == 200 and not info.json()["window_open"]
        assert info.json()["window_reason"] == TASK_EXPIRED
    at(state, scheduler, expires)
    assert task_info(client, raw).json()["message"] == LINK_EXPIRED


def test_h5_expiry_text_and_static_contract():
    h5 = Path(__file__).resolve().parents[1] / "app" / "h5"
    for name in ["task.html", "notice-receipt.html", "portal.html", "portal-create.html"]:
        page = (h5 / name).read_text(encoding="utf-8")
        assert "<title>IMTask</title>" in page
        assert "crypto.randomUUID()" not in page and "cdn." not in page
    assert LINK_EXPIRED in (h5 / "portal.html").read_text(encoding="utf-8")
    task = (h5 / "task.html").read_text(encoding="utf-8")
    assert "closedResubmission" in task and "renderSavedSubmission(data.submission" in task
    receipt = (h5 / "notice-receipt.html").read_text(encoding="utf-8")
    assert "NOTICE_TOKEN_EXPIRED" in receipt and "'已确认 · ' + data.confirmed_at" in receipt


def test_legacy_image_fallback_view_and_raw_expire_after_seven_days(ctx):
    state, scheduler, _, _, client, *_ = ctx
    source = state.data_dir / "通知图.png"
    source.write_bytes(b"notice image")
    url = urlsplit(scheduler._publish_image_link(str(source))).path
    copy = state.data_dir / "public_img" / url.rsplit("/", 1)[-1]
    published_at = parse_dt(format_dt(NOW)).timestamp()
    os.utime(copy, (published_at, published_at))
    at(state, scheduler, NOW + timedelta(days=7) - timedelta(minutes=1))
    assert client.get(url).status_code == 200
    raw = client.get(url, params={"raw": 1})
    assert raw.status_code == 200 and raw.headers["cache-control"] == "public, max-age=60"
    at(state, scheduler, NOW + timedelta(days=7))
    for path in (url, url + "?raw=1"):
        response = client.get(path)
        assert response.status_code == 404 and response.text == ATTACHMENT_EXPIRED
