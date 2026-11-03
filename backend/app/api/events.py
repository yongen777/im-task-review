# -*- coding: utf-8 -*-
"""量子密信回调事件入口 POST /api/app-events（严格对齐规格书 §3.2、§7.3、v1.2 三条激活路径与三条护栏）。

说明（路径 A 写实口径）：
- 量子密信平台真实下发的回调事件仅含 loginId / userId / toUserId 等标识字段，不含手机号，因此路径 A 在常规回调下不会触发；
- 路径 A 仅在平台显式下发合法 11 位手机号字段（phone / mobile）时生效，绝不猜测或拿 loginId 等非手机号字段充当手机号；
- 实际生产环境的主导激活路径是路径 C（用户点击机器人下发的 /h5/bind 链接填写姓名/手机号/部门）；
  2026-11-03 起「对话里发姓名手机号」的文字激活（原路径 B）已彻底停用。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional, Tuple

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment,misc]

from ..channels.zdxlz import SignatureVerificationError, ZdxlzChannel, is_event_timestamp_fresh
from ..scheduler import NotifierProtocol
from ..state import BizError, StateStore, is_valid_phone

logger = logging.getLogger(__name__)


def _plain_event_allowed() -> bool:
    """联调开关：是否允许处理**未加密**的事件报文（默认关闭）。"""
    return (os.environ.get("IMT_ALLOW_PLAIN_EVENT", "").strip().lower()
            in ("1", "true", "yes"))


def _event_ts_window_sec() -> int:
    """2026-10-29 P2-2：回调时间戳允许的偏移秒数，默认 300 秒；设 0 可临时关闭。"""
    raw = os.environ.get("IMT_EVENT_TS_WINDOW_SEC", "").strip()
    if not raw:
        return 300
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _looks_like_plain_json(body_text: str) -> bool:
    text = (body_text or "").strip()
    return text.startswith("{") and text.endswith("}")


def _extract_user_id(payload: Dict[str, Any]) -> str:
    """从回调事件 JSON 中提取发送方量子密信 userId (external_id)。"""
    for key in ("userId", "user_id", "fromUserId", "from_user_id", "external_id", "openId"):
        val = payload.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val).strip()
    for nested_key in ("sender", "user", "from", "data", "event"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("userId", "user_id", "id", "external_id", "openId"):
                val = nested.get(key)
                if isinstance(val, (str, int)) and str(val).strip():
                    return str(val).strip()
            inner_sender = nested.get("sender")
            if isinstance(inner_sender, dict):
                for key in ("userId", "user_id", "id"):
                    val = inner_sender.get(key)
                    if isinstance(val, (str, int)) and str(val).strip():
                        return str(val).strip()
    return ""


def _extract_user_name(payload: Dict[str, Any]) -> str:
    """事件里若平台带发送人姓名就取出来（不同平台字段名不一）。

    ⛔ 不猜：只认明确的姓名字段，不能拿 loginId/userId 充数。
    """
    for key in ("userName", "user_name", "senderName", "sender_name", "nickName", "nick_name", "displayName"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:20]
    for nested_key in ("sender", "user", "from"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("name", "userName", "nickName", "displayName"):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()[:20]
    return ""


def _extract_event_phone(payload: Dict[str, Any]) -> str:
    """提取平台随事件显式下发的手机号（路径 A 仅在平台下发手机号时生效；真实事件通常只有 loginId/userId/toUserId，绝不猜字段）。"""
    for key in ("phone", "mobile"):
        val = payload.get(key)
        if isinstance(val, (str, int)) and is_valid_phone(str(val).strip()):
            return str(val).strip()
    for nested_key in ("sender", "user"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("phone", "mobile"):
                val = nested.get(key)
                if isinstance(val, (str, int)) and is_valid_phone(str(val).strip()):
                    return str(val).strip()
    return ""


def _extract_message_text(payload: Dict[str, Any]) -> str:
    """从回调事件 JSON 中提取发送方文本内容（未登记用户统一回登记链接；已绑定用户用于「申请补交」等指令）。"""
    candidates = [payload.get("text"), payload.get("content")]
    for nested_key in ("message", "msg", "data", "event"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            candidates.extend([nested.get("text"), nested.get("content")])

    for item in candidates:
        if item is None:
            continue
        if isinstance(item, dict):
            for k in ("content", "text"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        if isinstance(item, str):
            s = item.strip()
            if not s:
                continue
            if s.startswith("{") and s.endswith("}"):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, dict):
                        for k in ("content", "text"):
                            v = parsed.get(k)
                            if isinstance(v, str) and v.strip():
                                return v.strip()
                except Exception:
                    pass
            return s
    return ""


def handle_inbound_text(
    *,
    state: StateStore,
    user_id: str,
    msg_text: str,
    event_phone: str = "",
    send: Callable[[str, str], None],
    platform: str = "",
    user_name: str = "",
    base_url: str = "",
) -> Tuple[int, Dict[str, Any]]:
    """两条通道共用的业务入口：拿到 (user_id, 文本) 之后的全部处理。

    2026-10-30 企微自建应用接入时抽出：
      · 量子密信：process_app_event 验签+解密后调用本函数；
      · 企业微信：/api/wecom/callback 解出 XML 明文后调用本函数。
    ⛔ 业务规则（登记引导 / 待归纳 / 申请补交 / 闲聊 ack）只写这一份，新通道只做格式转换，不复制规则。

    2026-11-03 用户口径：激活登记一律走 H5 表单（姓名/手机号/部门），
    「对话里发姓名手机号」的文字激活路径彻底停用。
    """

    # 路径 A（仅在平台下发手机号时生效；常规事件仅有 loginId/userId/toUserId 不会进入此分支）
    if event_phone:
        try:
            res = state.bind_member_with_guardrails(phone=event_phone, external_id=user_id, path="A", platform=platform or None)
            send(res["receipt"], f"bind_A_{event_phone}")
            return 200, {
                "ok": True, "path": "A", "bound": True, "already_same": res.get("already_same", False),
                "masked_phone": res["masked_phone"], "phone_masked": res["masked_phone"], "message": res["receipt"],
            }
        except BizError as exc:
            send(exc.message, f"bind_A_err_{event_phone}")
            return exc.status_code, {"ok": False, "path": "A", "bound": False, "code": exc.code, "message": exc.message}

    bound_member = state.touch_delivery_target_by_external_id(user_id, platform=platform or None)
    if bound_member is not None:
        # 2026-10-29 逾期补交审核制：文本「申请补交」**优先**处理，
        # ⛔ 不落入待归纳池、也不走下面那条「看不懂聊天」的固定 ack。
        apply_text = (msg_text or "").strip().strip("【】[]　 ")
        if apply_text == "申请补交":
            try:
                res = state.request_resubmit(bound_member["phone"], actor=f"user:{user_id}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("补交申请处理异常 user=%s: %s", user_id, exc)
                res = {"result": "none", "items": []}
            result = res.get("result")
            if result == "applied":
                reply = "已收到补交申请，请等待管理员审核。"
                names = [str(i.get("template_name") or "").strip() for i in (res.get("items") or [])]
                names = [n for n in names if n]
                if names:
                    reply += "\n本次申请涉及：" + "、".join(names)
            elif result == "already":
                reply = "该任务已申请过补交，不可重复申请。"
            elif result == "window_expired":
                reply = "已超补交申请窗口（需在截止时间后 24 小时内申请），本次不受理。"
            else:
                reply = "当前没有可申请补交的逾期任务。"
            send(reply, f"resubmit_{result}")
            return 200, {"ok": True, "path": "resubmit", "bound": True, "result": result}

        # 2026-10-27 用户需求：已绑定用户发送非手机号消息时，机器人固定回复确认
        # （此前已绑定用户发闲聊消息会静默无回复，交互不闭环）。
        ack_text = (
            "【OPS任务调度助手】\n"
            "你好，消息已收到。我是任务调度助手，只负责按设定时间推送任务提醒、收集上报内容，\n"
            "暂时看不懂聊天和其他文字指令，这里不需要你回复文字。\n"
            "任务提醒会按时自动送达，届时按提醒里的链接或表单提交即可；业务问题请联系对应管理员。"
        )
        send(ack_text, "ack_nonphone")
        return 200, {"ok": True, "bound": True, "phone": bound_member["phone"]}

    # ── 2026-11-03 用户口径：激活登记一律走 H5 表单（姓名/手机号/部门）──
    # 未登记用户发任何文本 → 统一回同一条登记提示文本 + 专属链接；
    # 已通过链接提交过登记、还在等管理员分配角色的人 → 回等待文案（不再重复发链接）。
    pending_row = None
    try:
        pending_row = state.get_pending_member(user_id)
    except Exception:  # noqa: BLE001 - 查不到只当未登记处理
        pending_row = None
    if pending_row is not None and (pending_row.get("phone") or pending_row.get("name")):
        wait_text = (
            "【OPS任务调度助手】\n"
            "你的登记信息已收到，请等待管理员为你分配业务角色，分配完成后即可接收任务提醒。"
        )
        send(wait_text, "pending_wait")
        return 200, {"ok": True, "bound": False, "pending": True, "message": wait_text}

    try:
        state.upsert_pending_member(external_id=user_id, last_message=msg_text[:100], name=user_name or None, platform=platform or None)
    except Exception as exc:  # noqa: BLE001 - 登记失败不影响把提示文本发给用户
        logger.warning("pending_member 自动登记失败 external_id=%s: %s", user_id, exc)
    bind_url = f"{base_url.rstrip('/')}/h5/bind#uid={user_id}" + (f"&plat={platform}" if platform else "")
    prompt_text = (
        "【OPS任务调度助手】\n"
        "你还未完成信息登记。请点击下方链接填写【姓名、手机号、部门】，完成后即可接收任务提醒：\n"
        f"{bind_url}"
    )
    send(prompt_text, "register_prompt")
    return 200, {"ok": True, "bound": False, "pending": True, "bind_url": bind_url, "message": prompt_text}


def process_app_event(
    *,
    state: StateStore,
    channel: ZdxlzChannel,
    public_base_url: str,
    timestamp: str,
    nonce: str,
    signature: str,
    body_text: str,
    notifier: Optional[NotifierProtocol] = None,
) -> Tuple[int, Dict[str, Any]]:
    """§3.2 & §7.3 核心处理逻辑，返回 (status_code, body_dict)：
    - 验签失败一律返回 401，绝不进入绑定逻辑；
    - 路径 A（仅在平台下发手机号时生效）：平台真实事件仅有 loginId/userId/toUserId 不含手机号；
    - 路径 C（主导路径）：未登记用户回统一提示文本 + /h5/bind 登记链接（填写姓名/手机号/部门）；
      已登记待分配角色的用户回等待文案；已绑定用户支持「申请补交」指令与固定 ack。
    """
    base_url = public_base_url.rstrip("/")
    active_notifier: NotifierProtocol = notifier if notifier is not None else channel
    # 2026-10-29：不管防重放窗口开不开，每次回调都记一条时间戳样本，
    # 用来核对平台下发的是秒还是毫秒、以及和服务器时钟差多少（窗口=0 时不会走到拒绝分支，
    # 原来那条 warning 永远不会出现，等于观察不到）。
    if timestamp:
        try:
            _ts_raw = str(timestamp).strip()
            _ts_int = int(float(_ts_raw))
            _ts_unit = "s" if _ts_int < 10 ** 12 else "ms"
            _ts_ms = _ts_int * 1000 if _ts_unit == "s" else _ts_int
            logger.info(
                "[callback-ts] raw=%s digits=%d unit=%s skew=%+.1fs window=%ss",
                _ts_raw, len(_ts_raw), _ts_unit, (int(time.time() * 1000) - _ts_ms) / 1000.0,
                _event_ts_window_sec(),
            )
        except Exception:
            logger.info("[callback-ts] raw=%s (无法解析为数字)", timestamp)
    else:
        logger.info("[callback-ts] 缺失 X-CTQ-Timestamp 头")


    def _send_bot_reply(receive_id: str, text: str, tag: str) -> None:
        if not receive_id or not text:
            return
        idem = hashlib.sha1(f"event_reply|{receive_id}|{tag}|{state.now_str()}".encode("utf-8")).hexdigest()
        try:
            res = active_notifier.send_text(receive_id, text, idem)
            # 2026-11-03 修复：回执失败不允许再静默吞掉（此前 except: pass，
            # 通道错位时用户侧就是“完全不回话”，日志里却一片干净）。
            if not getattr(res, "ok", True):
                logger.warning(
                    "回调回复发送失败 receive_id=%s tag=%s result=%s error=%s",
                    receive_id, tag, getattr(res, "result", ""), getattr(res, "error", ""),
                )
        except Exception as exc:  # noqa: BLE001 - 回复失败不影响事件应答（仍 200）
            logger.warning("回调回复发送异常 receive_id=%s tag=%s: %s", receive_id, tag, exc)

    try:
        event_payload = channel.parse_callback_event(timestamp=timestamp, nonce=nonce, signature=signature, body_text=body_text)
    except SignatureVerificationError:
        # 2026-10-26 联调开关：平台事件订阅**可以配成明文推送**（官方文档：「事件请求体可能为加密内容，
        # 也可能为未加密 JSON，是否加密取决于事件订阅配置」）。明文没有签名，任何人伪造 POST 就能冒充绑定，
        # 所以⛔ 默认关闭，只在联调期显式打开 IMT_ALLOW_PLAIN_EVENT=1，并在审计里留痕。
        if _plain_event_allowed() and _looks_like_plain_json(body_text):
            try:
                event_payload = json.loads(body_text)
            except Exception:
                return 401, {"code": "UNAUTHORIZED", "message": "回调事件签名校验失败"}
            logger.warning("收到未加密事件报文（IMT_ALLOW_PLAIN_EVENT 已开启，联调期使用）：%s",
                           body_text[:200])
        else:
            return 401, {"code": "UNAUTHORIZED", "message": "回调事件签名校验失败"}
    except Exception as exc:
        return 400, {"code": "BAD_PAYLOAD", "message": f"回调事件解密失败: {exc}"}

    # 2026-10-29 P2-2：时间戳新鲜度（防重放）。只在校验了签名/有 timestamp 时生效，
    # 联调明文模式可省略时间戳，不受影响。
    if timestamp and not is_event_timestamp_fresh(timestamp, window_sec=_event_ts_window_sec()):
        logger.warning("回调时间戳超出窗口，按重放拒绝 timestamp=%s", timestamp)
        return 401, {"code": "UNAUTHORIZED", "message": "回调事件时间戳已过期（疑似重放）"}

    user_id = _extract_user_id(event_payload)
    user_name = _extract_user_name(event_payload)
    if not user_id:
        # 2026-10-27 平台「URL 测试」事件修复：平台点击测试下发专用报文
        # （实抓样本：msgUid="http-callback-test"、extra.test=true、userId 为空、
        #   content="HTTP callback test message"），此前被 400 拦下导致平台判定
        #   「回调地址不可达或未按预期返回 200」。测试事件只做连通确认，
        #   绝不进入绑定流程；真实业务事件仍严格校验 userId。
        extra = event_payload.get("extra")
        is_platform_test = (
            str(event_payload.get("msgUid") or "") == "http-callback-test"
            or (isinstance(extra, dict) and extra.get("test") is True)
        )
        if is_platform_test:
            logger.info("收到平台 URL 测试事件，返回 200 确认")
            return 200, {"ok": True, "test": True, "message": "callback test acknowledged"}
        return 400, {"code": "VALIDATION_FAILED", "message": "事件缺失 userId"}

    event_phone = _extract_event_phone(event_payload)
    msg_text = _extract_message_text(event_payload).strip()


    # 业务分支统一走 handle_inbound_text（两通道共用，见函数注释）
    return handle_inbound_text(
        state=state, user_id=user_id, msg_text=msg_text, event_phone=event_phone,
        send=lambda text, tag: _send_bot_reply(user_id, text, tag),
        platform="zdxlz", user_name=user_name, base_url=base_url,
    )


def create_events_router(
    state: StateStore, channel: ZdxlzChannel, public_base_url: str, notifier: Optional[NotifierProtocol] = None
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/app-events")
    async def handle_app_events(request: Request):
        timestamp = (request.headers.get("X-CTQ-Timestamp") or request.headers.get("X-Timestamp") or "").strip()
        nonce = (request.headers.get("X-CTQ-Nonce") or request.headers.get("X-Nonce") or "").strip()
        signature = (request.headers.get("X-CTQ-Signature") or request.headers.get("X-Signature") or "").strip()
        raw_bytes = await request.body()
        body_text = raw_bytes.decode("utf-8", errors="replace")
        status_code, content = process_app_event(
            state=state, channel=channel, public_base_url=public_base_url,
            timestamp=timestamp, nonce=nonce, signature=signature, body_text=body_text, notifier=notifier,
        )
        return JSONResponse(status_code=status_code, content=content)

    @router.get("/api/app-events")
    async def verify_app_events(request: Request):
        """平台「URL 测试」兜底（2026-10-27 补）。

        量子密信后台的「URL 测试」若用 GET + echostr 校验地址可达性，只有 POST 会返回 405，
        导致配置页一直提示验证失败。这里做最小兼容：
        - 带 echostr → 原样回显纯文本（平台的校验逻辑会认为通过）；
        - 不带 → 返回 200 + 最小 JSON，表明端点在线（便于运维用浏览器/curl 探活）。
        真正的业务处理仍然只走上面的 POST，GET 不写库、不改状态。
        """
        from fastapi.responses import PlainTextResponse

        echostr = (request.query_params.get("echostr") or "").strip()
        if echostr:
            logger.info("回调地址校验：回显 echostr（长度 %d）", len(echostr))
            return PlainTextResponse(echostr)
        return JSONResponse(
            status_code=200,
            content={"ok": True, "endpoint": "/api/app-events", "method": "POST",
                     "hint": "事件推送请用 POST；带 echostr 的 GET 会原样回显。"},
        )

    return router
