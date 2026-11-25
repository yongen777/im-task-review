from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from openpyxl.utils.units import pixels_to_EMU, points_to_pixels
from PIL import Image

from app.api.admin import create_admin_router, register_admin_guard
from app.api.h5 import create_h5_router
from app.scheduler import FakeNotifier, Scheduler
from app.state import StateStore
from admin_test_helpers import post_verified_admin
from mysql_testbank import mysql_test_cfg


NOW = datetime(2026, 11, 17, 10, 0)
PERIOD = "2026-11-17"
PHONE = "13900009401"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)


@pytest.fixture
def ctx(tmp_path):
    store = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp_path, now_fn=lambda: NOW)
    notifier = FakeNotifier(now_fn=lambda: NOW)
    scheduler = Scheduler(store, public_base_url="http://test", notifier=notifier, now_fn=lambda: NOW)
    app = FastAPI()
    app.state.scheduler = scheduler
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=notifier, public_base_url="http://test"))
    app.include_router(create_h5_router(state=store, uploads_dir=tmp_path / "uploads", notifier=notifier))

    def login(username, password):
        client = TestClient(app)
        response = client.post("/api/admin/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    def sub(username):
        response = post_verified_admin(admin, {"username": username, "display_name": username})
        assert response.status_code == 200, response.text
        first_password = StateStore.DEFAULT_SUB_ADMIN_PASSWORD
        client = login(username, first_password)
        changed = client.post("/api/admin/change-password", json={
            "old_password": first_password, "new_password": "Sub@2026Ready",
        })
        assert changed.status_code == 200, changed.text
        return response.json()["account"]["id"], login(username, "Sub@2026Ready")

    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        admin = login("admin", "Admin@2026")
        owner_id, owner = sub("manual_owner")
        rid = store.create_role("代交组", owner_id=owner_id)["role_id"]
        store.add_role_member(rid, PHONE, "测试成员", owner_id=owner_id)
        store.upsert_delivery_target(PHONE, "test_recipient", activated_at="2026-11-16 08:00:00")
        tpl = store.create_template({
            "name": "代交任务", "kind": "form", "role_ids": [rid], "period": "day",
            "remind_at": "10:00", "deadline": "18:00", "text_tpl": "请提交",
            "need_review": False, "allow_note": True,
            "schema_json": [
                {"key": "qty", "label": "数量", "type": "number", "required": True},
                {"key": "summary", "label": "说明", "type": "text", "required": True},
            ],
            "spec": {"upload_modules": ["fields", "text", "image", "file"]},
        }, owner_id=owner_id)
        inst = store.ensure_instance(tpl["template_id"], PHONE, "测试成员", None, PERIOD, PERIOD + " 18:00:00")
        yield store, scheduler, notifier, admin, owner, sub, tpl, inst
    finally:
        store.close()


def endpoint(instance):
    return f"/api/admin/instances/{instance['id']}/mark-submitted"


@pytest.mark.parametrize("status", ["pending", "overdue", "rejected"])
def test_mark_with_no_content_stores_submission_and_revokes_all_instance_tokens(ctx, status):
    store, scheduler, notifier, _, owner, _, tpl, inst = ctx
    store.conn.execute(
        "UPDATE task_instance SET status = ?, reject_reason = '历史驳回原因', reviewer = 'old_reviewer' WHERE id = ?",
        (status, inst["id"]),
    )
    tokens = [store.issue_h5_token(PHONE, tpl["template_id"], PERIOD, inst["id"]) for _ in range(2)]
    another = store.ensure_instance(tpl["template_id"], PHONE, "测试成员", None,
                                    "2026-11-16", "2026-11-16 18:00:00")
    other_token = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-16", another["id"])

    response = owner.post(endpoint(inst))
    assert response.status_code == 200, response.text
    item = store.get_instance(inst["id"])
    assert item["status"] == response.json()["status"] == "approved"
    assert item["submitted_at"] == "2026-11-17 10:00:00"
    assert not item["reject_reason"] and not item["reviewer"]
    [saved] = store.list_submissions_for_instance(inst["id"])
    assert saved["source"] == "admin" and saved["status"] == "approved"
    assert not saved["note"] and not saved["refs_json"]
    assert all(store.get_h5_token(token)["consumed"] for token in tokens)
    assert not store.get_h5_token(other_token)["consumed"]
    info = owner.post("/api/h5/task/info", json={"token": tokens[0]})
    assert info.status_code == 200 and not info.json()["window_open"]
    upload = owner.post("/api/h5/task/upload", data={"token": tokens[0]},
                        files={"file": ("new.png", PNG, "image/png")})
    assert upload.status_code == 400 and upload.json()["code"] == "ALREADY_SUBMITTED"
    duplicate = owner.post("/api/h5/task/submit", json={"token": tokens[0]})
    assert duplicate.status_code == 400 and duplicate.json()["code"] == "ALREADY_SUBMITTED"
    assert owner.post(endpoint(inst), json={}).status_code == 400
    audit = store.conn.execute(
        "SELECT username, target_id FROM admin_audit WHERE action = 'instance.manual_mark_submitted'"
    ).fetchall()
    assert [(row["username"], row["target_id"]) for row in audit] == [("manual_owner", str(inst["id"]))]
    for second in (0, 30, 59):
        scheduler.run_once(NOW.replace(second=second))
    assert not notifier.sent_messages
    assert not store.list_reminder_logs(instance_id=inst["id"])


@pytest.mark.parametrize("status", ["submitted", "approved", "cancelled", "expired", "sent"])
def test_mark_rejects_other_states_without_side_effects(ctx, status):
    store, _, notifier, _, owner, _, tpl, inst = ctx
    token = store.issue_h5_token(PHONE, tpl["template_id"], PERIOD, inst["id"])
    store.update_instance_status(inst["id"], status)
    response = owner.post(endpoint(inst), json={"note": "拒绝修改"})
    assert response.status_code == 400, response.text
    assert store.get_instance(inst["id"])["status"] == status
    assert not store.list_submissions_for_instance(inst["id"])
    assert not store.get_h5_token(token)["consumed"]
    assert not notifier.sent_messages


def test_marks_require_owner_sub_admin_and_preserve_no_side_effects(ctx):
    store, _, notifier, admin, _, sub, _, inst = ctx
    _, outsider = sub("manual_other")
    for client in (admin, outsider):
        response = client.post(endpoint(inst), files={"attachments": ("image.png", PNG, "image/png")})
        assert response.status_code == 403, response.text
    assert store.get_instance(inst["id"])["status"] == "pending"
    assert not store.list_submissions_for_instance(inst["id"])
    assert not notifier.sent_messages


def test_reviewed_task_waits_and_legacy_no_review_submission_filters_as_completed(ctx):
    store, _, _, _, owner, _, tpl, inst = ctx
    store.update_template(tpl["template_id"], {"need_review": True})
    response = owner.post(endpoint(inst))
    assert response.status_code == 200 and response.json()["status"] == "submitted"
    assert store.get_instance(inst["id"])["status"] == "submitted"
    assert store.list_submissions_for_instance(inst["id"])[0]["status"] == "submitted"
    filters = {"template_id": tpl["template_id"], "period_key": PERIOD}
    assert owner.get("/api/admin/instances", params={**filters, "status": "submitted"}).json()["total"] == 1

    # 模拟存量免审代交，页面按当前模板口径筛选与统计。
    store.update_template(tpl["template_id"], {"need_review": False})
    assert owner.get("/api/admin/instances", params={**filters, "status": "submitted"}).json()["total"] == 0
    assert owner.get("/api/admin/instances", params={**filters, "status": "approved,sent"}).json()["total"] == 1
    stats = owner.get("/api/admin/stats", params=filters).json()["stats"]
    assert (stats["submitted"], stats["approved"]) == (0, 1)


def test_mark_optional_fields_and_attachments_show_in_detail_stats_and_excel(ctx):
    store, _, notifier, _, owner, _, tpl, inst = ctx
    spec = {**tpl["spec_json"], "allow_multiple_images": True}
    store.update_template(tpl["template_id"], {"spec": spec})
    pictures = []
    for index in range(10):
        picture = BytesIO()
        Image.new("RGB", (180, 140), color=(20 * index, 40, 180)).save(picture, format="PNG")
        pictures.append(("attachments", (f"现场{index + 1}.png", picture.getvalue(), "image/png")))
    assert owner.get(f"/api/admin/instances/{inst['id']}").json()["instance"]["attachment_limits"] == {"image": 10, "file": 1}
    response = owner.post(endpoint(inst), data={"payload": '{"fields":{"qty":0},"note":"线下统一代交"}'}, files=[
        *pictures,
        ("attachments", ("清单.pdf", b"%PDF-1.7\nexample", "application/pdf")),
    ])
    assert response.status_code == 200, response.text
    detail = owner.get(f"/api/admin/instances/{inst['id']}").json()
    assert detail["instance"]["status"] == "approved"
    assert detail["submissions"][0]["source"] == "admin"
    saved = detail["submission"]
    assert saved["source"] == "admin"
    assert saved["form_values"] == {"qty": 0}
    assert saved["note"] == "线下统一代交"
    assert [a["mime"] for a in saved["attachments"]] == ["image/png"] * 10 + ["application/pdf"]
    for attachment in saved["attachments"]:
        assert store.get_upload_ref(attachment["ref"])["consumed"]
        assert owner.get(attachment["url"]).status_code == 200
    params = {"template_id": tpl["template_id"], "period_key": PERIOD}
    stats = owner.get("/api/admin/stats-panel/overview", params=params)
    assert stats.status_code == 200, stats.text
    assert (stats.json()["kpis"]["done"], stats.json()["kpis"]["not_done"]) == (1, 0)
    pending_review = owner.get("/api/admin/instances/pending-reviews").json()
    assert not pending_review["submitted"]
    exported = owner.get("/api/admin/stats-panel/export", params=params)
    assert exported.status_code == 200, exported.text
    rows = list(load_workbook(BytesIO(exported.content), read_only=True).active.values)
    assert any("已完成" in row and PHONE in row for row in rows)
    assert [header for header in rows[0] if "图片" in header] == ["图片"]
    assert rows[0][-2:] == ("图片", "文件是否上传") and rows[1][-1] == "已上传"
    sheet = load_workbook(BytesIO(exported.content)).active
    anchors = [image.anchor for image in sheet._images]
    assert len(anchors) == 10
    assert {(anchor._from.row, anchor._from.col) for anchor in anchors} == {(1, rows[0].index("图片"))}
    assert len({(anchor._from.rowOff, anchor._from.colOff) for anchor in anchors}) == 10
    height = pixels_to_EMU(points_to_pixels(sheet.row_dimensions[2].height))
    assert all(anchor._from.rowOff + anchor.ext.cy <= height for anchor in anchors)
    for index, a in enumerate(anchors):
        for b in anchors[index + 1:]:
            assert (a._from.colOff + a.ext.cx <= b._from.colOff or b._from.colOff + b.ext.cx <= a._from.colOff
                    or a._from.rowOff + a.ext.cy <= b._from.rowOff or b._from.rowOff + b.ext.cy <= a._from.rowOff)
    assert not notifier.sent_messages


def test_bad_attachment_or_invalid_field_does_not_write_anything(ctx):
    store, _, _, _, owner, _, tpl, inst = ctx
    token = store.issue_h5_token(PHONE, tpl["template_id"], PERIOD, inst["id"])
    assert owner.post(endpoint(inst), json={"fields": {"qty": "非数字"}}).status_code == 400
    assert owner.post(endpoint(inst), files=[
        ("attachments", ("valid.png", PNG, "image/png")),
        ("attachments", ("bad.png", b"not an image", "image/png")),
    ]).status_code == 400
    assert store.get_instance(inst["id"])["status"] == "pending"
    assert not store.get_h5_token(token)["consumed"]
    assert not store.list_submissions_for_instance(inst["id"])
    assert not list((store.data_dir / "uploads").rglob("*.png"))


def test_parallel_marks_create_only_one_submission_and_audit(ctx):
    store, _, notifier, _, owner, _, _, inst = ctx
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: owner.post(endpoint(inst), json={}).status_code, range(3)))
    assert sorted(results) == [200, 400, 400]
    assert len(store.list_submissions_for_instance(inst["id"])) == 1
    count = store.conn.execute(
        "SELECT COUNT(*) FROM admin_audit WHERE action = 'instance.manual_mark_submitted'"
    ).fetchone()[0]
    assert count == 1 and not notifier.sent_messages


def test_failure_after_file_save_rolls_back_state_tokens_and_disk(ctx, monkeypatch):
    store, _, notifier, _, owner, _, tpl, inst = ctx
    token = store.issue_h5_token(PHONE, tpl["template_id"], PERIOD, inst["id"])
    original = store._insert_audit_unlocked

    def audit(username, action, *args):
        if action == "instance.manual_mark_submitted":
            raise RuntimeError("模拟审计写入失败")
        return original(username, action, *args)

    monkeypatch.setattr(store, "_insert_audit_unlocked", audit)
    with pytest.raises(RuntimeError, match="模拟审计写入失败"):
        owner.post(endpoint(inst), files={"attachments": ("rollback.png", PNG, "image/png")})
    assert store.get_instance(inst["id"])["status"] == "pending"
    assert not store.get_h5_token(token)["consumed"]
    assert not store.list_submissions_for_instance(inst["id"])
    assert store.conn.execute("SELECT COUNT(*) FROM upload_ref").fetchone()[0] == 0
    assert not any(file.is_file() for file in (store.data_dir / "uploads").rglob("*"))
    assert not notifier.sent_messages
