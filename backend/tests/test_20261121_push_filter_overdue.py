# -*- coding: utf-8 -*-
"""2026-11-21 四项调整：回执通知定时跳过已完成 · 手动推送过滤开关 ·
H5 逾期任务转「逾期提醒」+ mission 回执已读 · H5 三页统一返回图标。

覆盖：
  1. 回执通知：成员提前确认后，到点的定时推送自动跳过（不再重复打扰）；
  2. 手动推送 skip_completed=True 只向未完成人员建实例/推送，False 全员照推；
     全员已完成时明确拒绝（不建空批次不发消息）；push-preview 实时人数；
  3. 逾期任务：过截止仍未完成 → 待办保留「已逾期」提醒（无去完成通道），
     「我知道了」持久化后移除；后台统计「已逾期未交」口径不变；
  4. portal_task 已提交记录过截止后仍可编辑（成员在岗、模板未删/未停）；
  5. H5 三页结构契约：统一左上角返回图标，无「返回统计/返回工作台」文字按钮。
"""
from datetime import timedelta
from pathlib import Path

import pytest

from app import notice_receipts
from app.portal import PortalService
from app.state import BizError
from test_20261117_manual_mark_submitted import NOW, ctx  # noqa: F401  (fixture 复用)
from test_20261120_portal_panel import panel  # noqa: F401  (fixture 复用)

PHONE = "13900009401"
SECOND = "13900009402"
H5_DIR = Path(__file__).resolve().parents[1] / "app" / "h5"


# ── 脚手架 ────────────────────────────────────────────────────────────
def add_member(store, role_id, phone, name, owner_id):
    store.add_role_member(role_id, phone, name, owner_id=owner_id)
    store.upsert_delivery_target(phone, f"union-{phone}", external_type="union_id",
                                 activated_at="2026-11-16 08:00:00")


def make_manual_form(owner, role_id, name="推送过滤任务", deadline="2026-11-17 12:00:00"):
    response = owner.post("/api/admin/templates", json={
        "name": name, "kind": "form", "period": "day", "is_manual": True,
        "role_ids": [role_id], "manual_deadline_at": deadline,
        "text_tpl": "请{姓名}提交今日数据", "need_review": False,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": False}],
    })
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def make_scheduled_receipt_notice(owner, role_id, name="定时回执通知"):
    response = owner.post("/api/admin/templates", json={
        "name": name, "kind": "notice", "period": "day", "role_ids": [role_id],
        "notice_times": ["10:00", "11:00"], "text_tpl": "请{姓名}查阅本条通知。",
        "spec": {"notice_receipt": {"enabled": True, "mode": "link", "button_label": "确认已读"}},
    })
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def notice_messages(notifier, receive_id, name="定时回执通知"):
    return [m for m in notifier.sent_messages if m.receive_id == receive_id and name in m.text]


def mark_done(store, template_id, phone):
    """把该成员本周期实例标记为已完成（approved + 提交时间），模拟已提交。"""
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE task_instance SET status='approved', submitted_at=?, late_submit=0 "
            "WHERE template_id=? AND phone=? AND status IN ('pending','overdue','rejected')",
            (store.now_str(), template_id, phone))


# ── 1. 回执通知提前确认 → 定时推送自动跳过 ─────────────────────────────
def test_receipt_notice_skips_member_who_already_confirmed(ctx):
    store, scheduler, notifier, _, owner, _, existing, _ = ctx
    role_id = existing["role_ids"][0]
    tid = make_scheduled_receipt_notice(owner, role_id)

    # 10:00 时点：正常发出，成员收到通知并确认回执
    scheduler.run_once(NOW)
    sent_after_first = len(notice_messages(notifier, "test_recipient"))
    assert sent_after_first >= 1
    instance = next(i for i in store.list_instances_for_period(tid, NOW.date().isoformat())
                    if i["phone"] == PHONE)
    assert instance["receipt_prompt_at"] and not instance["receipt_at"]
    assert notice_receipts.confirm(store, instance["id"])

    # 11:00 时点：已回执成员被定时链跳过，不再重复打扰
    scheduler.run_once(NOW + timedelta(hours=1))
    assert len(notice_messages(notifier, "test_recipient")) == sent_after_first


# ── 2. 手动推送「过滤本周期已完成/已回执」 ──────────────────────────────
def test_manual_push_skip_completed_filters_and_blocks_when_all_done(ctx):
    store, scheduler, notifier, _, owner, _, existing, _ = ctx
    role_id = existing["role_ids"][0]
    owner_id = existing["owner_id"]
    add_member(store, role_id, SECOND, "第二成员", owner_id)
    tid = make_manual_form(owner, role_id)

    # 首次推送：全员照推（未勾选过滤）
    prep = scheduler.push_now_prepare(tid, skip_completed=False)
    assert prep["instance_count"] == 2 and prep["unfinished_count"] == 0
    mark_done(store, tid, PHONE)  # A 先完成

    # 未勾选：全员照推（含已完成成员的只读预告）
    prep_all = scheduler.push_now_prepare(tid, reprocess=True, skip_completed=False)
    assert prep_all["instance_count"] == 2
    # 勾选：只给未完成成员（B）建新批次实例
    prep_skip = scheduler.push_now_prepare(tid, reprocess=True, skip_completed=True)
    assert prep_skip["instance_count"] == 1 and prep_skip["unfinished_count"] == 1
    rows = [i for i in store.list_instances_for_period(tid, prep_skip["period_key"])]
    assert [r["phone"] for r in rows] == [SECOND]

    # 投递也按过滤执行：只发 B
    notifier.sent_messages.clear()
    stats = scheduler.push_now_deliver(tid, prep_skip)
    assert stats["pushed_instances"] == 1
    assert [m.receive_id for m in notifier.sent_messages] == [f"union-{SECOND}"]

    # 全员已完成 → 明确拒绝，不建空批次不发消息
    mark_done(store, tid, SECOND)
    with pytest.raises(BizError) as err:
        scheduler.push_now_prepare(tid, reprocess=True, skip_completed=True)
    assert err.value.code == "ALL_COMPLETED" and "全员已完成" in err.value.message

    # 预览与 API 层：人数口径 + 参数透传（先越过 3 秒推送防抖窗）
    preview = owner.get(f"/api/admin/templates/{tid}/push-preview").json()
    assert preview["total"] == 2 and preview["completed"] == 2 and preview["unfinished"] == 0
    later = NOW + timedelta(minutes=1)
    store._now_fn = scheduler._now_fn = (lambda: later)
    blocked = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1&skip_completed=1")
    assert blocked.status_code == 400 and "全员已完成" in blocked.text


@pytest.mark.parametrize(("deadline_type", "canonical", "legacy_day", "outside_day"), [
    ("month_end", "2026-11", "2026-11-13", "2026-10-31"),
    ("weekend", "2026-W47", "2026-11-16", "2026-11-15"),
])
@pytest.mark.parametrize("daily_batch", [False, True])
def test_manual_push_filters_legacy_daily_completion(ctx, deadline_type, canonical, legacy_day,
                                                    outside_day, daily_batch):
    store, scheduler, notifier, _, owner, _, existing, _ = ctx
    role_id = existing["role_ids"][0]
    add_member(store, role_id, SECOND, "第二成员", existing["owner_id"])
    store.conn.execute("UPDATE delivery_target SET activated_at='2026-10-01 08:00:00' WHERE phone IN (?,?)",
                       (PHONE, SECOND))
    response = owner.post("/api/admin/templates", json={
        "name": "历史周期过滤", "kind": "form", "period": "day", "role_ids": [role_id],
        "remind_at": "09:00", "has_deadline": True, "deadline_type": deadline_type, "deadline_time": "18:00",
        "text_tpl": "请{姓名}提交历史周期过滤数据", "need_review": False,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": True}],
    })
    assert response.status_code == 200, response.text
    tid = response.json()["template_id"]
    suffix = "#" + legacy_day.replace("-", "") + "090000" if daily_batch else ""
    for phone, key in [(PHONE, legacy_day + suffix), (SECOND, outside_day)]:
        inst = store.ensure_instance(tid, phone, "成员", None, key, "2026-11-30 18:00:00")
        token = store.issue_h5_token(phone, tid, key, inst["id"])
        assert store.submit_h5_task(raw_token=token, fields={"qty": "7"}, refs=[], note=None)["ok"]

    base = f"/api/admin/templates/{tid}"
    preview = owner.get(base + "/push-preview").json()
    assert (preview["canonical_key"], preview["total"], preview["completed"], preview["unfinished"]) == (canonical, 2, 1, 1)
    overview = owner.get("/api/admin/stats-panel/overview", params={"template_id": tid, "period_key": canonical})
    assert overview.json()["kpis"]["done"] == 1
    pre = owner.post(base + "/push-now?skip_completed=1").json()
    assert pre["needs_confirm"] and pre["target_count"] == 1 and pre["completed_count"] == 1
    assert not notifier.sent_messages

    response = owner.post(base + "/push-now?confirmed=1&skip_completed=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] and body["skip_completed"] and body["instance_count"] == 1
    [remaining] = store.list_instances_for_period(tid, body["period_key"])
    assert remaining["phone"] == SECOND
    assert [msg.receive_id for msg in notifier.sent_messages] == [f"union-{SECOND}"]

    token = store.issue_h5_token(SECOND, tid, body["period_key"], remaining["id"])
    assert store.submit_h5_task(raw_token=token, fields={"qty": "5"}, refs=[], note=None)["ok"]
    later = NOW + timedelta(seconds=10)
    store._now_fn = scheduler._now_fn = lambda: later
    blocked = owner.post(base + "/push-now?confirmed=1&skip_completed=1")
    assert blocked.status_code == 400 and blocked.json()["code"] == "ALL_COMPLETED"
    assert [msg.receive_id for msg in notifier.sent_messages] == [f"union-{SECOND}"]


@pytest.mark.parametrize("variable", [False, True])
def test_broadcast_ignores_skip_completed_on_first_push_and_repush(ctx, variable):
    store, scheduler, notifier, _, owner, _, existing, _ = ctx
    role_id = existing["role_ids"][0]
    add_member(store, role_id, SECOND, "第二成员", existing["owner_id"])
    body = "请{姓名}查阅纯广播通知"
    spec = {"notice_receipt": {"enabled": False}}
    if variable:
        spec["variable_notice"] = {"platform": "zdxlz", "rows": [
            {"row": 2, "cells": {"手机号": PHONE, "姓名": "测试成员"}},
            {"row": 3, "cells": {"手机号": SECOND, "姓名": "第二成员"}},
        ]}
    saved = owner.post("/api/admin/templates", json={
        "name": "纯广播通知", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": [] if variable else [role_id], "text_tpl": body, "spec": spec,
    })
    assert saved.status_code == 200, saved.text
    tid = saved.json()["template_id"]
    base = f"/api/admin/templates/{tid}"
    first = owner.post(base + "/push-now?skip_completed=1")
    assert first.status_code == 200, first.text
    assert first.json()["skip_completed"] is False and first.json()["instance_count"] == 2
    for tick in range(3):
        scheduler.run_once(NOW + timedelta(seconds=30 * tick))
    assert sorted(msg.receive_id for msg in notifier.sent_messages if "纯广播通知" in msg.text) == [
        "test_recipient", f"union-{SECOND}",
    ]

    # 模板关闭回执后，历史批次可能仍有回执记录。
    store.conn.execute("UPDATE task_instance SET receipt_at=? WHERE template_id=? AND phone=?",
                       (store.now_str(), tid, PHONE))
    later = NOW + timedelta(minutes=2)
    store._now_fn = scheduler._now_fn = lambda: later
    preview = owner.get(base + "/push-preview").json()
    assert (preview["total"], preview["completed"], preview["unfinished"]) == (2, 0, 2)
    pre = owner.post(base + "/push-now?skip_completed=1").json()
    assert pre["needs_confirm"] and pre["target_count"] == 2 and pre["skip_completed"] is False
    again = owner.post(base + "/push-now?confirmed=1&skip_completed=1")
    assert again.status_code == 200, again.text
    assert again.json()["skip_completed"] is False and again.json()["instance_count"] == 2
    assert again.json()["synced_completed"] == 0
    assert sorted(msg.receive_id for msg in notifier.sent_messages if "纯广播通知" in msg.text) == [
        "test_recipient", "test_recipient", f"union-{SECOND}", f"union-{SECOND}",
    ]


# ── 3. 逾期任务保留「已逾期」提醒 + 我知道了 ────────────────────────────
def test_overdue_todo_kept_until_acknowledged(ctx):
    store, scheduler, notifier, _, owner, _, existing, _ = ctx
    tid = make_manual_form(owner, existing["role_ids"][0], name="逾期任务")
    portal = PortalService(store)
    ident = {"phone": PHONE, "platform": "zdxlz"}
    instance = store.ensure_instance(tid, PHONE, "测试成员", None, "once", "2026-11-17 09:00:00")
    store.advance_instance_statuses(NOW)  # pending → overdue（已过截止）

    todos = portal.todos(ident)
    overdue = next(i for i in todos if i["template_id"] == tid)
    assert overdue["overdue"] is True and overdue["ack_period_key"] == "once"
    # 无「去完成」通道：逾期卡片不能签发提交令牌
    with pytest.raises(BizError) as err:
        portal.task_link(ident, instance["id"])
    assert err.value.code == "TASK_UNAVAILABLE"

    # 点「我知道了」→ 持久化已读，立即从待办移除，刷新/重进不再复现
    assert portal.dismiss_overdue(ident, tid, "once")["period_key"] == "once"
    assert all(i["template_id"] != tid for i in portal.todos(ident))
    assert (tid, "once") in store.overdue_acked_keys(PHONE)

    # 后台统计「已逾期未交」口径不受影响
    overview = store.stats_panel_overview(tid, "once", existing["owner_id"])
    assert overview["kpis"]["not_done"] == 1


# ── 4. portal_task 已提交记录过截止后仍可编辑 ──────────────────────────
def test_portal_task_submission_editable_after_deadline(panel):
    portal, leader, _ident_b, (store, scheduler, notifier, admin, owner, sub, tpl, inst) = panel
    task = portal.create_task(leader, {
        "name": "截止后仍可改", "description": "请补充情况", "phones": [leader["phone"]],
        "upload_modules": ["text"], "request_id": "req-edit-0001", "mode": "once",
    })
    template = store.get_template(task["task_id"])
    deadline = store.now_str()
    instance = store.ensure_instance(task["task_id"], leader["phone"], "测试成员", None, "once", deadline)
    raw = store.issue_h5_token(leader["phone"], task["task_id"], "once", instance["id"])
    store.submit_h5_task(raw_token=raw, note="首次提交", refs=[])
    saved = store.get_instance(instance["id"])
    assert saved["status"] == "approved"

    # 把截止时间挪到过去（成员仍在岗、模板未删未停）→ 历史提交仍可修改
    with store.lock, store.conn:
        store.conn.execute("UPDATE task_instance SET deadline_at='2026-11-17 09:00:00' WHERE id=?",
                           (instance["id"],))
    saved = store.get_instance(instance["id"])
    assert store.instance_close_reason(saved, template) is None
    assert store.h5_submission_editable(saved, template) is True


# ── 5. H5 三页统一返回图标结构契约 ──────────────────────────────────────
def test_h5_pages_share_top_left_back_icon_only():
    for name in ("portal.html", "task.html", "notice-receipt.html"):
        text = (H5_DIR / name).read_text(encoding="utf-8")
        assert 'id="page-back"' in text and 'aria-label="返回上一级"' in text, name
        assert "返回统计" not in text and "返回工作台" not in text, name
        assert "https://" not in text and "crypto.randomUUID" not in text, name
        assert "M15 5l-7 7 7 7" in text, name  # 统一线性左箭头
        assert ".page-back" in text and "44px" in text, name
    portal = (H5_DIR / "portal.html").read_text(encoding="utf-8")
    assert "我知道了" in portal and "data-ack" in portal
    assert "已逾期" in portal
