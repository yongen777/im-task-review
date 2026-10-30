# -*- coding: utf-8 -*-
"""企业微信自建应用「接收消息」回调路由（/api/wecom/callback）。

独立于量子密信的 /api/app-events，两条通道并存互不影响。

官方约束（https://developer.work.weixin.qq.com/document/path/90930）：
- GET  验证 URL：验签 → 解密 echostr → **1 秒内**返回明文（不加引号、不带 BOM、不带换行）
- POST 业务数据：**5 秒内**响应；超时会断开并**总共重试 3 次**
  → 所以这里拿到明文后**立刻回 `success`**，业务丢到后台线程处理
- 官方原文建议「接受回调后立即应答，业务异步处理」，且「不要强依赖回调」

业务逻辑不在这里重写：解析出 (userid, 文本) 后调用 events.handle_inbound_text，
与量子密信共用同一份规则（绑定 / 待归纳 / 申请补交 / 闲聊 ack）。
"""
from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse

from ..state import StateStore
from .events import handle_inbound_text
from ..channels.wecom_crypto import WXBizMsgCrypt, error_text

logger = logging.getLogger(__name__)


# ── 报文解析 ────────────────────────────────────────────────────────
def parse_wecom_xml(xml_bytes: Any) -> Dict[str, str]:
    """企微回调明文 XML → 扁平 dict（只取一层，够用）。"""
    root = ET.fromstring(xml_bytes if isinstance(xml_bytes, (bytes, bytearray)) else str(xml_bytes).encode("utf-8"))
    return {child.tag: (child.text or "") for child in root}


# ── 幂等：企微 5 秒超时会重试 3 次，同一条消息可能重复进来 ──────────────
_SEEN: Dict[str, float] = {}
_SEEN_TTL = 300.0
_SEEN_MAX = 2000
_seen_lock = threading.Lock()


def _already_handled(msg_id: str) -> bool:
    if not msg_id:
        return False
    now = time.time()
    with _seen_lock:
        # 顺手清过期
        if len(_SEEN) > _SEEN_MAX:
            for k in [k for k, t in _SEEN.items() if now - t > _SEEN_TTL]:
                _SEEN.pop(k, None)
        if msg_id in _SEEN:
            return True
        _SEEN[msg_id] = now
        return False


def reset_dedupe_cache() -> None:
    """测试用：清空幂等缓存。"""
    with _seen_lock:
        _SEEN.clear()


# ── 业务分派（纯函数，便于单测）─────────────────────────────────────
def handle_wecom_event(
    *,
    state: StateStore,
    fields: Dict[str, str],
    notifier: Any = None,
) -> Tuple[int, Dict[str, Any]]:
    """把企微回调字段映射到现有业务分支，只做格式转换，⛔ 不重写规则。"""
    msg_type = str(fields.get("MsgType") or "").lower()
    userid = str(fields.get("FromUserName") or "").strip()
    if not userid:
        return 400, {"code": "VALIDATION_FAILED", "message": "回调缺少 FromUserName"}

    def _send(text: str, tag: str) -> None:
        if notifier is None:
            logger.warning("未注入 notifier，放弃回复 userid=%s tag=%s", userid, tag)
            return
        try:
            notifier.send_text(userid, text, f"wecom_{tag}")
        except Exception as exc:  # noqa: BLE001 - 回执失败不能影响主链路
            logger.warning("企微回执发送失败 userid=%s: %s", userid, exc)

    if msg_type == "text":
        content = str(fields.get("Content") or "")
        return handle_inbound_text(
            state=state, user_id=userid, msg_text=content, event_phone="", send=_send,
        )

    if msg_type == "event":
        event = str(fields.get("Event") or "").lower()
        if event in ("enter_agent", "enter_agent_temp"):
            # 本轮只记录，不接业务（用于确认回调链路通了）
            logger.info("企微成员进入应用 userid=%s event=%s", userid, event)
            return 200, {"ok": True, "event": event}
        return 200, {"ok": True, "ignored_event": event}

    # 图片/文件等暂不处理：IMTask 的业务入口只认文本
    logger.info("企微回调类型暂不处理 msgtype=%s userid=%s", msg_type, userid)
    return 200, {"ok": True, "ignored_msgtype": msg_type}


# ── 路由 ────────────────────────────────────────────────────────────
def create_wecom_router(
    state: StateStore,
    notifier: Any = None,
    *,
    token: str = "",
    aes_key: str = "",
    corpid: str = "",
) -> APIRouter:
    router = APIRouter()

    def _crypt() -> Optional[WXBizMsgCrypt]:
        if not (token and aes_key and corpid):
            return None
        try:
            # ⚠️ receiveid 用 corpid（企业 ID），不是 agentid
            return WXBizMsgCrypt(token, aes_key, corpid)
        except Exception as exc:  # noqa: BLE001
            logger.error("企微回调加解密初始化失败（检查 EncodingAESKey 是否为 43 位）: %s", exc)
            return None

    @router.get("/api/wecom/callback")
    async def wecom_verify(
        msg_signature: str = Query("", alias="msg_signature"),
        timestamp: str = Query("", alias="timestamp"),
        nonce: str = Query("", alias="nonce"),
        echostr: str = Query("", alias="echostr"),
    ):
        crypt = _crypt()
        if crypt is None:
            return PlainTextResponse("wecom callback not configured", status_code=503)
        ret, plain = crypt.VerifyURL(msg_signature, timestamp, nonce, echostr)
        if ret != 0 or plain is None:
            logger.warning("企微回调 URL 验证失败 ret=%s %s", ret, error_text(ret))
            return PlainTextResponse(error_text(ret), status_code=401)
        # ⛔ 原样返回明文：不能加引号、不能带 BOM、不能带换行
        return PlainTextResponse(plain.decode("utf-8"))

    @router.post("/api/wecom/callback")
    async def wecom_receive(
        request: Request,
        background: BackgroundTasks,
        msg_signature: str = Query("", alias="msg_signature"),
        timestamp: str = Query("", alias="timestamp"),
        nonce: str = Query("", alias="nonce"),
    ):
        crypt = _crypt()
        if crypt is None:
            return PlainTextResponse("wecom callback not configured", status_code=503)
        raw = (await request.body()).decode("utf-8", errors="replace")
        ret, plain = crypt.DecryptMsg(raw, msg_signature, timestamp, nonce)
        if ret != 0 or plain is None:
            logger.warning("企微回调解密失败 ret=%s %s", ret, error_text(ret))
            return PlainTextResponse("", status_code=401)

        try:
            fields = parse_wecom_xml(plain)
        except Exception as exc:  # noqa: BLE001
            logger.warning("企微回调 XML 解析失败: %s", exc)
            return PlainTextResponse("", status_code=400)

        msg_id = str(fields.get("MsgId") or fields.get("CreateTime") or "")
        if _already_handled(f"{msg_id}|{fields.get('FromUserName', '')}"):
            logger.info("企微回调重复投递，已忽略 msgid=%s", msg_id)
            return PlainTextResponse("success")

        # ⭐ 5 秒硬约束：先应答，业务异步处理
        background.add_task(handle_wecom_event, state=state, fields=fields, notifier=notifier)
        return PlainTextResponse("success")

    return router
