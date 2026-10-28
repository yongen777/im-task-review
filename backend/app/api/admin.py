# -*- coding: utf-8 -*-
"""后台管理 API（§9.1 会话 / §9.2 模板 / §9.3 角色成员 / §9.4 实例与审核 / §9.5 日志 / §9.6 统计与导出 + v1.2 §16 P2 守卫）。"""
from __future__ import annotations

import base64
import csv
import hmac
import io
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        where.append("i.status = ?")
        params.append(status.strip())

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
            f"t.role_ids AS template_role_ids "
            f"{from_sql} {where_sql} ORDER BY i.id DESC LIMIT ? OFFSET ?",
            tuple(params + [s, offset]),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["late_submit"] = bool(d.get("late_submit", 0))
            d["need_review"] = bool(d.get("need_review", 0))
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


def create_admin_router(state: StateStore) -> APIRouter:
    router = APIRouter(prefix="/api/admin")

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

        user = state.verify_admin_credentials(username, password)
        if not user:
            return error_response(401, "BAD_CREDENTIALS", "用户名或密码错误")

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
            writer.writerow([
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
            ])

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
        inst["resubmit_deadline"] = (
            calc_resubmit_deadline(inst["deadline_at"], inst.get("reviewed_at"))
            if inst.get("status") == "rejected"
            else None
        )
        return {
            "ok": True,
            "instance": inst,
            "template": tpl,
            "submissions": submissions,
            "reminders": reminders,
        }

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
        return FileResponse(
            path=str(disk_path),
            media_type=up["mime"],
            filename=up["file_name"],
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
