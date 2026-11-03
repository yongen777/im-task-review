# -*- coding: utf-8 -*-
"""2026-11-03 补测：回调回复的**通道装配**（不是纯函数）。

背景：线上实锤 errcode=93006 invalid chatid —— 密信回调的回复被发给企微 bot，
用户侧表现为「机器人完全不回话」。修复方式是在 main.create_apps 里把
`_channel_notifier(notifiers, "zdxlz", chan)` 传给密信事件路由。

为什么单独一个文件：
`test_reply_channel_and_recycle.py::test_channel_notifier_picks_source_platform`
只验证了 `_channel_notifier(...)` 这个**纯函数**本身。把 create_apps 里那行换回
`notifier=active_notifier`（= 修复前的写法），那条用例依然全绿（workbuddy 反向实测）。
本用例真正起一套双通道应用、发一条明文回调，看回话落到哪个通道。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("IMT_ALLOW_PLAIN_EVENT", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

pytest.importorskip("httpx", reason="TestClient 需要 httpx")


class _FakeChannel:
    """模拟一个「有 send_text / parse_callback_event」的通道。"""

    def __init__(self) -> None:
        self.platform = "zdxlz"
        self.configured = True
        self.sent: list[tuple[str, str]] = []

    def send_text(self, receive_id, text, idem=None):  # noqa: ANN001, ANN003
        from app.scheduler import SendResult

        self.sent.append((receive_id, text))
        return SendResult(ok=True, result="sent", error=None)

    def send_image(self, *a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("本案不涉及图片")

    def parse_callback_event(self, *, timestamp, nonce, signature, body_text):  # noqa: ANN001
        return json.loads(body_text)

    def close(self) -> None:
        pass


class _FakeWecom:
    instances: list["_FakeWecom"] = []

    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.sent: list[tuple[str, str]] = []
        _FakeWecom.instances.append(self)

    def send_text(self, receive_id, text, idem=None):  # noqa: ANN001, ANN003
        from app.scheduler import SendResult

        self.sent.append((receive_id, text))
        return SendResult(ok=True, result="sent", error=None)

    def send_image(self, *a, **k):  # noqa: ANN002, ANN003
        from app.scheduler import SendResult

        return SendResult(ok=True, result="sent", error=None)

    def close(self) -> None:
        pass


def test_zdxlz_callback_reply_uses_zdxlz_channel(monkeypatch) -> None:
    """当前启用通道=企微时，密信回调的回复仍必须走密信通道。"""
    from app.config import Config
    from app.main import create_apps

    import app.channel_config as cfg_store
    import app.channels.wecom_aibot as bot_mod

    monkeypatch.setattr(cfg_store.ChannelConfigStore, "read_enabled_channels", lambda self: ["zdxlz", "wecom"])
    monkeypatch.setattr(bot_mod, "WecomAibotNotifier", _FakeWecom)
    _FakeWecom.instances = []

    tmp = Path(tempfile.mkdtemp(prefix="imtask_wire_"))
    cfg = Config(
        public_port=21131, admin_port=21132, public_base_url="https://task.example.com",
        data_dir=tmp, channel="wecom",  # 故意让「当前通道」=企微（就是出事时的状态）
        wecom_bot_id="bot_test", wecom_bot_secret="secret_test",
    )
    zdx = _FakeChannel()
    pub, _adm, store, chan = create_apps(config=cfg, channel=zdx)
    try:
        assert _FakeWecom.instances, "企微通道未装配，用例前提不成立"
        wecom = _FakeWecom.instances[0]
        with TestClient(pub) as c:
            r = c.post(
                "/api/app-events",
                content=json.dumps({"userId": "uid_wire", "text": {"content": "你好"}}),
                headers={"Content-Type": "application/json"},
            )
        assert r.status_code == 200, r.text
        assert len(zdx.sent) == 1, f"密信通道应收到 1 条回话，实际 {len(zdx.sent)}"
        assert zdx.sent[0][0] == "uid_wire"
        assert wecom.sent == [], f"⛔ 企微通道不该收到密信回调的回复：{wecom.sent}"
    finally:
        store.close()
