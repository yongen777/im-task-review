# -*- coding: utf-8 -*-
"""H5 任务提交与身份绑定接口（严格对齐规格书 §3.2、§3.7、§8、§9.8、R28~R40）。"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastapi import APIRouter, File, Form, Request, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
except ImportError:  # pragma: no cover
    APIRouter = File = Form = Request = UploadFile = HTMLResponse = JSONResponse = None  # type: ignore[assignment,misc]
    FileResponse = None  # type: ignore[assignment]

from ..scheduler import NotifierProtocol
from ..state import BizError, StateStore, calc_resubmit_deadline, format_deadline_rule, submission_expired_message

logger = logging.getLogger(__name__)

# 2026-10-28：补齐日常拍照格式——jpeg（Windows/相机常见）与 webp（微信转发常见）
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "jpg", "jpeg", "png", "webp"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
UPLOAD_READ_CHUNK_BYTES = 64 * 1024
MAX_HARD_UPLOAD_MB = 20

# 2026-10-29 P2-2：绑定接口按 IP 限流（内存计数，单进程生效；默认 5 次/分钟，设 0 关闭）
_BIND_RATE_LOCK = threading.Lock()
_BIND_RATE: Dict[str, List[float]] = {}


def _bind_rate_per_min() -> int:
    raw = os.environ.get("IMT_BIND_RATE_PER_MIN", "").strip()
    if not raw:
        return 5
    try:
        return max(0, int(raw))
    except ValueError:
        return 5


def reset_bind_rate() -> None:
    """测试用：清空绑定接口的 IP 限流计数。"""
    with _BIND_RATE_LOCK:
        _BIND_RATE.clear()


def _bind_rate_limited(client_ip: str) -> bool:
    limit = _bind_rate_per_min()
    if limit <= 0:
        return False
    now = time.time()
    key = client_ip or "unknown"
    with _BIND_RATE_LOCK:
        hits = [t for t in _BIND_RATE.get(key, []) if now - t < 60.0]
        if len(hits) >= limit:
            _BIND_RATE[key] = hits
            return True
        hits.append(now)
        _BIND_RATE[key] = hits
    return False

ZIP_BASED_MIMES = {
    "zip": "application/zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
OLE2_BASED_MIMES = {
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
}
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff"
PDF_MAGIC = b"%PDF-"
PK_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def sniff_magic_mime(ext: str, header_bytes: bytes) -> Optional[str]:
    """R35：通过文件头魔数嗅探真实 MIME，绝不信任客户端上传的 Content-Type。"""
    e = (ext or "").lower().lstrip(".")
    if not header_bytes:
        return None
    if e in ("jpg", "jpeg"):
        return "image/jpeg" if header_bytes.startswith(JPG_MAGIC) else None
    if e == "png":
        return "image/png" if header_bytes.startswith(PNG_MAGIC) else None
    if e == "webp":
        return "image/webp" if (header_bytes[:4] == b"RIFF" and header_bytes[8:12] == b"WEBP") else None
    if e == "pdf":
        return "application/pdf" if header_bytes.startswith(PDF_MAGIC) else None
    if e in ZIP_BASED_MIMES:
        return ZIP_BASED_MIMES[e] if header_bytes.startswith(PK_MAGICS) else None
    if e in OLE2_BASED_MIMES:
        return OLE2_BASED_MIMES[e] if header_bytes.startswith(OLE2_MAGIC) else None
    return None


def _extract_token_from_request(request: Request, body_token: Optional[str] = None) -> str:
    if body_token and str(body_token).strip():
        return str(body_token).strip()
    hdr = request.headers.get("X-H5-Token") or ""
    if hdr.strip():
        return hdr.strip()
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _check_submission_window(
    state: StateStore, inst: Dict[str, Any], tok: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str]]:
    if inst["status"] == "approved":
        return False, "任务已审核通过，无需重复提交"
    # 2026-11-12 任务C：cancelled（成员移出分组后作废）——旧链接进来窗口即关，
    # H5 显示只读提示；提交侧 submit_h5_task 还有服务端硬拦截兜底。
    if inst["status"] == "cancelled":
        return False, "该任务已被取消（成员已移出分组），无需提交"
    # 2026-11-12 任务E2/E3：截止即关窗——到点后统一提示
    # 「当前任务已过期（截止时间：…），无法提交」（用户拍板，不区分链接过期与任务截止）。
    # rejected 走 is_instance_window_closed 的重交截止分支（驳回重交机制保留）。
    if state.is_instance_window_closed(inst):
        return False, submission_expired_message(inst)
    return True, None


def load_h5_html(filename: str) -> str:
    target = Path(__file__).resolve().parent.parent / "h5" / filename
    return target.read_text(encoding="utf-8") if target.is_file() else f"<!DOCTYPE html><html><body><h1>{filename}</h1></body></html>"


def process_h5_bind(
    *, state: StateStore, data: Any,
    notifier: Optional[NotifierProtocol] = None,
    notifiers: Optional[Dict[str, NotifierProtocol]] = None,
    contacts: Optional[Any] = None,
) -> Tuple[int, Dict[str, Any]]:
    """§3.2 路径 C：H5 提交 11 位手机号完成绑定（严格执行三条护栏 + 审计 + 回执）。

    2026-11-03 多通道修正：原先回执固定用 active_notifier（实际就是密信通道），
    企微用户走 H5 绑定时会把回执发给密信通道，而 external_id 是企微 userid →
    必然失败且被静默吞掉，用户看不到回执。
    现在**绑哪个平台就用哪个平台的 notifier**：
      · platform 优先取请求体里的 platform（前端可显式声明），缺省用 state.platform；
      · 绑定本身也显式带 platform，保证「绑定记录 / 回执通道」是同一个平台。
    """
    if not isinstance(data, dict):
        return 400, {"code": "VALIDATION_FAILED", "message": "请求体必须为 JSON 对象"}
    phone = str(data.get("phone") or "").strip()
    external_id = str(data.get("external_id") or data.get("uid") or data.get("user_id") or data.get("userId") or "").strip()
    platform = str(data.get("platform") or "").strip().lower() or state.platform

    # 2026-11-03 新激活模型（用户/组长拍板）：后台先导入，用户才能激活。
    #   唯一路径：管理员把人员导入角色 → 用户发消息收链接 → 填表与后台名册比对：
    #   · 手机号+姓名都一致（部门不拦截）→ 绑定成功；
    #   · 手机号在名册但姓名不符 → 提示「信息与后台不一致」；
    #   · 手机号不在本平台名册 → 提示「管理员尚未在后台导入你的信息，请联系管理员」。
    #   ⛔ 不再有待归纳池流程。
    name = str(data.get("name") or "").strip()
    dept = str(data.get("dept") or "").strip()
    if not external_id:
        return 400, {"code": "VALIDATION_FAILED", "message": "链接缺少用户标识，请从机器人下发的链接进入"}
    if not name or len(name) > 20:
        return 400, {"code": "VALIDATION_FAILED", "message": "请填写姓名（1~20 个字）"}
    if not dept:
        return 400, {"code": "VALIDATION_FAILED", "message": "请选择部门"}

    # 防重复提交：已绑定的账号不允许「退出重进再提交覆盖」（同号回执、换号拒绝）
    bound = state.get_binding_by_external_id(external_id, platform=platform)
    if bound:
        bound_phone = str(bound.get("phone") or "")
        masked_bound = bound_phone[:3] + "****" + bound_phone[-4:] if len(bound_phone) == 11 else ""
        if bound_phone and bound_phone != phone:
            return 409, {"code": "DUPLICATE",
                        "message": f"该账号已绑定手机号 {masked_bound}，请勿重复登记；如需更换手机号请联系管理员"}
        return 200, {"ok": True, "path": "C", "bound": True, "already_same": True,
                    "phone": bound_phone, "masked_phone": masked_bound, "phone_masked": masked_bound,
                    "message": f"当前账号已关联手机号 {masked_bound}，无需重复登记。"}

    # 防冒绑：该手机号在本平台已被别的账号绑定时拒绝（与原护栏一致）
    existing = state.get_delivery_target(phone, platform=platform)
    owner = str((existing or {}).get("external_id") or "") if existing else ""
    if existing and owner and owner != external_id:
        return 409, {"code": "DUPLICATE", "message": "该手机号已被其他账号绑定，禁止覆盖；如非本人操作请联系管理员申诉"}
    if existing and owner == external_id:
        masked = phone[:3] + "****" + phone[-4:]
        return 200, {"ok": True, "path": "C", "bound": True, "already_same": True,
                    "phone": phone, "masked_phone": masked, "phone_masked": masked,
                    "message": f"当前账号已关联手机号 {masked}，无需重复登记。"}

    # 与后台名册比对：手机号必须在册，姓名必须一致（部门不拦截）
    if not state.phone_in_roster(phone, platform=platform):
        return 400, {"code": "NOT_IN_ROSTER",
                    "message": "管理员尚未在后台导入你的信息，请联系管理员"}
    matched = state.match_roster_member(phone, name, platform=platform)
    if matched is None:
        return 400, {"code": "ROSTER_MISMATCH",
                    "message": "你填写的信息与后台导入的信息不一致，请核对姓名与手机号后重新提交"}

    # 比对通过：完成绑定（H5 链接里的 uid 是 IM userId，先按旧口径写入）
    try:
        res = state.bind_member_with_guardrails(
            phone=phone, external_id=external_id, path="C", platform=platform
        )
    except BizError as exc:
        return exc.status_code, {"code": exc.code, "message": exc.message}

    # 2026-11-10 方案A：官方通讯录能按手机号查到 union_id 时，改写为 union_id 绑定
    # （此后推送不再要求该用户先跟机器人说过话）；查不到则保持 IM 绑定
    # （外部人员 / 通讯录降级时仍可用 —— 但依赖会话，行为同旧版）。
    if contacts is not None and platform == "zdxlz":
        try:
            user = contacts.search_by_phone(phone)
            union_id = str((user or {}).get("union_id") or "").strip()
            if union_id:
                state.upsert_delivery_target(phone, union_id, platform="zdxlz", external_type="union_id")
        except Exception:  # noqa: BLE001 - 改写失败不影响登记结果（上一行已按 IM 绑定成功）
            pass

    # 回执通道 = 绑定平台对应的通道；没配该通道时才退回传入的单 notifier
    nt = (notifiers or {}).get(platform) or notifier
    if nt is not None and external_id:
        idem = hashlib.sha1(f"bind_C|{external_id}|{phone}|{state.now_str()}".encode("utf-8")).hexdigest()
        try:
            nt.send_text(external_id, res["receipt"], idem)
        except Exception:
            pass

    return 200, {
        "ok": True, "path": "C", "bound": True, "phone": res["phone"],
        "masked_phone": res["masked_phone"], "phone_masked": res["masked_phone"], "message": res["receipt"],
    }


def _resolve_token_and_instance(state: StateStore, raw_token: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Tuple[int, Dict[str, Any]]]]:
    token_str = (raw_token or "").strip()
    if not token_str:
        return None, None, (401, {"code": "TOKEN_INVALID", "message": "缺少 H5 访问令牌"})
    tok = state.get_h5_token(token_str)
    if not tok:
        return None, None, (401, {"code": "TOKEN_INVALID", "message": "无效的 H5 访问令牌"})
    if tok["expires_at"] < state.now_str():
        # 2026-11-12 任务E2：链接过期提示与任务截止统一口径（用户拍板「不区分」），
        # 尽力解析实例拿真实截止时间；实例都没了才说「令牌已过期」。
        expired_inst: Optional[Dict[str, Any]] = None
        if tok.get("instance_id") is not None:
            expired_inst = state.get_instance(int(tok["instance_id"]))
        if expired_inst is None and tok.get("template_id") and tok.get("period_key"):
            expired_inst = state.get_instance_by_key(tok["template_id"], tok["phone"], tok["period_key"])
        if expired_inst is not None:
            return None, None, (401, {"code": "TOKEN_EXPIRED", "message": submission_expired_message(expired_inst)})
        return None, None, (401, {"code": "TOKEN_EXPIRED", "message": "H5 访问令牌已过期"})

    inst: Optional[Dict[str, Any]] = None
    if tok.get("instance_id") is not None:
        inst = state.get_instance(int(tok["instance_id"]))
    if inst is None and tok.get("template_id") and tok.get("period_key"):
        inst = state.get_instance_by_key(tok["template_id"], tok["phone"], tok["period_key"])
    if inst is None:
        # 2026-11-12 下午（四）：令牌有效但实例已不存在（模板物理删除/移除角色清理）→
        # 统一「任务已关闭」（不暴露「任务实例不存在」技术报错）
        return None, None, (403, {"code": "TASK_CLOSED", "message": "任务已关闭"})
    return tok, inst, None


def _validate_upload_target_and_limits(
    state: StateStore, raw_token: str, filename: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str, int, int, Optional[Tuple[int, Dict[str, Any]]]]:
    """前置校验令牌、窗口、模板类型与扩展名，计算该模板允许的最大字节数（未通过前绝不读取或落盘文件体）。"""
    tok, inst, err = _resolve_token_and_instance(state, raw_token)
    if err:
        return None, None, "", "", 0, 0, err

    tpl = state.get_template(inst["template_id"], include_deleted=True)
    # 2026-11-12 下午（四）：五类失效统一拦截「任务已关闭」，先于窗口判断；
    # 模板被物理删除后靠保留的 h5_token 行识别（不再是「该任务已被删除」技术分支）。
    close_reason = state.instance_close_reason(inst, tpl)
    if close_reason:
        return None, None, "", "", 0, 0, (403, {"code": "TASK_CLOSED", "message": close_reason})

    window_open, window_reason = _check_submission_window(state, inst, tok)
    if not window_open:
        # 2026-11-12 任务E2：到点后统一错误码 SUBMISSION_CLOSED + 统一文案
        return None, None, "", "", 0, 0, (400, {"code": "SUBMISSION_CLOSED", "message": window_reason or "当前任务提交窗口已关闭"})
    spec = tpl.get("spec_json") if isinstance(tpl.get("spec_json"), dict) else {}
    modules = spec.get("upload_modules") or []
    # 2026-10-28：上报类（form）勾了「图片 / 文件」模块也要允许上传，之前只放行 image/file 两种模板
    allows_upload = tpl["kind"] in ("image", "file") or (
        tpl["kind"] == "form" and any(m in modules for m in ("image", "file"))
    )
    if not allows_upload:
        return None, None, "", "", 0, 0, (400, {"code": "VALIDATION_FAILED", "message": "当前任务类型不支持上传文件"})

    orig_name = os.path.basename(filename or "").strip()
    if "." not in orig_name:
        return None, None, "", "", 0, 0, (400, {"code": "FILE_TYPE_REJECTED", "message": "文件缺少扩展名"})
    ext = orig_name.rsplit(".", 1)[-1].lower().strip()

    if ext not in ALLOWED_EXTENSIONS:
        return None, None, "", "", 0, 0, (400, {"code": "FILE_TYPE_REJECTED", "message": f"不支持的文件类型: .{ext}"})
    if tpl["kind"] == "image" and ext not in IMAGE_EXTENSIONS:
        return None, None, "", "", 0, 0, (400, {"code": "FILE_TYPE_REJECTED", "message": "图片类任务仅允许上传 jpg 或 png 格式"})

    default_max_mb = 9 if tpl["kind"] == "image" else MAX_HARD_UPLOAD_MB
    max_mb = min(int(tpl.get("max_file_mb") or default_max_mb), default_max_mb)
    max_bytes = max_mb * 1024 * 1024
    return inst, tpl, orig_name, ext, max_mb, max_bytes, None


async def read_upload_file_bounded(file: UploadFile, max_bytes: int) -> Tuple[Optional[bytes], bool]:
    """分块读取上传流（每块 64KB），一旦累计字节数超过 max_bytes 立即中止读取，防止大文件撑爆内存。"""
    buf = bytearray()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            return None, True
    return bytes(buf), False


def process_h5_task_info(*, state: StateStore, raw_token: str) -> Tuple[int, Dict[str, Any]]:
    """§9.8 H5 加载任务实例与模板详情。

    2026-11-12 下午（用户拍板，一-1/二-3/四）：旧链接**实时**读模板最新配置——
    页面渲染的名称/说明/字段/上传组件始终取库里最新的模板行（保存即生效）；
    已提交/已审核通过实例 H5 **打开放行**（正常渲染表单，window_open=True），
    重复提交只在最后一步「提交」时由后端终审拦截「已上传，请勿重复提交」。
    """
    tok, inst, err = _resolve_token_and_instance(state, raw_token)
    if err:
        return err
    tpl = state.get_template(inst["template_id"], include_deleted=True)
    # 2026-11-12 下午（四）：五类失效统一拦截「任务已关闭」（先于过期窗口判断）
    close_reason = state.instance_close_reason(inst, tpl)
    if close_reason:
        return 403, {"code": "TASK_CLOSED", "message": close_reason}

    window_open, window_reason = _check_submission_window(state, inst, tok)
    # 已提交/已审核通过 → 页面正常打开渲染表单（removed 只读锁死：用户拍板二-3）；
    # 令牌已消耗同样放行（提交侧有终审拦截，页面可用性不再由 consumed 决定）。
    if inst.get("status") in ("submitted", "approved"):
        window_open = True
        window_reason = ""
    # 2026-10-28：驳回后重交场景，前端要显示「重交截止」；统一在这里算好下发，
    # 避免页面自己猜重交窗口（与服务端 is_instance_window_closed 保持同一算法）。
    if inst.get("status") == "rejected":
        inst["resubmit_deadline"] = calc_resubmit_deadline(inst["deadline_at"], inst.get("reviewed_at"))
    return 200, {
        "ok": True,
        "window_open": window_open,
        "window_reason": window_reason,
        "instance": inst,
        "template": {
            "template_id": tpl["template_id"],
            "name": tpl["name"],
            "kind": tpl["kind"],
            "period": tpl["period"],
            "deadline": tpl["deadline"],
            # 2026-11-12 下午：统一截止规则样式（本月月底 17:00 / 本周日 17:00 / 今天 17:00 / 不限），
            # 旧链接保存模板后顶部徽章随最新配置实时更新（一-2 动态重算后 deadline_at 同步）。
            "deadline_rule": format_deadline_rule(tpl),
            "has_deadline": tpl.get("has_deadline"),
            "deadline_type": tpl.get("deadline_type"),
            "deadline_time": tpl.get("deadline_time"),
            "allow_note": tpl["allow_note"],
            "schema_json": tpl.get("schema_json"),
            "spec_json": tpl.get("spec_json"),
            # 2026-10-28：H5 要按后台勾选的模块（字段 / 文字说明 / 图片 / 文件）逐个渲染，
            # 之前只读 schema_json，导致勾了图片和文字说明也只有一个数字输入框。
            "upload_modules": list(((tpl.get("spec_json") or {}) if isinstance(tpl.get("spec_json"), dict) else {}).get("upload_modules") or []),
            "text_label": ((tpl.get("spec_json") or {}) if isinstance(tpl.get("spec_json"), dict) else {}).get("text_label") or "文字情况说明",
            "text_placeholder": ((tpl.get("spec_json") or {}) if isinstance(tpl.get("spec_json"), dict) else {}).get("text_placeholder") or "请填写补充说明…",
            "max_file_mb": tpl.get("max_file_mb"),
            "need_review": tpl.get("need_review"),
        },
    }


def process_h5_task_upload(
    *, state: StateStore, uploads_dir: Path, raw_token: str, filename: str, content_bytes: bytes
) -> Tuple[int, Dict[str, Any]]:
    """§3.7 & §9.8 H5 附件上传（扩展名白名单 + 大小限制 + 魔数嗅探 MIME + 校验失败磁盘零残留）。"""
    inst, tpl, orig_name, ext, max_mb, max_bytes, err = _validate_upload_target_and_limits(state, raw_token, filename)
    if err:
        return err
    assert inst is not None and tpl is not None

    if len(content_bytes) == 0:
        return 400, {"code": "FILE_TYPE_REJECTED", "message": "禁止上传空文件"}
    if len(content_bytes) > max_bytes:
        return 400, {"code": "FILE_TOO_LARGE", "message": f"文件大小超出上限（最大 {max_mb}MB）"}

    detected_mime = sniff_magic_mime(ext, content_bytes[:32])
    if not detected_mime:
        return 400, {"code": "FILE_TYPE_REJECTED", "message": "文件头魔数与扩展名不匹配，拒绝上传"}

    ref = uuid.uuid4().hex
    rel_path = f"{inst['period_key']}/{tpl['template_id']}/{ref}.{ext}"
    disk_path = uploads_dir / inst["period_key"] / tpl["template_id"] / f"{ref}.{ext}"
    disk_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        disk_path.write_bytes(content_bytes)
        rec = state.create_upload_ref(
            ref=ref, phone=inst["phone"], template_id=tpl["template_id"],
            file_name=orig_name, mime=detected_mime, size=len(content_bytes), rel_path=rel_path,
        )
    except Exception:
        if disk_path.exists():
            try:
                disk_path.unlink()
            except OSError:
                pass
        raise

    return 200, {
        "ok": True, "ref": rec["ref"], "file_name": rec["file_name"], "orig_name": rec["file_name"],
        "mime": rec["mime"], "size": rec["size"], "rel_path": rec["rel_path"],
    }


def _push_submit_feedback(
    *, state: StateStore, inst: Optional[Dict[str, Any]], submission_id: Any,
    notifier: Optional[NotifierProtocol] = None,
    notifiers: Optional[Dict[str, NotifierProtocol]] = None,
) -> None:
    """2026-11-03 用户口径：任务提交成功后给提交人回推一条「【任务名】提交成功」。

    · 通道 = 任务所属办公软件（与调度投递同口径），不串到别的平台；
    · 幂等：发送动作之前查重，同一 submission 只推一条；
    · 发送/记账失败只记日志，绝不影响提交结果本身。
    """
    try:
        if not inst:
            return
        tpl = state.get_template(str(inst.get("template_id") or "")) or {}
        plats = state.role_platforms(list(tpl.get("role_ids") or []))
        platform = plats.pop() if len(plats) == 1 else ""
        nt = (notifiers or {}).get(platform) if (platform and notifiers) else notifier
        if nt is None:
            logger.warning("提交反馈未发送（无可用通道）instance=%s", inst.get("id"))
            return
        target = (
            state.get_delivery_target(str(inst.get("phone") or ""), platform=platform)
            if (platform and notifiers)
            else state.get_delivery_target(str(inst.get("phone") or ""))
        )
        ext_id = str((target or {}).get("external_id") or "")
        ext_type = str((target or {}).get("external_type") or "user").strip().lower() or "user"
        if not ext_id:
            logger.warning("提交反馈未发送（成员未激活绑定）instance=%s", inst.get("id"))
            return
        now_s = state.now_str()
        idem = hashlib.sha1(f"submit_ack|{inst.get('id')}|{submission_id}".encode("utf-8")).hexdigest()
        if state.has_sent_reminder(idem):
            return
        res = nt.send_text(ext_id, f"【{tpl.get('name') or '任务'}】提交成功", idem, receive_type=ext_type)
        state.record_reminder_log(
            template_id=str(inst.get("template_id") or ""), instance_id=int(inst["id"]),
            phone=str(inst.get("phone") or ""), kind="submit_ack", planned_at=now_s,
            attempted_at=now_s, sent_at=now_s if res.ok else None,
            result="sent" if res.ok else "failed", retry=0,
            error=None if res.ok else (res.error or "SEND_FAILED"), idem_key=idem,
        )
    except Exception as exc:  # noqa: BLE001 - 反馈回推绝不能把提交接口拖挂
        logger.warning("提交反馈回推异常 instance=%s: %s", (inst or {}).get("id"), exc)


def process_h5_task_submit(
    *, state: StateStore, raw_token: str, data: Any,
    notifier: Optional[NotifierProtocol] = None,
    notifiers: Optional[Dict[str, NotifierProtocol]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """§8 & §9.8 H5 任务提交核心逻辑。"""
    if not isinstance(data, dict):
        return 400, {"code": "VALIDATION_FAILED", "message": "请求体必须为 JSON 对象"}

    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else payload.get("fields")
    file_ref = data.get("file_ref") or payload.get("file_ref")
    refs_raw = data.get("refs") if isinstance(data.get("refs"), list) else payload.get("refs")
    refs = [str(r) for r in (refs_raw or []) if str(r or "").strip()]
    if not file_ref and refs:
        file_ref = refs[0]
    note = data.get("note") if data.get("note") is not None else payload.get("note")

    try:
        # ⭐ 2026-10-28：上报类模板可同时提交「字段 + 文字说明 + 多张图片/文件」，
        # 之前只透传单个 file_ref，勾了图片/文件模块也存不下来。
        res = state.submit_h5_task(raw_token=raw_token, fields=fields, file_ref=file_ref, note=note, refs=refs)
    except BizError as exc:
        return exc.status_code, {"code": exc.code, "message": exc.message}

    tok = state.get_h5_token(raw_token)
    inst = state.get_instance(int(tok["instance_id"])) if tok and tok.get("instance_id") else None
    # 2026-11-03 用户口径：提交成功后回推一条「【任务名】提交成功」
    _push_submit_feedback(state=state, inst=inst, submission_id=res.get("submission_id"), notifier=notifier, notifiers=notifiers)
    return 200, {**res, "instance": inst}


def create_h5_router(
    state: StateStore, uploads_dir: Path,
    notifier: Optional[NotifierProtocol] = None,
    notifiers: Optional[Dict[str, NotifierProtocol]] = None,
    contacts: Optional[Any] = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/h5/task", response_class=HTMLResponse)
    async def h5_task_page():
        return HTMLResponse(content=load_h5_html("task.html"))

    @router.get("/h5/bind", response_class=HTMLResponse)
    async def h5_bind_page():
        return HTMLResponse(content=load_h5_html("bind.html"))

    # 2026-11-03：H5 登记表单的部门下拉选项（后台管理员配置，公开只读）
    @router.get("/api/h5/departments")
    async def api_h5_departments():
        return {"ok": True, "items": state.list_departments()}

    # 2026-11-03：登记页打开时查询是否已绑定（已绑定则页面直接锁定，防重复提交）
    @router.get("/api/h5/bind/status")
    async def api_h5_bind_status(uid: str = "", platform: str = ""):
        ext = (uid or "").strip()
        plat = (platform or "").strip().lower() or state.platform
        if not ext:
            return {"ok": True, "registered": False}
        bound = state.get_binding_by_external_id(ext, platform=plat)
        if bound:
            p = str(bound.get("phone") or "")
            masked = p[:3] + "****" + p[-4:] if len(p) == 11 else ""
            return {"ok": True, "registered": True, "bound": True, "phone_masked": masked}
        return {"ok": True, "registered": False}

    # 2026-10-29：通知附图的「公网直链」。平台 CDN 返回 application/octet-stream、
    # 且 32KB 的图要 5s+ 才下发完，手机端量子密信的图片消息一直转圈打不开（PC 端因有缓存才正常）。
    # 这里提供一条我们自己的直链：文件名是 uuid4（不可枚举），Content-Type 由扩展名推断，
    # 同域同端口，员工手机点开即看。仅供兜底，不影响原有图片消息。
    #
    # 2026-10-29 下午 H5 图文页改造（用户确认方案）：
    # - 不带 ?raw=1 时返回 HTML 页面：标题 = 发布时写入的同名 .title 文件（管理员填的图片名称），
    #   页面内 <img> 指向本 URL 的 ?raw=1 原图；无 .title 文件（旧直链）回退「数据图片」。
    # - ?raw=1 时返回原始图片字节（保留给直接引图的场景）。
    @router.get("/h5/img/{name}")
    async def h5_public_image(name: str, raw: Optional[str] = None):
        if FileResponse is None or HTMLResponse is None:  # pragma: no cover
            return JSONResponse(status_code=500, content={"code": "UNAVAILABLE", "message": "服务未就绪"})
        clean = (name or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|jpeg|png)", clean):
            return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "图片不存在"})
        img_dir = (uploads_dir.parent / "public_img").resolve()
        target = (img_dir / clean).resolve()
        if not target.is_file() or not target.is_relative_to(img_dir):
            return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "图片不存在"})
        if (raw or "").strip().lower() in ("1", "true", "yes"):
            return FileResponse(str(target), headers={"Cache-Control": "public, max-age=86400"})
        # H5 图文页：标题取发布时写入的同名 .title 文件（管理员填写的图片名称）
        title = "数据图片"
        try:
            title_file = img_dir / f"{clean}.title"
            if title_file.is_file():
                t = title_file.read_text(encoding="utf-8").strip()
                if t:
                    title = t
        except OSError:  # noqa: BLE001
            pass
        import html as _html
        safe_title = _html.escape(title)
        src_url = f"/h5/img/{clean}?raw=1"
        # 2026-10-30 用户口径：
        # 1) **不再渲染页面内的白色标题**——顶部浏览器/聊天窗已经显示 <title>，两个标题重复；
        # 2) **允许缩放**——原 viewport 写了 maximum-scale=1，把双指缩放彻底禁掉了；
        #    同时支持「点一下放大到原始尺寸、再点还原」，方便看报表细节。
        page = (
            "<!DOCTYPE html>\n"
            "<html lang=\"zh-CN\">\n"
            "<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{safe_title}</title>\n"
            "<style>\n"
            "html,body{margin:0;padding:0;background:#0f172a;min-height:100vh;"
            "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"
            "-webkit-text-size-adjust:100%;}\n"
            ".wrap{max-width:860px;margin:0 auto;padding:12px 12px 32px;box-sizing:border-box;"
            "overflow-x:auto;-webkit-overflow-scrolling:touch;}\n"
            "img{display:block;width:100%;max-width:100%;height:auto;border-radius:10px;"
            "box-shadow:0 6px 24px rgba(0,0,0,.35);background:#fff;touch-action:pinch-zoom;cursor:zoom-in;}\n"
            "</style>\n"
            "</head>\n"
            "<body>\n"
            "<div class=\"wrap\">\n"
            f"<img id=\"img\" src=\"{src_url}\" alt=\"{safe_title}\" loading=\"lazy\">\n"
            "</div>\n"
            "<script>\n"
            "(function(){var img=document.getElementById('img');var big=false;\n"
            "img.addEventListener('click',function(){big=!big;\n"
            "img.style.width=big?'auto':'100%';img.style.maxWidth=big?'none':'100%';\n"
            "img.style.cursor=big?'zoom-out':'zoom-in';\n"
            "if(!big){window.scrollTo(0,0);}});})();\n"
            "</script>\n"
            "</body>\n"
            "</html>\n"
        )
        return HTMLResponse(content=page)

    @router.post("/api/h5/bind")
    async def api_h5_bind(request: Request):
        # 2026-10-29 P2-2：绑定接口无令牌、只有手机号，按 IP 限流，挡批量撞号
        client_ip = request.client.host if getattr(request, "client", None) else "unknown"
        if _bind_rate_limited(client_ip):
            return JSONResponse(status_code=429, content={"code": "RATE_LIMITED", "message": "操作过于频繁，请稍后重试"})
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"code": "VALIDATION_FAILED", "message": "请求体必须为合法 JSON"})
        status_code, content = process_h5_bind(state=state, data=data, notifier=notifier, notifiers=notifiers, contacts=contacts)
        return JSONResponse(status_code=status_code, content=content)

    @router.post("/api/h5/task/info")
    async def api_h5_task_info(request: Request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        raw_token = _extract_token_from_request(request, data.get("token") if isinstance(data, dict) else None)
        status_code, content = process_h5_task_info(state=state, raw_token=raw_token)
        return JSONResponse(status_code=status_code, content=content)

    @router.post("/api/h5/task/upload")
    async def api_h5_task_upload(request: Request, file: UploadFile = File(...), token: Optional[str] = Form(default=None)):
        raw_token = _extract_token_from_request(request, token)
        filename = file.filename or ""
        _, _, _, _, max_mb, max_bytes, err = _validate_upload_target_and_limits(state, raw_token, filename)
        if err:
            return JSONResponse(status_code=err[0], content=err[1])

        content_bytes, too_large = await read_upload_file_bounded(file, max_bytes)
        if too_large or content_bytes is None:
            return JSONResponse(
                status_code=400,
                content={"code": "FILE_TOO_LARGE", "message": f"文件大小超出上限（最大 {max_mb}MB）"},
            )

        status_code, content = process_h5_task_upload(
            state=state, uploads_dir=uploads_dir, raw_token=raw_token, filename=filename, content_bytes=content_bytes
        )
        return JSONResponse(status_code=status_code, content=content)

    @router.post("/api/h5/task/submit")
    async def api_h5_task_submit(request: Request):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"code": "VALIDATION_FAILED", "message": "请求体必须为合法 JSON"})
        if not isinstance(data, dict):
            return JSONResponse(status_code=400, content={"code": "VALIDATION_FAILED", "message": "请求体必须为 JSON 对象"})
        raw_token = _extract_token_from_request(request, data.get("token"))
        status_code, content = process_h5_task_submit(
            state=state, raw_token=raw_token, data=data, notifier=notifier, notifiers=notifiers
        )
        return JSONResponse(status_code=status_code, content=content)

    return router
