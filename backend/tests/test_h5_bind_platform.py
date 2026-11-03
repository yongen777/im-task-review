# -*- coding: utf-8 -*-
"""H5 绑定回执必须走「绑定所在平台」的通道。

2026-11-03：原先回执固定用 active_notifier（实际是密信通道），
企微用户走 H5 绑定时回执发给密信通道 → external_id 是企微 userid，必然失败且被静默吞掉。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.api.h5 import process_h5_bind
from app.scheduler import FakeNotifier
from app.state import StateStore

PHONE = "13800000007"
DEPT = "一支局"


def _store() -> StateStore:
    store = StateStore(Path(tempfile.mkdtemp(prefix="imtask_h5bind_")) / "im.db")
    role = store.create_role("绑定测试组", actor="t", platform="wecom")
    store.add_role_member(role["role_id"], phone=PHONE, name="李四", dept=DEPT)
    store.create_department(DEPT, actor="t")
    return store


def test_h5_bind_receipt_goes_to_bound_platform() -> None:
    store = _store()
    try:
        z, w = FakeNotifier(), FakeNotifier()
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "woOC_wecom_uid", "platform": "wecom", "name": "李四", "dept": DEPT},
            notifier=z,                     # 旧行为会发到这里（错）
            notifiers={"zdxlz": z, "wecom": w},
        )
        assert code == 200 and body.get("bound") is True, body
        assert len(w.sent_messages) == 1, "企微回执应发 1 条"
        assert z.sent_messages == [], "⛔ 密信通道不该收到企微用户的回执"
        assert store.get_delivery_target(PHONE, platform="wecom") is not None
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None
    finally:
        store.close()


def test_h5_bind_defaults_to_current_platform() -> None:
    """请求体没带 platform 时，跟 state.platform 一致（绑定与回执不会错位）。"""
    store = _store()
    try:
        store.platform = "wecom"
        z, w = FakeNotifier(), FakeNotifier()
        code, _ = process_h5_bind(
            state=store, data={"phone": PHONE, "external_id": "woOC_uid_2", "name": "李四", "dept": DEPT},
            notifier=z, notifiers={"zdxlz": z, "wecom": w},
        )
        assert code == 200
        assert len(w.sent_messages) == 1 and z.sent_messages == []
    finally:
        store.close()


def test_h5_bind_falls_back_to_single_notifier() -> None:
    """没传 notifiers（单通道部署/旧调用）时退回原行为，不能因此丢回执。"""
    store = _store()
    try:
        z = FakeNotifier()
        code, _ = process_h5_bind(
            state=store, data={"phone": PHONE, "external_id": "uid_3", "name": "李四", "dept": DEPT}, notifier=z,
        )
        assert code == 200
        assert len(z.sent_messages) == 1
    finally:
        store.close()
