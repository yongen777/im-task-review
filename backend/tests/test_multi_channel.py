# -*- coding: utf-8 -*-
"""多通道并行回归（2026-11-03 用户口径，取代 11-02 的「双绑定双发」）。

新口径：**任务属于哪个办公软件，就只往那个办公软件发**。
- 角色按平台隔离（role.platform），模板挂角色 → 任务归属平台 = 角色平台；
- 密信的任务不再串到企微，企微的任务也不串到密信；
- 幂等仍按通道独立（zdxlz 保持旧键格式，历史记录继续命中，升级不重发）。

锁住：
1. 密信任务 + 双绑定成员 → 只发密信 1 条（连跑 3 个 tick 仍 1 条）；
2. 企微任务 + 双绑定成员 → 只发企微 1 条；
3. 企微任务但成员只在密信绑定 → 不发（记 skipped），不串到密信；
4. zdxlz 幂等键保持旧格式（不带平台后缀），wecom 键带 |wecom 后缀；
5. 无绑定不发送；
6. 回调绑定显式写 platform，不串通道；
7. 成员列表返回双平台激活状态。
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
from app.api.h5 import process_h5_bind  # noqa: E402
from app.config import SHANGHAI_TZ  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler, make_idem_key  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13800000001"
NAME = "张三"


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp(prefix="imtask_multi_")
    return StateStore(Path(tmp) / "im_task.db")


def _setup(store: StateStore, *, zdxlz: bool, wecom: bool, tpl_platform: str = "zdxlz") -> str:
    role = store.create_role(name="多通道路由组", platform=tpl_platform)
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


def test_zdxlz_task_delivers_only_zdxlz_multi_tick() -> None:
    store = _mk_store()
    try:
        tpl_id = _setup(store, zdxlz=True, wecom=True, tpl_platform="zdxlz")
        z, w = FakeNotifier(), FakeNotifier()
        sched = _scheduler(store, z, w)

        for _ in range(3):  # 连跑 3 个 tick：只允许 1 条
            sched.run_once()

        assert len(z.sent_messages) == 1, [m.text for m in z.sent_messages]
        assert len(w.sent_messages) == 0, [m.text for m in w.sent_messages]
        assert z.sent_messages[0].receive_id == "zdxlz_uid_1"

        notice_logs = [r for r in store.list_reminder_logs(tpl_id) if r["kind"] == "notice"]
        assert len(notice_logs) == 1, notice_logs
        legacy = make_idem_key(tpl_id, PHONE, "2026-11-02", "notice", 1, "2026-11-02 08:00:00")
        assert notice_logs[0]["idem_key"] == legacy
    finally:
        store.close()


def test_wecom_task_delivers_only_wecom() -> None:
    store = _mk_store()
    try:
        tpl_id = _setup(store, zdxlz=True, wecom=True, tpl_platform="wecom")
        z, w = FakeNotifier(), FakeNotifier()
        sched = _scheduler(store, z, w)
        for _ in range(3):
            sched.run_once()

        assert len(w.sent_messages) == 1, [m.text for m in w.sent_messages]
        assert len(z.sent_messages) == 0, [m.text for m in z.sent_messages]
        assert w.sent_messages[0].receive_id == "wecom_userid_1"

        notice_logs = [r for r in store.list_reminder_logs(tpl_id) if r["kind"] == "notice"]
        assert len(notice_logs) == 1, notice_logs
        legacy = make_idem_key(tpl_id, PHONE, "2026-11-02", "notice", 1, "2026-11-02 08:00:00")
        assert notice_logs[0]["idem_key"] == f"{legacy}|wecom"
    finally:
        store.close()


def test_wecom_task_member_only_zdxlz_bound_sends_none() -> None:
    """企微任务的成员只在密信有绑定：绝不串到密信发送，记 skipped。"""
    store = _mk_store()
    try:
        tpl_id = _setup(store, zdxlz=True, wecom=False, tpl_platform="wecom")
        z, w = FakeNotifier(), FakeNotifier()
        _scheduler(store, z, w).run_once()

        assert len(z.sent_messages) == 0
        assert len(w.sent_messages) == 0
        # 2026-11-09 新口径：该成员在**企微平台未激活** → 根本不生成实例 → 也没有 skipped 日志
        # （旧口径是「建实例、发送时记 skipped」；新口径把噪音挡在源头）
        assert store.list_instances_for_period(tpl_id, store.now().strftime("%Y-%m-%d")) == [], "未在企微激活的成员不生成实例"
        logs = [r for r in store.list_reminder_logs(tpl_id) if r["kind"] == "notice"]
        assert logs == [], "没有实例就不该有发送日志"
    finally:
        store.close()


def test_unbound_member_sends_nothing() -> None:
    store = _mk_store()
    try:
        _setup(store, zdxlz=False, wecom=False, tpl_platform="zdxlz")
        z, w = FakeNotifier(), FakeNotifier()
        sched = _scheduler(store, z, w)
        sched.run_once()
        assert len(z.sent_messages) == 0 and len(w.sent_messages) == 0
    finally:
        store.close()


def test_text_activation_dead_and_h5_registration_binds_wecom() -> None:
    """2026-11-03 口径：对话文字激活彻底停用；登记一律走 H5 表单（姓名/手机号/部门），绑定写入来源平台。"""
    store = _mk_store()
    try:
        role = store.create_role(name="绑定组", platform="wecom")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        store.create_department("市场部", actor="t")
        sent: list[tuple] = []

        def _send(text: str, tag: str) -> None:
            sent.append((text, tag))

        # 1) 未登记用户发「姓名 手机号」→ 不再绑定，只回统一登记链接
        code, body = handle_inbound_text(
            state=store, user_id="wecom_uid_bind", msg_text=f"{NAME} {PHONE}",
            send=_send, platform="wecom", base_url="http://x.example.com",
        )
        assert code == 200 and body.get("bound") is False, body
        assert store.get_delivery_target(PHONE, platform="wecom") is None, "⛔ 文字不得再触发绑定"
        assert "你还未完成信息登记" in body["message"]
        assert body["bind_url"].endswith("#uid=wecom_uid_bind&plat=wecom"), body["bind_url"]

        # 2) H5 登记表单（platform=wecom）→ 绑定写入 wecom，不串密信
        code2, body2 = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "wecom_uid_bind", "platform": "wecom", "name": NAME, "dept": "市场部"},
        )
        assert code2 == 200 and body2.get("bound") is True, body2
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

        m = store.list_role_members(role["role_id"], platform="zdxlz")["items"][0]
        assert m["activations"] == {"zdxlz": True, "wecom": True}
        assert m["activated"] is True

        m2 = store.list_role_members(role["role_id"], platform="wecom")["items"][0]
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