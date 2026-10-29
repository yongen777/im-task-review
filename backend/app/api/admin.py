# -*- coding: utf-8 -*-
"""后台管理 API（§9.1 会话 / §9.2 模板 / §9.3 角色成员 / §9.4 实例与审核 / §9.5 日志 / §9.6 统计与导出 + v1.2 §16 P2 守卫）。"""
from __future__ import annotations

import base64
import csv
import hmac
import io
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ..state import (
    SESSION_TTL_HOURS,
    BizError,
    StateStore,
    calc_resubmit_deadline,
    validate_new_password,
    verify_password,
)

COOKIE_NAME = "sid"
CSRF_HEADER = "X-CSRF-Token"

logger = logging.getLogger(__name__)

# §16 P2：must_change=1 时仅允许访问的白名单接口（其余 /api/admin/* 一律 403 MUST_CHANGE_PASSWORD）
MUST_CHANGE_ALLOWED_ENDPOINTS = {
    ("POST", "/api/admin/login"),
    ("GET", "/api/admin/me"),
    ("POST", "/api/admin/change-password"),
    ("POST", "/api/admin/logout"),
}


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """§9 通用错误响应结构：{"ok": false, "code": "...", "message": "...", "error": {"code": "...", "message": "..."}}。"""
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "code": code,
            "message": message,
            "error": {"code": code, "message": message},
        },
    )


def biz_error_response(exc: BizError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


def _parse_optional_bool(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    s = str(val).strip().lower()
    if s == "":
        return None
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no"):
        return False
    return None


def _clamp_page_size(page: int, size: int) -> tuple[int, int]:
    try:
        p = max(1, int(page))
    except Exception:
        p = 1
    try:
        s = max(1, min(200, int(size)))
    except Exception:
        s = 20
    return p, s


# ── 2026-10-29 P2-3：登录失败锁定（内存计数，单进程生效；默认 5 次锁 15 分钟）──
_LOGIN_FAIL_LOCK = threading.Lock()
_LOGIN_FAILURES: Dict[str, Dict[str, float]] = {}


def _csv_safe(val: Any) -> Any:
    """2026-10-29 P2-4：CSV 公式注入防护——以 = + - @ 制表符开头的单元格前置单引号。

    只处理字符串；数字原样返回，避免把实例 ID 变成文本。
    """
    if not isinstance(val, str):
        return val
    if val[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + val
    return val


def _csv_row(cells: List[Any]) -> List[Any]:
    return [_csv_safe(c) for c in cells]


def _login_max_fails() -> int:
    raw = os.environ.get("IMT_LOGIN_MAX_FAILS", "").strip()
    if not raw:
        return 5
    try:
        return max(0, int(raw))  # 0 = 关闭锁定
    except ValueError:
        return 5


def _login_locked_for(key: str) -> int:
    """返回剩余锁定秒数，0 表示未锁定。"""
    if _login_max_fails() <= 0:
        return 0
    now = time.time()
    with _LOGIN_FAIL_LOCK:
        info = _LOGIN_FAILURES.get(key)
        if not info:
            return 0
        left = int(info.get("locked_until", 0) - now)
        return max(0, left)


def _login_note_failure(key: str) -> None:
    max_fails = _login_max_fails()
    if max_fails <= 0:
        return
    now = time.time()
    with _LOGIN_FAIL_LOCK:
        info = _LOGIN_FAILURES.setdefault(key, {"count": 0.0, "locked_until": 0.0})
        info["count"] = float(info.get("count", 0)) + 1
        if info["count"] >= max_fails:
            info["locked_until"] = now + 900.0
            info["count"] = 0.0
        _LOGIN_FAILURES[key] = info


def _login_clear(key: str) -> None:
    with _LOGIN_FAIL_LOCK:
        _LOGIN_FAILURES.pop(key, None)


async def _read_json_body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def require_session(
    request: Request,
    state: StateStore,
    check_csrf: bool = False,
    allow_must_change: bool = False,
) -> tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """校验登录 Cookie sid；校验 must_change 强制改密（§16 P2）；若 check_csrf=True 则校验 X-CSRF-Token（R44, R45）。"""
    sid = request.cookies.get(COOKIE_NAME, "").strip()
    sess = state.get_admin_session(sid)
    if not sess:
        return None, error_response(401, "UNAUTHORIZED", "未登录或登录已过期")

    if bool(sess.get("must_change")) and not allow_must_change:
        return None, error_response(
            403, "MUST_CHANGE_PASSWORD", "首次登录必须先修改密码"
        )

    if check_csrf:
        header_csrf = (request.headers.get(CSRF_HEADER) or "").strip()
        if not header_csrf or not hmac.compare_digest(header_csrf, sess["csrf"]):
            return None, error_response(403, "CSRF_INVALID", "CSRF 令牌缺失或无效")

    return sess, None


def register_admin_guard(app: FastAPI, state: StateStore) -> None:
    """§16 P2 + R44：服务端层面对 /api/admin/* 统一拦截未登录（返回 401 JSON，不 302）与 must_change=1 状态。"""

    @app.middleware("http")
    async def _admin_session_and_must_change_guard(request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        method = request.method.upper()
        if path.startswith("/api/admin") and (method, path) != ("POST", "/api/admin/login"):
            sid = request.cookies.get(COOKIE_NAME, "").strip()
            sess = state.get_admin_session(sid)
            if not sess:
                return error_response(401, "UNAUTHORIZED", "未登录或登录已过期")
            if bool(sess.get("must_change")) and (
                method,
                path,
            ) not in MUST_CHANGE_ALLOWED_ENDPOINTS:
                return error_response(
                    403, "MUST_CHANGE_PASSWORD", "首次登录必须先修改密码"
                )
        return await call_next(request)


def _instance_filter_sql(
    state: StateStore,
    *,
    template_id: Optional[str] = None,
    template_name: Optional[str] = None,
    kind: Optional[str] = None,
    role_id: Optional[str] = None,
    phone: Optional[str] = None,
    q: Optional[str] = None,
    period_key: Optional[str] = None,
    status: Optional[str] = None,
    resubmit_status: Optional[str] = None,
) -> tuple[str, str, List[Any]]:
    """「任务实例」统一筛选条件（列表 / 统计 / 导出共用）。

    2026-10-28 修复：此前 `/api/admin/instances` 只接受 template_id / period_key / status / q，
    前端发来的 kind、role_id、phone 被 FastAPI 静默丢弃 —— 于是「选纯通知提醒」仍显示上传任务、
    「所属角色」筛选无效。这里补齐，并返回 (FROM, WHERE, params) 供两处一致复用。
    """
    from_sql = (
        "FROM task_instance i "
        "LEFT JOIN task_template t ON t.template_id = i.template_id"
    )
    where: List[str] = []
    params: List[Any] = []

    # 2026-10-29 用户口径：停用（enabled=0）或已删除的模板，其历史实例不再出现在
    # 任务实例列表与统计里——否则「停用」了还挂在列表上，看起来像没生效。
    where.append("t.enabled = 1 AND t.deleted = 0")

    if template_id and template_id.strip():
        where.append("i.template_id = ?")
        params.append(template_id.strip())
    if template_name and template_name.strip():
        where.append("t.name LIKE ?")
        params.append(f"%{template_name.strip()}%")
    if period_key and period_key.strip():
        where.append("i.period_key = ?")
        params.append(period_key.strip())
    if status and status.strip():
        # 2026-10-29：支持逗号分隔多状态（「已提醒 / 已完成」= approved,sent），
        # 否则统计卡片上的数字与点进去的列表条数对不上。
        _parts = [x.strip() for x in status.split(",") if x.strip()]
        if len(_parts) == 1:
            where.append("i.status = ?")
            params.append(_parts[0])
        elif _parts:
            where.append("i.status IN (%s)" % ",".join("?" * len(_parts)))
            params.extend(_parts)
    # 2026-10-29 逾期补交审核制：按「补交申请状态」单独筛选（pending/approved/rejected）
    if resubmit_status and resubmit_status.strip():
        where.append("i.resubmit_status = ?")
        params.append(resubmit_status.strip())

    # 任务类型：notice=纯通知；upload=所有上传类（form/image/file）
    k = (kind or "").strip()
    if k == "notice":
        where.append("t.kind = 'notice'")
    elif k == "upload":
        where.append("COALESCE(t.kind, '') <> 'notice'")
    elif k:
        where.append("t.kind = ?")
        params.append(k)

    # 所属角色：实例本身不存 role，按「模板的 role_ids」反查模板
    rid = (role_id or "").strip()
    if rid:
        with state.lock:
            tids = [
                r["template_id"]
                for r in state.conn.execute("SELECT template_id, role_ids FROM task_template").fetchall()
                if rid in (json.loads(r["role_ids"] or "[]") or [])
            ]
        if tids:
            where.append(f"i.template_id IN ({','.join('?' for _ in tids)})")
            params.extend(tids)
        else:
            where.append("1 = 0")

    if phone and phone.strip():
        where.append("i.phone LIKE ?")
        params.append(f"%{phone.strip()}%")
    if q and q.strip():
        kw = f"%{q.strip()}%"
        where.append("(i.name_snap LIKE ? OR i.phone LIKE ? OR COALESCE(i.dept_snap, '') LIKE ?)")
        params.extend([kw, kw, kw])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return from_sql, where_sql, params


def _query_instances(
    state: StateStore,
    *,
    template_id: Optional[str] = None,
    template_name: Optional[str] = None,
    period_key: Optional[str] = None,
    status: Optional[str] = None,
    resubmit_status: Optional[str] = None,
    kind: Optional[str] = None,
    role_id: Optional[str] = None,
    phone: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    size: int = 20,
) -> Dict[str, Any]:
    p, s = _clamp_page_size(page, size)
    offset = (p - 1) * s
    from_sql, where_sql, params = _instance_filter_sql(
        state,
        template_id=template_id,
        template_name=template_name,
        period_key=period_key,
        status=status,
        resubmit_status=resubmit_status,
        kind=kind,
        role_id=role_id,
        phone=phone,
        q=q,
    )
    with state.lock:
        total = int(
            state.conn.execute(
                f"SELECT COUNT(1) AS cnt {from_sql} {where_sql}",
                tuple(params),
            ).fetchone()["cnt"]
            or 0
        )
        rows = state.conn.execute(
            f"SELECT i.*, t.name AS template_name, t.kind AS template_kind, t.need_review, "
            f"t.role_ids AS template_role_ids, t.remind_at AS template_remind_at, "
            f"t.notice_times AS template_notice_times "
            f"{from_sql} {where_sql} ORDER BY i.id DESC LIMIT ? OFFSET ?",
            tuple(params + [s, offset]),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["late_submit"] = bool(d.get("late_submit", 0))
            d["need_review"] = bool(d.get("need_review", 0))
            # 2026-10-29：notice_times 在库里是 JSON 字符串，前端要按数组渲染执行时间，这里先解析
            _nt = d.get("template_notice_times")
            if isinstance(_nt, str):
                try:
                    d["template_notice_times"] = json.loads(_nt)
                except Exception:
                    d["template_notice_times"] = None
            # 前端表格按 inst.kind / inst.role_id 渲染任务类型与所属角色，这里直接补齐，
            # 免得每一行都退化成「上传任务 / 业务角色」的兜底文案。
            d["kind"] = d.get("template_kind") or "form"
            try:
                role_ids = json.loads(d.get("template_role_ids") or "[]") or []
            except Exception:
                role_ids = []
            d["role_id"] = role_ids[0] if role_ids else ""
            d["role_ids"] = role_ids
            d["resubmit_deadline"] = (
                calc_resubmit_deadline(d["deadline_at"], d.get("reviewed_at"))
                if d.get("status") == "rejected"
                else None
            )
            sub = state.get_latest_valid_submission(int(d["id"]))
            if sub is None:
                subs = state.list_submissions_for_instance(int(d["id"]))
                sub = subs[-1] if subs else None
            d["latest_submission"] = sub
            items.append(d)

        return {"total": total, "items": items}


def create_admin_router(state: StateStore, notifier: Any = None, public_base_url: str = "") -> APIRouter:
    router = APIRouter(prefix="/api/admin")
    # 2026-10-29 逾期补交审核：审核通过后要主动把新链接推给用户。
    # notifier 由 main.py 按现有装配方式注入（⛔ 不造全局单例）；缺省时只记日志不发送。
    _notifier = notifier

    # ==================================================================
    # §9.1 会话与认证接口（admin_session + HttpOnly Cookie）
    # ==================================================================
    @router.post("/login")
    async def login(request: Request):
        body = await _read_json_body(request)
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            return error_response(401, "BAD_CREDENTIALS", "用户名或密码错误")

        # 2026-10-29 P2-3：连续失败锁定，挡在线爆破
        client_ip = request.client.host if getattr(request, "client", None) else "unknown"
        fail_key = f"login:{username}:{client_ip}"
        left = _login_locked_for(fail_key)
        if left > 0:
            return error_response(429, "RATE_LIMITED", f"账号已锁定，请 {max(1, left // 60)} 分钟后再试")

        user = state.verify_admin_credentials(username, password)
        if not user:
            _login_note_failure(fail_key)
            return error_response(401, "BAD_CREDENTIALS", "用户名或密码错误")
        _login_clear(fail_key)

        sess = state.create_admin_session(int(user["id"]))
        state.write_audit(
            username=user["username"],
            action="admin.login",
            target_type="admin_user",
            target_id=user["username"],
            detail={"must_change": bool(user["must_change"])},
        )
        resp = JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "username": user["username"],
                "must_change": bool(user["must_change"]),
                "csrf_token": sess["csrf"],
            },
        )
        resp.set_cookie(
            key=COOKIE_NAME,
            value=sess["sid"],
            max_age=SESSION_TTL_HOURS * 3600,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return resp

    @router.post("/logout")
    async def logout(request: Request):
        sess, err = require_session(
            request, state, check_csrf=True, allow_must_change=True
        )
        if err is not None:
            return err
        assert sess is not None
        state.delete_admin_session(sess["sid"])
        state.write_audit(
            username=sess["username"],
            action="admin.logout",
            target_type="admin_user",
            target_id=sess["username"],
        )
        resp = JSONResponse(status_code=200, content={"ok": True})
        resp.delete_cookie(key=COOKIE_NAME, path="/")
        return resp

    @router.get("/me")
    async def get_me(request: Request):
        sess, err = require_session(
            request, state, check_csrf=False, allow_must_change=True
        )
        if err is not None:
            return err
        assert sess is not None
        return {
            "ok": True,
            "username": sess["username"],
            "must_change": bool(sess["must_change"]),
            "csrf_token": sess["csrf"],
        }

    @router.post("/change-password")
    async def change_password(request: Request):
        sess, err = require_session(
            request, state, check_csrf=True, allow_must_change=True
        )
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        old_password = str(body.get("old_password") or "")
        new_password = str(body.get("new_password") or "")

        user = state.get_admin_by_id(sess["user_id"])
        if not user or not verify_password(old_password, user["pwd_hash"]):
            return error_response(400, "OLD_PASSWORD_WRONG", "旧密码不正确")

        pwd_err = validate_new_password(new_password)
        if pwd_err:
            return error_response(400, "VALIDATION_FAILED", pwd_err)

        state.change_admin_password(sess["user_id"], new_password)
        state.write_audit(
            username=sess["username"],
            action="admin.change_password",
            target_type="admin_user",
            target_id=sess["username"],
        )
        return {"ok": True}

    # ==================================================================
    # §9.2 任务模板接口
    # ==================================================================
    @router.get("/templates")
    async def list_templates(
        request: Request,
        q: Optional[str] = None,
        kind: Optional[str] = None,
        enabled: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        enabled_bool = _parse_optional_bool(enabled)
        res = state.list_templates(
            q=q, kind=kind, enabled=enabled_bool, page=page, size=size
        )
        return {"ok": True, "total": res["total"], "items": res["items"]}

    @router.post("/templates")
    async def create_template(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            tpl = state.create_template(body, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "template_id": tpl["template_id"], "template": tpl}

    @router.get("/templates/{template_id}")
    async def get_template(template_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        tpl = state.get_template(template_id, include_deleted=False)
        if not tpl:
            return error_response(404, "NOT_FOUND", "任务模板不存在")
        return {"ok": True, "template": tpl}

    @router.put("/templates/{template_id}")
    async def update_template(template_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            tpl = state.update_template(template_id, body, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "template": tpl}

    @router.delete("/templates/{template_id}")
    async def delete_template(template_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            state.delete_template(template_id, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True}

    # ── 2026-10-28 通知类「数据图片提醒」素材上传 ──
    # 管理员在模板里上传一张图片，到点随通知消息直接推送；不再依赖「人工填报 + 服务端出图」。
    @router.post("/template-assets")
    async def upload_template_asset(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        filename = str(body.get("filename") or "").strip()
        b64 = str(body.get("content_base64") or "").strip()
        if b64.lower().startswith("data:") and "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            return error_response(400, "VALIDATION_FAILED", "图片内容解析失败，请重新选择文件")

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("png", "jpg", "jpeg", "webp"):
            return error_response(400, "FILE_TYPE_REJECTED", "仅支持 png / jpg / jpeg / webp 图片")
        if not raw:
            return error_response(400, "FILE_TYPE_REJECTED", "不能上传空文件")
        if len(raw) > 9 * 1024 * 1024:
            return error_response(400, "FILE_TYPE_REJECTED", "图片不能超过 9MB")
        head = raw[:12]
        is_png = head.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpg = head.startswith(b"\xff\xd8\xff")
        is_webp = head.startswith(b"RIFF") and raw[8:12] == b"WEBP"
        if not (is_png or is_jpg or is_webp):
            return error_response(400, "FILE_TYPE_REJECTED", "文件内容不是有效图片")

        cfg = getattr(request.app.state, "config", None)
        data_dir = Path(cfg.data_dir) if cfg is not None else Path(state.db.db_path).parent
        out_dir = data_dir / "notice_attach"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_ext = "jpg" if ext == "jpeg" else ext
        name = f"notice_{uuid.uuid4().hex[:12]}.{safe_ext}"
        (out_dir / name).write_bytes(raw)
        state.write_audit(
            username=sess["username"], action="template.asset_upload", target_type="task_template",
            target_id=name, detail={"file_name": filename, "size": len(raw)},
        )
        return {"ok": True, "path": f"notice_attach/{name}", "file_name": filename, "size": len(raw)}

    @router.get("/template-assets/{name}")
    async def get_template_asset(name: str, request: Request):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        safe_name = os.path.basename(name or "")
        if not safe_name:
            return error_response(404, "NOT_FOUND", "素材不存在")
        cfg = getattr(request.app.state, "config", None)
        data_dir = Path(cfg.data_dir) if cfg is not None else Path(state.db.db_path).parent
        path = data_dir / "notice_attach" / safe_name
        if not path.exists() or not path.is_file():
            return error_response(404, "NOT_FOUND", "素材不存在")
        return FileResponse(str(path))

    @router.post("/templates/{template_id}/preview")
    async def preview_template(template_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            stats = state.preview_template_targets(template_id)
        except BizError as exc:
            return biz_error_response(exc)
        return {
            "ok": True,
            "member_count": stats["member_count"],
            "activated_count": stats["activated_count"],
            "unactivated_count": stats["unactivated_count"],
        }

    # ==================================================================
    # §9.3 角色与成员接口
    # ==================================================================
    @router.get("/roles")
    async def list_roles(request: Request):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        items = state.list_roles()
        return {"ok": True, "items": items}

    @router.post("/roles")
    async def create_role(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            role = state.create_role(
                name=str(body.get("name") or ""),
                note=body.get("note"),
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "role_id": role["role_id"], "role": role}

    # ── 已激活待归纳人员（2026-10-28 方案B：用户发任意消息自动登记，管理员后续移入角色）──

    @router.get("/pending-members")
    async def list_pending_members(request: Request, limit: int = 200):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        items = state.list_pending_members(limit=limit)
        return {"ok": True, "total": len(items), "items": items}

    @router.post("/pending-members/move")
    async def move_pending_member(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        body = await _read_json_body(request)
        external_id = str(body.get("external_id") or "")
        phone = str(body.get("phone") or "")
        row = None
        if external_id:
            row = state.get_pending_member(external_id)
        if row is None and phone:
            row = next((r for r in state.list_pending_members(limit=500)
                        if str(r.get("phone") or "") == phone), None)
        if row is None:
            return JSONResponse(status_code=404, content={"ok": False, "code": "NOT_FOUND", "message": "待归纳人员不存在"})
        # ⛔ 2026-10-28 用户口径：管理员不得补录/改写手机号与姓名，
        #    只能使用用户在对话里自行发送的值（防冒名归纳）。
        stored_phone = str(row.get("phone") or "").strip()
        if not stored_phone:
            return JSONResponse(status_code=400, content={
                "ok": False, "code": "PHONE_REQUIRED",
                "message": "该用户尚未在对话中发送「姓名 手机号」，请等待其发送后再归纳",
            })
        try:
            res = state.assign_pending_member(
                external_id=str(row.get("external_id") or ""),
                phone=stored_phone,
                name=str(row.get("name") or ""),
                role_id=str(body.get("role_id") or body.get("target_role_id") or ""),
                dept=body.get("dept"),
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "assignment": res}

    @router.delete("/pending-members/{external_id}")
    async def remove_pending_member(external_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        try:
            state.remove_pending_member(external_id, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True}

    # ── 兼容路由：前端历史路径 unactivated-members（2026-10-28 修）
    #    背景：前端「一键移入/移除」调用 /api/admin/unactivated-members*，
    #    后端只有 /pending-members*，导致两个按钮 404「操作未成功」。
    #    这里做契约适配（认 phone / phones / target_role_id，也认 external_id / role_id），
    #    从而不必重新打包前端即可恢复功能。
    def _pending_find(key: str) -> Optional[Dict[str, Any]]:
        k = (key or "").strip()
        if not k:
            return None
        for row in state.list_pending_members(limit=500):
            if str(row.get("external_id") or "") == k or str(row.get("phone") or "") == k:
                return row
        return None

    @router.get("/unactivated-members")
    async def list_unactivated_alias(request: Request, limit: int = 200):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        items = state.list_pending_members(limit=limit)
        return {"ok": True, "total": len(items), "items": items}

    @router.post("/unactivated-members/move")
    async def move_unactivated_alias(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        body = await _read_json_body(request)
        role_id = str(body.get("target_role_id") or body.get("role_id") or "")
        if not role_id:
            return JSONResponse(status_code=400, content={"ok": False, "code": "VALIDATION_FAILED", "message": "缺少目标角色"})
        raw_targets = body.get("phones")
        if isinstance(raw_targets, list) and raw_targets:
            targets = [{"phone": str(p or ""), "external_id": "", "name": ""} for p in raw_targets]
        elif body.get("phone") or body.get("external_id"):
            targets = [{"phone": str(body.get("phone") or ""),
                        "external_id": str(body.get("external_id") or ""),
                        "name": str(body.get("name") or "")}]
        else:
            return JSONResponse(status_code=400, content={"ok": False, "code": "VALIDATION_FAILED", "message": "缺少人员标识"})

        moved = 0
        try:
            for t in targets:
                row = _pending_find(t["external_id"]) if t["external_id"] else None
                if row is None and t["phone"]:
                    row = _pending_find(t["phone"])
                if row is None:
                    continue
                stored_phone = str(row.get("phone") or "").strip()
                if not stored_phone:
                    return JSONResponse(status_code=400, content={
                        "ok": False, "code": "PHONE_REQUIRED",
                        "message": "该用户尚未在对话中发送「姓名 手机号」，请等待其发送后再归纳",
                    })
                state.assign_pending_member(
                    external_id=str(row.get("external_id") or ""),
                    phone=stored_phone,
                    name=str(row.get("name") or ""),
                    role_id=role_id,
                    dept=body.get("dept"),
                    actor=sess["username"],
                )
                moved += 1
        except BizError as exc:
            return biz_error_response(exc)
        if moved == 0:
            return JSONResponse(status_code=404, content={
                "ok": False, "code": "NOT_FOUND",
                "message": "没有匹配到待归纳人员（可能已被移入，或该人员尚未登记手机号）",
            })
        role = state.get_role(role_id) or {}
        return {"ok": True, "moved_count": moved, "target_role_id": role_id,
                "target_role_name": role.get("name") or role_id}

    @router.delete("/unactivated-members/{key}")
    async def remove_unactivated_alias(key: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        row = _pending_find(key)
        if row is None:
            return JSONResponse(status_code=404, content={"ok": False, "code": "NOT_FOUND", "message": "待归纳人员不存在（可能已被移入）"})
        try:
            state.remove_pending_member(str(row.get("external_id") or ""), actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True}

    @router.delete("/unactivated-members")
    @router.delete("/unactivated-members/")
    async def remove_unactivated_no_key(request: Request):
        """前端列表未刷新（phone 为空）时会发出无标识的 DELETE，给出可执行提示而不是 404。"""
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        return JSONResponse(status_code=400, content={
            "ok": False, "code": "VALIDATION_FAILED",
            "message": "缺少人员标识：请刷新页面后重试",
        })

    # ── 数据快照（2026-10-27 春促数据看板 · 人工填数本期主通道）──

    @router.get("/data-snapshots")
    async def list_data_snapshots(
        request: Request,
        source_key: Optional[str] = None,
        period_key: Optional[str] = None,
        limit: int = 50,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        try:
            items = state.list_data_snapshots(source_key=source_key, period_key=period_key, limit=limit)
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "total": len(items), "items": items}

    @router.post("/data-snapshots")
    async def upsert_data_snapshot(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            snap = state.upsert_data_snapshot(
                source_key=str(body.get("source_key") or ""),
                period_key=str(body.get("period_key") or ""),
                values=body.get("values") or {},
                note=body.get("note"),
                updated_by=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "snapshot": snap}

    @router.put("/roles/{role_id}")
    async def update_role(role_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            role = state.update_role(
                role_id=role_id,
                name=str(body.get("name") or ""),
                note=body.get("note"),
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "role": role}

    @router.delete("/roles/{role_id}")
    async def delete_role(role_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            state.delete_role(role_id, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True}

    @router.get("/roles/{role_id}/members")
    async def list_role_members(
        role_id: str,
        request: Request,
        q: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        try:
            res = state.list_role_members(role_id=role_id, q=q, page=page, size=size)
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "total": res["total"], "items": res["items"]}

    @router.post("/roles/{role_id}/members")
    async def add_role_member(role_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            member = state.add_role_member(
                role_id=role_id,
                phone=str(body.get("phone") or ""),
                name=str(body.get("name") or ""),
                dept=body.get("dept"),
                source="manual",
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "member": member}

    @router.get("/members/{phone}/roles")
    async def list_member_roles(phone: str, request: Request):
        """某人当前所在的全部角色（一人多角色：前端弹窗把已加入的角色置灰）。"""
        # GET 只读：与其它查询接口一致，不校验 CSRF（否则前端 api.get 不带 token 会 403）
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        return {"ok": True, "items": state.list_roles_of_phone(phone)}

    @router.post("/members/{phone}/roles")
    async def assign_member_to_roles(phone: str, request: Request):
        """2026-10-29：把**已有成员**一次加入多个其他角色（一人多角色）。

        场景：张三已在「片长/分支局长」，还需要同时在「云中台软研队伍」。
        成员表的唯一约束是 UNIQUE(role_id, phone)，本来就允许同一手机号出现在多个角色；
        缺的只是一个显式入口（否则只能在目标角色里重新手填姓名+手机号）。
        这里逐角色调用 add_role_member，已在该角色的记为 skipped，不影响其他角色。
        """
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        raw_role_ids = body.get("role_ids")
        if not isinstance(raw_role_ids, list) or not raw_role_ids:
            return error_response(400, "VALIDATION_FAILED", "请至少选择 1 个目标角色")
        name = str(body.get("name") or "").strip()
        if not name:
            return error_response(400, "VALIDATION_FAILED", "成员姓名不能为空")
        dept = body.get("dept")

        added: List[str] = []
        skipped: List[str] = []
        failed: List[Dict[str, Any]] = []
        for rid in [str(x).strip() for x in raw_role_ids if str(x).strip()]:
            try:
                state.add_role_member(
                    role_id=rid, phone=phone, name=name, dept=dept,
                    source="manual", actor=sess["username"],
                )
                added.append(rid)
            except BizError as exc:
                if exc.code == "DUPLICATE":
                    skipped.append(rid)
                else:
                    failed.append({"role_id": rid, "message": exc.message})
        return {"ok": True, "added": added, "skipped": skipped, "failed": failed}

    @router.put("/roles/{role_id}/members/{phone}")
    async def update_role_member(role_id: str, phone: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        try:
            member = state.update_role_member(
                role_id=role_id,
                phone=phone,
                name=body.get("name"),
                dept=body.get("dept"),
                enabled=body.get("enabled"),
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "member": member}

    @router.delete("/roles/{role_id}/members/{phone}")
    async def delete_role_member(role_id: str, phone: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            state.delete_role_member(role_id=role_id, phone=phone, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True}

    @router.post("/roles/{role_id}/members/import")
    async def import_role_members(role_id: str, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        csv_rows = body.get("csv_rows")
        dry_run = bool(body.get("dry_run", True))
        try:
            res = state.import_role_members(
                role_id=role_id,
                csv_rows=csv_rows,  # type: ignore[arg-type]
                dry_run=dry_run,
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return res

    # ==================================================================
    # §9.4 任务实例、审核与附件查看接口
    # ==================================================================
    @router.get("/instances/export")
    async def export_instances_csv(
        request: Request,
        template_id: Optional[str] = None,
        period_key: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        res = _query_instances(
            state,
            template_id=template_id,
            period_key=period_key,
            status=status,
            q=q,
            page=1,
            size=200,
        )
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([
            "实例ID", "模板ID", "任务名称", "周期", "姓名", "部门", "手机号",
            "状态", "是否补交", "截止时间", "提交时间", "审核人", "驳回原因", "最新提交内容/附件",
        ])
        for item in res["items"]:
            sub = item.get("latest_submission") or {}
            sub_summary = ""
            if sub.get("fields_json"):
                sub_summary = str(sub["fields_json"])
            elif sub.get("file_name"):
                sub_summary = str(sub["file_name"])
            writer.writerow(_csv_row([
                item["id"],
                item["template_id"],
                item.get("template_name") or "",
                item["period_key"],
                item["name_snap"],
                item.get("dept_snap") or "",
                item["phone"],
                item["status"],
                "是" if item.get("late_submit") else "否",
                item["deadline_at"],
                item.get("submitted_at") or "",
                item.get("reviewer") or "",
                item.get("reject_reason") or "",
                sub_summary,
            ]))

        state.write_audit(
            username=sess["username"],
            action="instance.export_csv",
            target_type="task_instance",
            target_id=template_id or "all",
            detail={"period_key": period_key, "status": status, "count": len(res["items"])},
        )
        return Response(
            content=buf.getvalue().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="instances_export.csv"'},
        )

    @router.get("/instances")
    async def list_instances(
        request: Request,
        template_id: Optional[str] = None,
        template_name: Optional[str] = None,
        period_key: Optional[str] = None,
        status: Optional[str] = None,
        resubmit_status: Optional[str] = None,
        kind: Optional[str] = None,
        role_id: Optional[str] = None,
        phone: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        res = _query_instances(
            state,
            template_id=template_id,
            resubmit_status=resubmit_status,
            template_name=template_name,
            period_key=period_key,
            status=status,
            kind=kind,
            role_id=role_id,
            phone=phone,
            q=q,
            page=page,
            size=size,
        )
        return {"ok": True, "total": res["total"], "page": page, "size": size, "items": res["items"]}

    @router.get("/instances/{instance_id}")
    async def get_instance_detail(instance_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        inst = state.get_instance(instance_id)
        if not inst:
            return error_response(404, "NOT_FOUND", "任务实例不存在")
        tpl = state.get_template(inst["template_id"], include_deleted=True)
        submissions = state.list_submissions_for_instance(instance_id)
        reminders = state.list_reminder_logs(instance_id=instance_id)
        inst["late_submit"] = bool(inst.get("late_submit", 0))
        # 2026-10-29：详情页的执行时间也要取模板计划时刻（原来拿 submitted_at 兜底，
        # 没提交就显示「待到达执行时刻」），与列表列共用同一份数据。
        inst["template_remind_at"] = (tpl or {}).get("remind_at")
        inst["template_notice_times"] = (tpl or {}).get("notice_times")
        inst["resubmit_deadline"] = (
            calc_resubmit_deadline(inst["deadline_at"], inst.get("reviewed_at"))
            if inst.get("status") == "rejected"
            else None
        )

        # 2026-10-28：详情页要能直接看到「字段 / 文字说明 / 图片 / 文件」，
        # 前端只需要一份归一化的 submission（含每个附件的文件名、类型、能否预览）。
        latest = submissions[-1] if submissions else None
        submission_view = None
        if latest:
            try:
                form_values = json.loads(latest.get("fields_json") or "{}") or {}
            except Exception:
                form_values = {}
            refs: List[str] = []
            if latest.get("refs_json"):
                try:
                    refs = [str(r) for r in (json.loads(latest["refs_json"]) or [])]
                except Exception:
                    refs = []
            if not refs and latest.get("file_ref"):
                refs = [str(latest["file_ref"])]
            attachments: List[Dict[str, Any]] = []
            fields_meta: List[Dict[str, Any]] = []
            for r in refs:
                up = state.get_upload_ref(r)
                if not up:
                    continue
                mime = str(up.get("mime") or "")
                attachments.append({
                    "ref": up["ref"],
                    "file_name": up.get("file_name") or up["ref"],
                    "mime": mime,
                    "size": int(up.get("size") or 0),
                    "is_image": mime.startswith("image/"),
                    "url": f"/api/admin/uploads/{up['ref']}",
                })
            # 字段标签用模板 schema 映射回中文名，前端不用再自己去查
            label_map = {}
            for f in (tpl.get("schema_json") or []) if tpl else []:
                if isinstance(f, dict) and f.get("key"):
                    label_map[str(f["key"])] = str(f.get("label") or f["key"])
            for k, v in (form_values or {}).items():
                fields_meta.append({"key": str(k), "label": label_map.get(str(k), str(k)), "value": "" if v is None else str(v)})
            submission_view = {
                "id": latest.get("id"),
                "instance_id": latest.get("instance_id"),
                "form_values": form_values,
                "fields": fields_meta,
                "note": latest.get("note") or "",
                "attachments": attachments,
                "attachment_ref": latest.get("file_ref"),
                "attachment_name": latest.get("file_name"),
                "submitted_at": latest.get("created_at"),
                "status": latest.get("status"),
            }

        return {
            "ok": True,
            "instance": inst,
            "template": tpl,
            "submissions": submissions,
            "submission": submission_view,
            "reminders": reminders,
        }

    # ── 2026-10-29 逾期补交审核 ────────────────────────────────────────
    # 通过：签发 30 分钟 resubmit token 并把新链接推给用户；拒绝：推拒绝文案。
    # 幂等由 state 侧保证（只有 resubmit_status='pending' 才能审），重复点击第二次会 400。
    def _push_resubmit_message(inst: Dict[str, Any], text: str, tag: str) -> bool:
        target = state.get_delivery_target(inst["phone"])
        ext_id = (target or {}).get("external_id")
        if _notifier is None or not ext_id:
            logger.warning("补交审核通知未发送（notifier 或 external_id 缺失） instance=%s", inst.get("id"))
            return False
        try:
            res = _notifier.send_text(ext_id, text, f"{tag}|{inst['id']}|{int(time.time() * 1000)}")
            return bool(getattr(res, "ok", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("补交审核通知发送失败 instance=%s: %s", inst.get("id"), exc)
            return False

    @router.post("/instances/{instance_id}/resubmit/approve")
    async def approve_instance_resubmit(instance_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            res = state.approve_resubmit(int(instance_id), actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)

        inst = res["instance"] or {}
        tpl = state.get_template(inst.get("template_id") or "", include_deleted=True) or {}
        base = (public_base_url or "").rstrip("/")
        h5_url = f"{base}/h5/task#t={res['raw_token']}"
        # 30 分钟有效期，文案按用户口径：先任务名，再 HH:MM，再链接，最后标有效期
        expires_disp = str(res.get("expires_at") or "")[11:16]
        text = (
            f"【{tpl.get('name') or '任务'}】补交链接已开通，请于 {expires_disp} 前提交：\n"
            f"{h5_url}\n（30 分钟内有效）"
        )
        sent = _push_resubmit_message(inst, text, "resubmit_ok")
        return {"ok": True, "instance": inst, "h5_url": h5_url,
                "expires_at": res.get("expires_at"), "sent": sent}

    @router.post("/instances/{instance_id}/resubmit/reject")
    async def reject_instance_resubmit(instance_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        reason = str(body.get("reason") or "").strip() or None
        try:
            res = state.reject_resubmit(int(instance_id), actor=sess["username"], reason=reason)
        except BizError as exc:
            return biz_error_response(exc)

        inst = res["instance"] or {}
        tpl = state.get_template(inst.get("template_id") or "", include_deleted=True) or {}
        text = f"【{tpl.get('name') or '任务'}】补交申请未通过，如有疑问请联系管理员。"
        sent = _push_resubmit_message(inst, text, "resubmit_no")
        return {"ok": True, "instance": inst, "sent": sent}

    @router.post("/instances/{instance_id}/review")
    async def review_instance(instance_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        action = str(body.get("action") or "").strip().lower()
        reason = str(body.get("reason") or body.get("reject_reason") or "").strip()

        try:
            updated = state.review_instance(
                instance_id=instance_id,
                action=action,
                reviewer=sess["username"],
                reason=reason,
            )
        except BizError as exc:
            return biz_error_response(exc)

        if action == "reject":
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                try:
                    scheduler.send_reject_notification(updated, reason=reason)
                except Exception:
                    pass

        return {"ok": True, "instance": updated}

    @router.get("/uploads/{ref}")
    async def download_upload_file(ref: str, request: Request):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        up = state.get_upload_ref(ref)
        if not up:
            return error_response(404, "NOT_FOUND", "附件不存在")
        cfg = getattr(request.app.state, "config", None)
        uploads_dir = cfg.uploads_dir if cfg is not None else (state.db.db_path.parent / "uploads")
        disk_path = uploads_dir / up["rel_path"]
        if not disk_path.is_file():
            return error_response(404, "NOT_FOUND", "附件文件不存在")
        # 2026-10-28：能在线看的（图片 / PDF / 纯文本）走 inline，浏览器直接打开；
        # 其余（docx/xlsx/zip…）才按附件下载，避免「点开图片却是下载」。
        mime = str(up["mime"] or "application/octet-stream")
        force_download = str(request.query_params.get("dl") or "") in ("1", "true", "yes")
        inline = (mime.startswith("image/") or mime in ("application/pdf", "text/plain")) and not force_download
        disposition = "inline" if inline else "attachment"
        # ⚠️ 中文文件名不能直接塞进 header（latin-1 编码会 500），
        # 按 RFC 5987 用 filename*=UTF-8'' 转义，另给 ASCII fallback。
        raw_name = str(up["file_name"] or "file")
        ascii_name = raw_name.encode("ascii", "ignore").decode() or "file"
        ascii_name = ascii_name.replace('"', "").replace("\\", "")
        return FileResponse(
            path=str(disk_path),
            media_type=mime,
            headers={
                "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(raw_name)}"
            },
        )

    # ==================================================================
    # §9.5 提醒日志与操作审计日志接口
    # ==================================================================
    @router.get("/reminder-logs")
    @router.get("/reminders")
    async def list_reminder_logs(
        request: Request,
        template_id: Optional[str] = None,
        instance_id: Optional[int] = None,
        phone: Optional[str] = None,
        kind: Optional[str] = None,
        result: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        p, s = _clamp_page_size(page, size)
        offset = (p - 1) * s
        where: List[str] = []
        params: List[Any] = []
        if template_id and template_id.strip():
            where.append("r.template_id = ?")
            params.append(template_id.strip())
        if instance_id is not None:
            where.append("r.instance_id = ?")
            params.append(int(instance_id))
        if phone and phone.strip():
            where.append("r.phone LIKE ?")
            params.append(f"%{phone.strip()}%")
        if kind and kind.strip():
            where.append("r.kind = ?")
            params.append(kind.strip())
        if result and result.strip():
            where.append("r.result = ?")
            params.append(result.strip())

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with state.lock:
            total = int(
                state.conn.execute(
                    f"SELECT COUNT(1) AS cnt FROM reminder_log r {where_sql}",
                    tuple(params),
                ).fetchone()["cnt"]
                or 0
            )
            rows = state.conn.execute(
                f"SELECT r.*, t.name AS template_name FROM reminder_log r "
                f"LEFT JOIN task_template t ON t.template_id = r.template_id "
                f"{where_sql} ORDER BY r.id DESC LIMIT ? OFFSET ?",
                tuple(params + [s, offset]),
            ).fetchall()
            items = [dict(r) for r in rows]
        return {"ok": True, "total": total, "items": items}

    @router.get("/audit-logs")
    @router.get("/audits")
    async def list_audit_logs(
        request: Request,
        username: Optional[str] = None,
        action: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        p, s = _clamp_page_size(page, size)
        offset = (p - 1) * s
        where: List[str] = []
        params: List[Any] = []
        if username and username.strip():
            where.append("username LIKE ?")
            params.append(f"%{username.strip()}%")
        if action and action.strip():
            where.append("action LIKE ?")
            params.append(f"%{action.strip()}%")

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with state.lock:
            total = int(
                state.conn.execute(
                    f"SELECT COUNT(1) AS cnt FROM admin_audit {where_sql}",
                    tuple(params),
                ).fetchone()["cnt"]
                or 0
            )
            rows = state.conn.execute(
                f"SELECT * FROM admin_audit {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                tuple(params + [s, offset]),
            ).fetchall()
            items: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                if d.get("detail"):
                    try:
                        d["detail_json"] = json.loads(d["detail"])
                    except Exception:
                        d["detail_json"] = None
                items.append(d)
        return {"ok": True, "total": total, "items": items}

    # ==================================================================
    # §9.6 统计概览接口
    # ==================================================================
    @router.get("/stats")
    async def get_stats(
        request: Request,
        template_id: Optional[str] = None,
        template_name: Optional[str] = None,
        period_key: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        role_id: Optional[str] = None,
        phone: Optional[str] = None,
        q: Optional[str] = None,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        # 2026-10-28：统计卡片与列表共用同一套筛选，避免「筛了列表但卡片数字不变」
        from_sql, where_sql, params = _instance_filter_sql(
            state,
            template_id=template_id,
            template_name=template_name,
            period_key=period_key,
            status=status,
            kind=kind,
            role_id=role_id,
            phone=phone,
            q=q,
        )
        counts = {
            "total": 0,
            "pending": 0,
            "submitted": 0,
            "approved": 0,
            "rejected": 0,
            "overdue": 0,
            "expired": 0,
            "late_submit": 0,
            # 2026-10-28 方案A：通知类实例发送成功后的终态（计入 total，单独计数）
            "sent": 0,
        }
        with state.lock:
            rows = state.conn.execute(
                f"SELECT i.status AS status, COUNT(1) AS cnt, "
                f"SUM(CASE WHEN i.late_submit = 1 THEN 1 ELSE 0 END) AS late_cnt "
                f"{from_sql} {where_sql} GROUP BY i.status",
                tuple(params),
            ).fetchall()
            for r in rows:
                st = str(r["status"])
                cnt = int(r["cnt"] or 0)
                counts["total"] += cnt
                counts["late_submit"] += int(r["late_cnt"] or 0)
                if st in counts:
                    counts[st] = cnt

        return {"ok": True, "stats": counts}

    return router
