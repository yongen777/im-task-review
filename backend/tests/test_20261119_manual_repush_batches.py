# -*- coding: utf-8 -*-
"""2026-11-19 需求：同日多批次手动推送 + 角色/部门快照 + 链接完整投递 + 回执页结构。

覆盖清单（任务单第 7.1 节六条）：
  1. 手动 form 重推（once → once#…）：预检不占冷却 marker、旧 pending 取消、已完保留、待办只剩 1 条；
  2. 周期 form 重推（YYYY-MM-DD → YYYY-MM-DD#…）：主批次取消、批次键可独立统计/导出；
  3. 角色快照 + 统计部门兜底：改绑角色后历史实例快照不漂移、原角色成员不塌成「未分配部门」；
  4. 通知 link 重推：旧实例取消、旧链接 404、已回执实例不被取消且链接仍可用；
  5. /stats-panel/batches 批次端点；
  6. notice-receipt.html 结构回归（无时间/无「图片」标题/无 crypto.randomUUID/无外链）。
"""
from pathlib import Path
from datetime import timedelta

import pytest

from app import notice_receipts
from app.state import BizError
from test_20261117_manual_mark_submitted import NOW, ctx  # noqa: F401  (fixture 复用)

SECOND = "13900009402"
PERIOD = NOW.date().isoformat()          # 2026-11-17
BATCH_STEM = NOW.strftime("%Y%m%d%H%M%S")  # 20261117100000
NO_DEPT = "未分配部门"


# ── 脚手架 ────────────────────────────────────────────────────────────
def advance(store, scheduler, minutes: int = 3):
    """把 state/调度器的时钟一起往前拨（重推确认必须越过 2 分钟冷却窗）。"""
    later = NOW + timedelta(minutes=minutes)
    store._now_fn = lambda: later
    scheduler._now_fn = lambda: later
    return later
def set_dept(store, role_id, phone, dept: str):
    """直接改名册部门（绕过官方通讯录核验，测试里只为构造部门归属）。"""
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE member SET dept=?, dept_path=? WHERE role_id=? AND phone=?",
            (dept.split("/")[-1], dept, role_id, phone))


def add_member(store, role_id, phone, name, owner_id, *, dept: str = "", ext: str = ""):
    """加角色成员 + 绑定（dept 直接写库，绕过官方通讯录核验的部门回填）。"""
    store.add_role_member(role_id, phone, name, owner_id=owner_id)
    if dept:
        set_dept(store, role_id, phone, dept)
    store.upsert_delivery_target(phone, ext or f"union-{phone}", external_type="union_id",
                                 activated_at="2026-11-16 08:00:00")


def make_manual_form(owner, role_id, name="重推任务", deadline="2026-11-17 12:00:00"):
    response = owner.post("/api/admin/templates", json={
        "name": name, "kind": "form", "period": "day", "is_manual": True,
        "role_ids": [role_id], "manual_deadline_at": deadline,
        "text_tpl": "请{姓名}提交今日数据",
        "need_review": False,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": False}],
    })
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def make_periodic_form(owner, role_id, name="周期重推任务", remind_at="09:00"):
    response = owner.post("/api/admin/templates", json={
        "name": name, "kind": "form", "period": "day", "remind_at": remind_at,
        "deadline": "18:00", "role_ids": [role_id], "text_tpl": "请{姓名}提交今日数据",
        "need_review": False,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": False}],
    })
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def make_manual_link_notice(owner, role_id, name="重推通知"):
    response = owner.post("/api/admin/templates", json={
        "name": name, "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": [role_id], "notice_times": [], "text_tpl": "请{姓名}查阅本条通知正文。",
        "spec": {"notice_receipt": {"enabled": True, "mode": "link", "button_label": "确认已读"}},
    })
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def marker_count(store, template_id):
    with store.lock:
        return int(store.conn.execute(
            "SELECT COUNT(1) FROM reminder_log WHERE template_id = ? AND kind = 'manual_push'",
            (template_id,)).fetchone()[0])


def token_of(notifier, receive_id):
    """取该接收人最后一条消息里的 H5 令牌（link 模式最后一条 = 确认链接）。"""
    messages = [m for m in notifier.sent_messages if m.receive_id == receive_id]
    return messages[-1].text.splitlines()[-1].partition("#t=")[2]


# ── 1. 手动 form 重推 ────────────────────────────────────────────────
def test_manual_form_repush_creates_new_batch_and_cancels_stale(ctx):
    store, scheduler, notifier, _, owner, _, existing, member = ctx
    role_id = existing["role_ids"][0]
    owner_id = existing["owner_id"]
    add_member(store, role_id, SECOND, "第二成员", owner_id)
    tid = make_manual_form(owner, role_id)

    first = owner.post(f"/api/admin/templates/{tid}/push-now")
    assert first.status_code == 200, first.text
    assert first.json()["queued"] is True and first.json()["new_batch"] is False
    assert first.json()["period_key"] == "once" and first.json()["instance_count"] == 2
    assert len(notifier.sent_messages) == 2

    # A 已审核通过、B 仍待提交
    old_instances = store.list_instances_for_period(tid, "once")
    a_old = next(i for i in old_instances if i["phone"] == member["phone"])
    b_old = next(i for i in old_instances if i["phone"] == SECOND)
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE task_instance SET status='approved', submitted_at=? WHERE id=?",
            (NOW.strftime("%Y-%m-%d %H:%M:%S"), a_old["id"]))

    before = marker_count(store, tid)
    again = owner.post(f"/api/admin/templates/{tid}/push-now")
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["needs_confirm"] is True and body["target_count"] == 2, body
    assert body["canonical_period_key"] == "once", body
    # 预检绝不写冷却 marker
    assert marker_count(store, tid) == before

    later = advance(store, scheduler, 3)  # 越过 2 分钟冷却窗后管理员点了「确认」
    confirmed = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert confirmed.status_code == 200, confirmed.text
    batch = confirmed.json()
    assert batch["new_batch"] is True and batch["instance_count"] == 2, batch
    assert batch["period_key"] == f"once#{later.strftime('%Y%m%d%H%M%S')}", batch
    assert batch["cancelled"] == 1, batch  # 只有 B 的旧 pending 被取消

    # 旧实例：已完成保留、未完成取消
    assert store.get_instance(a_old["id"])["status"] == "approved"
    assert store.get_instance(b_old["id"])["status"] == "cancelled"
    # 新批次：两人各一条 pending 实例
    fresh = store.list_instances_for_period(tid, batch["period_key"])
    assert sorted(i["phone"] for i in fresh) == sorted([member["phone"], SECOND])
    assert {i["status"] for i in fresh} == {"pending"}
    # 两人各收到一条新消息（首批 2 + 新批 2）
    assert len(notifier.sent_messages) == 4
    # H5 待办只剩新批次 1 条
    from app.portal import PortalService

    portal = PortalService(store)
    todos = portal.todos({"phone": member["phone"], "platform": "zdxlz"})
    assert [t["id"] for t in todos if t["template_id"] == tid] == [
        next(i["id"] for i in fresh if i["phone"] == member["phone"])]


# ── 2. 周期 form 重推 ────────────────────────────────────────────────
def test_periodic_form_repush_batch_is_independently_queryable(ctx):
    store, scheduler, notifier, _, owner, _, existing, member = ctx
    role_id = existing["role_ids"][0]
    owner_id = existing["owner_id"]
    add_member(store, role_id, SECOND, "第二成员", owner_id)
    tid = make_periodic_form(owner, role_id)

    scheduler.run_once(NOW)  # 定时链建当日实例并提醒
    assert len(store.list_instances_for_period(tid, PERIOD)) == 2

    pre = owner.post(f"/api/admin/templates/{tid}/push-now")
    assert pre.status_code == 200 and pre.json()["needs_confirm"] is True, pre.text
    assert pre.json()["canonical_period_key"] == PERIOD

    done = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["new_batch"] is True, body
    assert body["period_key"] == f"{PERIOD}#{BATCH_STEM}", body
    assert body["cancelled"] == 2, body

    main_rows = store.list_instances_for_period(tid, PERIOD)
    assert {r["status"] for r in main_rows} == {"cancelled"}
    batch_rows = store.list_instances_for_period(tid, body["period_key"])
    assert {r["status"] for r in batch_rows} == {"pending"}

    # 批次键可独立统计/明细/导出
    overview = owner.get("/api/admin/stats-panel/overview", params={
        "template_id": tid, "period_key": body["period_key"]})
    assert overview.status_code == 200, overview.text
    assert overview.json()["kpis"]["total"] == 2, overview.json()["kpis"]
    roster = owner.get("/api/admin/stats-panel/instances", params={
        "template_id": tid, "period_key": body["period_key"]})
    assert roster.status_code == 200 and roster.json()["total"] == 2, roster.text
    exported = owner.get("/api/admin/stats-panel/export", params={
        "template_id": tid, "period_key": body["period_key"]})
    assert exported.status_code == 200

    # 主批次被整批取消 → 统计为空（不会把两批混在一起）
    main_overview = owner.get("/api/admin/stats-panel/overview", params={
        "template_id": tid, "period_key": PERIOD})
    assert main_overview.json()["kpis"]["total"] == 0


# ── 3. 角色快照 + 统计部门兜底 ──────────────────────────────────────
def test_role_snapshot_and_dept_fallback_survive_role_rebinding(ctx):
    store, scheduler, notifier, _, owner, _, existing, member = ctx
    owner_id = existing["owner_id"]
    role_a = existing["role_ids"][0]
    phone_a = member["phone"]
    set_dept(store, role_a, phone_a, "德阳分公司/工会")
    role_b = owner.post("/api/admin/roles", json={"name": "角色乙"}).json()["role_id"]
    add_member(store, role_b, SECOND, "乙成员", owner_id, dept="德阳分公司/市场部")
    tid = make_manual_form(owner, role_a, name="快照任务")

    assert owner.post(f"/api/admin/templates/{tid}/push-now").status_code == 200
    old_a = store.get_instance_by_key(tid, phone_a, "once")
    assert old_a["role_name_snapshot"] == "代交组", old_a["role_name_snapshot"]
    # 甲已经审核通过（改绑角色后仍应保留、且统计里要看得到）
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE task_instance SET status='approved', submitted_at=? WHERE id=?",
            (NOW.strftime("%Y-%m-%d %H:%M:%S"), old_a["id"]))

    resp = owner.put(f"/api/admin/templates/{tid}", json={
        "name": "快照任务", "kind": "form", "period": "day", "is_manual": True,
        "role_ids": [role_b], "manual_deadline_at": "2026-11-17 12:00:00",
        "text_tpl": "请{姓名}提交今日数据", "need_review": False,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": False}],
    })
    assert resp.status_code == 200, resp.text

    advance(store, scheduler, 4)  # 越过冷却窗后再确认重推
    assert owner.post(f"/api/admin/templates/{tid}/push-now").json()["needs_confirm"] is True
    done = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert done.status_code == 200, done.text
    batch_key = done.json()["period_key"]

    # 历史实例快照不随模板角色漂移；新批次用新角色名
    assert store.get_instance(old_a["id"])["role_name_snapshot"] == "代交组"
    new_b = store.get_instance_by_key(tid, SECOND, batch_key)
    assert new_b["role_name_snapshot"] == "角色乙"

    listing = owner.get("/api/admin/instances", params={"template_id": tid, "period_key": PERIOD})
    assert listing.status_code == 200, listing.text
    rows = {row["id"]: row for row in listing.json()["items"]}
    assert rows[old_a["id"]]["role_name_snapshot"] == "代交组"
    assert rows[old_a["id"]]["role_ids"] == [role_b]  # 当前角色反查仍是新的（现状口径）
    detail = owner.get(f"/api/admin/instances/{old_a['id']}")
    assert detail.status_code == 200 and detail.json()["instance"]["role_name_snapshot"] == "代交组", detail.text

    # 统计/明细/导出：甲仍归属「工会」，不会塌成「未分配部门」
    params = {"template_id": tid, "period_key": "once"}
    overview = owner.get("/api/admin/stats-panel/overview", params=params)
    assert overview.status_code == 200, overview.text
    assert overview.json()["kpis"]["total"] == 1
    assert "德阳分公司/工会" in {node["path"] for node in overview.json()["dept_tree"]}, overview.json()["dept_tree"]
    roster = owner.get("/api/admin/stats-panel/instances", params=params).json()["items"]
    assert [(row["phone"], row["dept_path"]) for row in roster] == [(phone_a, "德阳分公司/工会")]
    exported = owner.get("/api/admin/stats-panel/export", params=params)
    assert exported.status_code == 200
    from io import BytesIO

    from openpyxl import load_workbook

    sheet = list(load_workbook(BytesIO(exported.content), read_only=True).active.values)
    assert any("工会" in str(cell) and NO_DEPT not in str(cell) for row in sheet for cell in row), sheet


# ── 4. 通知 link 重推 ────────────────────────────────────────────────
def test_link_notice_repush_cancels_unconfirmed_and_invalidates_old_link(ctx):
    store, scheduler, notifier, _, owner, _, existing, member = ctx
    role_id = existing["role_ids"][0]
    owner_id = existing["owner_id"]
    add_member(store, role_id, SECOND, "第二成员", owner_id)
    tid = make_manual_link_notice(owner, role_id)

    assert owner.post(f"/api/admin/templates/{tid}/push-now").status_code == 200
    old_rows = store.list_instances_for_period(tid, "once")
    a_old = next(i for i in old_rows if i["phone"] == member["phone"])
    b_old = next(i for i in old_rows if i["phone"] == SECOND)
    assert {i["status"] for i in old_rows} == {"sent"}
    assert all(i["receipt_prompt_at"] for i in old_rows)

    old_token_a = token_of(notifier, "test_recipient")
    old_token_b = token_of(notifier, f"union-{SECOND}")
    assert notice_receipts.read_target(store, old_token_a)[0]["id"] == a_old["id"]
    # B 先回执
    assert notice_receipts.confirm(store, b_old["id"])

    advance(store, scheduler, 5)
    pre = owner.post(f"/api/admin/templates/{tid}/push-now")
    assert pre.json()["needs_confirm"] is True
    done = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["new_batch"] is True and body["cancelled"] == 1, body

    assert store.get_instance(a_old["id"])["status"] == "cancelled"
    kept = store.get_instance(b_old["id"])
    assert kept["status"] == "sent" and kept["receipt_at"]  # 已回执：不取消、不重推

    # H5 待办只留新批次
    pending = store.pending_notice_receipts(member["phone"], "zdxlz", mode="link")
    assert [row["id"] for row in pending] == [
        i["id"] for i in store.list_instances_for_period(tid, body["period_key"])
        if i["phone"] == member["phone"]]
    # 旧链接失效
    with pytest.raises(BizError) as excinfo:
        notice_receipts.read_target(store, old_token_a)
    assert excinfo.value.status_code == 404 and excinfo.value.code == "NOTICE_UNAVAILABLE"
    # 已回执实例的链接仍可重放
    assert notice_receipts.read_target(store, old_token_b)[0]["id"] == b_old["id"]


# ── 5. 批次端点 ──────────────────────────────────────────────────────
def test_stats_panel_batches_lists_every_batch(ctx):
    store, scheduler, notifier, _, owner, _, existing, member = ctx
    role_id = existing["role_ids"][0]
    tid = make_manual_form(owner, role_id)

    assert owner.post(f"/api/admin/templates/{tid}/push-now").status_code == 200
    assert owner.post(f"/api/admin/templates/{tid}/push-now").json()["needs_confirm"] is True
    later = advance(store, scheduler, 6)
    assert owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1").status_code == 200

    response = owner.get("/api/admin/stats-panel/batches", params={"template_id": tid})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["period_key"] for item in items] == ["once", f"once#{later.strftime('%Y%m%d%H%M%S')}"]
    assert items[0]["instance_count"] == 1 and items[0]["alive_count"] == 0  # 旧批次被整批取消
    assert items[1]["instance_count"] == 1 and items[1]["alive_count"] == 1
    assert all(item["first_created_at"] for item in items)
    # 非属主 / 超管不可读
    assert owner.get("/api/admin/stats-panel/batches",
                     params={"template_id": "t_missing"}).status_code == 404


# ── 6. 回执页结构回归 ────────────────────────────────────────────────
def test_notice_receipt_page_has_no_time_no_image_heading_and_no_cdn():
    html = (Path(__file__).resolve().parents[1] / "app" / "h5" / "notice-receipt.html").read_text(
        encoding="utf-8")
    assert "发布时间" not in html and "送达时间" not in html
    assert "'图片'" not in html and ">图片<" not in html
    assert "crypto.randomUUID" not in html
    assert "cdn" not in html.lower() and "https://" not in html
    # 关键交互必须保留
    for kept in ("confirm", "zoom", "media?token=", "safe-area-inset-bottom", "max-width: 480px"):
        assert kept in html, kept
