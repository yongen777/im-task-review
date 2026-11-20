# -*- coding: utf-8 -*-
"""2026-11-19 需求：同日多批次手动推送 + 角色/部门快照 + 链接完整投递 + 回执页结构。
（2026-11-20 切期改造后按新口径维护：重推不再取消旧批次，改为完成态/回执态全族同步。）

覆盖清单：
  1. 手动 form 重推（once → once#…）：预检不占冷却 marker、旧批次保留、已完成成员
     新副本同步完成态（闭口只读）、待办按（模板+归属周期）去重只留最新 1 条；
  2. 周期 form 重推（YYYY-MM-DD → YYYY-MM-DD#…）：主批次保留、批次键可独立统计/导出、
     canonical 键聚合整个归属周期按人去重；
  3. 角色快照 + 统计部门兜底：改绑角色后历史实例快照不漂移、once 族按人去重 2 人次；
  4. 通知 link 重推：旧实例保留、旧链接继续有效、任一批次确认后全族同步回执；
  5. /stats-panel/batches 批次端点（按归属周期聚合）；
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
    """把 state/调度器的时钟一起往前拨（重推确认必须越过 3 秒防抖窗；分钟只为稳余量）。"""
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
def test_manual_form_repush_syncs_family_and_keeps_old_batch(ctx):
    """2026-11-20 新口径：重推不再取消旧批次；已完成成员副本同步完成态（闭口只读），
    未完成成员照常开放；待办按归属周期去重只留最新 1 条。"""
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

    later = advance(store, scheduler, 1)  # 越过 3 秒防抖窗后管理员点了「确认」
    confirmed = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert confirmed.status_code == 200, confirmed.text
    batch = confirmed.json()
    assert batch["new_batch"] is True and batch["instance_count"] == 2, batch
    assert batch["period_key"] == f"once#{later.strftime('%Y%m%d%H%M%S')}", batch
    assert batch["synced_completed"] == 1, batch  # A 的完成态同步到新副本

    # 旧实例：完成/未完成都保留（2026-11-20 重推不再取消）
    assert store.get_instance(a_old["id"])["status"] == "approved"
    assert store.get_instance(b_old["id"])["status"] == "pending"
    # 新批次：A 被同步为已完成（闭口只读回看），B 照常开放
    fresh = store.list_instances_for_period(tid, batch["period_key"])
    assert sorted(i["phone"] for i in fresh) == sorted([member["phone"], SECOND])
    a_new = next(i for i in fresh if i["phone"] == member["phone"])
    assert a_new["status"] == "approved" and a_new["submitted_at"], a_new
    assert next(i for i in fresh if i["phone"] == SECOND)["status"] == "pending"
    # 全员照推：A 也收到一条（含回看提示），共 2+2 条
    assert len(notifier.sent_messages) == 4
    a_msgs = [m for m in notifier.sent_messages if m.receive_id == "test_recipient"]
    assert "已在本周期完成" in a_msgs[-1].text
    # H5 待办按（模板 + 归属周期）去重，只展示最新 1 条
    from app.portal import PortalService

    portal = PortalService(store)
    todos = portal.todos({"phone": member["phone"], "platform": "zdxlz"})
    assert [t["id"] for t in todos if t["template_id"] == tid] == []
    todos_b = portal.todos({"phone": SECOND, "platform": "zdxlz"})
    assert [t["id"] for t in todos_b if t["template_id"] == tid] == [
        next(i["id"] for i in fresh if i["phone"] == SECOND)]


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

    later = advance(store, scheduler, 1)
    done = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["new_batch"] is True, body
    assert body["period_key"] == f"{PERIOD}#{later.strftime('%Y%m%d%H%M%S')}", body
    assert body["synced_completed"] == 0, body  # 无人完成 → 无同步

    # 2026-11-20 新口径：主批次不再取消（两批并存）
    main_rows = store.list_instances_for_period(tid, PERIOD)
    assert {r["status"] for r in main_rows} == {"pending"}
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

    # canonical 键聚合整个归属周期（含重推批次，按人去重不重复算人头）
    main_overview = owner.get("/api/admin/stats-panel/overview", params={
        "template_id": tid, "period_key": PERIOD})
    assert main_overview.json()["kpis"]["total"] == 2, main_overview.json()["kpis"]


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

    # 统计/明细/导出：once 族按归属周期聚合（A 历史行 + B 新批次），按人去重 2 人次；
    # 甲仍归属「工会」（历史部门兜底），乙归属其新角色部门，均不塌成「未分配部门」
    params = {"template_id": tid, "period_key": "once"}
    overview = owner.get("/api/admin/stats-panel/overview", params=params)
    assert overview.status_code == 200, overview.text
    assert overview.json()["kpis"]["total"] == 2
    assert "德阳分公司/工会" in {node["path"] for node in overview.json()["dept_tree"]}, overview.json()["dept_tree"]
    roster = owner.get("/api/admin/stats-panel/instances", params=params).json()["items"]
    assert {row["phone"]: row["dept_path"] for row in roster} == {
        phone_a: "德阳分公司/工会", SECOND: "德阳分公司/市场部"}, roster
    exported = owner.get("/api/admin/stats-panel/export", params=params)
    assert exported.status_code == 200
    from io import BytesIO

    from openpyxl import load_workbook

    sheet = list(load_workbook(BytesIO(exported.content), read_only=True).active.values)
    assert any("工会" in str(cell) and NO_DEPT not in str(cell) for row in sheet for cell in row), sheet


# ── 4. 通知 link 重推 ────────────────────────────────────────────────
def test_link_notice_repush_keeps_old_rows_and_syncs_receipt(ctx):
    """2026-11-20 新口径：重推不再取消旧通知实例；已回执成员的新副本同步回执态；
    未回执成员两批链接均可确认，任一确认后全族同步、待办去重只留最新 1 条。"""
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

    later = advance(store, scheduler, 1)
    pre = owner.post(f"/api/admin/templates/{tid}/push-now")
    assert pre.json()["needs_confirm"] is True
    done = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["new_batch"] is True and body["period_key"] == f"once#{later.strftime('%Y%m%d%H%M%S')}", body

    # 旧实例不取消：A 未回执保持 sent；B 已回执保留回执时间
    assert store.get_instance(a_old["id"])["status"] == "sent"
    kept = store.get_instance(b_old["id"])
    assert kept["status"] == "sent" and kept["receipt_at"]

    # 新副本：A 仍待确认（sent 未回执）、B 同步为已回执（不再打扰待确认）
    new_rows = store.list_instances_for_period(tid, body["period_key"])
    a_new = next(i for i in new_rows if i["phone"] == member["phone"])
    b_new = next(i for i in new_rows if i["phone"] == SECOND)
    assert a_new["status"] == "sent" and not a_new["receipt_at"]
    assert b_new["receipt_at"], b_new  # 家族回执同步
    assert b_new["receipt_text"] == b_new["receipt_phrase"], b_new  # 回退短语而非 NULL

    # H5 待办：portal 层按（模板 + 归属周期）去重，A 只看到最新 1 条
    from app.portal import PortalService

    todos = [t for t in PortalService(store).todos(
        {"phone": member["phone"], "platform": "zdxlz"}) if t["template_id"] == tid]
    assert [t["id"] for t in todos] == [a_new["id"]]

    # 2026-11-20 新口径：旧链接继续有效（成员从任一批次确认都行）
    assert notice_receipts.read_target(store, old_token_a)[0]["id"] == a_old["id"]
    # 已回执实例的链接仍可重放
    assert notice_receipts.read_target(store, old_token_b)[0]["id"] == b_old["id"]

    # A 通过旧链接确认 → 本周期新副本同步已回执（双向同步）
    assert notice_receipts.confirm(store, a_old["id"])
    assert store.get_instance(a_new["id"])["receipt_at"], "旧链接确认应同步到新副本"


# ── 5. 批次端点 ──────────────────────────────────────────────────────
def test_stats_panel_batches_groups_by_canonical_period(ctx):
    """2026-11-20 新口径：批次清单按**归属周期**聚合（once 一行 = once + once#… 全部实例），
    alive_count 含两批全部未取消行。"""
    store, scheduler, notifier, _, owner, _, existing, member = ctx
    role_id = existing["role_ids"][0]
    tid = make_manual_form(owner, role_id)

    assert owner.post(f"/api/admin/templates/{tid}/push-now").status_code == 200
    assert owner.post(f"/api/admin/templates/{tid}/push-now").json()["needs_confirm"] is True
    later = advance(store, scheduler, 1)
    assert owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1").status_code == 200

    response = owner.get("/api/admin/stats-panel/batches", params={"template_id": tid})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["period_key"] for item in items] == ["once"]
    assert items[0]["instance_count"] == 2  # once + once#… 两批 2 行实例
    assert items[0]["alive_count"] == 2     # 重推不再取消旧批次
    assert items[0]["first_created_at"]
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
