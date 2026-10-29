# -*- coding: utf-8 -*-
"""H5 任务提交与身份绑定接口（严格对齐规格书 §3.2、§3.7、§8、§9.8、R28~R40）。"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastapi import APIRouter, File, Form, Request, UploadFile
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:  # pragma: no cover
    APIRouter = File = Form = Request = UploadFile = HTMLResponse = JSONResponse = None  # type: ignore[assignment,misc]

from ..scheduler import NotifierProtocol
from ..state import BizError, StateStore, calc_resubmit_deadline

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


def _check_submission_window(state: StateStore, inst: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if state.is_instance_window_closed(inst):
        return False, "提交窗口已关闭"
    if inst["status"] == "approved":
        return False, "任务已审核通过，无需重复提交"
    return True, None


def load_h5_html(filename: str) -> str:
    target = Path(__file__).resolve().parent.parent / "h5" / filename
    return target.read_text(encoding="utf-8") if target.is_file() else f"<!DOCTYPE html><html><body><h1>{filename}</h1></body></html>"


def process_h5_bind(
    *, state: StateStore, data: Any, notifier: Optional[NotifierProtocol] = None
) -> Tuple[int, Dict[str, Any]]:
    """§3.2 路径 C：H5 提交 11 位手机号完成绑定（严格执行三条护栏 + 审计 + 回执）。"""
    if not isinstance(data, dict):
        return 400, {"code": "VALIDATION_FAILED", "message": "请求体必须为 JSON 对象"}
    phone = str(data.get("phone") or "").strip()
    external_id = str(data.get("external_id") or data.get("uid") or data.get("user_id") or data.get("userId") or "").strip()

    try:
        res = state.bind_member_with_guardrails(phone=phone, external_id=external_id, path="C")
    except BizError as exc:
        return exc.status_code, {"code": exc.code, "message": exc.message}

    if notifier is not None and external_id:
        idem = hashlib.sha1(f"bind_C|{external_id}|{phone}|{state.now_str()}".encode("utf-8")).hexdigest()
        try:
            notifier.send_text(external_id, res["receipt"], idem)
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
        return None, None, (401, {"code": "TOKEN_EXPIRED", "message": "H5 访问令牌已过期"})

    inst: Optional[Dict[str, Any]] = None
    if tok.get("instance_id") is not None:
        inst = state.get_instance(int(tok["instance_id"]))
    if inst is None and tok.get("template_id") and tok.get("period_key"):
        inst = state.get_instance_by_key(tok["template_id"], tok["phone"], tok["period_key"])
    if inst is None:
        return None, None, (404, {"code": "NOT_FOUND", "message": "任务实例不存在"})
    return tok, inst, None


def _validate_upload_target_and_limits(
    state: StateStore, raw_token: str, filename: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str, int, int, Optional[Tuple[int, Dict[str, Any]]]]:
    """前置校验令牌、窗口、模板类型与扩展名，计算该模板允许的最大字节数（未通过前绝不读取或落盘文件体）。"""
    _, inst, err = _resolve_token_and_instance(state, raw_token)
    if err:
        return None, None, "", "", 0, 0, err

    window_open, window_reason = _check_submission_window(state, inst)
    if not window_open:
        return None, None, "", "", 0, 0, (400, {"code": "WINDOW_CLOSED", "message": window_reason or "提交窗口已关闭"})

    tpl = state.get_template(inst["template_id"], include_deleted=True)
    if not tpl or tpl.get("deleted"):
        return None, None, "", "", 0, 0, (404, {"code": "NOT_FOUND", "message": "任务模板不存在"})
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
    """§9.8 H5 加载任务实例与模板详情。"""
    _, inst, err = _resolve_token_and_instance(state, raw_token)
    if err:
        return err
    tpl = state.get_template(inst["template_id"], include_deleted=True)
    if not tpl or tpl.get("deleted"):
        return 404, {"code": "NOT_FOUND", "message": "任务模板不存在"}

    window_open, window_reason = _check_submission_window(state, inst)
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


def process_h5_task_submit(*, state: StateStore, raw_token: str, data: Any) -> Tuple[int, Dict[str, Any]]:
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
    return 200, {**res, "instance": inst}


def create_h5_router(state: StateStore, uploads_dir: Path, notifier: Optional[NotifierProtocol] = None) -> APIRouter:
    router = APIRouter()

    @router.get("/h5/task", response_class=HTMLResponse)
    async def h5_task_page():
        return HTMLResponse(content=load_h5_html("task.html"))

    @router.get("/h5/bind", response_class=HTMLResponse)
    async def h5_bind_page():
        return HTMLResponse(content=load_h5_html("bind.html"))

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
        status_code, content = process_h5_bind(state=state, data=data, notifier=notifier)
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
        status_code, content = process_h5_task_submit(state=state, raw_token=raw_token, data=data)
        return JSONResponse(status_code=status_code, content=content)

    return router
