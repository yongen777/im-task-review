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
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ..channel_config import (
    CHANNEL_SCHEMA,
    ChannelConfigStore,
    build_callback_url,
    channel_configured,
    field_map,
    key_source,
    mask_value,
    masked_view,
    normalize_payload,
    validate_payload,
)
from ..config import format_dt
from ..state import (
    SESSION_TTL_HOURS,
    BizError,
    StateStore,
    calc_resubmit_deadline,
    effective_h5_modules,
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
    # 2026-11-05：切回超管不依赖代管账号是否已改密（代登录一个未改密的新号也要能一键切回）
    ("POST", "/api/admin/accounts/unassume"),
}

# ── 2026-11-04 多管理员分级（超级管理员 + 分级管理员）─────────────────────
SUPER_ADMIN_LEVEL = "super_admin"
# 超管允许的写操作：只有账号管理与通道配置（全局设置），业务数据一律只读
SUPER_ADMIN_WRITE_ALLOW_PREFIXES = (
    "/api/admin/accounts",
    "/api/admin/channel-config",
    # 2026-11-05 拍板：部门是全局共享库，增/改/删仅超管（分级只能通过导入自动建档）；
    # 成员池是超管的全局名册操作入口（分级自己的增删改仍走角色内接口，仅作用于自己名下）。
    "/api/admin/departments",
    "/api/admin/member-pool",
)
SUPER_ADMIN_WRITE_ALLOW_EXACT = {"/api/admin/change-password", "/api/admin/logout"}


def _admin_profile(state: StateStore, sess: Dict[str, Any]) -> Dict[str, Any]:
    """当前登录管理员档案（role_level / display_name / allowed_platforms）。"""
    user = state.get_admin_by_id(int(sess.get("user_id") or 0)) or {}
    return state.parse_admin_account(user)


def _is_super_admin(admin: Dict[str, Any]) -> bool:
    return str(admin.get("role_level") or "") == SUPER_ADMIN_LEVEL


def _owner_filter(admin: Dict[str, Any], owner_id_param: Optional[str]) -> Optional[int]:
    """列表接口的 owner 过滤值：超管=指定管理员或 None(全部)；分级管理员=自己。"""
    if _is_super_admin(admin):
        if owner_id_param and str(owner_id_param) not in ("all", ""):
            try:
                return int(owner_id_param)
            except (TypeError, ValueError):
                return None
        return None
    return int(admin.get("id") or 0)


def _platform_allowed(admin: Dict[str, Any], platform: Optional[str]) -> bool:
    """分级管理员的办公软件平台授权校验；超管恒为 True。"""
    plat = (platform or "").strip().lower()
    if not plat or _is_super_admin(admin):
        return True
    return plat in [str(p).lower() for p in (admin.get("allowed_platforms") or [])]


def _check_path_ownership(state: StateStore, admin: Dict[str, Any], path: str) -> Optional[JSONResponse]:
    """分级管理员：路径里带角色/模板/实例 id 的接口，校验资源归属；不是自己的 → 403。"""
    if _is_super_admin(admin):
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) < 4 or parts[0] != "api" or parts[1] != "admin":
        return None
    owner_id = int(admin.get("id") or 0)
    try:
        with state.lock:
            if parts[2] == "roles":
                row = state.conn.execute("SELECT owner_id FROM role WHERE role_id = ?", (parts[3],)).fetchone()
                if row is not None and int(row["owner_id"] or 1) != owner_id:
                    return error_response(403, "FORBIDDEN_OWNER", "该角色分组不属于当前管理员")
            elif parts[2] == "templates":
                row = state.conn.execute("SELECT owner_id FROM task_template WHERE template_id = ?", (parts[3],)).fetchone()
                if row is not None and int(row["owner_id"] or 1) != owner_id:
                    return error_response(403, "FORBIDDEN_OWNER", "该任务模板不属于当前管理员")
            elif parts[2] == "instances":
                row = state.conn.execute("SELECT owner_id FROM task_instance WHERE id = ?", (int(parts[3]),)).fetchone()
                if row is not None and int(row["owner_id"] or 1) != owner_id:
                    return error_response(403, "FORBIDDEN_OWNER", "该任务实例不属于当前管理员")
    except Exception:
        return None
    return None


def _check_template_role_scope(
    state: StateStore, admin: Dict[str, Any], body: Dict[str, Any]
) -> Optional[JSONResponse]:
    """2026-11-04：模板的 role_ids 必须全部属于当前管理员，且其平台在授权范围内。

    缺这一步的话，分级管理员可以把「别人的角色」挂到自己的模板上，
    于是自己的任务模板会给别人的成员派单——越权。
    """
    if _is_super_admin(admin):
        return None
    raw_ids = body.get("role_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [x for x in raw_ids.replace(",", " ").split() if x]
    role_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    if not role_ids:
        return None
    owner_id = int(admin.get("id") or 0)
    allowed = [str(p).lower() for p in (admin.get("allowed_platforms") or [])]
    try:
        with state.lock:
            for rid in role_ids:
                row = state.conn.execute(
                    "SELECT owner_id, platform FROM role WHERE role_id = ?", (rid,)
                ).fetchone()
                if row is None:
                    return error_response(400, "ROLE_NOT_FOUND", f"角色分组不存在：{rid}")
                if int(row["owner_id"] or 1) != owner_id:
                    return error_response(
                        403, "FORBIDDEN_OWNER", "只能选择自己创建的角色分组作为任务对象"
                    )
                plat = str(row["platform"] or "").lower()
                if allowed and plat not in allowed:
                    return error_response(
                        403, "PLATFORM_NOT_ALLOWED", "当前账号未被授权该办公软件平台"
                    )
    except Exception:
        return None
    return None


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
_LOGIN_FAIL_MAX_KEYS = 2000  # 2026-11-05：字典容量护栏，超过即淘汰 15 分钟未活跃的条目


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
        info["last_fail"] = now
        if info["count"] >= max_fails:
            info["locked_until"] = now + 900.0
            info["count"] = 0.0
        _LOGIN_FAILURES[key] = info
        # 2026-11-05 容量护栏：fail_key 含客户端可控的用户名/IP，爆破会不断造新 key。
        # 超过上限时先淘汰 15 分钟无动静的条目，字典不会随攻击无限膨胀。
        if len(_LOGIN_FAILURES) > _LOGIN_FAIL_MAX_KEYS:
            cutoff = now - 900.0
            for k in [k for k, v in _LOGIN_FAILURES.items() if float(v.get("last_fail") or 0) < cutoff]:
                _LOGIN_FAILURES.pop(k, None)


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
            # ── 2026-11-04 多管理员分级 ────────────────────────────────
            admin = _admin_profile(state, sess)
            if _is_super_admin(admin):
                # 超管：全局只读 + 账号/通道配置可写；业务写接口一律拒绝
                if method in ("POST", "PUT", "DELETE", "PATCH") and not (
                    path.startswith(SUPER_ADMIN_WRITE_ALLOW_PREFIXES)
                    or path in SUPER_ADMIN_WRITE_ALLOW_EXACT
                ):
                    return error_response(
                        403, "SUPER_ADMIN_READONLY",
                        "超级管理员仅负责全平台查看与账号分配，业务数据（任务/角色/成员）请由分级管理员账号维护",
                    )
            else:
                # 分级管理员：全局通道配置只读给超管；路径里的资源必须属于自己
                if path.startswith("/api/admin/channel-config"):
                    return error_response(403, "FORBIDDEN", "通道配置仅超级管理员可访问")
                forbidden = _check_path_ownership(state, admin, path)
                if forbidden is not None:
                    return forbidden
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
    platform: Optional[str] = None,
    owner_id: Optional[int] = None,
) -> tuple[str, str, List[Any]]:
    """「任务实例」统一筛选条件（列表 / 统计 / 导出共用）。

    2026-10-28 修复：此前 `/api/admin/instances` 只接受 template_id / period_key / status / q，
    前端发来的 kind、role_id、phone 被 FastAPI 静默丢弃 —— 于是「选纯通知提醒」仍显示上传任务、
    「所属角色」筛选无效。这里补齐，并返回 (FROM, WHERE, params) 供两处一致复用。
    """
    from_sql = (
        "FROM task_instance i "
        "LEFT JOIN task_template t ON t.template_id = i.template_id "
        "LEFT JOIN admin_user u ON u.id = i.owner_id"
    )
    where: List[str] = []
    params: List[Any] = []

    # 2026-10-29 用户口径：停用（enabled=0）或已删除的模板，其历史实例不再出现在
    # 任务实例列表与统计里——否则「停用」了还挂在列表上，看起来像没生效。
    where.append("t.enabled = 1 AND t.deleted = 0")

    # 2026-11-04 多管理员分级：实例按归属（继承模板 owner_id）隔离
    if owner_id is not None:
        where.append("i.owner_id = ?")
        params.append(int(owner_id))

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

    # 2026-11-02 多通道面板：任务归属平台 = 模板所选角色所属平台（角色已按办公软件隔离）。
    # 不再按「成员在该平台有绑定」判断——否则同一个人两边都绑定时，密信任务会串到企微面板。
    plat = (platform or "").strip().lower()
    if plat:
        where.append(
            "EXISTS (SELECT 1 FROM role r WHERE r.platform = ? AND instr(t.role_ids, r.role_id) > 0)"
        )
        params.append(plat)

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
    platform: Optional[str] = None,
    owner_id: Optional[int] = None,
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
        platform=platform,
        owner_id=owner_id,
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
            f"t.notice_times AS template_notice_times, "
            # 2026-11-04 多管理员分级：超管全局查看时需要看到「创建人」
            f"u.username AS owner_username, u.display_name AS owner_display_name "
            # 2026-10-30 用户需求：同一天内按「执行时刻」从早到晚排（08:00 在 09:00 上面），
            # 管理员顺着看一天的排期；日期维度仍是最近的在上面（period_key DESC）。
            # 通知类 remind_at 可能为空，用 COALESCE 兜底到末尾，最后再用 id 保证稳定顺序。
            f"{from_sql} {where_sql} "
            "ORDER BY i.period_key DESC, COALESCE(NULLIF(t.remind_at, ''), '99:99') ASC, i.id ASC "
            "LIMIT ? OFFSET ?",
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


def _probe_channel(channel: str, payload: Dict[str, str], *, notifier: Any = None) -> tuple[bool, str]:
    """用**临时凭证**做一次真实连通性自检：能换到 access_token 就算通。

    ⛔ 不写库、不改任何状态、不碰服务里正在用的通道实例 —— 纯粹探活。
    返回值 (ok, 人话说明)：错误文案要能直接给填表的人看，不能甩 Python 异常栈。
    """
    from types import SimpleNamespace

    if channel == "zdxlz":
        from ..channels.zdxlz import ZdxlzChannel

        base = payload.get("api_base") or "https://imtwo.zdxlz.com"
        shim = SimpleNamespace(
            api_base=base.rstrip("/"),
            app_id=payload.get("app_id", ""),
            app_secret=payload.get("app_secret", ""),
            verify_token=payload.get("verify_token", ""),
            encrypt_key=payload.get("encrypt_key", ""),
            channel_configured=True,
        )
        chan = ZdxlzChannel(shim)  # type: ignore[arg-type]
        try:
            token = chan.get_access_token(force_refresh=True)
            return True, f"连接成功，已换到 access_token（{mask_value(token)}）"
        except Exception as exc:  # noqa: BLE001 - 一律翻成人话
            text = str(exc)
            low = text.lower()
            if "401" in text or "403" in text:
                return False, "AppID 或 AppSecret 无效（服务端返回 401/403），请核对后重填"
            if "timeout" in low or "timed out" in low or "connect" in low:
                return False, f"网络不通或超时：{text[:120]}"
            return False, f"连接失败：{text[:160]}"
        finally:
            try:
                chan.close()
            except Exception:  # noqa: BLE001
                pass

    if channel == "wecom":
        from ..channels.wecom import WecomClient

        client = WecomClient(
            payload.get("corpid", ""), payload.get("agentid", ""), payload.get("secret", "")
        )
        try:
            token = client.get_token(force=True)
            return True, f"连接成功，已换到 access_token（{mask_value(token)}）"
        except Exception as exc:  # noqa: BLE001
            errcode = getattr(exc, "errcode", None)
            hint = {
                40001: "Secret 无效（40001），请到应用详情页重新复制",
                40013: "corpid 无效（40013），请核对企业 ID",
                40056: "agentid 无效（40056），必须是该企业下的应用 ID（整数）",
                60020: "服务器出口 IP 不在可信 IP 白名单里（60020），请让管理员添加",
                41004: "缺少 secret 参数（41004）",
            }.get(errcode) if errcode is not None else None
            if hint:
                return False, hint
            text = str(exc)
            low = text.lower()
            if "timeout" in low or "timed out" in low or "connect" in low:
                return False, f"网络不通或超时（服务器需能访问 qyapi.weixin.qq.com）：{text[:120]}"
            return False, f"连接失败：{text[:160]}"
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    if channel == "wecom_aibot":
        from ..channels.wecom_aibot import AibotBridge

        bot_id = str(payload.get("bot_id") or "").strip()
        secret = str(payload.get("bot_secret") or "").strip()
        live = getattr(notifier, "bridge", None)
        # ⚠️ 同一机器人只能维持一条长连接，新连接会把旧连接踢下线。
        #    所以当探测的正是「当前正在跑的那一个」时，直接回报已就绪，绝不再建第二条。
        if isinstance(live, AibotBridge) and live.is_ready() and live.bot_id == bot_id:
            return True, "当前通道正在使用该机器人，长连接已就绪（为避免把线上连接踢掉，本次未重复建连）"

        probe = AibotBridge(bot_id, secret, connect_timeout=8.0)
        if not probe.start():
            return False, probe.last_error or "无法启动长连接（SDK 未安装或凭证为空）"
        try:
            ready = probe.wait_ready(8.0)
            err = probe.last_error or ""
        finally:
            try:
                probe.stop()
            except Exception:  # noqa: BLE001
                pass
        if ready:
            return True, "连接成功：长连接已完成鉴权（BotID / Secret 有效）"
        if "SDK 未安装" in err:
            return False, "服务器未安装 wecom-aibot-python-sdk，无法建立长连接（需要 pip install）"
        low = err.lower()
        if "timeout" in low or "timed out" in low or "connect" in low:
            return False, (
                "8 秒内未能完成长连接鉴权，常见原因：① BotID / Secret 不正确；"
                "② 服务器出网 443 被拦，访问不了 openws.work.weixin.qq.com；③ 该机器人已在别处连接"
            )
        detail = f"（{err[:100]}）" if err else ""
        return False, f"长连接建立失败{detail}：请核对 BotID / Secret，并确认服务器可访问 openws.work.weixin.qq.com"

    return False, f"不支持的通道：{channel}"


def create_admin_router(state: StateStore, notifier: Any = None, public_base_url: str = "", notifiers: Optional[Dict[str, Any]] = None) -> APIRouter:
    router = APIRouter(prefix="/api/admin")
    # 2026-10-29 逾期补交审核：审核通过后要主动把新链接推给用户。
    # notifier 由 main.py 按现有装配方式注入（⛔ 不造全局单例）；缺省时只记日志不发送。
    _notifier = notifier
    # 2026-11-03 多通道：platform -> notifier，推送新链接时按任务归属平台选通道。
    _notifiers: Dict[str, Any] = dict(notifiers or {})

    # ==================================================================
    # §9.1 会话与认证接口（admin_session + HttpOnly Cookie）
    # ==================================================================
    @router.post("/login")
    async def login(request: Request):
        body = await _read_json_body(request)
        username = str(body.get("username") or "").strip()
        # 2026-11-04：初始密码常从中文输入法敲出全角「！」，登录时统一归一化
        password = StateStore.normalize_admin_password(str(body.get("password") or ""))
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
        if not int(user.get("enabled") or 0):
            return error_response(403, "ACCOUNT_DISABLED", "该管理员账号已被停用，请联系超级管理员")

        sess = state.create_admin_session(int(user["id"]))
        state.write_audit(
            username=user["username"],
            action="admin.login",
            target_type="admin_user",
            target_id=user["username"],
            detail={"must_change": bool(user["must_change"])},
        )
        _prof = state.parse_admin_account(user)
        resp = JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "username": user["username"],
                "must_change": bool(user["must_change"]),
                "csrf_token": sess["csrf"],
                "role_level": _prof.get("role_level", "sub_admin"),
                "display_name": _prof.get("display_name") or user["username"],
                "allowed_platforms": _prof.get("allowed_platforms") or [],
                "is_super_admin": _prof.get("role_level") == SUPER_ADMIN_LEVEL,
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
        _prof = _admin_profile(state, sess)
        return {
            "ok": True,
            "username": sess["username"],
            "must_change": bool(sess["must_change"]),
            "csrf_token": sess["csrf"],
            "id": _prof.get("id"),
            "role_level": _prof.get("role_level", "sub_admin"),
            "display_name": _prof.get("display_name") or sess["username"],
            "allowed_platforms": _prof.get("allowed_platforms") or [],
            "is_super_admin": _prof.get("role_level") == SUPER_ADMIN_LEVEL,
            # 2026-11-05：代登录标识（前端显示代管徽章 + 「切回」入口）
            "assumed": bool(sess.get("parent_sid")),
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
    # 2026-11-04 多管理员分级：管理员账号管理（仅超级管理员）
    # ==================================================================
    def _require_super(state: StateStore, sess: Dict[str, Any]) -> Optional[JSONResponse]:
        admin = _admin_profile(state, sess)
        if not _is_super_admin(admin):
            return error_response(403, "FORBIDDEN", "仅超级管理员可管理分级管理员账号")
        return None

    @router.get("/accounts")
    async def list_accounts(request: Request, platform: Optional[str] = None):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        # 2026-11-06 平台口径：传 platform 时卡片统计（角色/成员/模板/今日实例）按该平台过滤
        return {"ok": True, "items": state.list_admin_accounts(platform=platform),
                "default_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD}

    @router.post("/accounts")
    async def create_account(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        try:
            acct = state.create_admin_account(
                username=str(body.get("username") or ""),
                display_name=str(body.get("display_name") or ""),
                allowed_platforms=body.get("allowed_platforms"),
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "account": acct,
                "default_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD,
                "must_change_password": True}

    @router.put("/accounts/{admin_id}")
    async def update_account(admin_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        enabled = body.get("enabled")
        try:
            acct = state.update_admin_account(
                admin_id,
                display_name=body.get("display_name"),
                allowed_platforms=body.get("allowed_platforms"),
                enabled=(None if enabled is None else bool(enabled)),
                actor=sess["username"],
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "account": acct}

    @router.post("/accounts/{admin_id}/reset-password")
    async def reset_account_password(admin_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        try:
            res = state.reset_admin_password(admin_id, actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, **res, "default_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD}

    @router.post("/accounts/{admin_id}/assume")
    async def assume_account(admin_id: int, request: Request):
        """超管免退出切换身份（2026-11-05 用户需求）：以超管权限为目标分级管理员签发新会话。

        切换后即以目标身份操作（可建模板/发消息），全程审计 admin.assume；
        目标须启用、不能代超管；切回由前端用保存的原超管会话，不走本接口（代管身份是 sub_admin，重复调自然 403）。
        """
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        target = state.get_admin_by_id(int(admin_id))
        if not target:
            return error_response(404, "NOT_FOUND", "目标账号不存在")
        if str(target.get("role_level") or "") == SUPER_ADMIN_LEVEL:
            return error_response(400, "VALIDATION_FAILED", "不能代超级管理员账号登录")
        if not int(target.get("enabled") or 0):
            return error_response(400, "VALIDATION_FAILED", "目标账号已被停用，不能代登录")
        new_sess = state.create_admin_session(int(target["id"]), parent_sid=str(sess["sid"]))
        prof = state.parse_admin_account(target)
        state.write_audit(
            username=sess["username"], action="admin.assume", target_type="admin_user",
            target_id=str(target["username"]), detail={"as": str(target["username"])},
        )
        resp = JSONResponse(status_code=200, content={
            "ok": True, "csrf_token": new_sess["csrf"],
            "profile": {
                "username": str(target["username"]),
                "display_name": str(prof.get("display_name") or target["username"]),
                "role_level": str(prof.get("role_level") or StateStore.SUB_ADMIN),
                "allowed_platforms": prof.get("allowed_platforms") or [],
                "is_super_admin": False,
            },
        })
        resp.set_cookie(key=COOKIE_NAME, value=new_sess["sid"], max_age=SESSION_TTL_HOURS * 3600,
                        httponly=True, samesite="lax", path="/")
        return resp

    @router.post("/accounts/unassume")
    async def unassume_account(request: Request):
        """代管身份一键切回原超级管理员（2026-11-05）。HttpOnly Cookie 前端拿不到 sid，
        所以切回必须在服务端做：代管会话上挂了 parent_sid，这里反查原超管会话并重新下发 Cookie。
        普通分级管理员会话没有 parent_sid，自然 400 —— 不需要额外权限判断。"""
        sess, err = require_session(request, state, check_csrf=True, allow_must_change=True)
        if err is not None:
            return err
        assert sess is not None
        parent_sid = str(sess.get("parent_sid") or "")
        if not parent_sid:
            return error_response(400, "VALIDATION_FAILED", "当前会话不是代登录会话，无需切回")
        parent_sess = state.get_admin_session(parent_sid)
        if not parent_sess:
            return error_response(400, "SESSION_EXPIRED", "原超级管理员会话已失效，请重新登录")
        parent_user = state.get_admin_by_id(int(parent_sess["user_id"]))
        if not parent_user or str(parent_user.get("role_level") or "") != SUPER_ADMIN_LEVEL:
            return error_response(400, "SESSION_EXPIRED", "原会话已不再属于超级管理员，请重新登录")
        prof = state.parse_admin_account(parent_user)
        state.write_audit(
            username=sess["username"], action="admin.unassume", target_type="admin_user",
            target_id=str(parent_user["username"]), detail={"from": str(sess["username"])},
        )
        resp = JSONResponse(status_code=200, content={
            "ok": True, "csrf_token": parent_sess["csrf"],
            "profile": {
                "username": str(parent_user["username"]),
                "display_name": str(prof.get("display_name") or parent_user["username"]),
                "role_level": SUPER_ADMIN_LEVEL,
                "allowed_platforms": prof.get("allowed_platforms") or [],
                "is_super_admin": True,
            },
        })
        resp.set_cookie(key=COOKIE_NAME, value=parent_sid, max_age=SESSION_TTL_HOURS * 3600,
                        httponly=True, samesite="lax", path="/")
        return resp

    @router.post("/accounts/{admin_id}/transfer")
    async def transfer_account_data(admin_id: int, request: Request):
        """定向转移：把该管理员名下全部业务数据移交给另一分级管理员（删除前「先转移」选项用）。"""
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        to_id = body.get("to_admin_id")
        if to_id is None:
            return error_response(400, "VALIDATION_FAILED", "缺少接替管理员 to_admin_id")
        try:
            res = state.transfer_owner_between(int(admin_id), int(to_id), actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return res

    @router.delete("/accounts/{admin_id}")
    async def delete_account(admin_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        cfg = getattr(request.app.state, "config", None)
        uploads_dir = cfg.uploads_dir if cfg is not None else None
        # 2026-11-05 级联删除（不可逆）：执行前先做一次 DB 文件级备份 —— 备份是唯一恢复手段，失败即拒绝删除。
        try:
            backup_path = state.backup_database(tag=f"purge-{int(admin_id)}")
        except Exception as exc:  # noqa: BLE001 - 备份失败必须拦住不可逆删除
            return error_response(500, "BACKUP_FAILED", f"删除前备份失败，已中止删除：{exc}")
        try:
            res = state.delete_admin_account(int(admin_id), actor=sess["username"], uploads_dir=uploads_dir)
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, **res, "backup": backup_path}

    @router.get("/global-activated-members")
    async def global_activated_members(
        request: Request,
        q: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
    ):
        """全局搜索**已激活**成员：激活关系（delivery_target）全平台共享，跨管理员免二次激活。"""
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        _admin = _admin_profile(state, sess)
        _plat = (platform or "").strip().lower()
        if not _platform_allowed(_admin, _plat):
            return error_response(403, "PLATFORM_NOT_ALLOWED", "当前账号未被授权该办公软件平台")
        items = state.search_global_activated(q=q or "", platform=_plat or None, limit=limit)
        return {"ok": True, "items": items}

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
        platform: Optional[str] = None,
        owner_id: Optional[str] = None,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        enabled_bool = _parse_optional_bool(enabled)
        _admin = _admin_profile(state, sess)
        res = state.list_templates(
            q=q, kind=kind, enabled=enabled_bool, page=page, size=size, platform=platform,
            owner_id=_owner_filter(_admin, owner_id),
        )
        return {"ok": True, "total": res["total"], "items": res["items"]}

    @router.post("/templates")
    async def create_template(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        _admin = _admin_profile(state, sess)
        _scope_err = _check_template_role_scope(state, _admin, body)
        if _scope_err is not None:
            return _scope_err
        try:
            tpl = state.create_template(body, actor=sess["username"], owner_id=int(_admin.get("id") or 0))
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
        _admin = _admin_profile(state, sess)
        _scope_err = _check_template_role_scope(state, _admin, body)
        if _scope_err is not None:
            return _scope_err
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
    # 2026-11-03 部门选项（后台配置；成员表单与 H5 登记页共用）
    # ==================================================================
    @router.get("/departments")
    async def list_departments(request: Request):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        return {"ok": True, "items": state.list_departments()}

    @router.post("/departments")
    async def create_department(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        try:
            dept = state.create_department(str(body.get("name") or ""), actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "department": dept}

    @router.put("/departments/{dept_id}")
    async def update_department(dept_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        try:
            dept = state.update_department(int(dept_id), str(body.get("name") or ""), actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "department": dept}

    @router.delete("/departments/{dept_id}")
    async def delete_department(dept_id: int, request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        try:
            state.delete_department(int(dept_id), actor=sess["username"])
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True}

    # ── 全平台成员池（2026-11-05）：超管按「人」管理的全局名册，操作全局同步到所有管理员名下 ──
    @router.get("/member-pool")
    async def list_member_pool(request: Request, q: Optional[str] = None, size: int = 500, platform: Optional[str] = None):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        return {"ok": True, "items": state.list_member_pool(q=q or "", size=size, platform=platform)}

    @router.put("/member-pool/{phone}/status")
    async def member_pool_set_status(phone: str, request: Request, platform: Optional[str] = None):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        try:
            res = state.member_pool_set_status(phone, bool(body.get("enabled")), actor=sess["username"], platform=platform)
        except BizError as exc:
            return biz_error_response(exc)
        return res

    @router.put("/member-pool/{phone}")
    async def member_pool_update_profile(phone: str, request: Request, platform: Optional[str] = None):
        """改姓名/部门（platform 传入时仅同步该平台名册行）。⛔ 接口不收手机号 —— 手机号永不可改（用户红线）。"""
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        body = await _read_json_body(request)
        try:
            res = state.member_pool_update_profile(
                phone, name=body.get("name"), dept=body.get("dept"), actor=sess["username"], platform=platform)
        except BizError as exc:
            return biz_error_response(exc)
        return res

    @router.delete("/member-pool/{phone}")
    async def member_pool_delete(phone: str, request: Request, platform: Optional[str] = None):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        forbidden = _require_super(state, sess)
        if forbidden is not None:
            return forbidden
        try:
            res = state.member_pool_delete(phone, actor=sess["username"], platform=platform)
        except BizError as exc:
            return biz_error_response(exc)
        return res

    # ==================================================================
    # §9.3 角色与成员接口
    # ==================================================================
    @router.get("/roles")
    async def list_roles(request: Request, platform: Optional[str] = None, owner_id: Optional[str] = None):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        _admin = _admin_profile(state, sess)
        _owner = _owner_filter(_admin, owner_id)
        items = state.list_roles(platform=platform, owner_id=_owner)
        # 2026-11-05：在册人数按「人」去重（一人多角色只算一次；停用也算在册），
        # 替代前端把各角色人数加总的重复统计。
        return {"ok": True, "items": items, "member_distinct_total": state.member_distinct_total(platform=platform, owner_id=_owner)}

    @router.post("/roles")
    async def create_role(request: Request):
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        _admin = _admin_profile(state, sess)
        _plat = str(body.get("platform") or "zdxlz")
        if not _platform_allowed(_admin, _plat):
            return error_response(403, "PLATFORM_NOT_ALLOWED", "当前账号未被授权该办公软件平台")
        try:
            role = state.create_role(
                name=str(body.get("name") or ""),
                note=body.get("note"),
                actor=sess["username"],
                platform=_plat,
                owner_id=int(_admin.get("id") or 0),
            )
        except BizError as exc:
            return biz_error_response(exc)
        return {"ok": True, "role_id": role["role_id"], "role": role}

    # ── 已激活待归纳人员：2026-11-03 新激活模型（后台先导入才能激活）后整块废弃 ──
    # 待归纳池、移入角色、移除绑定等接口全部下线；历史路由保留 410 提示，防止旧前端缓存误用。

    @router.get("/pending-members")
    @router.get("/unactivated-members")
    async def list_pending_members_removed(request: Request):
        return JSONResponse(status_code=410, content={
            "ok": False, "code": "GONE",
            "message": "待归纳机制已下线：请先在「角色与成员」中导入/添加成员，成员给机器人发消息后会收到登记链接完成激活",
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
            items = state.list_data_snapshots(
                source_key=source_key, period_key=period_key, limit=limit,
                owner_id=_owner_filter(_admin_profile(state, sess), None),
            )
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
        platform: Optional[str] = None,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        try:
            res = state.list_role_members(role_id=role_id, q=q, page=page, size=size, platform=platform)
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
        _admin = _admin_profile(state, sess)
        _phone = str(body.get("phone") or "").strip()
        try:
            member = state.add_role_member(
                role_id=role_id,
                phone=_phone,
                name=str(body.get("name") or ""),
                dept=body.get("dept"),
                source="manual",
                actor=sess["username"],
                owner_id=int(_admin.get("id") or 0),
            )
        except BizError as exc:
            return biz_error_response(exc)
        # 2026-11-04：成员激活关系全局共享 —— 别的管理员已激活过就无需二次激活
        _global_reused = state.is_phone_activated(_phone, platform=None)
        return {
            "ok": True,
            "member": member,
            "global_reused": _global_reused,
            "global_reused_count": 1 if _global_reused else 0,
            "global_reused_phones": [_phone] if _global_reused else [],
        }

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
        # 2026-11-09 用户口径：分级管理员**没有**修改已存在成员姓名/部门的权限
        # （只有首次导入时能定，且要同步全局池；其余由超管统一维护）。
        # → 前端已把入口关掉，这里再加一道后端护栏（前端可被绕过、直接调 API）。
        _admin = _admin_profile(state, sess)
        if str(_admin.get("role_level") or "") != "super_admin":
            _cur = state.conn.execute(
                "SELECT name, dept FROM member WHERE role_id = ? AND phone = ?",
                ((role_id or "").strip(), (phone or "").strip()),
            ).fetchone()
            if _cur is not None:
                _new_name = body.get("name")
                _new_dept = body.get("dept")
                _name_chg = _new_name is not None and str(_new_name).strip() != str(_cur["name"] or "").strip()
                _dept_chg = _new_dept is not None and str(_new_dept).strip() != str(_cur["dept"] or "").strip()
                if _name_chg or _dept_chg:
                    return error_response(
                        403, "MEMBER_PROFILE_FORBIDDEN",
                        "分级管理员不能修改成员的姓名/部门（姓名与部门由超管统一维护）；"
                        "如需变更，请在首次导入时指定，或联系超管处理。",
                    )
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
        _admin = _admin_profile(state, sess)
        try:
            res = state.import_role_members(
                role_id=role_id,
                csv_rows=csv_rows,  # type: ignore[arg-type]
                dry_run=dry_run,
                actor=sess["username"],
                owner_id=int(_admin.get("id") or 0),
            )
        except BizError as exc:
            return biz_error_response(exc)
        # 2026-11-04：本次导入里有多少人已被（任意管理员）激活 → 免二次激活
        _reused_phones: List[str] = []
        for _row in (csv_rows or []):
            if isinstance(_row, dict):
                _p = str(_row.get("phone") or "").strip()
                if _p and _p not in _reused_phones and state.is_phone_activated(_p, platform=None):
                    _reused_phones.append(_p)
        res["global_reused"] = bool(_reused_phones)
        res["global_reused_count"] = len(_reused_phones)
        res["global_reused_phones"] = _reused_phones
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
        platform: Optional[str] = None,
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
            platform=platform,
            owner_id=_owner_filter(_admin, owner_id),
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
        platform: Optional[str] = None,
        page: int = 1,
        size: int = 20,
        owner_id: Optional[str] = None,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        _admin = _admin_profile(state, sess)
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
            platform=platform,
            owner_id=_owner_filter(_admin, owner_id),
            page=page,
            size=size,
        )
        return {"ok": True, "total": res["total"], "page": page, "size": size, "items": res["items"]}

    @router.get("/instances/pending-reviews")
    async def pending_reviews(request: Request, days: int = 7, platform: Optional[str] = None):
        """跨日期的「待我处理」聚合：用户提交待审 + 补交申请待审。

        为什么需要单开一个接口：实例列表的 period_key 是**单日精确匹配**，而补交待审的实例
        必然是往日的（逾期才可能补交），默认「今天」筛选下永远查不到，管理员必须先手改日期。
        这里按「提交/申请时刻」跨日聚合，不看任务日期；只读，不改任何状态。
        """
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        try:
            d = max(1, min(int(days or 7), 90))
        except Exception:  # noqa: BLE001
            d = 7
        # 用户口径：自然日口径（今天往前数 d 天，含今天），不是滚动 7×24h
        start_dt = (state.now() - timedelta(days=d - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_s = format_dt(start_dt)
        limit = 100
        plat_sql = ""
        plat_params: List[Any] = []
        if (platform or "").strip():
            # 2026-11-02：审核待办按任务归属平台（模板角色平台）过滤，不再按成员绑定。
            plat_sql = ("AND EXISTS (SELECT 1 FROM role r WHERE r.platform = ? "
                        "AND instr(t.role_ids, r.role_id) > 0) ")
            plat_params = [platform.strip().lower()]
        base = (
            # name_snap 是实例表列名，前端统一叫 member_name_snap，这里直接给别名省一层换算
            "SELECT i.*, i.name_snap AS member_name_snap, t.name AS template_name, t.kind AS template_kind "
            "FROM task_instance i JOIN task_template t ON t.template_id = i.template_id "
            "WHERE t.enabled = 1 AND t.deleted = 0 "
        )
        with state.lock:
            sub_rows = state.conn.execute(
                base + "AND i.status = 'submitted' AND COALESCE(i.submitted_at, i.created_at) >= ? "
                       + plat_sql +
                       "ORDER BY COALESCE(i.submitted_at, i.created_at) DESC, i.id DESC LIMIT ?",
                (start_s, *plat_params, limit + 1),
            ).fetchall()
            rq_rows = state.conn.execute(
                base + "AND i.resubmit_status = 'pending' AND COALESCE(i.resubmit_req_at, i.created_at) >= ? "
                       + plat_sql +
                       "ORDER BY COALESCE(i.resubmit_req_at, i.created_at) DESC, i.id DESC LIMIT ?",
                (start_s, *plat_params, limit + 1),
            ).fetchall()

        def _row(r):
            d0 = dict(r)
            d0["kind"] = d0.get("template_kind") or "form"
            d0["late_submit"] = bool(d0.get("late_submit", 0))
            d0["need_review"] = bool(d0.get("need_review", 0))
            return d0

        subs = [_row(r) for r in sub_rows]
        rqs = [_row(r) for r in rq_rows]
        return {
            "ok": True,
            "days": d,
            "since": start_s,
            "limit": limit,
            "truncated": {"submitted": len(subs) > limit, "resubmit": len(rqs) > limit},
            "submitted": subs[:limit],
            "resubmit": rqs[:limit],
            "total": min(len(subs), limit) + min(len(rqs), limit),
        }

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
        # 2026-10-30 详情补齐模板侧字段：
        # · kind / role_id：列表早就用模板补了，详情漏了，前端只能退化成「业务角色」
        # · need_review：决定状态文案是「已完成」还是「审核通过」
        # · modules：决定详情里显示哪些上传区块（管理员没勾文件就不该出现文件栏）
        _tpl = tpl or {}
        _role_ids = _tpl.get("role_ids") or []
        inst["kind"] = _tpl.get("kind") or "form"
        inst["need_review"] = bool(_tpl.get("need_review"))
        inst["role_id"] = _role_ids[0] if _role_ids else ""
        inst["role_ids"] = list(_role_ids)
        inst["modules"] = effective_h5_modules(_tpl)
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
        # 2026-11-03：按任务归属平台选通道（密信任务发密信、企微任务发企微），
        # 不再跟全局 active 通道走，避免补交新链接串到另一个办公软件。
        tpl = state.get_template(inst["template_id"])
        plats = state.role_platforms(list((tpl or {}).get("role_ids") or []))
        platform = plats.pop() if len(plats) == 1 else ""
        nt = _notifiers.get(platform) if (platform and _notifiers) else _notifier
        target = (
            state.get_delivery_target(inst["phone"], platform=platform)
            if (platform and _notifiers)
            else state.get_delivery_target(inst["phone"])
        )
        ext_id = (target or {}).get("external_id")
        if nt is None or not ext_id:
            logger.warning("补交审核通知未发送（notifier 或 external_id 缺失） instance=%s", inst.get("id"))
            return False
        try:
            res = nt.send_text(ext_id, text, f"{tag}|{inst['id']}|{int(time.time() * 1000)}")
            return bool(getattr(res, "ok", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("补交审核通知发送失败 instance=%s: %s", inst.get("id"), exc)
            return False

    @router.post("/channel-config/test-send")
    async def channel_test_send(request: Request):
        """给指定成员发一条测试消息（text / image），用于联调取证。

        为什么必须有这个端点：机器人**同一时间只能有一条长连接**，
        想在外面写脚本验证发图，一连接就会把线上连接踢掉。
        所以验证只能在**进程内**做 —— 复用 scheduler 正在用的那个 notifier。
        """
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        body = await _read_json_body(request)
        kind = str(body.get("kind") or "text").strip().lower()
        text = str(body.get("text") or "这是一条测试消息").strip()
        userid = str(body.get("userid") or "").strip()
        phone = str(body.get("phone") or "").strip()

        if not userid and phone:
            target = state.get_delivery_target(phone)
            userid = str((target or {}).get("external_id") or "").strip()
        if not userid:
            with state.lock:
                row = state.conn.execute(
                    "SELECT external_id FROM delivery_target WHERE platform = ? AND usable = 1 "
                    "ORDER BY activated_at DESC LIMIT 1",
                    (state.platform,),
                ).fetchone()
            userid = str(row["external_id"] if row else "")
        if not userid:
            return error_response(400, "NO_TARGET", "没有可用的投递绑定：请先在机器人里发送「姓名 手机号」完成激活")

        if _notifier is None or not hasattr(_notifier, "send_text"):
            return error_response(400, "NO_NOTIFIER", "当前没有可用的消息通道（notifier 未注入）")

        if kind == "image":
            if not hasattr(_notifier, "send_image"):
                return error_response(
                    400, "NO_IMAGE_SUPPORT",
                    f"当前通道（{state.platform}）不支持图片消息；只有企业微信智能机器人支持",
                )
            # 找一张现成的图：优先今天刚发的附图副本，其次看板图
            data_dir = Path(state.db.db_path).parent
            candidates = []
            for sub in ("public_img", "dashboards"):
                d = data_dir / sub
                if d.is_dir():
                    candidates.extend(sorted(d.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)[:5])
            candidates = [c for c in candidates if c.is_file()]
            if not candidates:
                return error_response(404, "NO_IMAGE", "data 目录下没有可用的 png（public_img / dashboards 都是空的）")
            img_path = candidates[0]
            res = _notifier.send_image(userid, str(img_path), "test_send_image")
            detail = f"图片消息已发送（{img_path.name}，{img_path.stat().st_size} 字节）" if res.ok else f"发送失败：{res.error}"
            return {"ok": bool(res.ok), "kind": "image", "to": userid, "file": img_path.name, "detail": detail}

        res = _notifier.send_text(userid, text, "test_send_text")
        return {
            "ok": bool(res.ok), "kind": "text", "to": userid,
            "detail": "文本已发送" if res.ok else f"发送失败：{res.error}",
        }

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
        # 2026-11-05 多管理员隔离：附件 ref 是随机哈希，但分级管理员不应能跨管理员下载附件（IDOR）。
        # 归属判定：模板属于自己，或该手机号在自己名下的角色里。不满足一律 404（不泄露存在性）。
        _admin = _admin_profile(state, sess)
        if not _is_super_admin(_admin):
            own_id = int(_admin.get("id") or 0)
            tpl = state.get_template(str(up.get("template_id") or ""), include_deleted=True) if up.get("template_id") else None
            owns_tpl = bool(tpl) and int(tpl.get("owner_id") or 0) == own_id
            if not owns_tpl:
                with state.lock:
                    in_own_role = state.conn.execute(
                        "SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                        "WHERE m.phone = ? AND r.owner_id = ? LIMIT 1",
                        (str(up.get("phone") or ""), own_id),
                    ).fetchone()
                if in_own_role is None:
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
        _admin = _admin_profile(state, sess)

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
        # 2026-11-04 多管理员分级：提醒日志按模板归属过滤（子账号只看自己的）
        _own = _owner_filter(_admin, None)
        if _own is not None:
            where.append("EXISTS (SELECT 1 FROM task_template t2 WHERE t2.template_id = r.template_id AND t2.owner_id = ?)")
            params.append(int(_own))

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
        # 2026-11-04 多管理员分级：分级管理员只看得到自己的操作记录；超管看全部
        _admin = _admin_profile(state, sess)
        if not _is_super_admin(_admin):
            where.append("username = ?")
            params.append(str(sess.get("username") or ""))
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
        platform: Optional[str] = None,
        owner_id: Optional[str] = None,
    ):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        _admin = _admin_profile(state, sess)
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
            platform=platform,
            owner_id=_owner_filter(_admin, owner_id),
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

    # ==================================================================
    # 2026-10-30 通道配置向导（首次安装填表即用，⛔ 不写 .env）
    # ==================================================================
    def _channel_store():
        """按当前 state 拼一个配置读写门面；data_dir 取数据库同级的父目录。"""
        data_dir = Path(state.db.db_path).parent
        return ChannelConfigStore(state.conn, state.lock, data_dir)

    def _env_fallback(cfg: Any, channel: str) -> Dict[str, str]:
        """库里没配时，把 env 里的现状当作「已配置」返回（掩码可见，证明当前在跑）。

        cfg 取 request.app.state.config；单测里单独组 router 时可能没有，直接当空处理。
        """
        if cfg is None:
            return {}
        out: Dict[str, str] = {}
        for src, dst in field_map(channel).items():
            val = str(getattr(cfg, dst, "") or "").strip()
            if val:
                out[src] = val
        return out

    @router.get("/channel-config")
    async def get_channel_config(request: Request):
        """返回每个通道的配置状态 + 掩码 + 回调地址（⛔ 永不回完整凭证）。"""
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None

        cc = _channel_store()
        cfg_now = getattr(request.app.state, "config", None)
        db_all = cc.read_all()
        active = cc.read_active_channel() or (os.environ.get("IMT_CHANNEL", "").strip().lower() or "zdxlz")
        enabled = cc.read_enabled_channels()
        if enabled is None:
            enabled = [active] if active in CHANNEL_SCHEMA else ["zdxlz"]
        source_kind, source_desc = key_source()

        channels = []
        any_configured = False
        for name, meta in CHANNEL_SCHEMA.items():
            if meta.get("hidden"):
                continue  # 2026-11-02 用户口径：自建应用通道已停用，不在后台展示
            db_payload = db_all.get(name) or {}
            env_payload = _env_fallback(cfg_now, name)
            if db_payload:
                effective, source = db_payload, "db"
            elif env_payload:
                effective, source = env_payload, "env"
            else:
                effective, source = {}, "none"
            configured = channel_configured(name, effective)
            any_configured = any_configured or configured
            channels.append({
                "channel": name,
                "label": meta["label"],
                "configured": configured,
                "source": source,
                "masked": masked_view(name, effective),
                "fields": meta["fields"],
                "callback_url": build_callback_url(public_base_url, name),
                "callback_path": meta["callback_path"],
                "is_active": name == active,
                "enabled": name in enabled,
            })

        return {
            "ok": True,
            "channels": channels,
            "active_channel": active,
            "enabled_channels": enabled,
            "any_configured": any_configured,
            "public_base_url": public_base_url,
            "enc_key_source": source_kind,
            "enc_key_desc": source_desc,
        }

    @router.post("/channel-config")
    async def save_channel_config(request: Request):
        """保存凭证：校验必填 → AES-GCM 加密入库 → 回掩码。生效需重启服务。"""
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return error_response(400, "VALIDATION_FAILED", "请求体不是合法 JSON")
        if not isinstance(body, dict):
            return error_response(400, "VALIDATION_FAILED", "请求体格式错误")

        channel = str(body.get("channel") or "").strip().lower()
        payload = body.get("payload")
        if channel not in CHANNEL_SCHEMA:
            return error_response(400, "VALIDATION_FAILED", "请选择要配置的通道")
        if not isinstance(payload, dict):
            return error_response(400, "VALIDATION_FAILED", "payload 必须是对象")

        cc = _channel_store()
        try:
            merged = cc.save(channel, payload, now_str=state.now_str())
        except ValueError as exc:
            return error_response(400, "VALIDATION_FAILED", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("保存通道配置失败")
            return error_response(500, "SAVE_FAILED", f"保存失败：{exc}")

        want_active = bool(body.get("set_active"))
        if want_active:
            try:
                cc.save_active_channel(channel, now_str=state.now_str())
                # 兼容旧前端：勾选启用 = 加入多通道启用列表（不再覆盖其他通道）
                current_enabled = cc.read_enabled_channels() or []
                if channel not in current_enabled:
                    cc.save_enabled_channels(current_enabled + [channel], now_str=state.now_str())
            except Exception:  # noqa: BLE001
                logger.exception("记录启用通道失败")

        logger.info(
            "通道配置已更新：channel=%s actor=%s set_active=%s",
            channel, sess.get("username"), want_active,
        )
        try:
            # 审计只记字段名，⛔ 不记任何凭证值
            state.write_audit(
                str(sess.get("username") or "admin"),
                "channel_config_save",
                "channel",
                channel,
                {"fields": sorted(merged.keys()), "set_active": want_active},
            )
        except Exception:  # noqa: BLE001 - 审计失败不影响保存
            logger.warning("写审计日志失败（通道配置）")

        return {
            "ok": True,
            "channel": channel,
            "masked": masked_view(channel, merged),
            "is_active": want_active,
            "restart_required": True,
            "message": "重启服务后生效：systemctl restart imtask",
        }

    @router.post("/channel-config/enabled")
    async def set_enabled_channels(request: Request):
        """多通道启用开关（可多选）。与凭证保存分离：切开关不必重填凭证。"""
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None
        try:
            body = await _read_json_body(request)
        except Exception:  # noqa: BLE001
            return error_response(400, "VALIDATION_FAILED", "请求体不是合法 JSON")
        raw = body.get("channels")
        if not isinstance(raw, list):
            return error_response(400, "VALIDATION_FAILED", "channels 必须是数组")
        cc = _channel_store()
        try:
            saved = cc.save_enabled_channels(raw, now_str=state.now_str())
            if saved:
                active = cc.read_active_channel()
                if active not in saved:
                    cc.save_active_channel(saved[0], now_str=state.now_str())
        except Exception as exc:  # noqa: BLE001
            logger.exception("保存多通道启用列表失败")
            return error_response(500, "SAVE_FAILED", f"保存失败：{exc}")
        try:
            state.write_audit(
                str(sess.get("username") or "admin"), "channel_config_enabled",
                "channel", ",".join(saved) or "none", {"enabled_channels": saved},
            )
        except Exception:  # noqa: BLE001
            logger.warning("写审计日志失败（多通道启用）")
        return {
            "ok": True,
            "enabled_channels": saved,
            "restart_required": True,
            "message": "重启服务后生效：systemctl restart imtask",
        }

    @router.post("/channel-config/test")
    async def test_channel_config(request: Request):
        """用提交的临时凭证做连通性自检（⛔ 不入库），错误信息要人话。"""
        sess, err = require_session(request, state, check_csrf=True)
        if err is not None:
            return err
        assert sess is not None

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return error_response(400, "VALIDATION_FAILED", "请求体不是合法 JSON")
        if not isinstance(body, dict):
            return error_response(400, "VALIDATION_FAILED", "请求体格式错误")

        channel = str(body.get("channel") or "").strip().lower()
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        if channel not in CHANNEL_SCHEMA:
            return error_response(400, "VALIDATION_FAILED", "请选择要测试的通道（zdxlz / wecom）")

        # 允许「只测改动过的字段」：没填的项用库里已有值补全（与保存的合并语义一致）
        cfg_now = getattr(request.app.state, "config", None)
        existing = _channel_store().read(channel) or _env_fallback(cfg_now, channel)
        probe = {**existing, **normalize_payload(channel, payload)}
        verr = validate_payload(channel, probe)
        if verr:
            return {"ok": False, "detail": verr, "channel": channel}

        ok, detail = _probe_channel(channel, probe, notifier=_notifier)
        return {"ok": ok, "detail": detail, "channel": channel}

    return router
