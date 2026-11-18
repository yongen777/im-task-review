from datetime import datetime, timedelta
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.h5 import create_h5_router, process_h5_task_upload
from app.config import format_dt
from app.scheduler import FakeNotifier, Scheduler
from app.state import StateStore
from mysql_testbank import mysql_test_cfg


PHONE = "13900009634"
TODAY = datetime(2026, 11, 18, 9)


@pytest.fixture
def seeded(tmp_path):
    now = [TODAY]
    store = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp_path, now_fn=lambda: now[0])
    role = store.create_role("巡检组")["role_id"]
    store.add_role_member(role, PHONE, "演示成员")
    store.upsert_delivery_target(PHONE, "fake-member")
    template = store.create_template({
        "name": "巡检记录", "kind": "form", "role_ids": [role], "period": "day",
        "text_tpl": "请填报", "remind_at": "10:00", "deadline": "18:00", "allow_note": True,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number"}],
        "spec": {"upload_modules": ["fields", "text"]},
    })
    notifier = FakeNotifier(now_fn=store.now)
    scheduler = Scheduler(store, "http://test", notifier=notifier, now_fn=store.now)
    try:
        yield store, role, template["template_id"], now, notifier, scheduler
    finally:
        store.close()


def instance(store, template, period="2026-11-18"):
    return store.ensure_instance(template, PHONE, "演示成员", "巡检组", period, "2026-11-18 18:00:00")


def submit(store, row):
    token = store.issue_h5_token(PHONE, row["template_id"], row["period_key"], row["id"])
    store.submit_h5_task(raw_token=token, fields={"qty": 2}, note="历史提交")
    return store.list_submissions_for_instance(row["id"])


@pytest.mark.parametrize("operation", ["template_disable", "member_disable", "member_remove", "template_unbind"])
def test_scope_change_only_cancels_unfinished(seeded, operation):
    store, role, template, now, notifier, scheduler = seeded
    pending = instance(store, template)
    complete = instance(store, template, "2026-11-17")
    history = submit(store, complete)
    assert history
    rejected = instance(store, template, "2026-11-16")
    store.conn.execute("UPDATE task_instance SET status='rejected' WHERE id=?", (rejected["id"],))
    if operation == "template_disable":
        store.update_template(template, {"enabled": False})
    elif operation == "member_disable":
        store.update_role_member(role, PHONE, enabled=False)
    elif operation == "member_remove":
        store.delete_role_member(role, PHONE)
    else:
        store.update_template(template, {"role_ids": []})

    assert store.get_instance(pending["id"])["status"] == "cancelled"
    assert store.get_instance(complete["id"])["status"] == "approved"
    assert store.list_submissions_for_instance(complete["id"]) == history
    status = "cancelled" if operation in ("member_remove", "template_unbind") else "rejected"
    assert store.get_instance(rejected["id"])["status"] == status
    for seconds in (0, 30, 60):
        now[0] = TODAY + timedelta(hours=1, seconds=seconds)
        scheduler.run_once()
    assert not notifier.sent_messages


def test_later_notice_slot_skips_a_disabled_member_even_when_first_slot_was_sent(seeded):
    store, role, _, now, notifier, scheduler = seeded
    template = store.create_template({
        "name": "两次设备通知", "kind": "notice", "role_ids": [role], "period": "day",
        "text_tpl": "请确认", "notice_times": ["09:00", "09:10"],
    })["template_id"]
    # Ensure the binding predates the trigger.
    store.conn.execute("UPDATE delivery_target SET activated_at=? WHERE phone=?",
                       (format_dt(TODAY - timedelta(days=1)), PHONE))
    scheduler.run_once()
    assert len(notifier.sent_messages) == 1
    store.update_role_member(role, PHONE, enabled=False)
    for seconds in (0, 30, 60):
        now[0] = TODAY + timedelta(minutes=10, seconds=seconds)
        scheduler.run_once()
    assert len(notifier.sent_messages) == 1
    assert store.get_instance_by_key(template, PHONE, "2026-11-18")["status"] == "sent"


def test_another_enabled_role_keeps_task_active(seeded):
    store, role, template, *_ = seeded
    another = store.create_role("补充巡检组")["role_id"]
    store.add_role_member(another, PHONE, "演示成员")
    store.update_template(template, {"role_ids": [role, another]})
    row = instance(store, template)
    store.update_role_member(role, PHONE, enabled=False)
    assert store.get_instance(row["id"])["status"] == "pending"
    store.delete_role_member(another, PHONE)
    assert store.get_instance(row["id"])["status"] == "cancelled"


def test_completed_h5_displays_fields_note_and_scoped_attachments(seeded):
    store, _, template, *_ = seeded
    store.update_template(template, {"spec": {"upload_modules": ["fields", "text", "image", "file"]}})
    root = store.data_dir / "uploads"
    app = FastAPI()
    app.include_router(create_h5_router(store, root))
    image = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
    files = {"巡检.png": image.getvalue(), "交接.pdf": b"%PDF-1.4\n"}

    def complete(period):
        row = instance(store, template, period)
        token = store.issue_h5_token(PHONE, template, period, row["id"])
        refs = []
        for name, content in files.items():
            status, upload = process_h5_task_upload(state=store, uploads_dir=root, raw_token=token,
                                                    filename=name, content_bytes=content)
            assert status == 200, upload
            refs.append(upload["ref"])
        store.submit_h5_task(raw_token=token, fields={"qty": 2}, note="交接完成", refs=refs)
        return token, refs

    first_token, first_refs = complete("2026-11-17")
    second_token, second_refs = complete("2026-11-18")
    with TestClient(app) as client:
        info = client.post("/api/h5/task/info", json={"token": first_token}).json()
        assert info["window_open"] is False
        assert info["window_reason"] is None
        assert info["submission"]["fields"] == {"qty": 2}
        assert info["submission"]["note"] == "交接完成"
        assert {a["file_name"] for a in info["submission"]["attachments"]} == files.keys()
        assert all("rel_path" not in a for a in info["submission"]["attachments"])
        for ref, content in zip(first_refs, files.values()):
            response = client.post("/api/h5/task/attachment", json={"ref": ref}, headers={"X-H5-Token": first_token})
            assert response.status_code == 200 and response.content == content
            assert response.headers["Cache-Control"] == "no-store"
        assert client.post("/api/h5/task/attachment", json={"ref": first_refs[0]},
                           headers={"X-H5-Token": second_token}).status_code == 404
        assert client.post("/api/h5/task/attachment", json={"ref": second_refs[0]}).status_code == 401
