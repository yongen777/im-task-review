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
