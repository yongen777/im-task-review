# -*- coding: utf-8 -*-
"""数据访问层（唯一允许写 SQL 的模块，覆盖阶段一 + 2A + 2B）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import NowFn, default_now, format_dt, parse_dt
from .db import Database

PBKDF2_ITERATIONS = 260_000
SESSION_TTL_HOURS = 24 * 7
H5_TOKEN_TTL_HOURS = 72

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
FIELD_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VALID_TEMPLATE_KINDS = ("form", "image", "file", "notice")
VALID_PERIODS = ("day", "week", "month")
VALID_FIELD_TYPES = ("text", "number", "date", "select")


class BizError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


StateError = BizError


def is_valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match((phone or "").strip()))


def mask_phone(phone: str) -> str:
    s = (phone or "").strip()
    return f"{s[:3]}****{s[-4:]}" if len(s) == 11 else s


def is_valid_hhmm(value: str) -> bool:
    return bool(HHMM_RE.match((value or "").strip()))


def calc_resubmit_deadline(deadline_at: str, reviewed_at: Optional[str]) -> str:
    """R27：驳回重交窗口 = max(原截止 + 72h, 驳回时刻 + 24h)。"""
    orig_end = parse_dt(deadline_at) + timedelta(hours=72)
    if not reviewed_at:
        return format_dt(orig_end)
    return format_dt(max(orig_end, parse_dt(reviewed_at) + timedelta(hours=24)))


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt_val = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_val.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256.{PBKDF2_ITERATIONS}.{salt_val}.{dk.hex()}".replace(".", "$")


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iter_str, salt_val, hex_digest = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_val.encode("utf-8"), int(iter_str))
        return hmac.compare_digest(dk.hex(), hex_digest)
    except Exception:
        return False


def validate_new_password(password: str) -> Optional[str]:
    if not isinstance(password, str) or len(password) < 8:
        return "新密码长度不能少于 8 位"
    if not (any(ch.isalpha() for ch in password) and any(ch.isdigit() for ch in password)):
        return "新密码必须同时包含字母与数字"
    return None


def _clamp_page_size(page: Any, size: Any) -> Tuple[int, int]:
    try:
        p = int(page)
    except Exception:
        p = 1
    try:
        s = int(size)
    except Exception:
        s = 20
    return max(1, p), max(1, min(200, s))


def _validate_form_schema(schema_raw: Any) -> List[Dict[str, Any]]:
    if isinstance(schema_raw, str):
        try:
            schema_raw = json.loads(schema_raw)
        except Exception:
            raise BizError(400, "VALIDATION_FAILED", "schema_json 必须是合法的 JSON 数组")
    if not isinstance(schema_raw, list) or len(schema_raw) < 1:
        raise BizError(400, "VALIDATION_FAILED", "表单类型模板必须至少包含 1 个字段 (schema_json)")

    seen_keys: set[str] = set()
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(schema_raw, start=1):
        if not isinstance(item, dict):
            raise BizError(400, "VALIDATION_FAILED", f"第 {idx} 个字段定义必须是对象")
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        ftype = str(item.get("type") or "").strip()
        if not key or not FIELD_KEY_RE.match(key):
            raise BizError(400, "VALIDATION_FAILED", f"第 {idx} 个字段 key 非法")
        if key in seen_keys:
            raise BizError(400, "VALIDATION_FAILED", f"字段 key 重复: {key}")
        seen_keys.add(key)
        if not label:
            raise BizError(400, "VALIDATION_FAILED", f"字段 {key} 的 label 不能为空")
        if ftype not in VALID_FIELD_TYPES:
            raise BizError(400, "VALIDATION_FAILED", f"字段 {key} 的 type 仅支持 text/number/date/select")

        field_obj: Dict[str, Any] = {"key": key, "label": label, "type": ftype, "required": bool(item.get("required", False))}
        unit = item.get("unit")
        if unit is not None and str(unit).strip():
            field_obj["unit"] = str(unit).strip()

        if ftype == "text":
            try:
                max_len = int(item.get("max_len", 200))
            except Exception:
                raise BizError(400, "VALIDATION_FAILED", f"字段 {key} 的 max_len 必须为正整数")
            if max_len <= 0:
                raise BizError(400, "VALIDATION_FAILED", f"字段 {key} 的 max_len 必须大于 0")
            field_obj["max_len"] = max_len
        elif ftype == "select":
            options = item.get("options")
            if not isinstance(options, list) or not options:
                raise BizError(400, "VALIDATION_FAILED", f"下拉字段 {key} 必须提供非空 options 列表")
            clean_opts = [str(o).strip() for o in options if str(o).strip()]
            if not clean_opts:
                raise BizError(400, "VALIDATION_FAILED", f"下拉字段 {key} 的 options 不能为空")
            field_obj["options"] = clean_opts
        normalized.append(field_obj)
    return normalized


def validate_form_submission_fields(schema: List[Dict[str, Any]], fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    input_fields = fields if isinstance(fields, dict) else {}
    cleaned: Dict[str, Any] = {}
    for fdef in schema:
        key, label, ftype = fdef["key"], fdef["label"], fdef["type"]
        raw_val = input_fields.get(key)
        str_val = "" if raw_val is None else str(raw_val).strip()
        if str_val == "":
            if fdef.get("required", False):
                raise BizError(400, "VALIDATION_FAILED", f"请填写{label}")
            cleaned[key] = ""
            continue
        if ftype == "number":
            try:
                float(str_val)
            except Exception:
                raise BizError(400, "VALIDATION_FAILED", f"{label}只能填数字")
            cleaned[key] = raw_val if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool) else str_val
        elif ftype == "text":
            max_len = int(fdef.get("max_len") or 200)
            if len(str_val) > max_len:
                raise BizError(400, "VALIDATION_FAILED", f"{label}不能超过 {max_len} 字")
            cleaned[key] = str_val
        elif ftype == "date":
            if not DATE_RE.match(str_val):
                raise BizError(400, "VALIDATION_FAILED", f"{label}日期格式不正确")
            try:
                datetime.strptime(str_val, "%Y-%m-%d")
            except ValueError:
                raise BizError(400, "VALIDATION_FAILED", f"{label}日期格式不正确")
            cleaned[key] = str_val
        elif ftype == "select":
            if str_val not in (fdef.get("options") or []):
                raise BizError(400, "VALIDATION_FAILED", f"{label}选项不在允许范围内")
            cleaned[key] = str_val
        else:
            cleaned[key] = str_val
    return cleaned


def _fmt_member(mid: int, role_id: str, phone: str, name: str, dept: Optional[str], enabled: Any, source: str, created_at: str, activated: Any) -> Dict[str, Any]:
    return {
        "id": int(mid), "role_id": role_id, "phone": phone, "name": name, "dept": dept,
        "enabled": bool(enabled), "source": source, "created_at": created_at, "activated": bool(activated),
    }


class StateStore:
    def __init__(self, db_path: Path, now_fn: NowFn = default_now) -> None:
        self._now_fn = now_fn
        self.db = Database(db_path, self.now_str)

    @property
    def conn(self):
        return self.db.conn

    @property
    def lock(self):
        return self.db.lock

    def now(self) -> datetime:
        return self._now_fn()

    def now_str(self) -> str:
        return format_dt(self._now_fn())

    def close(self) -> None:
        self.db.close()

    def ping(self) -> bool:
        try:
            with self.lock:
                row = self.conn.execute("SELECT 1 AS ok").fetchone()
                return bool(row and row["ok"] == 1)
        except Exception:
            return False

    def upsert_admin_user(self, username: str, password: str, must_change: bool = False, actor: str = "system") -> int:
        clean_user = username.strip()
        if not clean_user:
            raise ValueError("管理员用户名不能为空")
        pwd_err = validate_new_password(password)
        if pwd_err:
            raise ValueError(pwd_err)
        pwd_hash, now_s, must_flag = hash_password(password), self.now_str(), (1 if must_change else 0)
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT id FROM admin_user WHERE username = ?", (clean_user,)).fetchone()
            if existing:
                user_id = int(existing["id"])
                self.conn.execute("UPDATE admin_user SET pwd_hash = ?, must_change = ? WHERE id = ?", (pwd_hash, must_flag, user_id))
                self.conn.execute("DELETE FROM admin_session WHERE user_id = ?", (user_id,))
                action = "admin.reset_password_cli"
            else:
                cur = self.conn.execute("INSERT INTO admin_user(username, pwd_hash, must_change, created_at) VALUES (?, ?, ?, ?)", (clean_user, pwd_hash, must_flag, now_s))
                user_id, action = int(cur.lastrowid), "admin.create_cli"
            self._insert_audit_unlocked(actor, action, "admin_user", clean_user, {"must_change": bool(must_change)}, now_s)
            return user_id

    def get_admin_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM admin_user WHERE username = ?", (username.strip(),)).fetchone()
            return dict(row) if row else None

    def get_admin_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def verify_admin_credentials(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        user = self.get_admin_by_username(username)
        return user if (user and verify_password(password, user["pwd_hash"])) else None

    def change_admin_password(self, user_id: int, new_password: str) -> None:
        pwd_err = validate_new_password(new_password)
        if pwd_err:
            raise ValueError(pwd_err)
        with self.lock, self.conn:
            self.conn.execute("UPDATE admin_user SET pwd_hash = ?, must_change = 0 WHERE id = ?", (hash_password(new_password), user_id))

    def create_admin_session(self, user_id: int, ttl_hours: int = SESSION_TTL_HOURS) -> Dict[str, str]:
        sid, csrf = secrets.token_hex(32), secrets.token_hex(24)
        now_dt = self.now()
        created_at, expires_at = format_dt(now_dt), format_dt(now_dt + timedelta(hours=ttl_hours))
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO admin_session(sid, user_id, csrf, created_at, last_seen, expires_at) VALUES (?, ?, ?, ?, ?, ?)", (sid, user_id, csrf, created_at, created_at, expires_at))
        return {"sid": sid, "csrf": csrf, "created_at": created_at, "expires_at": expires_at}

    def get_admin_session(self, sid: str) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT s.sid, s.user_id, s.csrf, s.created_at, s.last_seen, s.expires_at, u.username, u.must_change "
                "FROM admin_session s JOIN admin_user u ON u.id = s.user_id WHERE s.sid = ?",
                (sid,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] < now_s:
                self.conn.execute("DELETE FROM admin_session WHERE sid = ?", (sid,))
                return None
            self.conn.execute("UPDATE admin_session SET last_seen = ? WHERE sid = ?", (now_s, sid))
            return {
                "sid": row["sid"], "user_id": int(row["user_id"]), "csrf": row["csrf"],
                "created_at": row["created_at"], "last_seen": now_s, "expires_at": row["expires_at"],
                "username": row["username"], "must_change": bool(row["must_change"]),
            }

    def delete_admin_session(self, sid: str) -> None:
        if sid:
            with self.lock, self.conn:
                self.conn.execute("DELETE FROM admin_session WHERE sid = ?", (sid,))

    def _insert_audit_unlocked(self, username: str, action: str, target_type: Optional[str] = None, target_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None, created_at: Optional[str] = None) -> int:
        detail_str = json.dumps(detail, ensure_ascii=False) if detail is not None else None
        cur = self.conn.execute("INSERT INTO admin_audit(username, action, target_type, target_id, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)", (username, action, target_type, target_id, detail_str, created_at or self.now_str()))
        return int(cur.lastrowid)

    def write_audit(self, username: str, action: str, target_type: Optional[str] = None, target_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> int:
        with self.lock, self.conn:
            return self._insert_audit_unlocked(username, action, target_type, target_id, detail)

    def list_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM admin_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def is_phone_in_members(self, phone: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", ((phone or "").strip(),)).fetchone() is not None

    def upsert_delivery_target(self, phone: str, external_id: str, platform: str = "zdxlz") -> Dict[str, Any]:
        clean_phone, clean_ext = (phone or "").strip(), (external_id or "").strip()
        if not is_valid_phone(clean_phone):
            raise BizError(400, "PHONE_INVALID", "手机号格式不正确")
        if not clean_ext:
            raise BizError(400, "VALIDATION_FAILED", "external_id 不能为空")
        now_s = self.now_str()
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT activated_at FROM delivery_target WHERE platform = ? AND phone = ?", (platform, clean_phone)).fetchone()
            if existing:
                activated_at = existing["activated_at"]
                self.conn.execute(
                    "UPDATE delivery_target SET external_id = ?, usable = 1, last_event_at = ? WHERE platform = ? AND phone = ?",
                    (clean_ext, now_s, platform, clean_phone),
                )
            else:
                activated_at = now_s
                self.conn.execute(
                    "INSERT INTO delivery_target(platform, phone, external_id, usable, activated_at, last_event_at) VALUES (?, ?, ?, 1, ?, ?)",
                    (platform, clean_phone, clean_ext, activated_at, now_s),
                )
            return {"platform": platform, "phone": clean_phone, "external_id": clean_ext, "usable": 1, "activated_at": activated_at, "last_event_at": now_s}

    def bind_member_with_guardrails(self, phone: str, external_id: str, path: str, platform: str = "zdxlz") -> Dict[str, Any]:
        """v1.1 激活绑定（路径 A/B/C）与三条护栏：在册校验 + 防覆盖校验 + 脱敏回执与审计。
        若原记录因 500 无会话被置为 usable=0，重新绑定成功时恢复 usable=1 并保留原 activated_at。
        """
        clean_phone, clean_ext, path_code = (phone or "").strip(), (external_id or "").strip(), (path or "B").strip().upper()
        if not is_valid_phone(clean_phone):
            raise BizError(400, "PHONE_INVALID", "手机号格式不正确")
        if not clean_ext:
            raise BizError(400, "VALIDATION_FAILED", "缺少用户标识 external_id")
        now_s = self.now_str()
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", (clean_phone,)).fetchone():
                raise BizError(400, "VALIDATION_FAILED", "该手机号不在任务名单中，请联系管理员核实")
            existing = self.conn.execute("SELECT * FROM delivery_target WHERE platform = ? AND phone = ?", (platform, clean_phone)).fetchone()
            if existing:
                is_usable = int(existing["usable"] if "usable" in existing.keys() and existing["usable"] is not None else 1) == 1
                if is_usable:
                    if path_code == "A" and existing["external_id"] == clean_ext:
                        self.conn.execute("UPDATE delivery_target SET last_event_at = ? WHERE platform = ? AND phone = ?", (now_s, platform, clean_phone))
                        masked = mask_phone(clean_phone)
                        return {"ok": True, "already_same": True, "phone": clean_phone, "masked_phone": masked, "external_id": clean_ext, "receipt": f"绑定成功：当前账号已关联手机号 {masked}。如非本人操作，请联系管理员申诉。"}
                    raise BizError(409, "DUPLICATE", "该手机号已被绑定，禁止覆盖；如非本人操作请联系管理员申诉")

                # usable == 0（曾因 500 无会话标记不可用）：恢复 usable=1，保留原 activated_at
                self.conn.execute(
                    "UPDATE delivery_target SET external_id = ?, usable = 1, last_event_at = ? WHERE platform = ? AND phone = ?",
                    (clean_ext, now_s, platform, clean_phone),
                )
            else:
                self.conn.execute(
                    "INSERT INTO delivery_target(platform, phone, external_id, usable, activated_at, last_event_at) VALUES (?, ?, ?, 1, ?, ?)",
                    (platform, clean_phone, clean_ext, now_s, now_s),
                )

            masked = mask_phone(clean_phone)
            receipt = f"绑定成功：当前账号已关联手机号 {masked}。如非本人操作，请立即联系管理员申诉处理。"
            self._insert_audit_unlocked("system", f"target.bind_{path_code.lower()}", "delivery_target", clean_phone, {"masked_phone": masked, "external_id": clean_ext, "path": path_code}, now_s)
            return {"ok": True, "already_same": False, "phone": clean_phone, "masked_phone": masked, "external_id": clean_ext, "receipt": receipt}

    def touch_delivery_target_by_external_id(self, external_id: str, platform: str = "zdxlz") -> Optional[Dict[str, Any]]:
        clean_ext = (external_id or "").strip()
        if not clean_ext:
            return None
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM delivery_target WHERE platform = ? AND external_id = ?", (platform, clean_ext)).fetchone()
            if not row:
                return None
            self.conn.execute("UPDATE delivery_target SET usable = 1, last_event_at = ? WHERE platform = ? AND phone = ?", (now_s, platform, row["phone"]))
            return {"platform": row["platform"], "phone": row["phone"], "external_id": row["external_id"], "usable": 1, "activated_at": row["activated_at"], "last_event_at": now_s}

    def get_delivery_target(self, phone: str, platform: str = "zdxlz", include_unusable: bool = False) -> Optional[Dict[str, Any]]:
        with self.lock:
            sql = "SELECT * FROM delivery_target WHERE platform = ? AND phone = ?" + ("" if include_unusable else " AND usable = 1")
            row = self.conn.execute(sql, (platform, (phone or "").strip())).fetchone()
            return dict(row) if row else None

    def get_delivery_target_by_external_id(self, external_id: str, platform: str = "zdxlz", include_unusable: bool = False) -> Optional[Dict[str, Any]]:
        with self.lock:
            sql = "SELECT * FROM delivery_target WHERE platform = ? AND external_id = ?" + ("" if include_unusable else " AND usable = 1")
            row = self.conn.execute(sql, (platform, (external_id or "").strip())).fetchone()
            return dict(row) if row else None

    def mark_delivery_target_unusable(self, phone: str, platform: str = "zdxlz") -> None:
        """发送返回 500 无会话时标记 usable=0，保留 external_id 与 activated_at 供排障。"""
        with self.lock, self.conn:
            self.conn.execute("UPDATE delivery_target SET usable = 0 WHERE platform = ? AND phone = ?", (platform, (phone or "").strip()))

    def delete_delivery_target(self, phone: str, platform: str = "zdxlz") -> None:
        """兼容性别名：将绑定标记为不可用 (usable=0)，不物理抹除 external_id 与 activated_at。"""
        self.mark_delivery_target_unusable(phone, platform=platform)

    def _generate_unique_id_unlocked(self, table: str, id_col: str, prefix: str) -> str:
        while True:
            candidate = f"{prefix}{secrets.token_hex(4)}"
            if not self.conn.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ?", (candidate,)).fetchone():
                return candidate

    def list_roles(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT r.role_id, r.name, r.note, r.created_at, COUNT(m.id) AS member_count "
                "FROM role r LEFT JOIN member m ON m.role_id = r.role_id GROUP BY r.role_id ORDER BY r.created_at ASC, r.role_id ASC"
            ).fetchall()
            return [{"role_id": r["role_id"], "name": r["name"], "note": r["note"], "created_at": r["created_at"], "member_count": int(r["member_count"] or 0)} for r in rows]

    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT r.*, (SELECT COUNT(1) FROM member m WHERE m.role_id = r.role_id) AS member_count FROM role r WHERE r.role_id = ?", ((role_id or "").strip(),)).fetchone()
            return {"role_id": row["role_id"], "name": row["name"], "note": row["note"], "created_at": row["created_at"], "member_count": int(row["member_count"] or 0)} if row else None

    def create_role(self, name: str, note: Optional[str] = None, actor: str = "system") -> Dict[str, Any]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise BizError(400, "VALIDATION_FAILED", "角色名称不能为空")
        clean_note, now_s = (str(note).strip() if note is not None and str(note).strip() else None), self.now_str()
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM role WHERE name = ?", (clean_name,)).fetchone():
                raise BizError(409, "ROLE_NAME_TAKEN", "角色名称已存在")
            role_id = self._generate_unique_id_unlocked("role", "role_id", "r_")
            self.conn.execute("INSERT INTO role(role_id, name, note, created_at) VALUES (?, ?, ?, ?)", (role_id, clean_name, clean_note, now_s))
            self._insert_audit_unlocked(actor, "role.create", "role", role_id, {"name": clean_name, "note": clean_note}, now_s)
            return {"role_id": role_id, "name": clean_name, "note": clean_note, "created_at": now_s, "member_count": 0}

    def update_role(self, role_id: str, name: str, note: Optional[str] = None, actor: str = "system") -> Dict[str, Any]:
        clean_id, clean_name = (role_id or "").strip(), (name or "").strip()
        if not clean_name:
            raise BizError(400, "VALIDATION_FAILED", "角色名称不能为空")
        clean_note, now_s = (str(note).strip() if note is not None and str(note).strip() else None), self.now_str()
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT * FROM role WHERE role_id = ?", (clean_id,)).fetchone()
            if not existing:
                raise BizError(404, "NOT_FOUND", "角色不存在")
            if self.conn.execute("SELECT 1 FROM role WHERE name = ? AND role_id != ?", (clean_name, clean_id)).fetchone():
                raise BizError(409, "ROLE_NAME_TAKEN", "角色名称已存在")
            self.conn.execute("UPDATE role SET name = ?, note = ? WHERE role_id = ?", (clean_name, clean_note, clean_id))
            cnt = int(self.conn.execute("SELECT COUNT(1) AS cnt FROM member WHERE role_id = ?", (clean_id,)).fetchone()["cnt"] or 0)
            self._insert_audit_unlocked(actor, "role.update", "role", clean_id, {"name": clean_name, "note": clean_note}, now_s)
            return {"role_id": clean_id, "name": clean_name, "note": clean_note, "created_at": existing["created_at"], "member_count": cnt}

    def delete_role(self, role_id: str, actor: str = "system") -> None:
        clean_id, now_s = (role_id or "").strip(), self.now_str()
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT * FROM role WHERE role_id = ?", (clean_id,)).fetchone()
            if not existing:
                raise BizError(404, "NOT_FOUND", "角色不存在")
            if int(self.conn.execute("SELECT COUNT(1) AS cnt FROM member WHERE role_id = ?", (clean_id,)).fetchone()["cnt"] or 0) > 0:
                raise BizError(409, "ROLE_IN_USE", "该角色下仍有成员，无法删除")
            for trow in self.conn.execute("SELECT role_ids FROM task_template WHERE deleted = 0").fetchall():
                try:
                    rids = json.loads(trow["role_ids"] or "[]")
                except Exception:
                    rids = []
                if clean_id in rids:
                    raise BizError(409, "ROLE_IN_USE", "该角色已被任务模板引用，无法删除")
            self.conn.execute("DELETE FROM role WHERE role_id = ?", (clean_id,))
            self._insert_audit_unlocked(actor, "role.delete", "role", clean_id, {"name": existing["name"]}, now_s)

    def list_role_members(self, role_id: str, q: Optional[str] = None, page: int = 1, size: int = 20) -> Dict[str, Any]:
        clean_role_id = (role_id or "").strip()
        p, s = _clamp_page_size(page, size)
        offset, kw = (p - 1) * s, (q or "").strip()
        with self.lock:
            if not self.conn.execute("SELECT 1 FROM role WHERE role_id = ?", (clean_role_id,)).fetchone():
                raise BizError(404, "NOT_FOUND", "角色不存在")
            where_sql, params = "WHERE m.role_id = ?", [clean_role_id]
            if kw:
                where_sql += " AND (m.name LIKE ? OR m.phone LIKE ? OR COALESCE(m.dept, '') LIKE ?)"
                params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
            total = int(self.conn.execute(f"SELECT COUNT(1) AS cnt FROM member m {where_sql}", tuple(params)).fetchone()["cnt"] or 0)
            rows = self.conn.execute(
                f"SELECT m.*, dt.external_id FROM member m "
                f"LEFT JOIN delivery_target dt ON dt.platform = 'zdxlz' AND dt.phone = m.phone AND dt.usable = 1 "
                f"{where_sql} ORDER BY m.id ASC LIMIT ? OFFSET ?",
                tuple(params + [s, offset]),
            ).fetchall()
            items = [_fmt_member(r["id"], r["role_id"], r["phone"], r["name"], r["dept"], r["enabled"], r["source"], r["created_at"], r["external_id"]) for r in rows]
            return {"total": total, "items": items}

    def add_role_member(self, role_id: str, phone: str, name: str, dept: Optional[str] = None, source: str = "manual", actor: str = "system") -> Dict[str, Any]:
        clean_role_id, clean_phone, clean_name = (role_id or "").strip(), (phone or "").strip(), (name or "").strip()
        clean_dept = str(dept).strip() if dept is not None and str(dept).strip() else None
        if not is_valid_phone(clean_phone):
            raise BizError(400, "PHONE_INVALID", "手机号格式不正确")
        if not clean_name:
            raise BizError(400, "VALIDATION_FAILED", "姓名不能为空")
        now_s = self.now_str()
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM role WHERE role_id = ?", (clean_role_id,)).fetchone():
                raise BizError(404, "NOT_FOUND", "角色不存在")
            if self.conn.execute("SELECT 1 FROM member WHERE role_id = ? AND phone = ?", (clean_role_id, clean_phone)).fetchone():
                raise BizError(409, "DUPLICATE", "该角色下手机号已存在")
            cur = self.conn.execute("INSERT INTO member(role_id, phone, name, dept, enabled, source, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)", (clean_role_id, clean_phone, clean_name, clean_dept, source, now_s))
            dt_row = self.conn.execute("SELECT external_id FROM delivery_target WHERE platform = 'zdxlz' AND phone = ? AND usable = 1", (clean_phone,)).fetchone()
            self._insert_audit_unlocked(actor, "member.create", "member", f"{clean_role_id}:{clean_phone}", {"name": clean_name, "dept": clean_dept, "source": source}, now_s)
            return _fmt_member(int(cur.lastrowid), clean_role_id, clean_phone, clean_name, clean_dept, True, source, now_s, dt_row and dt_row["external_id"])

    def update_role_member(self, role_id: str, phone: str, name: Optional[str] = None, dept: Optional[str] = None, enabled: Optional[bool] = None, actor: str = "system") -> Dict[str, Any]:
        clean_role_id, clean_phone, now_s = (role_id or "").strip(), (phone or "").strip(), self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM member WHERE role_id = ? AND phone = ?", (clean_role_id, clean_phone)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "成员不存在")
            new_name = str(name).strip() if name is not None else row["name"]
            if not new_name:
                raise BizError(400, "VALIDATION_FAILED", "姓名不能为空")
            new_dept = row["dept"] if dept is None else (str(dept).strip() or None)
            new_enabled = (1 if bool(enabled) else 0) if enabled is not None else int(row["enabled"])
            self.conn.execute("UPDATE member SET name = ?, dept = ?, enabled = ? WHERE id = ?", (new_name, new_dept, new_enabled, int(row["id"])))
            dt_row = self.conn.execute("SELECT external_id FROM delivery_target WHERE platform = 'zdxlz' AND phone = ? AND usable = 1", (clean_phone,)).fetchone()
            self._insert_audit_unlocked(actor, "member.update", "member", f"{clean_role_id}:{clean_phone}", {"name": new_name, "dept": new_dept, "enabled": bool(new_enabled)}, now_s)
            return _fmt_member(int(row["id"]), clean_role_id, clean_phone, new_name, new_dept, new_enabled, row["source"], row["created_at"], dt_row and dt_row["external_id"])

    def delete_role_member(self, role_id: str, phone: str, actor: str = "system") -> None:
        clean_role_id, clean_phone, now_s = (role_id or "").strip(), (phone or "").strip(), self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM member WHERE role_id = ? AND phone = ?", (clean_role_id, clean_phone)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "成员不存在")
            self.conn.execute("DELETE FROM member WHERE id = ?", (int(row["id"]),))
            self._insert_audit_unlocked(actor, "member.delete", "member", f"{clean_role_id}:{clean_phone}", {"name": row["name"], "dept": row["dept"]}, now_s)

    def import_role_members(self, role_id: str, csv_rows: List[Dict[str, Any]], dry_run: bool = True, actor: str = "system") -> Dict[str, Any]:
        """R43 + §9.3：批量导入成员（行解析加 try 保护，非法行进 invalid 列表而不抛 500）。"""
        clean_role_id = (role_id or "").strip()
        if not isinstance(csv_rows, list):
            raise BizError(400, "VALIDATION_FAILED", "csv_rows 必须是数组")
        now_s = self.now_str()
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM role WHERE role_id = ?", (clean_role_id,)).fetchone():
                raise BizError(404, "NOT_FOUND", "角色不存在")
            existing_phones = {r["phone"] for r in self.conn.execute("SELECT phone FROM member WHERE role_id = ?", (clean_role_id,)).fetchall()}
            will_add, already_exists = 0, 0
            invalid: List[Dict[str, Any]] = []
            to_insert: List[Tuple[str, str, Optional[str]]] = []
            batch_seen_phones: set[str] = set()

            for idx, raw_row in enumerate(csv_rows, start=1):
                if not isinstance(raw_row, dict):
                    invalid.append({"row": idx, "phone": "", "reason": "行数据格式错误"})
                    continue
                try:
                    raw_row_no = raw_row.get("row")
                    row_num = int(raw_row_no) if raw_row_no is not None and str(raw_row_no).strip() != "" else idx
                    phone = str(raw_row.get("phone") or "").strip()
                    name = str(raw_row.get("name") or "").strip()
                    dept_raw = raw_row.get("dept")
                    dept = str(dept_raw).strip() if dept_raw is not None and str(dept_raw).strip() else None
                except Exception:
                    fallback_phone = ""
                    try:
                        fallback_phone = str(raw_row.get("phone") or "").strip()
                    except Exception:
                        pass
                    invalid.append({"row": idx, "phone": fallback_phone, "reason": "行数据格式错误"})
                    continue

                if not is_valid_phone(phone):
                    invalid.append({"row": row_num, "phone": phone, "reason": "手机号格式不正确"})
                    continue
                if not name:
                    invalid.append({"row": row_num, "phone": phone, "reason": "姓名不能为空"})
                    continue
                if phone in existing_phones or phone in batch_seen_phones:
                    already_exists += 1
                    continue

                batch_seen_phones.add(phone)
                will_add += 1
                to_insert.append((phone, name, dept))

            if not dry_run:
                for phone, name, dept in to_insert:
                    self.conn.execute("INSERT INTO member(role_id, phone, name, dept, enabled, source, created_at) VALUES (?, ?, ?, ?, 1, 'import', ?)", (clean_role_id, phone, name, dept, now_s))
                self._insert_audit_unlocked(actor, "member.import", "role", clean_role_id, {"will_add": will_add, "already_exists": already_exists, "invalid_count": len(invalid)}, now_s)

            return {"ok": True, "dry_run": bool(dry_run), "will_add": will_add, "already_exists": already_exists, "invalid": invalid}

    def _deserialize_template_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        try:
            role_ids = json.loads(d.get("role_ids") or "[]")
        except Exception:
            role_ids = []
        schema_raw, notice_raw = d.get("schema_json"), d.get("notice_times")
        schema_json = json.loads(schema_raw) if schema_raw else None
        notice_times = json.loads(notice_raw) if notice_raw else None
        try:
            data_keys = json.loads(d.get("data_keys") or "[]")
        except Exception:
            data_keys = []
        try:
            spec_json = json.loads(d.get("spec_json") or "null")
        except Exception:
            spec_json = None
        return {
            "template_id": d["template_id"], "name": d["name"], "kind": d["kind"], "role_ids": role_ids,
            "schema_json": schema_json, "allow_note": bool(d.get("allow_note", 0)), "text_tpl": d["text_tpl"],
            "remind_at": d.get("remind_at"), "notice_times": notice_times, "deadline": d.get("deadline"),
            "period": d["period"], "anchor_weekday": d.get("anchor_weekday"), "anchor_dom": d.get("anchor_dom"),
            "deadline_offset": int(d.get("deadline_offset") or 0), "need_review": bool(d.get("need_review", 0)),
            "max_file_mb": d.get("max_file_mb"), "enabled": bool(d.get("enabled", 1)), "deleted": bool(d.get("deleted", 0)),
            "attach_image": bool(d.get("attach_image", 0)), "attach_image_path": d.get("attach_image_path"),
            "data_keys": data_keys, "spec_json": spec_json,
            "created_by": d["created_by"], "created_at": d["created_at"], "updated_at": d["updated_at"],
        }

    def _validate_and_normalize_template_unlocked(self, payload: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if existing is not None:
            if "period" in payload and str(payload.get("period") or "").strip() not in ("", existing["period"]):
                raise BizError(400, "PERIOD_IMMUTABLE", "任务周期创建后不可修改")
            if "kind" in payload and str(payload.get("kind") or "").strip() not in ("", existing["kind"]):
                raise BizError(400, "KIND_IMMUTABLE", "模板类型创建后不可修改")

        merged: Dict[str, Any] = dict(existing) if existing else {}
        merged.update(payload)

        name = str(merged.get("name") or "").strip()
        if not name:
            raise BizError(400, "VALIDATION_FAILED", "模板名称 name 不能为空")
        kind = str(merged.get("kind") or "").strip()
        if kind not in VALID_TEMPLATE_KINDS:
            raise BizError(400, "VALIDATION_FAILED", "模板类型 kind 必须是 form/image/file/notice")

        role_ids_raw = merged.get("role_ids")
        if not isinstance(role_ids_raw, list) or not role_ids_raw:
            raise BizError(400, "VALIDATION_FAILED", "必须至少选择 1 个适用角色 (role_ids)")
        role_ids: List[str] = []
        for rid in role_ids_raw:
            s_rid = str(rid or "").strip()
            if s_rid and s_rid not in role_ids:
                role_ids.append(s_rid)
        if not role_ids:
            raise BizError(400, "VALIDATION_FAILED", "必须至少选择 1 个适用角色 (role_ids)")

        placeholders = ",".join("?" for _ in role_ids)
        found_roles = {r["role_id"] for r in self.conn.execute(f"SELECT role_id FROM role WHERE role_id IN ({placeholders})", tuple(role_ids)).fetchall()}
        missing_roles = [r for r in role_ids if r not in found_roles]
        if missing_roles:
            raise BizError(400, "VALIDATION_FAILED", f"关联角色不存在: {', '.join(missing_roles)}")

        text_tpl = str(merged.get("text_tpl") or "").strip()
        if not text_tpl:
            raise BizError(400, "VALIDATION_FAILED", "提醒文案 text_tpl 不能为空")

        if kind == "notice":
            if str(merged.get("period") or "day").strip() != "day":
                raise BizError(400, "VALIDATION_FAILED", "通知类模板 period 固定为 day")
            period = "day"
        else:
            period = str(merged.get("period") or "").strip()
            if period not in VALID_PERIODS:
                raise BizError(400, "VALIDATION_FAILED", "周期 period 必须是 day/week/month")

        anchor_weekday, anchor_dom, deadline_offset = None, None, 0
        if period == "week":
            anchor_weekday = 1 if merged.get("anchor_weekday") is None else int(merged.get("anchor_weekday"))
            if not (1 <= anchor_weekday <= 7):
                raise BizError(400, "VALIDATION_FAILED", "anchor_weekday 必须在 1~7 之间 (1=周一)")
            deadline_offset = 0 if merged.get("deadline_offset") is None else int(merged.get("deadline_offset"))
            if not (0 <= deadline_offset <= 6):
                raise BizError(400, "VALIDATION_FAILED", "deadline_offset 必须在 0~6 天之间")
        elif period == "month":
            anchor_dom = 1 if merged.get("anchor_dom") is None else int(merged.get("anchor_dom"))
            if not (1 <= anchor_dom <= 31):
                raise BizError(400, "VALIDATION_FAILED", "anchor_dom 必须在 1~31 之间")
            deadline_offset = 0 if merged.get("deadline_offset") is None else int(merged.get("deadline_offset"))
            if not (0 <= deadline_offset <= 6):
                raise BizError(400, "VALIDATION_FAILED", "deadline_offset 必须在 0~6 天之间")

        schema_json_str, remind_at, deadline, notice_times_str, max_file_mb = None, None, None, None, None
        allow_note, need_review = False, False

        if kind == "notice":
            nt_raw = merged.get("notice_times")
            if isinstance(nt_raw, str):
                try:
                    nt_raw = json.loads(nt_raw)
                except Exception:
                    raise BizError(400, "VALIDATION_FAILED", "notice_times 必须是 HH:MM 数组")
            if not isinstance(nt_raw, list) or not (1 <= len(nt_raw) <= 3):
                raise BizError(400, "VALIDATION_FAILED", "通知类模板 notice_times 必须包含 1~3 个时刻")
            clean_times: List[str] = []
            for t in nt_raw:
                ts = str(t or "").strip()
                if not is_valid_hhmm(ts):
                    raise BizError(400, "VALIDATION_FAILED", f"notice_times 时刻格式非法: {ts}")
                if ts not in clean_times:
                    clean_times.append(ts)
            notice_times_str = json.dumps(clean_times, ensure_ascii=False)
            # 通知类模板的调度只看 notice_times，但「执行时间」列 / 编辑弹窗读的是 remind_at。
            # 不写这里会让每次保存都把执行时间清空，列表显示空白、下次编辑还得重填。
            remind_at = clean_times[0]
        else:
            remind_at, deadline = str(merged.get("remind_at") or "").strip(), str(merged.get("deadline") or "").strip()
            if not is_valid_hhmm(remind_at) or not is_valid_hhmm(deadline):
                raise BizError(400, "VALIDATION_FAILED", "remind_at 与 deadline 必须为合法的 HH:MM 格式")
            if remind_at >= deadline:
                raise BizError(400, "VALIDATION_FAILED", "提醒时间 remind_at 必须早于截止时间 deadline")
            need_review, allow_note = bool(merged.get("need_review", False)), bool(merged.get("allow_note", False))
            if kind == "form":
                schema_json_str = json.dumps(_validate_form_schema(merged.get("schema_json")), ensure_ascii=False)
            elif kind in ("image", "file"):
                m_raw = merged.get("max_file_mb")
                if m_raw is not None and str(m_raw).strip() != "":
                    try:
                        max_file_mb = int(m_raw)
                    except Exception:
                        raise BizError(400, "VALIDATION_FAILED", "max_file_mb 必须是正整数")
                    upper = 9 if kind == "image" else 20
                    if not (1 <= max_file_mb <= upper):
                        raise BizError(400, "VALIDATION_FAILED", f"{kind} 类型 max_file_mb 必须在 1~{upper} 之间")

        # 2026-10-27 春促数据看板：attach_image 开关 + data_keys 变量清单校验
        attach_image = bool(merged.get("attach_image", False))
        data_keys_raw = merged.get("data_keys")
        if isinstance(data_keys_raw, str):
            try:
                data_keys_raw = json.loads(data_keys_raw) if data_keys_raw.strip() else []
            except Exception:
                raise BizError(400, "VALIDATION_FAILED", "data_keys 必须是 [{key,label,unit}] 数组")
        if data_keys_raw is None:
            data_keys_raw = []
        if not isinstance(data_keys_raw, list):
            raise BizError(400, "VALIDATION_FAILED", "data_keys 必须是数组")
        seen_keys = set()
        data_keys_norm = []
        for item in data_keys_raw:
            if not isinstance(item, dict):
                raise BizError(400, "VALIDATION_FAILED", "data_keys 每项必须是 {key,label,unit}")
            k = str(item.get("key") or "").strip()
            label = str(item.get("label") or "").strip()
            if not k or not label:
                raise BizError(400, "VALIDATION_FAILED", "data_keys 每项的 key 与 label 不能为空")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", k):
                raise BizError(400, "VALIDATION_FAILED", f"data_keys.key 仅支持字母数字下划线: {k}")
            if k in seen_keys:
                raise BizError(400, "VALIDATION_FAILED", f"data_keys.key 重复: {k}")
            seen_keys.add(k)
            data_keys_norm.append({"key": k, "label": label, "unit": str(item.get("unit") or "").strip()})
        # 2026-10-28 通知类「数据图片提醒」：管理员上传的图片/文件路径（相对 data 目录）
        attach_image_path = str(merged.get("attach_image_path") or "").strip() or None
        if attach_image_path:
            attach_image = True  # 有图就等价于开启附图，避免前端只传了路径没传开关
        if attach_image and not data_keys_norm and not attach_image_path:
            raise BizError(
                400, "VALIDATION_FAILED",
                "勾选了「数据图片提醒」就必须先上传要推送的图片/文件，否则到点无图可发",
            )
        data_keys_str = json.dumps(data_keys_norm, ensure_ascii=False)

        return {
            "name": name, "kind": kind, "role_ids_json": json.dumps(role_ids, ensure_ascii=False),
            "schema_json": schema_json_str, "allow_note": 1 if allow_note else 0, "text_tpl": text_tpl,
            "remind_at": remind_at, "notice_times": notice_times_str, "deadline": deadline, "period": period,
            "anchor_weekday": anchor_weekday, "anchor_dom": anchor_dom, "deadline_offset": deadline_offset,
            "need_review": 1 if need_review else 0, "max_file_mb": max_file_mb, "enabled": 1 if bool(merged.get("enabled", True)) else 0,
            "attach_image": 1 if attach_image else 0, "attach_image_path": attach_image_path,
            "data_keys": data_keys_str,
            "spec_json": json.dumps(merged.get("spec_json"), ensure_ascii=False) if merged.get("spec_json") not in (None, "") else None,
        }

    def create_template(self, payload: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
        now_s = self.now_str()
        with self.lock, self.conn:
            norm = self._validate_and_normalize_template_unlocked(payload, existing=None)
            template_id = self._generate_unique_id_unlocked("task_template", "template_id", "t_")
            self.conn.execute(
                "INSERT INTO task_template(template_id, name, kind, role_ids, schema_json, allow_note, text_tpl, remind_at, notice_times, deadline, period, anchor_weekday, anchor_dom, deadline_offset, need_review, max_file_mb, attach_image, attach_image_path, data_keys, spec_json, enabled, deleted, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    template_id, norm["name"], norm["kind"], norm["role_ids_json"], norm["schema_json"],
                    norm["allow_note"], norm["text_tpl"], norm["remind_at"], norm["notice_times"], norm["deadline"],
                    norm["period"], norm["anchor_weekday"], norm["anchor_dom"], norm["deadline_offset"],
                    norm["need_review"], norm["max_file_mb"], norm["attach_image"], norm["attach_image_path"],
                    norm["data_keys"], norm["spec_json"],
                    norm["enabled"], actor, now_s, now_s,
                ),
            )
            self._insert_audit_unlocked(actor, "template.create", "task_template", template_id, {"name": norm["name"], "kind": norm["kind"], "period": norm["period"]}, now_s)
            return self._deserialize_template_row(self.conn.execute("SELECT * FROM task_template WHERE template_id = ?", (template_id,)).fetchone())

    def get_template(self, template_id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        with self.lock:
            sql = "SELECT * FROM task_template WHERE template_id = ?" + ("" if include_deleted else " AND deleted = 0")
            row = self.conn.execute(sql, ((template_id or "").strip(),)).fetchone()
            return self._deserialize_template_row(row) if row else None

    def list_templates(self, q: Optional[str] = None, kind: Optional[str] = None, enabled: Optional[bool] = None, page: int = 1, size: int = 20) -> Dict[str, Any]:
        p, s = _clamp_page_size(page, size)
        offset = (p - 1) * s
        where_clauses, params = ["deleted = 0"], []
        if q and q.strip():
            where_clauses.append("name LIKE ?")
            params.append(f"%{q.strip()}%")
        if kind and kind.strip():
            where_clauses.append("kind = ?")
            params.append(kind.strip())
        if enabled is not None:
            where_clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        where_sql = "WHERE " + " AND ".join(where_clauses)
        with self.lock:
            total = int(self.conn.execute(f"SELECT COUNT(1) AS cnt FROM task_template {where_sql}", tuple(params)).fetchone()["cnt"] or 0)
            rows = self.conn.execute(f"SELECT * FROM task_template {where_sql} ORDER BY created_at DESC, template_id DESC LIMIT ? OFFSET ?", tuple(params + [s, offset])).fetchall()
            return {"total": total, "items": [self._deserialize_template_row(r) for r in rows]}

    def update_template(self, template_id: str, payload: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
        clean_id, now_s = (template_id or "").strip(), self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM task_template WHERE template_id = ? AND deleted = 0", (clean_id,)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "任务模板不存在")
            norm = self._validate_and_normalize_template_unlocked(payload, existing=self._deserialize_template_row(row))
            self.conn.execute(
                "UPDATE task_template SET name = ?, kind = ?, role_ids = ?, schema_json = ?, allow_note = ?, text_tpl = ?, "
                "remind_at = ?, notice_times = ?, deadline = ?, anchor_weekday = ?, anchor_dom = ?, deadline_offset = ?, "
                "need_review = ?, max_file_mb = ?, attach_image = ?, attach_image_path = ?, data_keys = ?, spec_json = ?, enabled = ?, updated_at = ? WHERE template_id = ?",
                (
                    norm["name"], norm["kind"], norm["role_ids_json"], norm["schema_json"], norm["allow_note"],
                    norm["text_tpl"], norm["remind_at"], norm["notice_times"], norm["deadline"], norm["anchor_weekday"],
                    norm["anchor_dom"], norm["deadline_offset"], norm["need_review"], norm["max_file_mb"],
                    norm["attach_image"], norm["attach_image_path"], norm["data_keys"], norm["spec_json"],
                    norm["enabled"], now_s, clean_id,
                ),
            )
            self._insert_audit_unlocked(actor, "template.update", "task_template", clean_id, {"name": norm["name"], "enabled": bool(norm["enabled"])}, now_s)
            return self._deserialize_template_row(self.conn.execute("SELECT * FROM task_template WHERE template_id = ?", (clean_id,)).fetchone())

    def delete_template(self, template_id: str, actor: str = "system") -> None:
        clean_id, now_s = (template_id or "").strip(), self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT template_id, name FROM task_template WHERE template_id = ? AND deleted = 0", (clean_id,)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "任务模板不存在")
            self.conn.execute("UPDATE task_template SET deleted = 1, enabled = 0, updated_at = ? WHERE template_id = ?", (now_s, clean_id))
            self._insert_audit_unlocked(actor, "template.delete", "task_template", clean_id, {"name": row["name"]}, now_s)

    def get_dedup_enabled_members_for_roles(self, role_ids: List[str]) -> List[Dict[str, Any]]:
        clean_ids = [str(r).strip() for r in (role_ids or []) if str(r).strip()]
        if not clean_ids:
            return []
        placeholders = ",".join("?" for _ in clean_ids)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT m.phone, m.name, m.dept, dt.external_id FROM member m "
                f"LEFT JOIN delivery_target dt ON dt.platform = 'zdxlz' AND dt.phone = m.phone AND dt.usable = 1 "
                f"WHERE m.role_id IN ({placeholders}) AND m.enabled = 1 ORDER BY m.id ASC",
                tuple(clean_ids),
            ).fetchall()
            seen: set[str] = set()
            deduped: List[Dict[str, Any]] = []
            for r in rows:
                ph = r["phone"]
                if ph not in seen:
                    seen.add(ph)
                    deduped.append({"phone": ph, "name": r["name"], "dept": r["dept"], "external_id": r["external_id"], "activated": bool(r["external_id"])})
            return deduped

    def preview_template_targets(self, template_id: str) -> Dict[str, int]:
        tpl = self.get_template(template_id, include_deleted=False)
        if not tpl:
            raise BizError(404, "NOT_FOUND", "任务模板不存在")
        members = self.get_dedup_enabled_members_for_roles(tpl["role_ids"])
        member_count, activated_count = len(members), sum(1 for m in members if m["activated"])
        return {"member_count": member_count, "activated_count": activated_count, "unactivated_count": member_count - activated_count}

    def list_active_templates(self, include_notice: bool = True) -> List[Dict[str, Any]]:
        with self.lock:
            sql = "SELECT * FROM task_template WHERE enabled = 1 AND deleted = 0" + ("" if include_notice else " AND kind != 'notice'") + " ORDER BY template_id ASC"
            return [self._deserialize_template_row(r) for r in self.conn.execute(sql).fetchall()]

    # ── 已激活待归纳人员（2026-10-28 方案B：用户发任意消息自动录入，管理员后续归纳角色）──
    def phone_in_roster(self, phone: str) -> bool:
        """手机号是否已在成员名单（member 表）里。用于「先入待归纳池、不拦截」的分流判断。"""
        clean = (phone or "").strip()
        if not clean:
            return False
        with self.lock:
            return self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", (clean,)).fetchone() is not None

    def upsert_pending_member(self, external_id: str, last_message: str = "",
                              phone: Optional[str] = None,
                              name: Optional[str] = None) -> Dict[str, Any]:
        """用户给机器人发任意消息时自动录入待归纳池（幂等，已存在则仅更新变化字段）。

        ⭐ 2026-10-28 用户口径：手机号**不再要求先在名单里**——
           提交的手机号直接落池（标记未核实），由管理员归纳角色时才决定归属。
        """
        clean_id = (external_id or "").strip()
        if not clean_id:
            raise BizError(400, "VALIDATION_FAILED", "external_id 不能为空")
        clean_phone = (phone or "").strip() or None
        clean_name = (name or "").strip() or None
        now_s = self.now_str()
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT id FROM pending_member WHERE external_id = ?", (clean_id,)).fetchone()
            if existing:
                self.conn.execute(
                    "UPDATE pending_member SET last_message = ?,"
                    " phone = COALESCE(?, phone), name = COALESCE(?, name), updated_at = ?"
                    " WHERE external_id = ?",
                    (last_message[:200], clean_phone, clean_name, now_s, clean_id),
                )
            else:
                self.conn.execute(
                    "INSERT INTO pending_member(external_id, phone, name, last_message, activated_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (clean_id, clean_phone, clean_name, last_message[:200], now_s, now_s),
                )
        return self.get_pending_member(clean_id) or {"external_id": clean_id}

    def get_pending_member(self, external_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM pending_member WHERE external_id = ?", ((external_id or "").strip(),)).fetchone()
            return dict(row) if row else None

    def list_pending_members(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM pending_member ORDER BY activated_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
            return [dict(r) for r in rows]

    def assign_pending_member(self, external_id: str, phone: str, name: str, role_id: str, dept: Optional[str] = None, actor: str = "system") -> Dict[str, Any]:
        """管理员将待归纳人员移入角色（同时建立 delivery_target 绑定，使其可接收提醒推送）。"""
        clean_id = (external_id or "").strip()
        clean_phone = (phone or "").strip()
        if not clean_id:
            raise BizError(400, "VALIDATION_FAILED", "external_id 不能为空")
        if not clean_phone:
            raise BizError(400, "PHONE_REQUIRED",
                           "该人员尚未登记手机号，请让其在机器人对话中发送 11 位手机号后再归纳")
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM pending_member WHERE external_id = ?", (clean_id,)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "待归纳人员不存在")
            d = dict(row)
            # 添加到角色成员
            self.add_role_member(role_id=role_id, phone=clean_phone, name=name or d.get("name") or clean_phone[-4:], dept=dept or "", actor=actor)
            # 绑定 delivery_target（使其可接收推送）
            self.upsert_delivery_target(phone=clean_phone, external_id=clean_id)
            # 从待归纳池移除
            self.conn.execute("DELETE FROM pending_member WHERE external_id = ?", (clean_id,))
            self._insert_audit_unlocked(actor, "pending_member.assign", "pending_member", clean_id, {"phone": clean_phone, "role_id": role_id}, now_s)
        return {"ok": True, "external_id": clean_id, "phone": clean_phone, "role_id": role_id}

    def remove_pending_member(self, external_id: str, actor: str = "system") -> None:
        """从待归纳池移除人员（解除绑定，用户需重新发消息方可再次录入）。"""
        clean_id = (external_id or "").strip()
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT id FROM pending_member WHERE external_id = ?", (clean_id,)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "待归纳人员不存在")
            self.conn.execute("DELETE FROM pending_member WHERE external_id = ?", (clean_id,))
            self._insert_audit_unlocked(actor, "pending_member.remove", "pending_member", clean_id, None, now_s)

    # ── 数据快照（2026-10-27 春促数据看板：人工填数本期主通道，后续可替换为自动取数）──
    def upsert_data_snapshot(self, source_key: str, period_key: str, values: Dict[str, Any], note: Optional[str] = None, updated_by: str = "system") -> Dict[str, Any]:
        clean_src, clean_pk = (source_key or "").strip(), (period_key or "").strip()
        if not clean_src or not clean_pk:
            raise BizError(400, "VALIDATION_FAILED", "source_key 与 period_key 不能为空")
        if not isinstance(values, dict) or not values:
            raise BizError(400, "VALIDATION_FAILED", "values 必须是非空对象")
        for k, v in values.items():
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", str(k)):
                raise BizError(400, "VALIDATION_FAILED", f"values 的 key 仅支持字母数字下划线: {k}")
            if not isinstance(v, (str, int, float)):
                raise BizError(400, "VALIDATION_FAILED", f"values 的值必须是文本或数字: {k}")
        now_s = self.now_str()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO data_snapshot(source_key, period_key, values_json, note, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_key, period_key) DO UPDATE SET values_json = excluded.values_json, note = excluded.note, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                (clean_src, clean_pk, json.dumps({k: (v if isinstance(v, str) else str(v)) for k, v in values.items()}, ensure_ascii=False), note, updated_by, now_s),
            )
            self._insert_audit_unlocked(updated_by, "data_snapshot.upsert", "data_snapshot", f"{clean_src}|{clean_pk}", {"keys": sorted(values.keys())}, now_s)
            return self.get_data_snapshot(clean_src, clean_pk) or {"source_key": clean_src, "period_key": clean_pk, "values": values, "note": note, "updated_at": now_s}

    def get_data_snapshot(self, source_key: str, period_key: str) -> Optional[Dict[str, Any]]:
        clean_src, clean_pk = (source_key or "").strip(), (period_key or "").strip()
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM data_snapshot WHERE source_key = ? AND period_key = ?", (clean_src, clean_pk)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                values = json.loads(d.get("values_json") or "{}")
            except Exception:
                values = {}
            return {"source_key": d["source_key"], "period_key": d["period_key"], "values": values, "note": d.get("note"), "updated_by": d.get("updated_by"), "updated_at": d["updated_at"]}

    def get_data_snapshot_values(self, source_key: str, period_key: str) -> Dict[str, str]:
        snap = self.get_data_snapshot(source_key, period_key)
        return dict(snap["values"]) if snap else {}

    def list_data_snapshots(self, source_key: Optional[str] = None, period_key: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        where_clauses, params = ["1=1"], []
        if source_key and source_key.strip():
            where_clauses.append("source_key LIKE ?")
            params.append(f"%{source_key.strip()}%")
        if period_key and period_key.strip():
            where_clauses.append("period_key = ?")
            params.append(period_key.strip())
        where_sql = "WHERE " + " AND ".join(where_clauses)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM data_snapshot {where_sql} ORDER BY updated_at DESC LIMIT ?", tuple(params + [max(1, min(int(limit), 200))])
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                try:
                    values = json.loads(d.get("values_json") or "{}")
                except Exception:
                    values = {}
                out.append({"source_key": d["source_key"], "period_key": d["period_key"], "values": values, "note": d.get("note"), "updated_by": d.get("updated_by"), "updated_at": d["updated_at"]})
            return out

    def issue_h5_token(self, phone: str, template_id: str, period_key: str, instance_id: Optional[int], purpose: str = "submit", ttl_hours: int = H5_TOKEN_TTL_HOURS) -> str:
        now_dt = self.now()
        created_at, expires_at = format_dt(now_dt), format_dt(now_dt + timedelta(hours=ttl_hours))
        with self.lock, self.conn:
            while True:
                raw_token = secrets.token_urlsafe(24)
                token_id = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                if not self.conn.execute("SELECT 1 FROM h5_token WHERE token_id = ?", (token_id,)).fetchone():
                    break
            self.conn.execute(
                "INSERT INTO h5_token(token_id, phone, template_id, period_key, instance_id, purpose, created_at, expires_at, consumed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (token_id, phone, template_id, period_key, instance_id, purpose, created_at, expires_at),
            )
            return raw_token

    def get_h5_token(self, raw_token: str) -> Optional[Dict[str, Any]]:
        clean = (raw_token or "").strip()
        if not clean:
            return None
        token_id = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        with self.lock:
            row = self.conn.execute("SELECT * FROM h5_token WHERE token_id = ?", (token_id,)).fetchone()
            return dict(row) if row else None

    def consume_h5_token(self, token_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE h5_token SET consumed = 1 WHERE token_id = ?", (token_id,))

    def create_upload_ref(self, *, ref: str, phone: str, template_id: str, file_name: str, mime: str, size: int, rel_path: str) -> Dict[str, Any]:
        now_s = self.now_str()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO upload_ref(ref, phone, template_id, file_name, mime, size, rel_path, consumed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (ref, phone, template_id, file_name, mime, int(size), rel_path, now_s),
            )
            return {"ref": ref, "phone": phone, "template_id": template_id, "file_name": file_name, "mime": mime, "size": int(size), "rel_path": rel_path, "consumed": False, "created_at": now_s}

    def get_upload_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM upload_ref WHERE ref = ?", ((ref or "").strip(),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["consumed"] = bool(d["consumed"])
            return d

    def get_latest_valid_submission(self, instance_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM submission WHERE instance_id = ? AND status != 'rejected' ORDER BY id DESC LIMIT 1", (int(instance_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["fields"] = json.loads(d["fields_json"]) if d.get("fields_json") else None
            return d

    def list_submissions_for_instance(self, instance_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM submission WHERE instance_id = ? ORDER BY id ASC", (int(instance_id),)).fetchall()
            items: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["fields"] = json.loads(d["fields_json"]) if d.get("fields_json") else None
                items.append(d)
            return items

    def is_instance_window_closed(self, instance: Dict[str, Any], now_dt: Optional[datetime] = None) -> bool:
        now_s = format_dt(now_dt or self.now())
        if instance["status"] == "expired":
            return True
        if instance["status"] == "rejected":
            return now_s > calc_resubmit_deadline(instance["deadline_at"], instance.get("reviewed_at"))
        return now_s > format_dt(parse_dt(instance["deadline_at"]) + timedelta(hours=72))

    def submit_h5_task(
        self, *, raw_token: str, fields: Optional[Dict[str, Any]] = None, file_ref: Optional[str] = None,
        note: Optional[str] = None, refs: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        tok = self.get_h5_token(raw_token)
        if not tok or tok.get("purpose") != "submit":
            raise BizError(404, "NOT_FOUND", "提交链接无效或不存在")
        cur_dt = self.now()
        now_s = format_dt(cur_dt)
        self.advance_instance_statuses(cur_dt)

        inst = self.get_instance(int(tok["instance_id"])) if tok.get("instance_id") else None
        if not inst:
            raise BizError(404, "NOT_FOUND", "任务实例不存在")
        tpl = self.get_template(inst["template_id"], include_deleted=True)
        if not tpl:
            raise BizError(404, "NOT_FOUND", "任务模板不存在")
        if inst["status"] == "approved":
            raise BizError(400, "VALIDATION_FAILED", "该任务已提交成功，无需重复提交")
        if inst["status"] == "expired" or tok["expires_at"] < now_s or self.is_instance_window_closed(inst, cur_dt):
            raise BizError(400, "WINDOW_CLOSED", "该任务已超过补交期限（72 小时），无法提交")
        if bool(tok["consumed"]):
            raise BizError(400, "VALIDATION_FAILED", "该链接已提交过，请勿重复使用")

        fields_json_str, up_row = None, None
        clean_note = str(note).strip()[:500] if ((tpl.get("allow_note") or tpl["kind"] == "form") and note is not None and str(note).strip()) else None

        # 上报类（form）可以同时带多个附件：文字说明走 note，图片/文件走 refs
        clean_refs = [str(r).strip() for r in (refs or []) if str(r or "").strip()] if isinstance(refs, list) else []
        if not clean_refs and file_ref:
            clean_refs = [str(file_ref).strip()]
        ref_rows: List[Dict[str, Any]] = []

        if tpl["kind"] == "form":
            fields_json_str = json.dumps(validate_form_submission_fields(tpl.get("schema_json") or [], fields), ensure_ascii=False)
            for r in clean_refs:
                row = self.get_upload_ref(r)
                if not row or row["phone"] != inst["phone"] or row["template_id"] != inst["template_id"]:
                    raise BizError(400, "VALIDATION_FAILED", "上传文件引用无效")
                if row["consumed"]:
                    raise BizError(400, "VALIDATION_FAILED", "该文件已被使用，请重新上传")
                ref_rows.append(row)
        elif tpl["kind"] in ("image", "file"):
            clean_ref = (clean_refs[0] if clean_refs else "").strip()
            if not clean_ref:
                raise BizError(400, "VALIDATION_FAILED", "请先上传文件后再提交")
            up_row = self.get_upload_ref(clean_ref)
            if not up_row or up_row["phone"] != inst["phone"] or up_row["template_id"] != inst["template_id"]:
                raise BizError(400, "VALIDATION_FAILED", "上传文件引用无效")
            if up_row["consumed"]:
                raise BizError(400, "VALIDATION_FAILED", "该文件已被使用，请重新上传")
            ref_rows = [up_row]

        refs_json_str = json.dumps([r["ref"] for r in ref_rows], ensure_ascii=False) if ref_rows else None
        new_status = "submitted" if tpl["need_review"] else "approved"
        late_submit = 1 if (now_s > inst["deadline_at"] or int(inst.get("late_submit") or 0) == 1) else 0

        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO submission(instance_id, fields_json, file_ref, file_name, mime, size, note, refs_json, source, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'user', ?, ?)",
                (
                    int(inst["id"]), fields_json_str, up_row["ref"] if up_row else None, up_row["file_name"] if up_row else None,
                    up_row["mime"] if up_row else None, up_row["size"] if up_row else None, clean_note, refs_json_str,
                    new_status, now_s,
                ),
            )
            sub_id = int(cur.lastrowid)
            for r in ref_rows:
                self.conn.execute("UPDATE upload_ref SET consumed = 1 WHERE ref = ?", (r["ref"],))
            self.conn.execute("UPDATE task_instance SET status = ?, submitted_at = ?, late_submit = ? WHERE id = ?", (new_status, now_s, late_submit, int(inst["id"])))
            self.conn.execute("UPDATE h5_token SET consumed = 1 WHERE token_id = ?", (tok["token_id"],))

        return {"ok": True, "submission_id": sub_id, "status": new_status, "late_submit": bool(late_submit), "message": "已提交，等待审核" if tpl["need_review"] else "已完成"}

    def review_instance(self, instance_id: int, action: str, reviewer: str, reason: Optional[str] = None) -> Dict[str, Any]:
        act, clean_reason = (action or "").strip().lower(), (reason or "").strip()
        if act not in ("approve", "reject"):
            raise BizError(400, "VALIDATION_FAILED", "action 必须是 approve 或 reject")
        if act == "reject" and len(clean_reason) < 2:
            raise BizError(400, "VALIDATION_FAILED", "驳回原因不少于 2 个字")
        now_s = self.now_str()
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM task_instance WHERE id = ?", (int(instance_id),)).fetchone():
                raise BizError(404, "NOT_FOUND", "任务实例不存在")
            latest_sub = self.conn.execute("SELECT id FROM submission WHERE instance_id = ? ORDER BY id DESC LIMIT 1", (int(instance_id),)).fetchone()
            if act == "approve":
                self.conn.execute("UPDATE task_instance SET status = 'approved', reviewed_at = ?, reviewer = ? WHERE id = ?", (now_s, reviewer, int(instance_id)))
                if latest_sub:
                    self.conn.execute("UPDATE submission SET status = 'approved' WHERE id = ?", (int(latest_sub["id"]),))
            else:
                self.conn.execute(
                    "UPDATE task_instance SET status = 'rejected', reviewed_at = ?, reviewer = ?, reject_reason = ?, reject_count = reject_count + 1 WHERE id = ?",
                    (now_s, reviewer, clean_reason, int(instance_id)),
                )
                if latest_sub:
                    self.conn.execute("UPDATE submission SET status = 'rejected' WHERE id = ?", (int(latest_sub["id"]),))
            self._insert_audit_unlocked(reviewer, f"instance.{act}", "task_instance", str(instance_id), {"reason": clean_reason if act == "reject" else None}, now_s)
            res = dict(self.conn.execute("SELECT * FROM task_instance WHERE id = ?", (int(instance_id),)).fetchone())
            res["resubmit_deadline"] = calc_resubmit_deadline(res["deadline_at"], res.get("reviewed_at")) if res["status"] == "rejected" else None
            return res

    def ensure_instance(self, template_id: str, phone: str, name_snap: str, dept_snap: Optional[str], period_key: str, deadline_at: str) -> Dict[str, Any]:
        now_s = self.now_str()
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO task_instance(template_id, phone, name_snap, dept_snap, period_key, status, deadline_at, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (template_id, phone, name_snap, dept_snap, period_key, deadline_at, now_s),
            )
            created = cur.rowcount > 0
            res = dict(self.conn.execute("SELECT * FROM task_instance WHERE template_id = ? AND phone = ? AND period_key = ?", (template_id, phone, period_key)).fetchone())
            res["created"] = created
            return res

    def ensure_template_instances(self, template_id: str, period_key: str, deadline_at: str) -> List[Dict[str, Any]]:
        tpl = self.get_template(template_id, include_deleted=False)
        # 2026-10-28 方案A：notice 也建实例（pending → sent），不再在这里拦掉
        if not tpl or not tpl["enabled"]:
            return []
        members = self.get_dedup_enabled_members_for_roles(tpl["role_ids"])
        now_s = self.now_str()
        with self.lock, self.conn:
            for m in members:
                self.conn.execute(
                    "INSERT OR IGNORE INTO task_instance(template_id, phone, name_snap, dept_snap, period_key, status, deadline_at, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (template_id, m["phone"], m["name"], m["dept"], period_key, deadline_at, now_s),
                )
            return [dict(r) for r in self.conn.execute("SELECT * FROM task_instance WHERE template_id = ? AND period_key = ? ORDER BY id ASC", (template_id, period_key)).fetchall()]

    def list_instances_for_period(self, template_id: str, period_key: str) -> List[Dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM task_instance WHERE template_id = ? AND period_key = ? ORDER BY id ASC", (template_id, period_key)).fetchall()]

    def update_instance_status(self, instance_id: int, status: str) -> None:
        """2026-10-28 方案A：通知实例发送成功后标记为 sent（只在 pending 上生效，避免覆盖后续状态）。"""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE task_instance SET status = ? WHERE id = ? AND status = 'pending'",
                (str(status), int(instance_id)),
            )

    def list_overdue_instances(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM task_instance WHERE status = 'overdue' ORDER BY id ASC").fetchall()]

    def get_instance(self, instance_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM task_instance WHERE id = ?", (int(instance_id),)).fetchone()
            return dict(row) if row else None

    def get_instance_by_key(self, template_id: str, phone: str, period_key: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM task_instance WHERE template_id = ? AND phone = ? AND period_key = ?", ((template_id or "").strip(), (phone or "").strip(), (period_key or "").strip())).fetchone()
            return dict(row) if row else None

    def advance_instance_statuses(self, now_dt: Optional[datetime] = None) -> Dict[str, int]:
        cur_dt = now_dt or self.now()
        now_s, cutoff_72h = format_dt(cur_dt), format_dt(cur_dt - timedelta(hours=72))
        # 2026-10-28 方案A：通知类实例只有 pending → sent 两个状态，没有补交概念，
        # 不参与 overdue / expired 流转（否则每天 23:59 之后会整片变成「已逾期」）。
        not_notice = "template_id NOT IN (SELECT template_id FROM task_template WHERE kind = 'notice')"
        with self.lock, self.conn:
            c1 = self.conn.execute(
                f"UPDATE task_instance SET status = 'overdue' WHERE status = 'pending' AND deadline_at < ? AND {not_notice}",
                (now_s,),
            )
            c2 = self.conn.execute(
                f"UPDATE task_instance SET status = 'expired' WHERE status = 'overdue' AND deadline_at < ? AND {not_notice}",
                (cutoff_72h,),
            )
            to_expired = int(c2.rowcount or 0)
            for r in self.conn.execute("SELECT id, deadline_at, reviewed_at FROM task_instance WHERE status = 'rejected'").fetchall():
                if calc_resubmit_deadline(r["deadline_at"], r["reviewed_at"]) < now_s:
                    c3 = self.conn.execute("UPDATE task_instance SET status = 'expired' WHERE id = ? AND status = 'rejected'", (int(r["id"]),))
                    to_expired += int(c3.rowcount or 0)
            return {"to_overdue": int(c1.rowcount or 0), "to_expired": to_expired}

    def has_sent_reminder(self, idem_key: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM reminder_log WHERE idem_key = ? AND result = 'sent' LIMIT 1", (idem_key,)).fetchone() is not None

    def get_reminder_by_idem_key(self, idem_key: str, sent_only: bool = True) -> Optional[Dict[str, Any]]:
        with self.lock:
            sql = "SELECT * FROM reminder_log WHERE idem_key = ? AND result = 'sent' ORDER BY id DESC LIMIT 1" if sent_only else "SELECT * FROM reminder_log WHERE idem_key = ? ORDER BY retry DESC, id DESC LIMIT 1"
            row = self.conn.execute(sql, (idem_key,)).fetchone()
            return dict(row) if row else None

    def list_reminder_attempts(self, idem_key: str) -> List[Dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM reminder_log WHERE idem_key = ? ORDER BY retry ASC, id ASC", (idem_key,)).fetchall()]

    def record_reminder_log(
        self,
        *,
        template_id: str,
        instance_id: Optional[int],
        phone: str,
        kind: str,
        planned_at: str,
        sent_at: Optional[str],
        result: str,
        error: Optional[str],
        idem_key: str,
        retry: int = 0,
        attempted_at: Optional[str] = None,
    ) -> Optional[int]:
        attempt_ts = attempted_at or sent_at or self.now_str()
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO reminder_log("
                "template_id, instance_id, phone, kind, planned_at, attempted_at, sent_at, result, error, retry, idem_key"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (template_id, instance_id, phone, kind, planned_at, attempt_ts, sent_at, result, error, int(retry), idem_key),
            )
            return int(cur.lastrowid) if cur.rowcount > 0 else None

    def list_reminder_logs(self, template_id: Optional[str] = None, instance_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        if template_id is not None:
            where.append("template_id = ?")
            params.append(template_id)
        if instance_id is not None:
            where.append("instance_id = ?")
            params.append(int(instance_id))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with self.lock:
            return [dict(r) for r in self.conn.execute(f"SELECT * FROM reminder_log {where_sql} ORDER BY id ASC", tuple(params)).fetchall()]
