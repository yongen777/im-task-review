# -*- coding: utf-8 -*-
"""2026-11-23 任务实例与审核 · 执行状态细分联动 + 详情弹窗统一（A 级专项）。

覆盖：
  1. 细分状态精确查询：通知 6 项 / 上传 9 项（含 pending 细分「待推送 / 待上传」、
     已完成（管理员代交）、通知已送达 / 待回复 / 已回复 / 未送达），kind 组合筛选
     与「全部类型」下通用键（push_pending / cancelled）的跨类型语义；
  2. /stats 的 refined 计数与列表行完全同源（sum == total，逐键对上）；
  3. get_instance_detail：通知类返回该次下发的 notice_view 快照（正文 + 图片/文件
     附件 URL）与 receipt_at / refined_status（await_reply → confirm → replied）；
     无快照时回退模板当前正文与附件；上传类附带 latest_submission.source。
"""
from datetime import datetime
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router, register_admin_guard
from app.notice_receipts import confirm as confirm_receipt
from app.scheduler import FakeNotifier, Scheduler
from app.state import StateStore
from admin_test_helpers import post_verified_admin
from mysql_testbank import mysql_test_cfg

NOW = datetime(2026, 11, 23, 10, 0)
PERIOD = "2026-11-23"
DEADLINE = PERIOD + " 18:00:00"
PAST = PERIOD + " 08:00:00"          # 2 小时前 → advance 后 overdue
STALE_PERIOD = "2026-11-19"
STALE = STALE_PERIOD + " 08:00:00"   # 4 天前 → advance 后 expired（>72h 窗口）
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


@pytest.fixture
def ctx(tmp_path):
    store = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp_path, now_fn=lambda: NOW)
    notifier = FakeNotifier(now_fn=lambda: NOW)
    scheduler = Scheduler(store, public_base_url="http://test", notifier=notifier, now_fn=lambda: NOW)
    app = FastAPI()
    app.state.scheduler = scheduler
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=notifier, public_base_url="http://test"))
    client = TestClient(app)

    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    admin = TestClient(app)
    response = admin.post("/api/admin/login", json={"username": "admin", "password": "Admin@2026"})
    assert response.status_code == 200, response.text
    admin.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    created = post_verified_admin(admin, {"username": "refined_owner"})
    assert created.status_code == 200, created.text
    owner_id = created.json()["account"]["id"]

    def owner_login(password):
        client = TestClient(app)
        login = client.post("/api/admin/login", json={"username": "refined_owner", "password": password})
        assert login.status_code == 200, login.text
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        return client

    first = owner_login(StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
    changed = first.post("/api/admin/change-password", json={
        "old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "Sub@2026Ready"})
    assert changed.status_code == 200, changed.text
    owner = owner_login("Sub@2026Ready")

    try:
        yield store, scheduler, notifier, owner, owner_id
    finally:
        store.close()


def make_form(store, owner_id, rid, name, *, review=False):
    return store.create_template({
        "name": name, "kind": "form", "period": "day", "remind_at": "10:00",
        "deadline": "18:00", "role_ids": [rid], "need_review": review,
        "text_tpl": f"请提交{name}", "allow_note": True, "schema_json": [],
        "spec": {"upload_modules": ["text"]},
    }, owner_id=owner_id)["template_id"]


def submit_as_user(store, phone, tid, inst, note="成员提交"):
    raw = store.issue_h5_token(phone, tid, inst["period_key"], inst["id"])
    store.submit_h5_task(raw_token=raw, note=note, refs=[])
    return store.get_instance(inst["id"])


@pytest.fixture
def dataset(ctx):
    """造齐 13 种细分状态各至少 1 行（cancelled 2 行），并回填预期实例清单。"""
    store, scheduler, notifier, owner, owner_id = ctx
    rid = store.create_role("细分状态组", owner_id=owner_id)["role_id"]
    member_a, member_b, member_c = "13900009501", "13900009502", "13900009503"
    # 甲/乙在角色里（推送类实例按人生成）；丙**不在角色**——只留一条「已取消」行，
    # 与真实口径一致（cancelled = 成员被移出分组后历史实例作废）。
    for phone, name in ((member_a, "成员甲"), (member_b, "成员乙")):
        store.add_role_member(rid, phone, name, owner_id=owner_id)
        store.upsert_delivery_target(phone, f"union-{phone}", external_type="union_id",
                                     activated_at="2026-11-16 08:00:00")
    expected: dict = {}

    # ── 上传任务 ─────────────────────────────────────────────────────
    tid_free = make_form(store, owner_id, rid, "免审日报", review=False)
    expected["done"] = submit_as_user(store, member_a, tid_free,
                                      store.ensure_instance(tid_free, member_a, "成员甲", None, PERIOD, DEADLINE))
    manual_row = store.ensure_instance(tid_free, member_b, "成员乙", None, PERIOD, DEADLINE)
    assert owner.post(f"/api/admin/instances/{manual_row['id']}/mark-submitted", json={}).status_code == 200
    expected["done_admin"] = store.get_instance(manual_row["id"])

    tid_review = make_form(store, owner_id, rid, "待审周报", review=True)
    expected["reviewing"] = submit_as_user(store, member_a, tid_review,
                                           store.ensure_instance(tid_review, member_a, "成员甲", None, PERIOD, DEADLINE))

    tid_reject = make_form(store, owner_id, rid, "驳回报表", review=True)
    reject_row = submit_as_user(store, member_a, tid_reject,
                                store.ensure_instance(tid_reject, member_a, "成员甲", None, PERIOD, DEADLINE))
    store.review_instance(reject_row["id"], "reject", "refined_owner", "数据不完整，请补充后重交。")
    expected["rejected"] = store.get_instance(reject_row["id"])

    tid_push = make_form(store, owner_id, rid, "推送任务", review=False)
    pushed = store.ensure_instance(tid_push, member_a, "成员甲", None, PERIOD, DEADLINE)
    store.record_reminder_log(template_id=tid_push, instance_id=pushed["id"], phone=member_a,
                              kind="remind", planned_at=PAST, sent_at=PAST, result="sent",
                              error=None, idem_key=f"test|remind|{pushed['id']}")
    expected["upload_pending"] = store.get_instance(pushed["id"])
    expected["push_pending"] = store.ensure_instance(tid_push, member_b, "成员乙", None, PERIOD, DEADLINE)
    cancelled_upload = store.ensure_instance(tid_push, member_c, "成员丙", None, "2026-11-20", DEADLINE)
    expected["cancelled_upload"] = store.get_instance(cancelled_upload["id"])

    tid_late = make_form(store, owner_id, rid, "逾期任务", review=False)
    expected["overdue"] = store.ensure_instance(tid_late, member_a, "成员甲", None, "2026-11-22", PAST)
    expected["expired"] = store.ensure_instance(tid_late, member_b, "成员乙", None, STALE_PERIOD, STALE)
    with store.lock, store.conn:
        store.conn.execute("UPDATE task_instance SET status='cancelled' WHERE id=?",
                           (expected["cancelled_upload"]["id"],))

    # ── 通知提醒 ─────────────────────────────────────────────────────
    from app.notice_media import store_asset
    image = store_asset(store, owner_id, "image", "巡检照片.png", BytesIO(PNG), len(PNG))
    pdf_bytes = b"%PDF-1.4 %simplified preview payload%"
    pdf = store_asset(store, owner_id, "file", "交接明细.pdf", BytesIO(pdf_bytes), len(pdf_bytes))

    tid_receipt = store.create_template({
        "name": "回执通知", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": [rid], "text_tpl": "请查阅机房巡检通知内容。",
        "spec": {"notice_receipt": {"enabled": True, "mode": "link", "button_label": "确认已读"},
                 "notice_items": [{"format": "image", "asset_id": image["asset_id"], "file_name": "巡检照片.png"},
                                  {"format": "file", "asset_id": pdf["asset_id"], "file_name": "交接明细.pdf"}]},
    }, owner_id=owner_id)["template_id"]
    scheduler.push_now(tid_receipt)
    rows = {r["phone"]: r for r in store.list_instances_for_period(tid_receipt, "once")}
    expected["await_reply"] = rows[member_a]
    confirm_receipt(store, rows[member_b]["id"])
    expected["replied"] = store.get_instance(rows[member_b]["id"])

    tid_plain = store.create_template({
        "name": "部门周知", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": [rid], "text_tpl": "本周例会调整至周四下午。",
        "spec": {"notice_items": [{"format": "image", "asset_id": image["asset_id"], "file_name": "巡检照片.png"}]},
    }, owner_id=owner_id)["template_id"]
    scheduler.push_now(tid_plain)
    plain_rows = store.list_instances_for_period(tid_plain, "once")
    expected["sent"] = plain_rows[0]

    tid_stale_notice = store.create_template({
        "name": "未送达通知", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": [rid], "text_tpl": "此通知从未送达。", "spec": {},
    }, owner_id=owner_id)["template_id"]
    expected["notice_expired"] = store.ensure_instance(tid_stale_notice, member_a, "成员甲", None, "once", PAST)
    notice_cancelled = store.ensure_instance(tid_stale_notice, member_b, "成员乙", None, "once", PAST)
    with store.lock, store.conn:
        store.conn.execute("UPDATE task_instance SET status='cancelled' WHERE id=?",
                           (notice_cancelled["id"],))
    expected["cancelled_notice"] = store.get_instance(notice_cancelled["id"])

    store.advance_instance_statuses(NOW)
    # advance 后重新读取（overdue / expired / notice_expired 的终态以库为准）
    for key in ("overdue", "expired", "notice_expired"):
        expected[key] = store.get_instance(expected[key]["id"])
    return expected, member_a, member_b, image, pdf


# ── 1. 细分状态精确查询（列表口径） ────────────────────────────────────
def _ids(owner, query):
    response = owner.get("/api/admin/instances?" + query)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def test_refined_notice_filters(ctx, dataset):
    expected, *_ = dataset
    owner = ctx[3]
    # 已送达：部门周知推送给甲/乙两名成员（无回执 → 各 1 行「已送达」）
    sent_ids = _ids(owner, "kind=notice&status=sent")
    assert len(sent_ids) == 2 and expected["sent"]["id"] in sent_ids
    assert _ids(owner, "kind=notice&status=await_reply") == {expected["await_reply"]["id"]}
    assert _ids(owner, "kind=notice&status=replied") == {expected["replied"]["id"]}
    assert _ids(owner, "kind=notice&status=notice_expired") == {expected["notice_expired"]["id"]}
    assert _ids(owner, "kind=notice&status=cancelled") == {expected["cancelled_notice"]["id"]}
    # 通知类没有已驳回（上传专属键在 notice 下查不出行）
    assert _ids(owner, "kind=notice&status=rejected") == set()


def test_refined_upload_filters(ctx, dataset):
    expected, member_a, member_b, image, pdf = dataset
    owner = ctx[3]
    assert _ids(owner, "kind=upload&status=push_pending") == {expected["push_pending"]["id"]}
    assert _ids(owner, "kind=upload&status=upload_pending") == {expected["upload_pending"]["id"]}
    assert _ids(owner, "kind=upload&status=reviewing") == {expected["reviewing"]["id"]}
    assert _ids(owner, "kind=upload&status=done") == {expected["done"]["id"]}
    assert _ids(owner, "kind=upload&status=done_admin") == {expected["done_admin"]["id"]}
    assert _ids(owner, "kind=upload&status=rejected") == {expected["rejected"]["id"]}
    assert _ids(owner, "kind=upload&status=overdue") == {expected["overdue"]["id"]}
    assert _ids(owner, "kind=upload&status=expired") == {expected["expired"]["id"]}
    assert _ids(owner, "kind=upload&status=cancelled") == {expected["cancelled_upload"]["id"]}
    # 组合筛选（KPI 卡片点击的形态）：已完成 + 管理员代交
    assert _ids(owner, "kind=upload&status=done,done_admin") == {
        expected["done"]["id"], expected["done_admin"]["id"]}


def test_refined_common_filter_semantics_without_kind(ctx, dataset):
    """「全部任务类型」下的通用键：待推送 / 已取消跨类型合并语义。"""
    expected, *_ = dataset
    owner = ctx[3]
    # push_pending 不带 kind：上传的「未送达待办」+ 通知的 pending（此处无通知 pending 行）
    assert _ids(owner, "status=push_pending") == {expected["push_pending"]["id"]}
    assert _ids(owner, "status=cancelled") == {
        expected["cancelled_upload"]["id"], expected["cancelled_notice"]["id"]}
    # 原生 DB 状态键向后兼容：raw pending 两条都在
    assert _ids(owner, "status=pending") == {
        expected["push_pending"]["id"], expected["upload_pending"]["id"]}


def test_refined_status_field_on_items(ctx, dataset):
    """列表行自带 refined_status（前端徽章直接消费，与筛选同源）。"""
    expected, *_ = dataset
    owner = ctx[3]
    rows = {item["id"]: item for item in
            owner.get("/api/admin/instances?size=100").json()["items"]}
    assert rows[expected["done_admin"]["id"]]["refined_status"] == "done_admin"
    assert rows[expected["await_reply"]["id"]]["refined_status"] == "await_reply"
    assert rows[expected["push_pending"]["id"]]["refined_status"] == "push_pending"
    assert rows[expected["upload_pending"]["id"]]["refined_status"] == "upload_pending"
    assert rows[expected["replied"]["id"]]["refined_status"] == "replied"
    assert rows[expected["cancelled_notice"]["id"]]["refined_status"] == "cancelled"
    assert rows[expected["notice_expired"]["id"]]["refined_status"] == "notice_expired"


# ── 2. /stats refined 计数与列表同源 ──────────────────────────────────
def test_stats_refined_counts(ctx, dataset):
    expected, *_ = dataset
    response = ctx[3].get("/api/admin/stats")
    assert response.status_code == 200, response.text
    stats = response.json()["stats"]
    refined = stats["refined"]
    assert refined["push_pending"] == 1
    assert refined["upload_pending"] == 1
    assert refined["reviewing"] == 1
    assert refined["done"] == 1
    assert refined["done_admin"] == 1
    assert refined["rejected"] == 1
    assert refined["overdue"] == 1
    assert refined["expired"] == 1
    assert refined["cancelled"] == 2
    assert refined["sent"] == 2
    assert refined["await_reply"] == 1
    assert refined["replied"] == 1
    assert refined["notice_expired"] == 1
    assert sum(refined.values()) == stats["total"] == 15
    # 带 kind 的 refined 计数同样精确（KPI 卡片随类型联动）
    notice_stats = ctx[3].get("/api/admin/stats?kind=notice").json()["stats"]
    assert notice_stats["refined"]["await_reply"] == 1 and notice_stats["refined"]["done"] == 0
    assert notice_stats["total"] == sum(notice_stats["refined"].values())


# ── 3. get_instance_detail：通知正文附件 + receipt_at + 代交来源 ────────
def test_notice_detail_returns_snapshot_body_media_and_receipt(ctx, dataset):
    expected, member_a, member_b, image, pdf = dataset
    owner = ctx[3]
    detail = owner.get(f"/api/admin/instances/{expected['await_reply']['id']}").json()
    assert detail["instance"]["refined_status"] == "await_reply"
    assert detail["instance"]["receipt_mode"] == "link"
    assert detail["instance"]["receipt_at"] is None
    view = detail["notice_view"]
    assert view["source"] == "snapshot"
    assert "请查阅机房巡检通知内容" in view["body"]
    media = {item["kind"]: item for item in view["media"]}
    assert media["image"]["url"] == f"/api/admin/notice-assets/{image['asset_id']}"
    assert media["image"]["name"] == "巡检照片.png"
    assert media["file"]["url"] == f"/api/admin/notice-assets/{pdf['asset_id']}"
    assert media["file"]["name"] == "交接明细.pdf"

    # 成员确认回执后：receipt_at 落库、细分状态切换「已回复」
    confirm_receipt(ctx[0], expected["await_reply"]["id"])
    detail = owner.get(f"/api/admin/instances/{expected['await_reply']['id']}").json()
    assert detail["instance"]["refined_status"] == "replied"
    assert detail["instance"]["receipt_at"]
    assert _ids(owner, "kind=notice&status=await_reply") == set()
    assert _ids(owner, "kind=notice&status=replied") == {
        expected["replied"]["id"], expected["await_reply"]["id"]}


def test_notice_detail_falls_back_to_template_body_and_media(ctx, dataset):
    """无快照（未推送 / 无回执的通知）→ 回退模板当前正文与附件。"""
    expected, member_a, member_b, image, pdf = dataset
    owner = ctx[3]
    detail = owner.get(f"/api/admin/instances/{expected['notice_expired']['id']}").json()
    assert detail["notice_view"]["source"] == "template"
    assert detail["notice_view"]["body"] == "此通知从未送达。"
    assert detail["notice_view"]["media"] == []  # 未送达模板未配置附件
    # 部门周知（无回执模式不落快照）→ 同样回退模板正文 + spec 里的图片附件可在线预览
    plain_detail = owner.get(f"/api/admin/instances/{expected['sent']['id']}").json()
    assert plain_detail["notice_view"]["source"] == "template"
    assert plain_detail["notice_view"]["body"] == "本周例会调整至周四下午。"
    assert plain_detail["notice_view"]["media"][0]["url"] == f"/api/admin/notice-assets/{image['asset_id']}"


def test_upload_detail_carries_latest_submission_source(ctx, dataset):
    expected, *_ = dataset
    detail = ctx[3].get(f"/api/admin/instances/{expected['done_admin']['id']}").json()
    assert detail["instance"]["refined_status"] == "done_admin"
    assert detail["instance"]["latest_submission"]["source"] == "admin"
    assert detail["submission"]["source"] == "admin"
    user_detail = ctx[3].get(f"/api/admin/instances/{expected['done']['id']}").json()
    assert user_detail["instance"]["refined_status"] == "done"
    assert user_detail["instance"]["latest_submission"]["source"] == "user"
