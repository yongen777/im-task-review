# -*- coding: utf-8 -*-
"""企业微信「智能机器人」长连接通道（迁移目标通道）。

2026-10-30 选型改为**智能机器人 + API 模式（长连接）**，理由是与量子密信的机器人对话形态一致，
现有「激活 → 绑定 → 待归纳 → 推送」整套逻辑可原样迁移，只换协议层。

官方协议（文档 101463 / 60904，SDK 已核对）：
- 连接地址        wss://openws.work.weixin.qq.com
- 订阅（鉴权）    {"cmd":"aibot_subscribe","body":{"bot_id":..,"secret":..}}
- 收用户消息      {"cmd":"aibot_msg_callback", body:{from:{userid}, msgtype, text:{content}, chatid, chattype}}
- 主动推送        cmd = "aibot_send_msg"，body 里带 chatid
                  ⭐ SDK 源码注释原文：「chatid：会话 ID，**单聊填用户的 userid**，群聊填对应群聊的 chatid」
                     也就是说主动推私聊只需要 userid —— 是否需要「用户先说过话」要联调实测，
                     保守做法仍是保留现有激活逻辑（先发一条即可建立会话）。
- 同一机器人同时只能有**一个**长连接，新连接会踢掉旧连接 → 多进程部署必须做主备，不能同时连。

线程模型：
SDK 是 asyncio 原生（`client.run()` 阻塞）。我们的调度器是同步线程，所以这里起一条后台线程
跑事件循环，把 `send_message` 用 `run_coroutine_threadsafe` 投递进去，同步等结果。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict, Optional

from .zdxlz import SendResult

logger = logging.getLogger(__name__)

try:  # 官方 SDK：pip install wecom-aibot-python-sdk
    from aibot import WSClient, WSClientOptions
except Exception:  # pragma: no cover - 未安装时给出明确提示，而不是启动即崩
    WSClient = None  # type: ignore[assignment]
    WSClientOptions = None  # type: ignore[assignment]


class AibotBridge:
    """把长连接客户端的生命周期和「同步发送」包起来。

    ⚠️ 一个机器人只能有一个长连接：多副本部署时只有一个实例能持有连接，
       另一个会被踢掉并收到断开事件。真要高可用得做主备切换，不要两个同时连。
    """

    def __init__(self, bot_id: str, secret: str, *, client: Optional[Any] = None,
                 connect_timeout: float = 15.0) -> None:
        self.bot_id = (bot_id or "").strip()
        self.secret = (secret or "").strip()
        self._client = client
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._last_error: Optional[str] = None
        self._connect_timeout = connect_timeout
        self._stopping = False

    # ── 生命周期 ────────────────────────────────────────────────────
    def is_available(self) -> bool:
        return self._client is not None or WSClient is not None

    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def start(self) -> bool:
        """起后台线程建连；不阻塞调用方。返回是否成功启动（不代表已连上）。"""
        if not self.is_available():
            self._last_error = "wecom-aibot SDK 未安装（pip install wecom-aibot-python-sdk）"
            logger.error(self._last_error)
            return False
        if not (self.bot_id and self.secret):
            self._last_error = "缺少 BotID / Secret"
            logger.error("企业微信智能机器人缺少 BotID/Secret，长连接不会建立")
            return False
        if self._thread and self._thread.is_alive():
            return True

        if self._client is None:
            self._client = WSClient(
                WSClientOptions(bot_id=self.bot_id, secret=self.secret)
            )
        client = self._client

        # SDK 的 on 支持同步/异步回调；这里用同步函数即可
        try:
            client.on("authenticated", self._on_authenticated)
            client.on("disconnected", self._on_disconnected)
        except Exception as exc:  # noqa: BLE001 - 不同 SDK 版本事件名可能有差异
            logger.warning("注册长连接事件失败（不影响主流程）: %s", exc)

        self._stopping = False
        self._thread = threading.Thread(target=self._run_forever, name="wecom-aibot-ws", daemon=True)
        self._thread.start()
        return True

    def _run_forever(self) -> None:
        try:
            # client.run() 内部自建事件循环并阻塞；连接/心跳/重连都由 SDK 负责
            self._client.run()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"长连接异常退出: {exc}"
            logger.exception("企业微信智能机器人长连接异常退出")
        finally:
            self._ready.clear()

    def _on_authenticated(self, *args: Any, **kwargs: Any) -> None:
        # 认证成功时我们才拿得到正在运行的事件循环，后续投递发送任务要用它
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._ready.set()
        logger.info("企业微信智能机器人长连接已就绪（bot_id=%s）", self.bot_id)

    def _on_disconnected(self, *args: Any, **kwargs: Any) -> None:
        self._ready.clear()
        logger.warning("企业微信智能机器人长连接已断开（SDK 会自动重连）")

    def stop(self) -> None:
        self._stopping = True
        self._ready.clear()
        try:
            if self._client is not None and hasattr(self._client, "disconnect"):
                self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ── 发送 ────────────────────────────────────────────────────────
    def send_markdown(self, chatid: str, content: str, *, timeout: float = 10.0) -> SendResult:
        """主动推送一条 markdown 消息（单聊 chatid 传 userid）。

        未就绪时返回 retryable=True：调度器会按退避重试，不会当成永久失败。
        """
        target = (chatid or "").strip()
        if not target:
            return SendResult(ok=False, result="failed", retryable=False, error="EMPTY_CHATID")
        loop = self._loop
        if not self._ready.is_set() or loop is None or not loop.is_running():
            return SendResult(ok=False, result="failed", retryable=True, error="NOT_CONNECTED")
        if self._client is None:
            return SendResult(ok=False, result="failed", retryable=True, error="NO_CLIENT")

        body: Dict[str, Any] = {"msgtype": "markdown", "markdown": {"content": content}}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.send_message(target, body), loop
            )
            frame = fut.result(timeout=timeout)
        except (asyncio.TimeoutError, FutureTimeoutError):
            # Python 3.10 里 concurrent.futures.TimeoutError 与 asyncio.TimeoutError 是两个类，
            # 3.11 才合并；生产是 3.10，所以两个都要捕获，否则超时会被记成未知异常。
            return SendResult(ok=False, result="failed", retryable=True, error="SEND_TIMEOUT")
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, result="failed", retryable=True, error=f"EXCEPTION: {exc}")

        errcode = 0
        errmsg = ""
        if isinstance(frame, dict):
            errcode = int(frame.get("errcode") or 0)
            errmsg = str(frame.get("errmsg") or "")
            headers = frame.get("headers")
            if isinstance(headers, dict) and not errcode:
                errcode = int(headers.get("errcode") or 0)
                errmsg = errmsg or str(headers.get("errmsg") or "")
        if errcode != 0:
            return SendResult(ok=False, result="failed", status_code=200,
                              retryable=False, error=f"{errcode}: {errmsg}")
        return SendResult(ok=True, result="sent", status_code=200)


class WecomAibotNotifier:
    """把 AibotBridge 适配成 scheduler 需要的 NotifierProtocol。

    receive_id 语义：企微成员 userid（单聊 chatid 直接用 userid）。
    """

    def __init__(self, config: Any, *, bridge: Optional[AibotBridge] = None,
                 auto_start: Optional[bool] = None) -> None:
        bot_id = getattr(config, "wecom_bot_id", "")
        secret = getattr(config, "wecom_bot_secret", "")
        # 注入 bridge 说明生命周期由调用方管理，这里不重复 start（否则会把已就绪状态重置）
        self._owns_bridge = bridge is None
        self.bridge = bridge or AibotBridge(bot_id, secret)
        if auto_start is None:
            auto_start = self._owns_bridge
        if not (bot_id and secret):
            logger.error(
                "企业微信智能机器人配置不完整（bot_id/secret 为空），发送会全部失败。"
                " 需要在 .env 里补 IMT_WECOM_BOT_ID / IMT_WECOM_BOT_SECRET"
            )
        elif auto_start:
            self.bridge.start()

    def send_text(self, receive_id: str, text: str, idem_key: str) -> SendResult:
        """用 markdown 承载纯文本（企微机器人主动推送支持 markdown，纯文本原样即可）。"""
        try:
            return self.bridge.send_markdown(receive_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("企业微信智能机器人发送异常 to=%s: %s", receive_id, exc)
            return SendResult(ok=False, result="failed", retryable=True, error=f"EXCEPTION: {exc}")

    def close(self) -> None:
        self.bridge.stop()
