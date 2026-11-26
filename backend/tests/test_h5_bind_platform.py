# -*- coding: utf-8 -*-
"""H5 绑定按平台隔离，绑定成功反馈只在 H5 中展示。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402
from app.api.h5 import process_h5_bind
from app.scheduler import FakeNotifier
from app.state import StateStore

PHONE = "13800000007"
DEPT = "一支局"


def _store() -> StateStore:
    store = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_h5bind_")))
    role = store.create_role("绑定测试组", actor="t", platform="wecom")
    store.add_role_member(role["role_id"], phone=PHONE, name="李四", dept=DEPT)
    store.create_department(DEPT, actor="t")
    return store


def test_h5_bind_records_platform_without_sending_chat_feedback() -> None:
    store = _store()
    try:
        z, w = FakeNotifier(), FakeNotifier()
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "woOC_wecom_uid", "platform": "wecom", "name": "李四", "dept": DEPT},
            notifier=z,
            notifiers={"zdxlz": z, "wecom": w},
        )
        assert code == 200 and body.get("bound") is True, body
        assert w.sent_messages == z.sent_messages == []
        assert store.get_delivery_target(PHONE, platform="wecom") is not None
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None
    finally:
        store.close()


def test_h5_bind_defaults_to_current_platform() -> None:
    """请求体没带 platform 时，绑定使用 state.platform。"""
    store = _store()
    try:
        store.platform = "wecom"
        z, w = FakeNotifier(), FakeNotifier()
        code, _ = process_h5_bind(
            state=store, data={"phone": PHONE, "external_id": "woOC_uid_2", "name": "李四", "dept": DEPT},
            notifier=z, notifiers={"zdxlz": z, "wecom": w},
        )
        assert code == 200
        assert store.get_delivery_target(PHONE, platform="wecom") is not None
        assert w.sent_messages == z.sent_messages == []
    finally:
        store.close()


def test_h5_bind_is_silent_with_legacy_notifier_argument() -> None:
    store = _store()
    try:
        z = FakeNotifier()
        code, _ = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "uid_3", "platform": "wecom", "name": "李四", "dept": DEPT},
            notifier=z,
        )
        assert code == 200
        assert z.sent_messages == []
    finally:
        store.close()
