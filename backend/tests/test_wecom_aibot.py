# -*- coding: utf-8 -*-
"""企业微信智能机器人（长连接）通道的单元测试。

只测我们容易写错的地方，不真连企业微信：
1. 未就绪时必须返回 retryable=True（让调度退避重试，而不是把消息判死）
2. 主动推送的 chatid 单聊要填 **userid**（SDK 源码注释原文），消息体是 markdown
3. errcode 非 0 要判失败且不可重试
4. 缺凭证时不能悄悄「发送成功」

⛔ 不测：WS 握手、心跳、重连（SDK 负责）；回调消息解析（等联调再写）。
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

from app.channels.wecom_aibot import AibotBridge, WecomAibotNotifier


class FakeAibotClient:
    """最小替身：记录 on/run/send_message 的调用。"""

    def __init__(self, reply: Optional[Dict[str, Any]] = None, delay: float = 0.0) -> None:
        self.handlers: Dict[str, Any] = {}
        self.calls: List[tuple] = []
        self.ran = False
        self.reply = reply if reply is not None else {"errcode": 0, "errmsg": "ok"}
        self.delay = delay

    def on(self, name: str, fn: Any) -> None:
        self.handlers[name] = fn

    def run(self) -> None:  # 真实 SDK 会阻塞；替身立刻返回
        self.ran = True

    async def send_message(self, chatid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((chatid, body))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


def _make_ready_bridge(client: FakeAibotClient) -> AibotBridge:
    """在后台线程里跑一个真实事件循环，让 run_coroutine_threadsafe 有地方投递。"""
    bridge = AibotBridge("bot_x", "secret_x", client=client)
    loop = asyncio.new_event_loop()
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()
    # 等 loop 真正跑起来再返回，否则 send 里 is_running() 可能还是 False（偶发 NOT_CONNECTED）
    for _ in range(200):
        if loop.is_running():
            break
        time.sleep(0.01)
    bridge._loop = loop          # noqa: SLF001 - 测试直接注入
    bridge._ready.set()
    return bridge


# ── 1. 未就绪 / 缺凭证 ──────────────────────────────────────────────
def test_send_before_connected_is_retryable() -> None:
    bridge = AibotBridge("bot_x", "secret_x", client=FakeAibotClient())
    res = bridge.send_markdown("zhangsan", "内容")
    assert res.ok is False
    assert res.retryable is True
    assert res.error == "NOT_CONNECTED"


def test_start_without_credentials_fails_loudly() -> None:
    bridge = AibotBridge("", "", client=FakeAibotClient())
    assert bridge.start() is False
    assert bridge.last_error and "BotID" in bridge.last_error


def test_empty_chatid_is_not_retryable() -> None:
    bridge = _make_ready_bridge(FakeAibotClient())
    res = bridge.send_markdown("  ", "内容")
    assert res.ok is False and res.retryable is False and res.error == "EMPTY_CHATID"


# ── 2. 正常发送 ────────────────────────────────────────────────────
def test_send_uses_userid_as_chatid_and_markdown_body() -> None:
    client = FakeAibotClient()
    bridge = _make_ready_bridge(client)
    res = bridge.send_markdown("zhangsan", "【晨会安排】请于 08:15 前完成")
    assert res.ok is True
    assert len(client.calls) == 1
    chatid, body = client.calls[0]
    # 单聊：chatid 就是 userid（官方 SDK 注释原文）
    assert chatid == "zhangsan"
    assert body["msgtype"] == "markdown"
    assert body["markdown"]["content"] == "【晨会安排】请于 08:15 前完成"


def test_multiline_content_preserved() -> None:
    client = FakeAibotClient()
    bridge = _make_ready_bridge(client)
    text = "第一行\n第二行\nhttp://example.com/h5/task#t=abc"
    bridge.send_markdown("u1", text)
    assert client.calls[0][1]["markdown"]["content"] == text


# ── 3. 错误处理 ────────────────────────────────────────────────────
def test_errcode_nonzero_is_failure_and_not_retryable() -> None:
    client = FakeAibotClient(reply={"errcode": 40001, "errmsg": "invalid credential"})
    bridge = _make_ready_bridge(client)
    res = bridge.send_markdown("u1", "x")
    assert res.ok is False
    assert res.retryable is False
    assert "40001" in (res.error or "")


def test_errcode_inside_headers_is_detected() -> None:
    client = FakeAibotClient(reply={"headers": {"errcode": 40003, "errmsg": "invalid userid"}})
    bridge = _make_ready_bridge(client)
    res = bridge.send_markdown("u1", "x")
    assert res.ok is False and "40003" in (res.error or "")


def test_timeout_is_retryable() -> None:
    client = FakeAibotClient(delay=0.5)
    bridge = _make_ready_bridge(client)
    res = bridge.send_markdown("u1", "x", timeout=0.05)
    assert res.ok is False
    assert res.retryable is True
    assert res.error == "SEND_TIMEOUT"


# ── 4. notifier 适配层 ─────────────────────────────────────────────
class _Cfg:
    wecom_bot_id = "bot_x"
    wecom_bot_secret = "secret_x"


def test_notifier_delegates_to_bridge() -> None:
    client = FakeAibotClient()
    bridge = _make_ready_bridge(client)
    n = WecomAibotNotifier(_Cfg(), bridge=bridge)
    res = n.send_text("zhangsan", "hello", "idem-1")
    assert res.ok is True
    assert client.calls[0][0] == "zhangsan"


def test_notifier_returns_failure_instead_of_raising() -> None:
    class _Boom(FakeAibotClient):
        async def send_message(self, chatid, body):  # type: ignore[override]
            raise RuntimeError("ws closed")

    bridge = _make_ready_bridge(_Boom())
    n = WecomAibotNotifier(_Cfg(), bridge=bridge)
    res = n.send_text("u1", "x", "idem")
    assert res.ok is False
    assert res.retryable is True
    assert "ws closed" in (res.error or "")


def test_disconnect_clears_ready_flag() -> None:
    bridge = _make_ready_bridge(FakeAibotClient())
    assert bridge.is_ready() is True
    bridge._on_disconnected()
    assert bridge.is_ready() is False
    assert bridge.send_markdown("u1", "x").retryable is True


# ── 2026-10-30 新增：收消息（aibot_msg_callback）与装配 ────────────────
def _wait_until(fn, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.01)
    return fn()


def _text_frame(userid: str = "zhangsan", content: str = "你好", chattype: str = "single") -> Dict[str, Any]:
    return {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "text",
            "chattype": chattype,
            "chatid": userid if chattype == "single" else "group_chat_1",
            "from": {"userid": userid},
            "text": {"content": content},
        },
    }


def test_inbound_text_reaches_handler_and_replies() -> None:
    """单聊文本 → 取 from.userid + text.content 交给业务层；回复走 send_message(userid)。"""
    client = FakeAibotClient()
    bridge = _make_ready_bridge(client)
    got: List[tuple] = []

    def _handler(userid: str, text: str, send) -> None:
        got.append((userid, text))
        send("已收到", "tag1")

    bridge._on_text = _handler  # noqa: SLF001
    bridge._on_message_text(_text_frame("zhangsan", "申请补交"))

    assert _wait_until(lambda: bool(got) and bool(client.calls))
    assert got == [("zhangsan", "申请补交")]
    chatid, body = client.calls[0]
    assert chatid == "zhangsan"                      # 单聊 chatid = userid
    assert body["msgtype"] == "markdown"
    assert body["markdown"]["content"] == "已收到"


def test_group_message_is_ignored() -> None:
    """群聊消息本轮只记日志，不进业务。"""
    client = FakeAibotClient()
    bridge = _make_ready_bridge(client)
    called: List[tuple] = []
    bridge._on_text = lambda u, t, s: called.append((u, t))  # noqa: SLF001

    bridge._on_message_text(_text_frame("zhangsan", "大家好", chattype="group"))
    time.sleep(0.1)
    assert called == []


def test_non_text_message_does_not_enter_business() -> None:
    client = FakeAibotClient()
    bridge = _make_ready_bridge(client)
    called: List[tuple] = []
    bridge._on_text = lambda u, t, s: called.append((u, t))  # noqa: SLF001

    bridge._on_other_message({"body": {"msgtype": "image", "from": {"userid": "zhangsan"}}})
    time.sleep(0.05)
    assert called == []


def test_inbound_without_handler_is_safe() -> None:
    """没注入业务处理器时只记 warning，绝不能把异常抛回 SDK 事件循环。"""
    bridge = AibotBridge("bot_x", "secret_x", client=FakeAibotClient())
    bridge._on_message_text(_text_frame())  # noqa: SLF001 - 不抛异常即通过
    bridge._on_message_text({})            # 空帧也不能炸


def test_malformed_frame_does_not_raise() -> None:
    bridge = AibotBridge("bot_x", "secret_x", client=FakeAibotClient())
    bridge._on_text = lambda u, t, s: None  # noqa: SLF001
    for bad in (None, {}, {"body": None}, {"body": {"text": "x"}}):
        bridge._on_message_text(bad)  # noqa: SLF001


# ── 向导 / 装配 ─────────────────────────────────────────────────────
def test_wizard_schema_for_aibot_has_no_callback() -> None:
    """长连接无回调地址：callback_path 为空，前端据此隐藏「回调地址」那一栏。"""
    from app.channel_config import CHANNEL_SCHEMA, build_callback_url, validate_payload

    meta = CHANNEL_SCHEMA["wecom_aibot"]
    assert meta["callback_path"] == ""
    assert meta["label"] == "企业微信（智能机器人）"
    assert [f["key"] for f in meta["fields"]] == ["bot_id", "bot_secret"]
    assert all(f["required"] and f["secret"] for f in meta["fields"])
    assert build_callback_url("https://task.example.com", "wecom_aibot") == ""
    assert validate_payload("wecom_aibot", {"bot_id": "aib1", "bot_secret": "s"}) is None
    assert "缺少必填项" in (validate_payload("wecom_aibot", {"bot_id": "aib1"}) or "")


def test_create_apps_maps_aibot_platform_to_wecom(tmp_path) -> None:
    """绑定映射：智能机器人与自建应用共用 userid 体系 → platform 记 'wecom'。

    这样先激活机器人，将来切自建应用不用重新激活。
    """
    from app.config import Config
    from app.main import create_apps

    cfg = Config(
        public_port=21021, admin_port=21022, public_base_url="https://task.example.com",
        data_dir=tmp_path, channel="wecom_aibot",
        # 不填 bot 凭证 → notifier 不会真的去建长连接（只打一条 error 日志）
    )
    pub, adm, store, chan = create_apps(config=cfg)
    try:
        assert store.platform == "wecom"
    finally:
        store.close()
        chan.close()


# ── 测试连接（_probe_channel 的 aibot 分支） ─────────────────────────
class _FakeProbeBridge:
    instances: List["_FakeProbeBridge"] = []
    ready = False
    err = "Connection timeout to openws.work.weixin.qq.com"

    def __init__(self, bot_id: str, secret: str, connect_timeout: float = 15.0) -> None:
        self.bot_id = bot_id
        self.secret = secret
        self.last_error = _FakeProbeBridge.err
        self.stopped = False
        _FakeProbeBridge.instances.append(self)

    def start(self) -> bool:
        return True

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return _FakeProbeBridge.ready

    def stop(self) -> None:
        self.stopped = True

    def is_ready(self) -> bool:
        return _FakeProbeBridge.ready


def test_probe_aibot_reports_readable_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import admin as admin_mod

    _FakeProbeBridge.instances = []
    _FakeProbeBridge.ready = False
    monkeypatch.setattr("app.channels.wecom_aibot.AibotBridge", _FakeProbeBridge)

    ok, detail = admin_mod._probe_channel("wecom_aibot", {"bot_id": "aib1", "bot_secret": "s"})
    assert ok is False
    assert "8 秒内未能完成长连接鉴权" in detail
    assert _FakeProbeBridge.instances[0].stopped is True  # 探完必须断开，别占着连接


def test_probe_aibot_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import admin as admin_mod

    _FakeProbeBridge.instances = []
    _FakeProbeBridge.ready = True
    monkeypatch.setattr("app.channels.wecom_aibot.AibotBridge", _FakeProbeBridge)

    ok, detail = admin_mod._probe_channel("wecom_aibot", {"bot_id": "aib1", "bot_secret": "s"})
    assert ok is True
    assert "连接成功" in detail


def test_probe_aibot_never_kicks_live_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测的就是线上正在跑的那个机器人时，不能再建第二条连接（会把线上踢掉）。"""
    from types import SimpleNamespace

    from app.api import admin as admin_mod

    _FakeProbeBridge.instances = []
    _FakeProbeBridge.ready = True
    monkeypatch.setattr("app.channels.wecom_aibot.AibotBridge", _FakeProbeBridge)

    live = _FakeProbeBridge("aib_live", "sec")
    ok, detail = admin_mod._probe_channel(
        "wecom_aibot", {"bot_id": "aib_live", "bot_secret": "sec"},
        notifier=SimpleNamespace(bridge=live),
    )
    assert ok is True
    assert "已就绪" in detail
    # 除了 live 自己，不应再 new 出第二个探针实例
    assert _FakeProbeBridge.instances == [live]
