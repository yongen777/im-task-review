# -*- coding: utf-8 -*-
"""阶段 3（2026-10-27 春促按角色提醒）：业务数据变量注入 + 数据快照 + attach_image 看板图片投递。

覆盖：
1. render_text_tpl 的 extra_vars（注入成功 / 未知变量原样保留 / 缺失数据不替换）；
2. StateStore 数据快照 upsert / get / 幂等覆盖；
3. 模板校验：attach_image=1 强制要 data_keys，data_keys.key 仅字母数字下划线、去重；
4. 调度端到端：notice 文本 + notice_img 图片两条投递、无快照/无 image_sender/未激活成员的分枝。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.channels.zdxlz import SendResult
from app.config import SHANGHAI_TZ
from app.scheduler import FakeNotifier, Scheduler, render_text_tpl
from app.state import BizError, StateStore


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp(prefix="imtask_t3_")
    return StateStore(Path(tmp) / "im_task.db")


def _mk_scheduler(store: StateStore, notifier=None, image_sender=None) -> Scheduler:
    # 时钟定在 08:05（notice_times=08:00 已触发、且在 30 分钟补偿窗口内）
    return Scheduler(
        state=store,
        public_base_url="http://127.0.0.1:18090",
        notifier=notifier or FakeNotifier(),
        now_fn=lambda: datetime(2026, 10, 28, 8, 5, 0, tzinfo=SHANGHAI_TZ),
        image_sender=image_sender,
    )


class FakeImageSender:
    """内存替身图片发送器：记录 prepare/send 明细供断言。"""

    def __init__(self, fail_phones: set[str] | None = None) -> None:
        self.prepared: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.fail_phones = fail_phones or set()

    def prepare_image(self, file_path: str):
        self.prepared.append(file_path)
        return {"file_key": "fk_test_001", "width": 540, "height": 960, "mime_type": "png"}

    def send_image_prepared(self, receive_id: str, prepared, idem_key: str) -> SendResult:
        self.sent.append((receive_id, prepared["file_key"]))
        return SendResult(True, "sent", 200)


def _setup_notice_tpl(store: StateStore, *, attach_image: bool = True) -> str:
    role = store.create_role(name="片长/分支局长", note="t3")
    store.add_role_member(role["role_id"], phone="13800000001", name="张三", dept="一支局")
    tpl = store.create_template(
        {
            "name": "春促·测试模板 08:00",
            "kind": "notice",
            "role_ids": [role["role_id"]],
            "text_tpl": "今日完成率 {completion_rate}%，意向单 {intent_count} 单。祝工作顺利 {unknown_var}！",
            "notice_times": ["08:00"],
            "period": "day",
            "attach_image": attach_image,
            "data_keys": [
                {"key": "completion_rate", "label": "1+1155完成率", "unit": "%"},
                {"key": "intent_count", "label": "意向单", "unit": "单"},
            ],
            "enabled": True,
        },
        actor="t3",
    )
    return tpl["template_id"]


# ── 1. render_text_tpl extra_vars ──────────────────────────────────────────────

def test_render_extra_vars_injects_known_and_keeps_unknown():
    out = render_text_tpl(
        "完成率 {completion_rate}%，{姓名}你好，占位 {nope}",
        member_name="张三", member_dept="一支局", template_name="T",
        extra_vars={"completion_rate": "86.5"},
    )
    assert "86.5%" in out
    assert "张三" in out
    assert "{nope}" in out  # 未知变量原样保留


def test_render_extra_vars_missing_value_keeps_placeholder():
    out = render_text_tpl(
        "{completion_rate} 与 {intent_count}",
        member_name="张三", member_dept=None, template_name="T",
        extra_vars={"completion_rate": "86.5"},  # intent_count 缺失
    )
    assert "86.5" in out
    assert "{intent_count}" in out


# ── 2. 数据快照 ────────────────────────────────────────────────────────────────

def test_data_snapshot_upsert_get_roundtrip():
    store = _mk_store()
    snap = store.upsert_data_snapshot("t_x", "2026-10-28", {"completion_rate": "86.5"}, note="晨会前", updated_by="tester")
    assert snap["values"]["completion_rate"] == "86.5"
    again = store.upsert_data_snapshot("t_x", "2026-10-28", {"completion_rate": "91.2"}, updated_by="tester")
    assert again["values"]["completion_rate"] == "91.2"  # 幂等覆盖
    assert store.get_data_snapshot_values("t_x", "2026-10-28") == {"completion_rate": "91.2"}
    assert store.get_data_snapshot_values("t_x", "2026-10-27") == {}  # 无记录返回空


def test_data_snapshot_rejects_bad_key_or_empty():
    store = _mk_store()
    with pytest.raises(BizError):
        store.upsert_data_snapshot("t_x", "2026-10-28", {})  # 空 values
    with pytest.raises(BizError):
        store.upsert_data_snapshot("t_x", "2026-10-28", {"非法key": "1"})  # key 含中文


# ── 3. 模板校验 ────────────────────────────────────────────────────────────────

def test_template_attach_image_requires_data_keys():
    store = _mk_store()
    role = store.create_role(name="r1", note="t3")
    with pytest.raises(BizError):
        store.create_template(
            {
                "name": "坏模板", "kind": "notice", "role_ids": [role["role_id"]],
                "text_tpl": "x", "notice_times": ["08:00"], "period": "day",
                "attach_image": True, "data_keys": [],
            }
        )


def test_template_data_keys_dedup_and_format():
    store = _mk_store()
    role = store.create_role(name="r1", note="t3")
    with pytest.raises(BizError):
        store.create_template(
            {
                "name": "重复key", "kind": "notice", "role_ids": [role["role_id"]],
                "text_tpl": "x", "notice_times": ["08:00"], "period": "day",
                "attach_image": True,
                "data_keys": [
                    {"key": "a", "label": "A"},
                    {"key": "a", "label": "A2"},
                ],
            }
        )
    with pytest.raises(BizError):
        store.create_template(
            {
                "name": "非法key", "kind": "notice", "role_ids": [role["role_id"]],
                "text_tpl": "x", "notice_times": ["08:00"], "period": "day",
                "attach_image": True,
                "data_keys": [{"key": "完成率", "label": "完成率"}],
            }
        )


# ── 4. 调度端到端：notice 文本 + notice_img 图片 ────────────────────────────────

def test_notice_with_image_delivers_text_and_image():
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=True)
    store.upsert_delivery_target("13800000001", "ext_user_001")  # 激活成员（模拟已发过消息建立会话）
    store.upsert_data_snapshot(tpl_id, "2026-10-28", {"completion_rate": "86.5", "intent_count": "45"})

    notifier = FakeNotifier()
    img_sender = FakeImageSender()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=img_sender)
    stats = scheduler.run_once()

    assert stats["sent"] == 2  # 1 条文本 + 1 条图片
    assert len(notifier.sent_messages) == 1
    assert "86.5%" in notifier.sent_messages[0].text
    assert "45" in notifier.sent_messages[0].text
    assert len(img_sender.sent) == 1  # 图片发给 1 名成员

    kinds = [r["kind"] for r in store.list_reminder_logs(tpl_id)]
    assert "notice" in kinds and "notice_img" in kinds


def test_notice_without_snapshot_skips_image():
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=True)  # 不填快照
    store.upsert_delivery_target("13800000001", "ext_user_001")

    notifier = FakeNotifier()
    img_sender = FakeImageSender()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=img_sender)
    stats = scheduler.run_once()

    assert len(notifier.sent_messages) == 1  # 文本照发
    assert len(img_sender.sent) == 0  # 无数据不发图
    kinds = [r["kind"] for r in store.list_reminder_logs(tpl_id)]
    assert "notice_img" not in kinds


def test_notice_image_sender_none_text_only():
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=True)
    store.upsert_delivery_target("13800000001", "ext_user_001")
    store.upsert_data_snapshot(tpl_id, "2026-10-28", {"completion_rate": "86.5"})

    notifier = FakeNotifier()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=None)
    stats = scheduler.run_once()

    assert stats["sent"] == 1  # 仅文本
    kinds = [r["kind"] for r in store.list_reminder_logs(tpl_id)]
    assert kinds == ["notice"]


def test_notice_unactivated_member_skipped_and_image_skipped():
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=True)
    store.upsert_data_snapshot(tpl_id, "2026-10-28", {"completion_rate": "86.5"})

    notifier = FakeNotifier()
    img_sender = FakeImageSender()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=img_sender)
    stats = scheduler.run_once()

    # 成员未激活（无 delivery_target）-> 文本 skipped，图片直接跳过不记录
    assert stats["skipped"] == 1
    assert stats["sent"] == 0
    assert len(img_sender.sent) == 0


# ── 5. 2026-10-28 方案A：notice 也生成任务实例（pending → sent） ──────────────

PKEY = "2026-10-28"


def _scheduler_at(store: StateStore, hour: int, minute: int, *, notifier=None, image_sender=None) -> Scheduler:
    return Scheduler(
        state=store,
        public_base_url="http://127.0.0.1:18090",
        notifier=notifier or FakeNotifier(),
        now_fn=lambda: datetime(2026, 10, 28, hour, minute, 0, tzinfo=SHANGHAI_TZ),
        image_sender=image_sender,
    )


def test_notice_creates_instance_and_marks_sent():
    """notice 模板到点后为每个成员生成 1 条实例，发送成功即标记 sent（且幂等）。"""
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=False)
    store.upsert_delivery_target("13800000001", "ext_user_001")

    notifier = FakeNotifier()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=None)  # 08:05，08:00 已到点
    stats = scheduler.run_once()

    assert stats["sent"] == 1
    instances = store.list_instances_for_period(tpl_id, PKEY)
    assert len(instances) == 1
    assert instances[0]["phone"] == "13800000001"
    assert instances[0]["status"] == "sent"
    assert instances[0]["name_snap"] == "张三"
    # 通知没有截止概念：deadline 用当天 23:59 占位
    assert instances[0]["deadline_at"].startswith("2026-10-28 23:59")

    # 幂等：同一时刻再跑一轮，不重复建实例、不重复发
    stats2 = scheduler.run_once()
    assert stats2["sent"] == 0
    assert len(store.list_instances_for_period(tpl_id, PKEY)) == 1
    assert len(notifier.sent_messages) == 1


def test_notice_instance_pending_before_trigger_and_disabled_skips():
    """2026-10-28 调整：未到点也按天预生成实例（pending「待提醒」），停用模板则不生成。"""
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=False)
    store.upsert_delivery_target("13800000001", "ext_user_001")

    early = _scheduler_at(store, 7, 0)  # 07:00 早于 08:00
    early.run_once()
    instances = store.list_instances_for_period(tpl_id, PKEY)
    assert len(instances) == 1
    assert instances[0]["status"] == "pending"  # 待提醒，还没发

    # 停用后再调度，不再新增实例（原有那条保持不动）
    store.update_template(tpl_id, {"enabled": False}, actor="t3")
    _scheduler_at(store, 8, 5).run_once()
    assert len(store.list_instances_for_period(tpl_id, PKEY)) == 1


def test_disabled_notice_template_creates_no_instance():
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=False)
    store.upsert_delivery_target("13800000001", "ext_user_001")
    store.update_template(tpl_id, {"enabled": False}, actor="t3")

    _scheduler_at(store, 8, 5).run_once()
    assert store.list_instances_for_period(tpl_id, PKEY) == []


def test_notice_instance_sent_with_image():
    """带数据看板图片的 notice：文本 + 图片都发出，实例仍然标记 sent。"""
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=True)
    store.upsert_delivery_target("13800000001", "ext_user_001")
    store.upsert_data_snapshot(tpl_id, PKEY, {"completion_rate": "86.5", "intent_count": "45"})

    notifier = FakeNotifier()
    img_sender = FakeImageSender()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=img_sender)
    stats = scheduler.run_once()

    assert stats["sent"] == 2  # 文本 + 图片
    instances = store.list_instances_for_period(tpl_id, PKEY)
    assert len(instances) == 1
    assert instances[0]["status"] == "sent"
    assert len(img_sender.sent) == 1


def test_notice_instance_stays_pending_for_unactivated_member():
    """未绑定成员的实例要建出来，但状态保持 pending（发送记录为 skipped）。"""
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=False)

    notifier = FakeNotifier()
    scheduler = _mk_scheduler(store, notifier=notifier, image_sender=None)
    stats = scheduler.run_once()

    assert stats["skipped"] == 1
    instances = store.list_instances_for_period(tpl_id, PKEY)
    assert len(instances) == 1
    assert instances[0]["status"] == "pending"
    assert [r["result"] for r in store.list_reminder_logs(tpl_id)] == ["skipped"]


def test_notice_instance_not_advanced_to_overdue():
    """通知实例不参与 overdue / expired 流转（过了 23:59 也不会变已逾期）。"""
    store = _mk_store()
    tpl_id = _setup_notice_tpl(store, attach_image=False)
    store.upsert_delivery_target("13800000001", "ext_user_001")
    _mk_scheduler(store).run_once()

    late = _scheduler_at(store, 23, 59)  # 当天最后一分钟，deadline_at 已过
    late.state.advance_instance_statuses(datetime(2026, 10, 29, 0, 30, tzinfo=SHANGHAI_TZ))
    instances = store.list_instances_for_period(tpl_id, PKEY)
    assert instances[0]["status"] == "sent"



