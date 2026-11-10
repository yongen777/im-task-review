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
import base64
import hashlib
import logging
import math
import os
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict, Optional, Tuple

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
                 connect_timeout: float = 15.0,
                 on_text: Optional[Any] = None) -> None:
        self.bot_id = (bot_id or "").strip()
        self.secret = (secret or "").strip()
        self._client = client
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._last_error: Optional[str] = None
        self._connect_timeout = connect_timeout
        self._stopping = False
        # 2026-10-30 收消息：on_text(userid, text, send) —— 由 main 注入
        # handle_inbound_text，业务规则一条不复制，这里只做格式转换。
        self._on_text = on_text
        # path -> (media_id, expire_ts, mtime, size)：同图复用，别按人重复上传
        self._media_cache: Dict[str, Tuple[str, float, float, int]] = {}
        self._media_lock = threading.Lock()

    # ── 生命周期 ────────────────────────────────────────────────────
    def is_available(self) -> bool:
        return self._client is not None or WSClient is not None

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """等长连接就绪（供「测试连接」用；不阻塞已连上的实例）。"""
        return self._ready.wait(timeout)

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
            # 收用户消息：SDK 的 message_handler 会按 msgtype 再发细分事件，
            # 我们只处理文本（绑定手机号 / 申请补交 / 闲聊 ack 都是文本）。
            client.on("message.text", self._on_message_text)
            client.on("message", self._on_other_message)
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

    # ── 收消息（aibot_msg_callback） ────────────────────────────────
    def _on_message_text(self, frame: Any) -> None:
        """文本消息：单聊取 from.userid + text.content，交给业务层；群聊只记日志。

        ⚠️ 这个回调跑在 SDK 的事件循环线程里：不能在这里做阻塞调用（DB 写入、
           `run_coroutine_threadsafe(...).result()` 都会卡死整条连接），
           所以业务逻辑丢到独立线程，回复也由那个线程投递回事件循环。
        """
        try:
            body = (frame or {}).get("body") or {}
            sender = str(((body.get("from") or {}).get("userid")) or "").strip()
            chattype = str(body.get("chattype") or "").strip().lower()
            chatid = str(body.get("chatid") or "").strip()
            content = str(((body.get("text") or {}).get("content")) or "")
            logger.info(
                "智能机器人收到文本消息 chattype=%s userid=%s chatid=%s len=%d",
                chattype or "(空)", sender or "(空)", chatid or "(空)", len(content),
            )
            if chattype and chattype != "single":
                logger.info("群聊消息本轮不处理（chatid=%s userid=%s）", chatid, sender)
                return
            if not sender or not content.strip():
                return
            handler = self._on_text
            if handler is None:
                logger.warning("未注入入站处理器，消息被忽略（userid=%s）", sender)
                return
            threading.Thread(
                target=self._dispatch_inbound, args=(handler, sender, content),
                name="wecom-aibot-inbound", daemon=True,
            ).start()
        except Exception:  # noqa: BLE001 - 绝不能把异常抛回 SDK 的事件循环
            logger.exception("智能机器人消息解析失败")

    def _on_other_message(self, frame: Any) -> None:
        """非文本消息（图片/语音/文件/图文混排）：本轮只记日志，不进入业务。"""
        try:
            body = (frame or {}).get("body") or {}
            sender = str(((body.get("from") or {}).get("userid")) or "").strip()
            logger.info("智能机器人收到非文本消息 msgtype=%s userid=%s（本轮不处理）",
                        body.get("msgtype"), sender)
        except Exception:  # noqa: BLE001
            pass

    def _dispatch_inbound(self, handler: Any, userid: str, text: str) -> None:
        def _send(reply_text: str, tag: str = "") -> None:  # noqa: ARG001 - 与 zdxlz 的 send(text, tag) 对齐
            res = self.send_markdown(userid, reply_text)
            if not res.ok:
                logger.warning("智能机器人回复失败 userid=%s: %s", userid, res.error)

        try:
            handler(userid, text, _send)
        except Exception:  # noqa: BLE001
            logger.exception("智能机器人入站消息处理失败 userid=%s", userid)

    def stop(self) -> None:
        self._stopping = True
        self._ready.clear()
        try:
            if self._client is not None and hasattr(self._client, "disconnect"):
                self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ── 发送 ────────────────────────────────────────────────────────
    # ── 传输层封装（可注入，便于测试脱离跨线程时序）────────────────
    def _transport_ready(self) -> bool:
        """长连接是否可发（就绪标志 + 事件循环确实在跑）。

        抽成方法是为了测试：单测里把这个方法替掉，就不必真的起一条事件循环线程，
        避免「靠线程调度时序」的脆弱用例（2026-11-02 验收反馈：另一台机器 6 条超时）。
        ⛔ 不要在这里加操作系统判断，环境差异属于测试的事。
        """
        loop = self._loop
        return bool(self._ready.is_set() and loop is not None and loop.is_running())

    def _run_sync(self, coro: Any, timeout: float) -> Any:
        """在后台事件循环里跑协程并同步等结果。"""
        loop = self._loop
        if loop is None:
            raise RuntimeError("loop_not_running")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def send_markdown(self, chatid: str, content: str, *, timeout: float = 10.0) -> SendResult:
        """主动推送一条 markdown 消息（单聊 chatid 传 userid）。

        未就绪时返回 retryable=True：调度器会按退避重试，不会当成永久失败。
        """
        target = (chatid or "").strip()
        if not target:
            return SendResult(ok=False, result="failed", retryable=False, error="EMPTY_CHATID")
        if not self._transport_ready():
            return SendResult(ok=False, result="failed", retryable=True, error="NOT_CONNECTED")
        if self._client is None:
            return SendResult(ok=False, result="failed", retryable=True, error="NO_CLIENT")

        body: Dict[str, Any] = {"msgtype": "markdown", "markdown": {"content": content}}
        try:
            frame = self._run_sync(self._client.send_message(target, body), timeout)
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


    # ── 图片消息（三步分片上传 → 图片消息）──────────────────────────
    # 2026-11-02 纠正我下过的一个错误结论：此前说「智能机器人发不了图片」。
    # 错在只看官方 Python SDK 的封装（只有 download，没有 upload），没去看协议本身。
    # 协议是支持的（官方文档 101970）：aibot_upload_media_init → _chunk × N → _finish 拿 media_id，
    # 再用 msgtype=image 发出去。约束：单分片 ≤512KB、≤100 片、图片 ≤10MB、
    # 仅 png/jpg/jpeg/gif、media_id 有效期 3 天、上传频率 30 次/分钟。
    UPLOAD_CHUNK_SIZE = 512 * 1024
    # 官方限制：上传 30 次/分钟。一个模板有 20 个收件人时，若每人各传一次，
    # 一个时点就 20 次；两个模板同时到点直接触发限流。所以同一张图只传一次。
    # media_id 官方有效期 3 天，缓存留 2 天余量。
    MEDIA_CACHE_TTL_SECONDS = 2 * 24 * 3600

    @staticmethod
    def _frame_error(frame: Dict[str, Any]) -> Tuple[int, str]:
        """errcode 可能在顶层，也可能在 headers 里（线上两种都出现过）。"""
        if not isinstance(frame, dict):
            return 0, ""
        errcode = int(frame.get("errcode") or 0)
        errmsg = str(frame.get("errmsg") or "")
        headers = frame.get("headers")
        if isinstance(headers, dict) and not errcode:
            errcode = int(headers.get("errcode") or 0)
            errmsg = errmsg or str(headers.get("errmsg") or "")
        return errcode, errmsg

    async def _cmd(self, cmd: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """发一条通用长连接命令并等回执（上传三步复用；与 send_message 走同一个 WS 队列）。"""
        from aibot.utils import generate_req_id

        frame = await self._client._ws_manager.send_reply(  # noqa: SLF001 - SDK 未暴露泛用命令入口
            generate_req_id(cmd), body, cmd
        )
        return frame if isinstance(frame, dict) else {}

    async def _upload_image_async(self, data: bytes, filename: str) -> Tuple[Optional[str], str]:
        total = len(data)
        chunks = max(1, math.ceil(total / self.UPLOAD_CHUNK_SIZE))
        if chunks > 100:
            return None, "TOO_MANY_CHUNKS(%d>100)" % chunks

        init = await self._cmd(
            "aibot_upload_media_init",
            {
                "type": "image",
                "filename": filename,
                "total_size": total,
                "total_chunks": chunks,
                "md5": hashlib.md5(data).hexdigest(),
            },
        )
        code, msg = self._frame_error(init)
        upload_id = str((init.get("body") or {}).get("upload_id") or "").strip()
        if code or not upload_id:
            return None, "INIT_FAILED %s:%s" % (code, msg)

        for idx in range(chunks):
            seg = data[idx * self.UPLOAD_CHUNK_SIZE:(idx + 1) * self.UPLOAD_CHUNK_SIZE]
            b64 = base64.b64encode(seg).decode("ascii")
            frame = await self._cmd(
                "aibot_upload_media_chunk",
                {"upload_id": upload_id, "chunk_index": idx, "base64_data": b64},
            )
            code, msg = self._frame_error(frame)
            if code:
                # 官方文档与不同 SDK 对分片字段名有分歧（data / base64_data），
                # 换另一个再试一次 —— 分片上传本身幂等，重复不会出错。
                frame2 = await self._cmd(
                    "aibot_upload_media_chunk",
                    {"upload_id": upload_id, "chunk_index": idx, "data": b64},
                )
                code2, msg2 = self._frame_error(frame2)
                if code2:
                    return None, "CHUNK_FAILED[%d] %s:%s / %s:%s" % (idx, code, msg, code2, msg2)

        finish = await self._cmd("aibot_upload_media_finish", {"upload_id": upload_id})
        code, msg = self._frame_error(finish)
        media_id = str((finish.get("body") or {}).get("media_id") or "").strip()
        if code or not media_id:
            return None, "FINISH_FAILED %s:%s" % (code, msg)
        return media_id, ""

    def _cache_get(self, file_path: str) -> Optional[str]:
        """命中且未过期（且文件没变）就复用 media_id。"""
        try:
            st = os.stat(file_path)
        except OSError:
            return None
        with self._media_lock:
            item = self._media_cache.get(file_path)
            if not item:
                return None
            media_id, expire_ts, mtime, size = item
            if time.time() >= expire_ts or st.st_mtime != mtime or st.st_size != size:
                self._media_cache.pop(file_path, None)
                return None
            return media_id

    def _cache_put(self, file_path: str, media_id: str) -> None:
        try:
            st = os.stat(file_path)
        except OSError:
            return
        with self._media_lock:
            self._media_cache[file_path] = (
                media_id, time.time() + self.MEDIA_CACHE_TTL_SECONDS, st.st_mtime, st.st_size
            )

    def _cache_drop(self, file_path: str) -> None:
        with self._media_lock:
            self._media_cache.pop(file_path, None)

    def upload_image(self, file_path: str, *, timeout: float = 45.0) -> Optional[str]:
        """上传本地图片，返回 media_id；失败返回 None（原因见日志）。

        2026-11-02：加 media_id 缓存 —— 同一张图（路径+mtime+大小不变）2 天内只上传一次。
        否则 20 个收件人就是 20 次上传，踩官方 30 次/分钟的限流。
        """
        cached = self._cache_get(file_path)
        if cached:
            return cached
        media_id = self._upload_fresh(file_path, timeout=timeout)
        if media_id:
            self._cache_put(file_path, media_id)
        return media_id

    def _upload_fresh(self, file_path: str, *, timeout: float = 45.0) -> Optional[str]:
        """强制真上传一次（不走缓存）。"""
        if self._client is None or not self._transport_ready():
            logger.warning("上传图片失败：长连接未就绪")
            return None
        try:
            with open(file_path, "rb") as fh:
                data = fh.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("上传图片读取失败 %s: %s", file_path, exc)
            return None
        fname = os.path.basename(file_path)
        try:
            media_id, err = self._run_sync(self._upload_image_async(data, fname), timeout)
        except (asyncio.TimeoutError, FutureTimeoutError):
            logger.warning("上传图片超时 %s", file_path)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("上传图片异常 %s: %s", file_path, exc)
            return None
        if not media_id:
            logger.warning("上传图片失败 %s: %s", file_path, err)
        return media_id

    def send_image(self, chatid: str, file_path: str, *, timeout: float = 60.0) -> SendResult:
        """发一条真正的图片消息：先三步上传拿 media_id，再 msgtype=image 发出去。

        ⚠️ 与 send_markdown 同样的约束：**不能**在 SDK 事件循环线程里同步调用本方法
           （内部会 run_coroutine_threadsafe 再等结果 → 自己等自己死锁）。
        """
        target = (chatid or "").strip()
        if not target:
            return SendResult(ok=False, result="failed", retryable=False, error="EMPTY_CHATID")
        if not self._transport_ready():
            return SendResult(ok=False, result="failed", retryable=True, error="NOT_CONNECTED")

        body: Dict[str, Any] = {"msgtype": "image", "image": {"media_id": ""}}
        for attempt in (0, 1):
            used_cache = self._cache_get(file_path) is not None
            media_id = self.upload_image(file_path, timeout=timeout)
            if not media_id:
                # 上传失败按可重试处理：可能是长连接抖动或频率限制（30 次/分钟）
                return SendResult(ok=False, result="failed", retryable=True, error="UPLOAD_FAILED")
            body["image"]["media_id"] = media_id
            try:
                frame = self._run_sync(self._client.send_message(target, body), timeout)
            except (asyncio.TimeoutError, FutureTimeoutError):
                return SendResult(ok=False, result="failed", retryable=True, error="SEND_TIMEOUT")
            except Exception as exc:  # noqa: BLE001
                return SendResult(ok=False, result="failed", retryable=True, error="EXCEPTION: %s" % exc)

            errcode, errmsg = self._frame_error(frame if isinstance(frame, dict) else {})
            if errcode == 0:
                return SendResult(ok=True, result="sent", status_code=200)
            # 用的是缓存、且被判失效（media_id 过期/被清理）：丢掉缓存重传一次
            if attempt == 0 and used_cache:
                logger.warning("缓存 media_id 被判失效（%s: %s），重传一次", errcode, errmsg)
                self._cache_drop(file_path)
                continue
            return SendResult(ok=False, result="failed", status_code=200,
                              retryable=False, error="%s: %s" % (errcode, errmsg))
        return SendResult(ok=False, result="failed", retryable=False, error="SEND_FAILED")


class WecomAibotNotifier:
    """把 AibotBridge 适配成 scheduler 需要的 NotifierProtocol。

    receive_id 语义：企微成员 userid（单聊 chatid 直接用 userid）。
    """

    def __init__(self, config: Any, *, bridge: Optional[AibotBridge] = None,
                 auto_start: Optional[bool] = None, on_text: Optional[Any] = None) -> None:
        bot_id = getattr(config, "wecom_bot_id", "")
        secret = getattr(config, "wecom_bot_secret", "")
        # 注入 bridge 说明生命周期由调用方管理，这里不重复 start（否则会把已就绪状态重置）
        self._owns_bridge = bridge is None
        self.bridge = bridge or AibotBridge(bot_id, secret, on_text=on_text)
        if on_text is not None and bridge is not None:
            # 注入的 bridge 可能是测试用假对象，没有 _on_text 也正常
            bridge._on_text = on_text  # noqa: SLF001
        if auto_start is None:
            auto_start = self._owns_bridge
        if not (bot_id and secret):
            logger.error(
                "企业微信智能机器人配置不完整（bot_id/secret 为空），发送会全部失败。"
                " 需要在 .env 里补 IMT_WECOM_BOT_ID / IMT_WECOM_BOT_SECRET"
            )
        elif auto_start:
            self.bridge.start()

    def send_text(self, receive_id: str, text: str, idem_key: str, receive_type: str = "user") -> SendResult:
        # 2026-11-10 方案A：receive_type 为 zdxlz union_id 机制专属参数，企微一律按 userid 投递，忽略。
        del receive_type
        """用 markdown 承载纯文本（企微机器人主动推送支持 markdown，纯文本原样即可）。"""
        try:
            return self.bridge.send_markdown(receive_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("企业微信智能机器人发送异常 to=%s: %s", receive_id, exc)
            return SendResult(ok=False, result="failed", retryable=True, error=f"EXCEPTION: {exc}")

    def send_image(self, receive_id: str, file_path: str, idem_key: str) -> SendResult:
        """发原生图片消息：调度器检测到通道有这个方法，就用图片替代 H5 链接。"""
        try:
            return self.bridge.send_image(receive_id, file_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("企业微信智能机器人发图异常 to=%s: %s", receive_id, exc)
            return SendResult(ok=False, result="failed", retryable=True, error=f"EXCEPTION: {exc}")

    def close(self) -> None:
        self.bridge.stop()
