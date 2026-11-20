# -*- coding: utf-8 -*-
"""2026-11-20 切期改造：自然周期切期 + 定时/手动重推状态同步 + 统计按周期按人去重。

8 组场景（用户任务单第四节）：
  ① 每日定时 + 同日重推：完成态同步全族、待办只留最新 1 条、统计不重复算人头；
  ② 每日过零点进次日新期（次日凌晨定时前手推 → 新期，当日定时仍照常提醒）；
  ③ 周任务多执行日定时只催未完成（已完成成员不收第二条提醒）；
  ④ 周任务内手动全员重推（已完成闭口只读 + 旧链接完成双向同步 + 待办清零）；
  ⑤ 每周三当天截止任务周四手推全员闭口、过周日 24 点进下周重开；
  ⑥ 月任务多执行日与跨月（跨月自然切换新周期）；
  ⑦ 一次性任务反复重推（多批并存 + 3 秒防抖 + 主批次整批取消后的复活路径）；
  ⑧ 统计面板按归属周期按人去重（明细/导出同口径，多批不重复算人头）。

时间锚点：2026-11-17（周二）为基准（ctx 的 NOW）。时钟用 at() 手拨。
"""
from datetime import datetime, timedelta

import pytest

from app.portal import PortalService
from app.state import BizError

from test_20261117_manual_mark_submitted import PHONE, ctx  # noqa: F401  (fixture 复用)

SECOND = "13900009402"


# ── 脚手架 ────────────────────────────────────────────────────────────
def at(store, scheduler, dt):
    """把 state/调度器时钟拨到 dt（测试推进时间的唯一入口）。

    时钟跨过会话 7 天 TTL 时同步续借管理员会话，避免长周期场景 401。
    """
    store._now_fn = lambda: dt
    scheduler._now_fn = lambda: dt
    from app.config import format_dt

    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE admin_session SET expires_at=?",
            (format_dt(dt + timedelta(days=30)),))
    return dt


def add_member(store, role_id, phone, owner_id):
    store.add_role_member(role_id, phone, "成员" + phone[-2:], owner_id=owner_id)
    store.upsert_delivery_target(phone, f"union-{phone}", external_type="union_id",
                                 activated_at="2026-11-01 08:00:00")


def make_form(owner, role_id, *, name, exec_weekdays=None, exec_dom_days=None,
              remind_at="09:00", deadline_type="same_day", deadline_time="18:00",
              has_deadline=True, is_manual=False, manual_deadline_at=None):
    payload = {
        "name": name, "kind": "form", "period": "day", "role_ids": [role_id],
        "text_tpl": "请{姓名}提交" + name, "need_review": False,
        "schema_json": [{"key": "qty", "label": "数量", "type": "number", "required": False}],
    }
    if is_manual:
        payload.update({"is_manual": True, "manual_deadline_at": manual_deadline_at})
    else:
        payload["remind_at"] = remind_at
        payload["has_deadline"] = has_deadline
        if has_deadline:
            payload["deadline_type"] = deadline_type
            payload["deadline_time"] = deadline_time
        if exec_weekdays is not None:
            payload["exec_weekdays"] = exec_weekdays
        if exec_dom_days is not None:
            payload["exec_dom_days"] = exec_dom_days
    resp = owner.post("/api/admin/templates", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["template_id"]


def submit(store, phone, tid, inst, qty="7"):
    tok = store.issue_h5_token(phone, tid, inst["period_key"], int(inst["id"]))
    return store.submit_h5_task(raw_token=tok, fields={"qty": qty}, refs=[], note=None)


def inst_of(store, tid, phone, pk):
    return store.get_instance_by_key(tid, phone, pk)


def todos_of(store, phone, tid=None):
    portal = PortalService(store)
    items = portal.todos({"phone": phone, "platform": "zdxlz"})
    return [t for t in items if tid is None or t["template_id"] == tid]


def reminder_texts(notifier, name):
    return [m.text for m in notifier.sent_messages
            if m.text.startswith(f"【任务提醒】{name}")]


def push_once(owner, tid, confirmed=False):
    url = f"/api/admin/templates/{tid}/push-now" + ("?confirmed=1" if confirmed else "")
    return owner.post(url, json={})


def stats_overview(owner, tid, period_key):
    resp = owner.get("/api/admin/stats-panel/overview",
                     params={"template_id": tid, "period_key": period_key})
    assert resp.status_code == 200, resp.text
    return resp.json()


def stats_roster(owner, tid, period_key):
    resp = owner.get("/api/admin/stats-panel/instances",
                     params={"template_id": tid, "period_key": period_key})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── ① 每日定时 + 同日重推：双向同步 ─────────────────────────────────
def test_1_daily_repush_syncs_completion_family_and_dedups_todo(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    tid = make_form(owner, role_id, name="日任务一")  # 全周执行 + 当天 18:00 截止 → 日周期

    at(store, scheduler, datetime(2026, 11, 17, 9, 0, 10))  # 周二定时链
    scheduler.run_once(store.now())
    assert len(store.list_instances_for_period(tid, "2026-11-17")) == 2
    assert len(reminder_texts(notifier, "日任务一")) == 2

    a_old = inst_of(store, tid, PHONE, "2026-11-17")
    b_old = inst_of(store, tid, SECOND, "2026-11-17")
    submit(store, PHONE, tid, a_old, qty="11")  # A 先通过定时批次完成

    pre = push_once(owner, tid)
    assert pre.json()["needs_confirm"] is True and pre.json()["canonical_period_key"] == "2026-11-17"

    later = at(store, scheduler, datetime(2026, 11, 17, 9, 30, 0))
    done = push_once(owner, tid, confirmed=True)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["period_key"] == "2026-11-17#" + later.strftime("%Y%m%d%H%M%S"), body
    assert body["instance_count"] == 2

    # 旧批次不被取消：B 主批次仍 pending；A 主批次仍 approved
    assert store.get_instance(a_old["id"])["status"] == "approved"
    assert store.get_instance(b_old["id"])["status"] == "pending"
    # 新批次：A 副本被同步为完成（闭口只读），B 副本正常开放
    a_new = inst_of(store, tid, PHONE, body["period_key"])
    b_new = inst_of(store, tid, SECOND, body["period_key"])
    assert a_new["status"] == "approved" and a_new["submitted_at"], a_new
    assert b_new["status"] == "pending"
    # 全员照推：A（已完成）也收到消息，文案标注「已在本周期完成」
    a_texts = [m.text for m in notifier.sent_messages if m.receive_id == "test_recipient"
               and m.text.startswith("【任务提醒】日任务一")]
    assert a_texts and "已在本周期完成" in a_texts[-1]
    b_texts = [m.text for m in notifier.sent_messages if m.receive_id == f"union-{SECOND}"
               and m.text.startswith("【任务提醒】日任务一")]
    assert b_texts and "已在本周期完成" not in b_texts[-1]

    # A 待办清零；B 只显示最新批次 1 条
    assert todos_of(store, PHONE, tid) == []
    assert [t["id"] for t in todos_of(store, SECOND, tid)] == [b_new["id"]]
    assert [t["id"] for t in store.list_pending_todo(SECOND) if t["template_id"] == tid] == [b_new["id"]]

    # B 反过来通过旧批次链接完成 → 新批次副本双向同步
    submit(store, SECOND, tid, b_old, qty="22")
    assert store.get_instance(b_new["id"])["status"] == "approved"
    assert todos_of(store, SECOND, tid) == []

    # 统计：整周期聚合并按人去重（两批 4 行实例 → 2 人次，全部完成）
    overview = stats_overview(owner, tid, "2026-11-17")
    assert overview["kpis"]["total"] == 2 and overview["kpis"]["done"] == 2, overview["kpis"]
    roster = stats_roster(owner, tid, "2026-11-17")
    assert roster["total"] == 2 and len(roster["items"]) == 2, roster["total"]
    # A 的明细内容沿周期家族回查（副本实例无自身提交行也能带出内容）
    row_a = next(r for r in roster["items"] if r["phone"] == PHONE)
    assert row_a["content"] and "11" in str(row_a["content"]), row_a


# ── ② 每日过零点进次日新期 ───────────────────────────────────────────
def test_2_daily_midnight_rolls_into_next_period(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    tid = make_form(owner, role_id, name="日任务二")

    at(store, scheduler, datetime(2026, 11, 17, 9, 0, 10))
    scheduler.run_once(store.now())  # 周二：建 2026-11-17 实例 + 2 条提醒

    # 次日凌晨 00:01（定时未到）手推 → 直接进 2026-11-18 新期
    at(store, scheduler, datetime(2026, 11, 18, 0, 1, 0))
    first = push_once(owner, tid)
    assert first.status_code == 200 and first.json()["queued"] is True, first.text
    body = first.json()
    assert body["period_key"] == "2026-11-18" and body["new_batch"] is False, body
    assert body["deadline_at"] == "2026-11-18 18:00:00"
    assert store.get_instance_by_key(tid, PHONE, "2026-11-18")["status"] == "pending"

    # 17 号实例被 advance 流转 overdue（周期已闭），18 号正常开放
    a_old = inst_of(store, tid, PHONE, "2026-11-17")
    assert store.get_instance(a_old["id"])["status"] == "overdue"
    b_new = inst_of(store, tid, SECOND, "2026-11-18")
    assert [t["id"] for t in todos_of(store, SECOND, tid)] == [b_new["id"]]

    # 当日 09:00 定时链照常：同周期不建新行，提醒正常发出
    at(store, scheduler, datetime(2026, 11, 18, 9, 0, 20))
    scheduler.run_once(store.now())
    assert len(store.list_instances_for_period(tid, "2026-11-18")) == 2  # 不新增
    assert len(reminder_texts(notifier, "日任务二")) == 6  # 周二定时 2 + 凌晨手推 2 + 周三定时 2

    # B 在新周期提交成功；旧周期实例不被新周期完成影响
    submit(store, SECOND, tid, b_new)
    assert store.get_instance(b_new["id"])["status"] == "approved"
    assert store.get_instance(inst_of(store, tid, SECOND, "2026-11-17")["id"])["status"] == "overdue"


# ── ③ 周任务多执行日：定时只催未完成 ─────────────────────────────────
def test_3_week_multi_exec_reminds_only_incomplete(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    # 周一 + 周四执行、本周日 20:00 截止 → 周容器（整周一个实例批次）
    tid = make_form(owner, role_id, name="周任务三", exec_weekdays=[1, 4],
                    deadline_type="weekend", deadline_time="20:00")

    at(store, scheduler, datetime(2026, 11, 16, 9, 0, 10))  # 周一
    scheduler.run_once(store.now())
    rows = store.list_instances_for_period(tid, "2026-W47")
    assert len(rows) == 2 and {r["deadline_at"] for r in rows} == {"2026-11-22 20:00:00"}

    a_mon = inst_of(store, tid, PHONE, "2026-W47")
    submit(store, PHONE, tid, a_mon, qty="3")  # A 周一完成

    at(store, scheduler, datetime(2026, 11, 19, 9, 0, 10))  # 周四（第二执行日）
    scheduler.run_once(store.now())
    assert len(store.list_instances_for_period(tid, "2026-W47")) == 2  # 同周期不建新行
    texts = reminder_texts(notifier, "周任务三")
    assert len(texts) == 3  # 周一 2 条 + 周四只催未完成的 B 1 条
    assert notifier.sent_messages[-1].receive_id == f"union-{SECOND}"
    assert "已在本周期完成" not in texts[-1]
    # A 无待办；B 待办仍是同一条（不因第二个执行日翻倍）
    assert todos_of(store, PHONE, tid) == []
    assert len(todos_of(store, SECOND, tid)) == 1


# ── ④ 周任务内手动全推：已完成闭口只读 + 双向同步 ─────────────────────
def test_4_week_repush_full_family_sync(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    tid = make_form(owner, role_id, name="周任务四", exec_weekdays=[1, 4],
                    deadline_type="weekend", deadline_time="20:00")

    at(store, scheduler, datetime(2026, 11, 16, 9, 0, 10))  # 周一定时
    scheduler.run_once(store.now())
    a_main = inst_of(store, tid, PHONE, "2026-W47")
    b_main = inst_of(store, tid, SECOND, "2026-W47")
    submit(store, PHONE, tid, a_main, qty="4")

    later = at(store, scheduler, datetime(2026, 11, 19, 10, 0, 0))  # 周四手推
    assert push_once(owner, tid).json()["needs_confirm"] is True
    done = push_once(owner, tid, confirmed=True)
    body = done.json()
    assert body["period_key"] == "2026-W47#" + later.strftime("%Y%m%d%H%M%S"), body

    a_new = inst_of(store, tid, PHONE, body["period_key"])
    b_new = inst_of(store, tid, SECOND, body["period_key"])
    assert a_new["status"] == "approved" and a_new["submitted_at"]  # 闭口同步
    assert b_new["status"] == "pending"
    # A 只读回看：副本实例没有自己的提交行，家族回查能带出本周期原提交
    from app.api.h5 import _completed_submission

    view = _completed_submission(store, a_new)
    assert view is not None and str((view.get("fields") or {}).get("qty")) == "4", view
    with pytest.raises(BizError) as excinfo:
        submit(store, PHONE, tid, a_new)
    assert excinfo.value.code == "ALREADY_SUBMITTED"

    # B 通过**旧主批次**链接完成 → 新批次副本双向同步 + 待办清零
    submit(store, SECOND, tid, b_main, qty="5")
    assert store.get_instance(b_new["id"])["status"] == "approved"
    assert todos_of(store, SECOND, tid) == []

    overview = stats_overview(owner, tid, "2026-W47")
    assert overview["kpis"]["total"] == 2 and overview["kpis"]["done"] == 2, overview["kpis"]


# ── ⑤ 周三当天截止：周四手推全员闭口、过周日夜进下周重开 ─────────────
def test_5_wed_deadline_closed_after_cutoff_reopens_next_week(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    # 仅周三执行 + 当天 18:00 截止 → 周容器，锚点=本周三
    tid = make_form(owner, role_id, name="周三截止五", exec_weekdays=[3],
                    deadline_type="same_day", deadline_time="18:00")

    at(store, scheduler, datetime(2026, 11, 18, 9, 0, 10))  # 周三
    scheduler.run_once(store.now())
    b_main = inst_of(store, tid, SECOND, "2026-W47")
    submit(store, PHONE, tid, inst_of(store, tid, PHONE, "2026-W47"), qty="6")

    later = at(store, scheduler, datetime(2026, 11, 19, 16, 0, 0))  # 周四手推（截止已过）
    assert push_once(owner, tid).json()["needs_confirm"] is True
    done = push_once(owner, tid, confirmed=True)
    body = done.json()
    assert body["period_key"] == "2026-W47#" + later.strftime("%Y%m%d%H%M%S"), body
    # 周期锚点截止（不是周四顺延）→ 全员闭口
    assert body["deadline_at"] == "2026-11-18 18:00:00"

    a_new = inst_of(store, tid, PHONE, body["period_key"])
    b_new = inst_of(store, tid, SECOND, body["period_key"])
    assert a_new["status"] == "approved"  # 已完成：闭口展示完成态
    assert store.get_instance(b_new["id"])["status"] == "overdue"  # 未完成：闭口为已截止
    # 全员照推：已完成显示已完成、未完成显示已截止
    a_last = [m.text for m in notifier.sent_messages if m.receive_id == "test_recipient"][-1]
    b_last = [m.text for m in notifier.sent_messages if m.receive_id == f"union-{SECOND}"][-1]
    assert "已在本周期完成" in a_last
    assert "任务已截止" in b_last
    # B 通道关闭：无法提交；待办改为「已逾期」提醒（2026-11-21 需求三：不再直接隐藏，
# 成员点「我知道了」后才移除）
    with pytest.raises(BizError) as excinfo:
        submit(store, SECOND, tid, b_new)
    assert excinfo.value.code == "SUBMISSION_CLOSED"
    overdue_todo = todos_of(store, SECOND, tid)
    assert len(overdue_todo) == 1 and overdue_todo[0]["overdue"] is True, overdue_todo
    from app.portal import PortalService
    PortalService(store).dismiss_overdue({"phone": SECOND, "platform": "zdxlz"}, tid, "2026-W47")
    assert todos_of(store, SECOND, tid) == []
    overview = stats_overview(owner, tid, "2026-W47")
    assert overview["kpis"]["total"] == 2 and overview["kpis"]["done"] == 1, overview["kpis"]

    # 过周日 24:00 → 下周一凌晨手推进 2026-W48 新期，重新开放至下周三 18:00
    at(store, scheduler, datetime(2026, 11, 23, 0, 30, 0))
    reopened = push_once(owner, tid)
    assert reopened.status_code == 200 and reopened.json()["queued"] is True, reopened.text
    assert reopened.json()["period_key"] == "2026-W48", reopened.json()
    assert reopened.json()["deadline_at"] == "2026-11-25 18:00:00"
    b_next = inst_of(store, tid, SECOND, "2026-W48")
    assert [t["id"] for t in todos_of(store, SECOND, tid)] == [b_next["id"]]
    submit(store, SECOND, tid, b_next, qty="7")  # 新周期正常提交
    assert store.get_instance(b_next["id"])["status"] == "approved"
    # 上周期闭口行不被新周期完成影响（逾期超 72h 后由 advance 归档为 expired）
    assert store.get_instance(b_new["id"])["status"] == "expired"

    # 下周三 09:00 定时链：同 W48 周期不建新行，只提醒未完成的 A
    at(store, scheduler, datetime(2026, 11, 25, 9, 0, 10))
    scheduler.run_once(store.now())
    assert len(store.list_instances_for_period(tid, "2026-W48")) == 2
    texts = [m.text for m in notifier.sent_messages
             if m.text.startswith("【任务提醒】周三截止五")]
    # 周三定时 2 + 周四重推 2 + 周一凌晨手推 2 + 下周三定时 1（只催未完成的 A）
    assert len(texts) == 7
    assert notifier.sent_messages[-1].receive_id == "test_recipient"


# ── ⑥ 月任务多执行日与跨月 ───────────────────────────────────────────
def test_6_month_multi_exec_and_cross_month(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    # 每月 5 号 + 20 号执行、月底 23:00 截止 → 月容器
    tid = make_form(owner, role_id, name="月任务六", exec_dom_days=[5, 20],
                    deadline_type="month_end", deadline_time="23:00")

    at(store, scheduler, datetime(2026, 11, 5, 9, 0, 10))
    scheduler.run_once(store.now())
    rows = store.list_instances_for_period(tid, "2026-11")
    assert len(rows) == 2 and {r["deadline_at"] for r in rows} == {"2026-11-30 23:00:00"}

    submit(store, PHONE, tid, inst_of(store, tid, PHONE, "2026-11"), qty="8")
    at(store, scheduler, datetime(2026, 11, 20, 9, 0, 10))  # 第二执行日：只催 B
    scheduler.run_once(store.now())
    assert len(store.list_instances_for_period(tid, "2026-11")) == 2
    assert len(reminder_texts(notifier, "月任务六")) == 3

    # 月内手推 1 次 → 新批次；统计按月聚合按人去重
    later = at(store, scheduler, datetime(2026, 11, 21, 10, 0, 0))
    done = push_once(owner, tid, confirmed=True)
    assert done.json()["period_key"] == "2026-11#" + later.strftime("%Y%m%d%H%M%S"), done.json()
    b_new = inst_of(store, tid, SECOND, done.json()["period_key"])
    assert len(todos_of(store, SECOND, tid)) == 1  # 待办去重
    overview = stats_overview(owner, tid, "2026-11")
    assert overview["kpis"]["total"] == 2 and overview["kpis"]["done"] == 1, overview["kpis"]

    # 跨月：12 月 5 日定时 → 2026-12 新周期全新开放
    at(store, scheduler, datetime(2026, 12, 5, 9, 0, 10))
    scheduler.run_once(store.now())
    rows_dec = store.list_instances_for_period(tid, "2026-12")
    assert len(rows_dec) == 2 and {r["status"] for r in rows_dec} == {"pending"}
    assert {r["deadline_at"] for r in rows_dec} == {"2026-12-31 23:00:00"}
    b_dec = inst_of(store, tid, SECOND, "2026-12")
    assert [t["id"] for t in todos_of(store, SECOND, tid)] == [b_dec["id"]]
    overview = stats_overview(owner, tid, "2026-12")
    assert overview["kpis"]["total"] == 2 and overview["kpis"]["done"] == 0, overview["kpis"]


# ── ⑦ 一次性任务反复重推 + 3 秒防抖 + 主批次复活 ─────────────────────
def test_7_manual_once_multi_repush_cooldown_and_revive(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    tid = make_form(owner, role_id, name="一次任务七", is_manual=True,
                    manual_deadline_at="2026-11-30 18:00:00")

    at(store, scheduler, datetime(2026, 11, 17, 9, 0, 0))
    first = push_once(owner, tid)
    assert first.json()["period_key"] == "once" and first.json()["instance_count"] == 2
    submit(store, PHONE, tid, inst_of(store, tid, PHONE, "once"), qty="9")

    # 3 秒防抖：1 秒内连点确认 → 429；第 4 秒放行
    at(store, scheduler, datetime(2026, 11, 17, 9, 0, 1))
    assert push_once(owner, tid).json()["needs_confirm"] is True  # 预检不受冷却影响
    blocked = push_once(owner, tid, confirmed=True)
    assert blocked.status_code == 429 and blocked.json()["code"] == "PUSH_COOLDOWN", blocked.text
    later2 = at(store, scheduler, datetime(2026, 11, 17, 9, 0, 4))
    second = push_once(owner, tid, confirmed=True)
    assert second.status_code == 200, second.text
    assert second.json()["period_key"] == "once#" + later2.strftime("%Y%m%d%H%M%S")

    later3 = at(store, scheduler, datetime(2026, 11, 17, 9, 0, 9))
    third = push_once(owner, tid, confirmed=True)
    assert third.json()["period_key"] == "once#" + later3.strftime("%Y%m%d%H%M%S")

    # 三批并存：A 副本全部同步为已完成；B 的待办只显示最新 1 条
    for pk in ("once#" + later2.strftime("%Y%m%d%H%M%S"), "once#" + later3.strftime("%Y%m%d%H%M%S")):
        assert inst_of(store, tid, PHONE, pk)["status"] == "approved"
    b_latest = inst_of(store, tid, SECOND, third.json()["period_key"])
    assert len(todos_of(store, SECOND, tid)) == 1
    assert todos_of(store, SECOND, tid)[0]["id"] == b_latest["id"]

    # 主批次整批取消（模拟成员资格变动）→ 重推直接复活 canonical 键
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE task_instance SET status='cancelled' WHERE template_id=? AND period_key='once'",
            (tid,))
    at(store, scheduler, datetime(2026, 11, 17, 9, 0, 15))
    revived = push_once(owner, tid, confirmed=True)
    assert revived.status_code == 200, revived.text
    assert revived.json()["period_key"] == "once", revived.json()  # 不带 # 批次后缀
    a_once = inst_of(store, tid, PHONE, "once")
    b_once = inst_of(store, tid, SECOND, "once")
    assert b_once["status"] == "pending"  # 未完成行复活重新开放
    # A 在本周期已有完成记录（once#… 批次）→ 复活副本立即同步完成态（含提交时间）
    assert a_once["status"] == "approved" and a_once["submitted_at"]
    # 批次副本不受复活影响
    assert inst_of(store, tid, PHONE, "once#" + later2.strftime("%Y%m%d%H%M%S"))["status"] == "approved"


# ── ⑧ 统计面板按周期按人去重 ─────────────────────────────────────────
def test_8_stats_panel_dedup_by_person_across_batches(ctx):
    store, scheduler, notifier, _, owner, _, m_tpl, _ = ctx
    role_id, owner_id = m_tpl["role_ids"][0], m_tpl["owner_id"]
    add_member(store, role_id, SECOND, owner_id)
    tid = make_form(owner, role_id, name="统计八", is_manual=True,
                    manual_deadline_at="2026-11-30 18:00:00")

    at(store, scheduler, datetime(2026, 11, 17, 9, 0, 0))
    push_once(owner, tid)
    submit(store, PHONE, tid, inst_of(store, tid, PHONE, "once"), qty="10")
    for sec in (4, 9, 14):  # 连续重推 3 批（越过 3 秒冷却）
        at(store, scheduler, datetime(2026, 11, 17, 9, 0, sec))
        push_once(owner, tid, confirmed=True)

    # once 族共 4 批 8 行实例，但统计只按人算：total=2、done=1
    rows_all = store.conn.execute(
        "SELECT COUNT(1) AS c FROM task_instance WHERE template_id=?", (tid,)).fetchone()
    assert int(rows_all["c"]) == 8
    overview = stats_overview(owner, tid, "once")
    assert overview["kpis"]["total"] == 2, overview["kpis"]
    assert overview["kpis"]["done"] == 1 and overview["kpis"]["not_done"] == 1, overview["kpis"]
    roster = stats_roster(owner, tid, "once")
    assert roster["total"] == 2
    done_row = next(r for r in roster["items"] if r["phone"] == PHONE)
    assert done_row["status"] == "approved" and "10" in str(done_row["content"])
    pending_row = next(r for r in roster["items"] if r["phone"] == SECOND)
    assert pending_row["status"] == "pending"
    # 导出装配同口径（按人 2 行）；HTTP 导出 200
    bundle = store.stats_panel_export_rows(tid, "once", owner_id)
    assert len(bundle["rows"]) == 2, [r["phone"] for r in bundle["rows"]]
    exported = owner.get("/api/admin/stats-panel/export",
                         params={"template_id": tid, "period_key": "once"})
    assert exported.status_code == 200
    # 批次端点退化为「归属周期」聚合：once 一行 8 实例全 alive
    batches = owner.get("/api/admin/stats-panel/batches", params={"template_id": tid}).json()
    assert [item["period_key"] for item in batches["items"]] == ["once"]
    assert batches["items"][0]["instance_count"] == 8
    assert batches["items"][0]["alive_count"] == 8
