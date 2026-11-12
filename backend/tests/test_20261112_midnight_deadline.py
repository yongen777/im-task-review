# -*- coding: utf-8 -*-
"""2026-11-12 下午 · 截止 00:00 = 当天 24 点（次日 0 点）专项用例（图三报错修复）。

用户拍板：每月 12 号 14:00 提醒、截止 00:00 的模板应能创建——00:00 是「当天 24 点」，
不是「当天 0 点」（旧实现按当天 0 点算 → remind_at>=deadline 400 / 实例即时过期）。

覆盖三层：
  ① 纯函数：calc_instance_deadline 三种 deadline_type + 旧算法 calc_deadline_at 的 00:00 都 +1 天；
  ② 创建校验：same_day + 00:00 豁免 remind_at<deadline 顺序校验（18:00 等其它时刻仍拦截）；
  ③ 端到端窗口：00:00 截止实例当天可提交，次日 0 点后关窗且文案带次日时间戳。

全部相对时刻（模块级 CLOCK），不碰 data/im_task.db。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scheduler import calc_deadline_at, calc_instance_deadline  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

PHONE = "13900001001"
CLOCK = {"now": datetime(2026, 11, 12, 14, 0, 0)}  # 2026-11-12 周四 14:00


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_mid_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _seed(store: StateStore) -> str:
    rid = store.create_role("午夜组", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    store.upsert_delivery_target(PHONE, f"uid_zdxlz_{PHONE[-4:]}", platform="zdxlz")
    return rid


def _tpl(store: StateStore, rid: str, *, deadline_time: str = "00:00", remind_at: str = "14:00",
         deadline_type: str = "same_day", period: str = "month", anchor_dom: int = 12,
         legacy: bool = False) -> dict:
    payload = {
        "name": "TA使用统计", "kind": "form", "role_ids": [rid], "period": period,
        "anchor_dom": anchor_dom if period == "month" else None,
        "remind_at": remind_at, "text_tpl": "请提交", "need_review": False, "enabled": True,
        "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
        "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        "has_deadline": True, "deadline_type": deadline_type, "deadline_time": deadline_time,
    }
    if legacy:
        payload.pop("deadline_type")
        payload.pop("deadline_time")
        payload["deadline"] = deadline_time
    return store.create_template(payload, actor="t")


# ════════════════════ ① 纯函数 ════════════════════

def test_m1_calc_same_day_midnight_is_next_day() -> None:
    """same_day + 00:00 → 次日 0 点（当天 24 点）；其它时刻不变。"""
    anchor = datetime(2026, 11, 12, 14, 0, 0)
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "same_day", "deadline_time": "00:00"}, anchor
    ) == "2026-11-13 00:00:00"
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "same_day", "deadline_time": "18:00"}, anchor
    ) == "2026-11-12 18:00:00"


def test_m1_calc_weekend_and_month_end_midnight() -> None:
    """weekend + 00:00 → 周日的 24 点（周一 0 点）；month_end + 00:00 → 次月 1 号 0 点。"""
    anchor = datetime(2026, 11, 12, 14, 0, 0)  # 周四；本周日 = 11-15；11 月底 = 11-30
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "weekend", "deadline_time": "00:00"}, anchor
    ) == "2026-11-16 00:00:00"
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "weekend", "deadline_time": "20:30"}, anchor
    ) == "2026-11-15 20:30:00"
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "month_end", "deadline_time": "00:00"}, anchor
    ) == "2026-12-01 00:00:00"
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "month_end", "deadline_time": "23:59"}, anchor
    ) == "2026-11-30 23:59:00"


def test_m1_legacy_deadline_midnight_is_next_day() -> None:
    """旧模板回落（deadline_type 空，旧列 deadline=00:00）同样 +1 天；offset 组合正确。"""
    anchor = datetime(2026, 11, 12, 14, 0, 0)
    assert calc_instance_deadline(
        {"has_deadline": None, "deadline_type": None, "deadline": "00:00",
         "deadline_offset": 0, "period": "day"}, anchor
    ) == "2026-11-13 00:00:00"
    assert calc_instance_deadline(
        {"has_deadline": None, "deadline_type": None, "deadline": "00:00",
         "deadline_offset": 3, "period": "week"}, anchor
    ) == "2026-11-16 00:00:00"
    assert calc_deadline_at("2026-11-12", "18:00") == "2026-11-12 18:00:00"  # 非 00:00 不动


# ════════════════════ ② 创建校验 ════════════════════

def test_m2_same_day_midnight_template_creates() -> None:
    """图三场景：每月 12 号、执行 14:00、截止 00:00 → 必须创建成功。"""
    store = _store()
    try:
        rid = _seed(store)
        tpl = _tpl(store, rid)
        assert tpl["deadline"] == "00:00" and tpl["deadline_time"] == "00:00"
        # 0 点提醒 + 0 点截止 也合法（0 点提醒 < 次日 0 点截止）
        tpl2 = _tpl(store, rid, remind_at="00:00")
        assert tpl2["remind_at"] == "00:00"
    finally:
        store.close()


def test_m2_non_midnight_ordering_still_blocked() -> None:
    """非 00:00 的顺序矛盾仍拦截（remind 19:00 > deadline 18:00 → 400）。"""
    store = _store()
    try:
        rid = _seed(store)
        try:
            _tpl(store, rid, deadline_time="18:00", remind_at="19:00")
            raise AssertionError("remind_at >= deadline 仍必须 400")
        except BizError as exc:
            assert exc.status_code == 400 and exc.code == "VALIDATION_FAILED", (exc.status_code, exc.code)
    finally:
        store.close()


# ════════════════════ ③ 端到端窗口 ════════════════════

def test_m3_midnight_deadline_window_e2e() -> None:
    """每月 12 号实例：截止 = 11-13 00:00（12 号 24 点）——
    当天 14:00 可正常提交；未交者跨过次日 0 点流转 overdue，提交被关窗
    且文案带 2026-11-13 00:00:00。"""
    store = _store()
    try:
        rid = store.create_role("午夜组", actor="t")["role_id"]
        store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
        store.upsert_delivery_target(PHONE, f"uid_zdxlz_{PHONE[-4:]}", platform="zdxlz")
        phone2 = "13900002002"
        store.add_role_member(rid, phone2, "李四", "市场部", actor="t")
        store.upsert_delivery_target(phone2, f"uid_zdxlz_{phone2[-4:]}", platform="zdxlz")
        tpl = _tpl(store, rid)
        insts = store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-13 00:00:00")
        by_phone = {m["phone"]: m for m in insts}
        assert set(by_phone) == {PHONE, phone2}
        assert by_phone[PHONE]["deadline_at"] == "2026-11-13 00:00:00", by_phone[PHONE]

        raw = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", by_phone[PHONE]["id"])
        # 当天 14:00 提交 → 成功（旧实现此刻已过期；need_review=False 直接 approved）
        res = store.submit_h5_task(raw_token=raw, fields={"summary": "当天提交"})
        assert res["status"] == "approved", res

        # 次日 0 点后：未交者流转 overdue，提交关窗且文案带次日时间戳
        CLOCK["now"] = datetime(2026, 11, 13, 0, 0, 30)
        store.advance_instance_statuses(CLOCK["now"])
        inst = store.get_instance(by_phone[phone2]["id"])
        assert inst["status"] == "overdue", inst
        raw2 = store.issue_h5_token(phone2, tpl["template_id"], "2026-11-12", by_phone[phone2]["id"])
        try:
            store.submit_h5_task(raw_token=raw2, fields={"summary": "过期还想交"})
            raise AssertionError("跨过 0 点后必须拒绝提交")
        except BizError as exc:
            assert exc.status_code == 400 and exc.code == "SUBMISSION_CLOSED", (exc.status_code, exc.code)
            assert exc.message == "当前任务已过期（截止时间：2026-11-13 00:00:00），无法提交", exc.message
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 14, 0, 0)
        store.close()
