# -*- coding: utf-8 -*-
"""2026-10-29 执行日（exec_weekdays）：day 周期模板可以只在指定周几产生业务。

口径：
- 非执行日 = 没业务：不建实例、不发提醒、无逾期无补交（只在调度循环顶部过滤，⛔ 不在下游补判断）
- 仅 period="day" 生效；week/month 已有锚点，传 exec_weekdays 直接 400
- 未配置 / 配置为空或坏 → **全周**（fail-open：宁可多催一天，不许业务静默失联）
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pytest

from app.config import SHANGHAI_TZ, format_dt
from app.scheduler import FakeNotifier, Scheduler
from app.state import BizError, StateStore

PHONE = "13900009101"


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp()
    return StateStore(Path(tmp) / "im.db")


def _dt(day: int = 29, hour: int = 9, minute: int = 0, month: int = 10) -> datetime:
    # 2026-10-29 是周四（isoweekday=4）；2026-10-31 是周六（6）；2026-11-02 是周一（1）
    return datetime(2026, month, day, hour, minute, 0)


def _base_payload(rid: str, *, kind: str = "notice", **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": f"执行日测试-{kind}", "kind": kind, "role_ids": [rid],
        "text_tpl": "今天要做的事", "remind_at": "08:15", "period": "day",
    }
    if kind == "notice":
        payload["notice_times"] = ["08:15"]
    else:
        payload["deadline"] = "18:00"
        payload["schema_json"] = [{"key": "summary", "label": "摘要", "type": "text"}]
    payload.update(extra)
    return payload


def _role(store: StateStore) -> str:
    rid = store.create_role("执行日组", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    store.upsert_delivery_target(PHONE, "uid_ew")
    return rid


# ── 校验侧 ────────────────────────────────────────────────────────────
def test_empty_exec_weekdays_rejected() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        with pytest.raises(BizError) as err:
            store.create_template(_base_payload(rid, exec_weekdays=[]), actor="t")
        assert "至少选择一天" in err.value.message
    finally:
        store.close()


@pytest.mark.parametrize("bad", [[0], [8], ["x"], [1, 9]])
def test_invalid_exec_weekdays_rejected(bad: list) -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        with pytest.raises(BizError) as err:
            store.create_template(_base_payload(rid, exec_weekdays=bad), actor="t")
        assert "1~7" in err.value.message
    finally:
        store.close()


def test_week_template_cannot_have_exec_weekdays() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        # 通知类模板 period 被强制为 day，所以这里用 form 类构造周模板
        payload = _base_payload(rid, kind="form", exec_weekdays=[1, 2])
        payload.update({"period": "week", "anchor_weekday": 1, "deadline_offset": 0})
        with pytest.raises(BizError) as err:
            store.create_template(payload, actor="t")
        assert "仅每日周期支持执行日" in err.value.message
    finally:
        store.close()


def test_update_without_field_keeps_original() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid, exec_weekdays=[2, 4]), actor="t")
        assert tpl["exec_weekdays"] == [2, 4]

        # 只改名字，不带 exec_weekdays → 保留原值（部分更新语义）
        store.update_template(tpl["template_id"], {"name": "改个名字"}, actor="t")
        assert store.get_template(tpl["template_id"])["exec_weekdays"] == [2, 4]

        # 显式传新值 → 覆盖
        store.update_template(tpl["template_id"], {"exec_weekdays": [1, 3, 5]}, actor="t")
        assert store.get_template(tpl["template_id"])["exec_weekdays"] == [1, 3, 5]
    finally:
        store.close()


def test_legacy_template_without_field_is_all_week() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid), actor="t")
        assert tpl["exec_weekdays"] is None
        # 老数据（列里是 NULL）同样读成 None，调度层按全周处理
        store.conn.execute("UPDATE task_template SET exec_weekdays = NULL WHERE template_id = ?", (tpl["template_id"],))
        store.conn.commit()
        assert store.get_template(tpl["template_id"])["exec_weekdays"] is None
    finally:
        store.close()


# ── 调度侧（周六 2026-10-31）───────────────────────────────────────────
def test_notice_skipped_on_non_exec_day_without_polluting_logs() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid, exec_weekdays=[1, 2, 3, 4, 5]), actor="t")
        notifier = FakeNotifier()
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=notifier,
                          now_fn=lambda: _dt(31, 8, 20))  # 周六，已过 08:15
        stats = sched.run_once()

        assert stats["generated_instances"] == 0
        assert stats["sent"] == 0
        assert notifier.sent_messages == []
        assert store.list_instances_for_period(tpl["template_id"], "2026-10-31") == []
        # ⛔ reminder_log 零新增（不允许出现非执行日的 missed 行）
        assert store.list_reminder_logs(tpl["template_id"]) == []
    finally:
        store.close()


def test_upload_skipped_on_non_exec_day() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid, kind="form", exec_weekdays=[1, 2, 3, 4, 5]), actor="t")
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=FakeNotifier(),
                          now_fn=lambda: _dt(31, 9, 0))
        stats = sched.run_once()
        assert stats["generated_instances"] == 0
        assert store.list_instances_for_period(tpl["template_id"], "2026-10-31") == []
    finally:
        store.close()


def test_without_exec_weekdays_runs_every_day() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid), actor="t")
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=FakeNotifier(),
                          now_fn=lambda: _dt(31, 8, 20))
        stats = sched.run_once()
        assert stats["generated_instances"] >= 1
        assert len(store.list_instances_for_period(tpl["template_id"], "2026-10-31")) == 1
    finally:
        store.close()


def test_exec_day_including_saturday_runs() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid, exec_weekdays=[6]), actor="t")
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=FakeNotifier(),
                          now_fn=lambda: _dt(31, 8, 20))
        stats = sched.run_once()
        assert stats["generated_instances"] >= 1
        assert len(store.list_instances_for_period(tpl["template_id"], "2026-10-31")) == 1
    finally:
        store.close()


def test_week_template_anchor_unaffected() -> None:
    """week 模板走 anchor_weekday，不受 exec_weekdays 影响。"""
    store = _mk_store()
    try:
        rid = _role(store)
        # 2026-11-02 是周一
        payload = _base_payload(rid, kind="form")
        payload.update({"period": "week", "anchor_weekday": 1, "deadline_offset": 0})
        tpl = store.create_template(payload, actor="t")

        sched_mon = Scheduler(state=store, public_base_url="http://e.com", notifier=FakeNotifier(),
                              now_fn=lambda: _dt(2, 7, 0, month=11))  # 2026-11-02 周一 07:00
        sched_mon.run_once()
        from app.scheduler import period_key as _pk

        wk_mon = _pk("week", _dt(2, 7, 0, month=11))
        assert len(store.list_instances_for_period(tpl["template_id"], wk_mon)) == 1

        # 周六不产生周的实例
        sched_sat = Scheduler(state=store, public_base_url="http://e.com", notifier=FakeNotifier(),
                              now_fn=lambda: _dt(31, 7, 0))
        sched_sat.run_once()
        wk_sat = _pk("week", _dt(31, 7, 0))
        assert store.list_instances_for_period(tpl["template_id"], wk_sat) == []
    finally:
        store.close()


def test_exec_weekdays_serialized_as_json_in_row() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = store.create_template(_base_payload(rid, exec_weekdays=[5, 1, 3, 3]), actor="t")
        # 去重 + 升序
        assert tpl["exec_weekdays"] == [1, 3, 5]
        row = store.conn.execute("SELECT exec_weekdays FROM task_template WHERE template_id = ?", (tpl["template_id"],)).fetchone()
        assert json.loads(row["exec_weekdays"]) == [1, 3, 5]
    finally:
        store.close()
