# -*- coding: utf-8 -*-
"""2026-11-03 补测：提交回推的「发送前查重」这道防线。

为什么单独一个文件：
`test_registration_flow.py::test_submit_feedback_pushed_once_on_task_platform`
只覆盖了「令牌已消耗 → 再次提交被 400 拒掉」，而那次拒绝发生在**进入回推之前**——
把 `_push_submit_feedback` 里的 `if state.has_sent_reminder(idem): return` 整行删掉，
那 9 条用例仍然全绿（workbuddy 反向验证实测）。
也就是说：日志重复写、调度重试、接口重入这类场景下是否会重复打扰用户，此前没有测试兜底。
本用例直接连调三次回推，锁死「同一 submission 只发一条」。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.h5 import _push_submit_feedback  # noqa: E402
from app.config import format_dt  # noqa: E402
from app.scheduler import FakeNotifier  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13600001234"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_ack_")) / "im.db")


def _instance(store: StateStore, platform: str):
    role = store.create_role(f"{platform}回推组", actor="t", platform=platform)
    store.add_role_member(role["role_id"], phone=PHONE, name="钱八")
    store.upsert_delivery_target(PHONE, "uid_1", platform=platform)
    tpl = store.create_template(
        {
            "name": "回推任务", "kind": "form", "role_ids": [role["role_id"]],
            "schema_json": [{"key": "n", "label": "户数", "type": "number", "required": True}],
            "text_tpl": "x", "remind_at": "08:00", "deadline": "23:59", "period": "day",
            "need_review": False, "enabled": True,
        },
        actor="t",
    )
    now = store.now().replace(tzinfo=None, microsecond=0)
    return store.ensure_template_instances(
        tpl["template_id"], format_dt(now)[:10], format_dt(now + timedelta(hours=8))
    )[0]


def test_pre_send_dedupe_blocks_repeated_feedback() -> None:
    store = _store()
    try:
        inst = _instance(store, "zdxlz")
        z, w = FakeNotifier(), FakeNotifier()
        for _ in range(3):  # 模拟重试/重入：同一 submission 连推三次
            _push_submit_feedback(
                state=store, inst=inst, submission_id=888,
                notifier=z, notifiers={"zdxlz": z, "wecom": w},
            )
        assert len(z.sent_messages) == 1, f"重复推送未被拦住，实际发出 {len(z.sent_messages)} 条"
        assert z.sent_messages[0].text == "【回推任务】提交成功"
        assert w.sent_messages == []
    finally:
        store.close()


def test_pre_send_dedupe_is_per_submission() -> None:
    """换一个 submission（用户真的又交了一次）应当照常再推一条。"""
    store = _store()
    try:
        inst = _instance(store, "wecom")
        z, w = FakeNotifier(), FakeNotifier()
        _push_submit_feedback(state=store, inst=inst, submission_id=1, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        _push_submit_feedback(state=store, inst=inst, submission_id=2, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        assert len(w.sent_messages) == 2, "不同 submission 应各推一条"
        assert z.sent_messages == [], "企微任务不得串到密信"
    finally:
        store.close()
