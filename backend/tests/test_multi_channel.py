# -*- coding: utf-8 -*-
"""2026-11-02 多通道并行（用户口径 B：双绑定双发）回归。

锁住：
1. 同一成员在两个平台都有绑定时，每个平台各发 1 条（不是只发优先级最高的一条）；
2. 每通道独立幂等：连跑 3 个 tick 仍各 1 条；
3. zdxlz 幂等键保持旧格式（历史记录继续命中，升级不重发），wecom 键带平台后缀；
4. 单绑定只发该平台；无绑定不发送；
5. 回调绑定显式写 platform，不串通道；
6. 成员列表返回双平台激活状态。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.events import handle_inbound_text  # noqa: E402
from app.channels.zdxlz import SendResult  # noqa: E402
from app.config import SHANGHAI_TZ  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler, make_idem_key  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13800000001"
NAME = "张三"


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp(prefix="imtask_multi_")
    return StateStore(Path(tmp) / "im_task.db")


def _setup(store: StateStore, *, zdxlz: bool, wecom: bool) -> str:
    role = store.create_role(name="多通道路由组")
    store.add_role_member(role["role_id"], phone=PHONE, name=NAME, dept="一支局")
    if zdxlz:
        store.upsert_delivery_target(PHONE, "zdxlz_uid_1", platform="zdxlz")
    if wecom:
        store.upsert_delivery_target(PHONE, "wecom_userid_1", platform="wecom")
    tpl = store.create_template(
        {
            "name": "多通道测试通知",
            "kind": "notice",
            "role_ids": [role["role_id"]],
            "text_tpl": "今日通知测试正文",
            "notice_times": ["08:00"],
            "period": "day",
            "enabled": True,
        },
        actor="t",
    )
    return tpl["template_id"]


def _scheduler(store: StateStore, z: FakeNotifier, w: FakeNotifier) -> Scheduler:
    return Scheduler(
        state=store,
        public_base_url="http://127.0.0.1:18090",
        notifier=z,
        now_fn=lambda: datetime(2026, 11, 2, 8, 5, 0, tzinfo=SHANGHAI_TZ),
        notifiers={"zdxlz": z, "wecom": w},
    )


def test_dual_bound_sends_both_channels_once_per_tick() -> None:
    store = _mk_store()
    try:
        tpl_id = _setup(store, zdxlz=True, wecom=True)
        z, w = FakeNotifier(), FakeNotifier()
        sched = _scheduler(store, z, w)

        for _ in range(3):  # 连跑 3 个 tick
            sched.run_once()

        assert len(z.sent_messages) == 1, [m.text for m in z.sent_messages]
        assert len(w.sent_messages) == 1, [m.text for m in w.sent_messages]
        assert z.sent_messages[0].receive_id == "zdxlz_uid_1"
        assert w.sent_messages[0].receive_id == "wecom_userid_1"

        logs = store.list_reminder_logs(tpl_id)
        notice_logs = [r for r in logs if r["kind"] == "notice"]
        assert len(notice_logs) == 2, notice_logs
        idems = {r["idem_key"] for r in notice_logs}
        assert len(idems) == 2
    finally:
        store.close()


def test_zdxlz_idem_keeps_legacy_format_and_wecom_has_suffix() -> None:
    store = _mk_store()
    try:
        tpl_id = _setup(store, zdxlz=True, wecom=True)
        z, w = FakeNotifier(), FakeNotifier()
        _scheduler(store, z, w).run_once()

        logs = [r for r in store.list_reminder_logs(tpl_id) if r["kind"] == "notice"]
        legacy = make_idem_key(tpl_id, PHONE, "2026-11-02", "notice", 1, "2026-11-02 08:00:00")
        assert any(r["idem_key"] == legacy for r in logs), logs
        assert any(r["idem_key"] == f"{legacy}|wecom" for r in logs), logs
    finally:
        store.close()


def test_single_bound_sends_only_that_channel() -> None:
    store = _mk_store()
    try:
        _setup(store, zdxlz=False, wecom=True)
        z, w = FakeNotifier(), FakeNotifier()
        _scheduler(store, z, w).run_once()
        assert len(z.sent_messages) == 0
        assert len(w.sent_messages) == 1
    finally:
        store.close()


def test_unbound_member_sends_nothing() -> None:
    store = _mk_store()
    try:
        _setup(store, zdxlz=False, wecom=False)
        z, w = FakeNotifier(), FakeNotifier()
        sched = _scheduler(store, z, w)
        sched.run_once()
        assert len(z.sent_messages) == 0 and len(w.sent_messages) == 0
    finally:
        store.close()


def test_inbound_text_binds_on_explicit_platform() -> None:
    store = _mk_store()
    try:
        role = store.create_role(name="绑定组")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        sent: list[tuple] = []

        def _send(text: str, tag: str) -> None:
            sent.append((text, tag))

        code, body = handle_inbound_text(
            state=store, user_id="wecom_uid_bind", msg_text=f"{NAME} {PHONE}",
            send=_send, platform="wecom",
        )
        assert code == 200 and body.get("bound") is True, body
        assert store.get_delivery_target(PHONE, platform="wecom") is not None
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None
    finally:
        store.close()


def test_member_list_returns_per_platform_activations() -> None:
    store = _mk_store()
    try:
        role = store.create_role(name="激活组")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        store.upsert_delivery_target(PHONE, "zdxlz_uid", platform="zdxlz")
        store.upsert_delivery_target(PHONE, "wecom_uid", platform="wecom")

        store.platform = "zdxlz"
        m = store.list_role_members(role["role_id"])["items"][0]
        assert m["activations"] == {"zdxlz": True, "wecom": True}
        assert m["activated"] is True

        store.platform = "wecom"
        m2 = store.list_role_members(role["role_id"])["items"][0]
        assert m2["activated"] is True
    finally:
        store.close()


def test_instance_filter_sql_platform_condition() -> None:
    """后台所选办公软件 -> 只显示该平台角色上的任务实例（2026-11-02 改为按模板角色归属）。"""
    from app.api.admin import _instance_filter_sql

    store = _mk_store()
    try:
        _from, where, params = _instance_filter_sql(store, platform="wecom")
        assert "EXISTS" in where and "r.platform" in where
        assert "wecom" in params
    finally:
        store.close()


def test_pending_list_filters_by_platform() -> None:
    store = _mk_store()
    try:
        store.upsert_pending_member("zdx_uid", last_message="a", platform="zdxlz")
        store.upsert_pending_member("wo_uid", last_message="b", platform="wecom")
        z = store.list_pending_members(platform="zdxlz")
        w = store.list_pending_members(platform="wecom")
        assert [x["external_id"] for x in z] == ["zdx_uid"]
        assert [x["external_id"] for x in w] == ["wo_uid"]
    finally:
        store.close()