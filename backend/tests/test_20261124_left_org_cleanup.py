import json
from io import BytesIO

from openpyxl import load_workbook
from PIL import Image

from app.portal import PortalService
from test_20261117_directory_and_upload_cleanup import Directory, role, seed
from test_20261117_manual_mark_submitted import PHONE, PERIOD, ctx


SYNC = "/api/admin/directory/sync"
CLEANUP = "/api/admin/directory/cleanup-left"
SOLO = "13900009402"
ACTIVE = "13900009403"


def test_sync_reports_deduplicated_left_members_without_removing_them(ctx):
    store, _, _, _, owner, sub, template, instance = ctx
    owner_id = template["owner_id"]
    first = template["role_ids"][0]
    second = role(store, "第二角色", owner_id=owner_id)
    seed(store, second, PHONE, "测试成员", owner_id=owner_id)
    other_id, _ = sub("left_sync_other")
    foreign = role(store, "其他管理员角色", owner_id=other_id)
    seed(store, foreign, PHONE, "测试成员", owner_id=other_id)
    store.contacts = Directory({PHONE: ("ok", None)})

    response = owner.post(SYNC, json={"platform": "zdxlz"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True, "total": 1, "updated": 0, "left": 1, "failed": 0,
        "left_members": [{
            "phone": PHONE, "name": "测试成员", "dept_path": "总部/旧部门",
            "role_ids": [first, second], "role_names": ["代交组", "第二角色"],
        }],
    }
    assert all(store.list_role_members(rid)["items"][0]["is_left_org"] for rid in (first, second))
    assert not store.list_role_members(foreign)["items"][0]["is_left_org"]
    assert store.get_delivery_target(PHONE, "zdxlz")
    assert store.get_instance(instance["id"])["status"] == "pending"


def _report(client, template_id, period=PERIOD):
    params = {"template_id": template_id, "period_key": period}
    overview = client.get("/api/admin/stats-panel/overview", params=params)
    detail = client.get("/api/admin/stats-panel/instances", params=params)
    export = client.get("/api/admin/stats-panel/export", params=params)
    assert all(r.status_code == 200 for r in (overview, detail, export))
    with BytesIO(export.content) as stream:
        workbook = load_workbook(stream, read_only=True)
        try:
            excel_phones = {str(row[2]) for row in workbook.active.iter_rows(min_row=2, values_only=True)}
        finally:
            workbook.close()
    return overview.json()["kpis"], {row["phone"] for row in detail.json()["items"]}, excel_phones


def _historical_submission(store, template, status, period):
    template_id = template["template_id"]
    instance = store.ensure_instance(template_id, PHONE, "测试成员", "旧部门", period, PERIOD + " 18:00:00")
    attachments = {}
    for extension, mime in (("png", "image/png"), ("txt", "text/plain")):
        ref = f"left_history_{status}_{extension}"
        relative = f"{period}/{template_id}/{ref}.{extension}"
        path = store.data_dir / "uploads" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if extension == "png":
            Image.new("RGB", (3, 3), "white").save(path)
        else:
            path.write_text("原始提交附件", encoding="utf-8")
        store.create_upload_ref(ref=ref, phone=PHONE, template_id=template_id, file_name=path.name,
                                mime=mime, size=path.stat().st_size, rel_path=relative)
        attachments[ref] = path
    token = store.issue_h5_token(PHONE, template_id, period, instance["id"])
    store.submit_h5_task(raw_token=token, fields={"qty": 8, "summary": "离职前提交"},
                         note="历史说明", refs=list(attachments))
    assert store.get_instance(instance["id"])["status"] == status
    return instance, attachments


def test_cleanup_removes_only_current_scope_pending_work_and_preserves_history(ctx):
    store, _, _, _, owner, sub, template, pending = ctx
    owner_id, template_id = template["owner_id"], template["template_id"]
    first = template["role_ids"][0]
    second = role(store, "第二组", owner_id=owner_id)
    foreign_owner, _ = sub("left_cleanup_other")
    foreign = role(store, "其他管理员组", owner_id=foreign_owner)
    wecom = role(store, "企微组", platform="wecom", owner_id=owner_id)
    for rid, phone, scope_owner, platform in (
        (second, PHONE, owner_id, "zdxlz"), (first, SOLO, owner_id, "zdxlz"),
        (first, ACTIVE, owner_id, "zdxlz"), (foreign, PHONE, foreign_owner, "zdxlz"),
        (wecom, PHONE, owner_id, "wecom"),
    ):
        seed(store, rid, phone, "测试成员", platform=platform, owner_id=scope_owner)
    portal = PortalService(store)
    for scope_owner, rid, platform in ((owner_id, first, "zdxlz"), (foreign_owner, foreign, "zdxlz"),
                                      (owner_id, wecom, "wecom")):
        portal.save_grant(platform, scope_owner, PHONE, {"enabled": True, "role_ids": [rid]}, "tester")
    retained_template = store.create_template({
        "name": "其他管理员任务", "kind": "form", "role_ids": [foreign], "period": "day",
        "remind_at": "10:00", "deadline": "18:00", "text_tpl": "请提交",
    }, owner_id=foreign_owner)
    retained = store.ensure_instance(retained_template["template_id"], PHONE, "测试成员", None,
                                     PERIOD, PERIOD + " 18:00:00")

    approved = _historical_submission(store, template, "approved", "2026-11-15")
    store.update_template(template_id, {"need_review": True})
    submitted = _historical_submission(store, template, "submitted", "2026-11-16")
    solo = store.ensure_instance(template_id, SOLO, "离职成员", "旧部门", PERIOD, PERIOD + " 18:00:00")
    store.ensure_instance(template_id, ACTIVE, "在职成员", "旧部门", PERIOD, PERIOD + " 18:00:00")
    assert _report(owner, template_id)[1:] == ({PHONE, SOLO, ACTIVE}, {PHONE, SOLO, ACTIVE})

    store.contacts = Directory({PHONE: ("ok", None), SOLO: ("ok", None), ACTIVE: ("error", None)})
    assert owner.post(SYNC, json={"platform": "zdxlz"}).status_code == 200
    response = owner.post(CLEANUP, json={"platform": "zdxlz", "phones": [PHONE, SOLO, PHONE, ACTIVE]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["removed"] == [PHONE, SOLO] and result["skipped"] == [ACTIVE]
    assert result["removed_memberships"] == 3 and result["cancelled_instances"] == 2
    assert result["revoked_grants"] == 1
    assert [row["phone"] for row in store.list_role_members(first)["items"]] == [ACTIVE]
    assert store.list_role_members(second)["total"] == 0
    assert all(store.list_role_members(rid)["items"][0]["phone"] == PHONE for rid in (foreign, wecom))
    assert store.get_delivery_target(SOLO, "zdxlz") is None
    assert all(store.get_delivery_target(PHONE, p) for p in ("zdxlz", "wecom"))
    assert all(store.get_instance(row["id"])["status"] == "cancelled" for row in (pending, solo))
    assert store.get_instance(retained["id"])["status"] == "pending"

    kpis, roster, excel = _report(owner, template_id)
    assert kpis["total"] == 1 and roster == excel == {ACTIVE}
    for (instance, attachments), status in zip((approved, submitted), ("approved", "submitted")):
        assert store.get_instance(instance["id"])["status"] == status
        [saved] = store.list_submissions_for_instance(instance["id"])
        assert store.submission_refs(saved) == list(attachments)
        assert all(path.is_file() and store.get_upload_ref(ref)["consumed"] for ref, path in attachments.items())
        assert _report(owner, template_id, instance["period_key"])[1:] == ({PHONE}, {PHONE})

    grants = store.conn.execute("SELECT owner_id, platform, enabled FROM portal_grant WHERE phone=?", (PHONE,)).fetchall()
    assert {(row["owner_id"], row["platform"]): row["enabled"] for row in grants} == {
        (owner_id, "zdxlz"): 0, (foreign_owner, "zdxlz"): 1, (owner_id, "wecom"): 1,
    }
    audit = store.conn.execute("SELECT username, detail FROM admin_audit WHERE action=?",
                               ("member.left_org_cleanup",)).fetchone()
    assert audit["username"] == "manual_owner" and json.loads(audit["detail"])["phones"] == [PHONE, SOLO]
    assert owner.post(CLEANUP, json={"platform": "zdxlz", "phones": [PHONE]}).json()["removed"] == []
    assert {row["phone"] for row in store.template_deliverable_members(template)} == {ACTIVE}


def test_cleanup_cancels_unacknowledged_notices_and_keeps_receipts(ctx):
    store, _, _, _, owner, _, template, _ = ctx
    owner_id, rid = template["owner_id"], template["role_ids"][0]
    seed(store, rid, SOLO, "回执成员", owner_id=owner_id)
    notice = store.create_template({
        "name": "带回执通知", "kind": "notice", "role_ids": [rid], "period": "day",
        "notice_times": ["10:00"], "text_tpl": "请知悉", "spec": {"notice_receipt": {"enabled": True, "mode": "text"}},
    }, owner_id=owner_id)
    by_phone = {row["phone"]: row for row in store.ensure_template_instances(
        notice["template_id"], PERIOD, PERIOD + " 18:00:00")}
    store.update_instance_status(by_phone[PHONE]["id"], "sent")
    store.conn.execute("UPDATE task_instance SET receipt_at=?, receipt_text=? WHERE id=?",
                       (store.now_str(), "已知悉", by_phone[SOLO]["id"]))
    store.contacts = Directory({PHONE: ("ok", None), SOLO: ("ok", None)})
    assert owner.post(SYNC, json={"platform": "zdxlz"}).status_code == 200
    response = owner.post(CLEANUP, json={"platform": "zdxlz", "phones": [PHONE, SOLO]})
    assert response.status_code == 200, response.text
    assert store.get_instance(by_phone[PHONE]["id"])["status"] == "cancelled"
    acknowledged = store.get_instance(by_phone[SOLO]["id"])
    assert acknowledged["status"] == "pending" and acknowledged["receipt_text"] == "已知悉"
    assert _report(owner, notice["template_id"])[1:] == ({SOLO}, {SOLO})


def test_cleanup_denies_super_admin_and_uses_session_owner(ctx):
    store, _, _, admin, owner, sub, template, _ = ctx
    _, other = sub("left_cleanup_forbidden")
    rid = template["role_ids"][0]
    store.contacts = Directory({PHONE: ("ok", None)})
    assert owner.post(SYNC, json={"platform": "zdxlz"}).status_code == 200
    request = {"platform": "zdxlz", "phones": [PHONE], "owner_id": template["owner_id"]}
    response = admin.post(CLEANUP, json=request)
    assert response.status_code == 403 and response.json()["code"] == "SUPER_ADMIN_READONLY"
    response = other.post(CLEANUP, json=request)
    assert response.status_code == 200 and response.json()["skipped"] == [PHONE]
    assert store.list_role_members(rid)["items"][0]["is_left_org"]
