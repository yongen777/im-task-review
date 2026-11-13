# -*- coding: utf-8 -*-
"""数据访问层（唯一允许写 SQL 的模块，覆盖阶段一 + 2A + 2B）。"""
from __future__ import annotations

import concurrent.futures
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

# ── 2026-11-12 任务E1：截止与 Token 有效期口径（用户拍板） ──────────────
# 无截止模板的实例截止时间哨兵：deadline_at 列 NOT NULL 不动，存该哨兵后
# 所有「now > deadline_at」的字符串比较天然永不为真 → 永不过期；
# 展示层（H5/后台/消息文案）统一特判显示「不限」。
NO_DEADLINE_SENTINEL = "9999-12-31 23:59:59"
# Token 有效期保守策略：有截止 = min(截止后 24h, 720h)；无截止 = 720h（30 天）。
H5_TOKEN_TTL_CAP_HOURS = 720
H5_TOKEN_TTL_GRACE_HOURS = 24
# 有效期下限（小时）：截止已过等极端场景仍保证链接能打开看到「任务已过期」提示，
# 而不是「链接无效」；提交侧另有窗口硬拦截，这里只管链接寿命。
H5_TOKEN_TTL_FLOOR_HOURS = 1.0

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
FIELD_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROLE_ID_RE = re.compile(r"r_[0-9a-fA-F]+")


def parse_role_ids(raw: Any) -> List[str]:
    """2026-11-12 晚（需求四 · 角色删除引用加固）：role_ids 字段的**规范解析**。

    统一支持 JSON 数组字符串与逗号/分号分隔格式；解析失败时按 role_id 词法兜底提取，
    保证「只要该角色被任意未删除模板引用，删除就 100% 拦截」——
    旧实现 `json.loads` 失败直接置空列表会导致**漏判误删**（用户点名的隐患）。
    """
    s = raw if isinstance(raw, str) else ("" if raw is None else json.dumps(raw, ensure_ascii=False))
    s = (s or "").strip()
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, (list, tuple)):
            return [str(x).strip() for x in data if str(x).strip()]
        if isinstance(data, str):
            s = data.strip()
        else:
            return [str(data).strip()] if str(data).strip() else []
    except Exception:
        pass
    # 逗号 / 分号 / 空白 / 括号引号的兜底切分（历史脏数据 / 手工导入行）
    tokens = re.split(r"[,;，；\s\[\]【】{}「」()（）'\"]+", s)
    found = [t for t in (str(t).strip() for t in tokens) if t and ROLE_ID_RE.fullmatch(t)]
    if found:
        return found
    # 极端脏数据：连切分都提不出来 → 保守放行解析为空，由调用方做字符串包含兜底
    return []

VALID_TEMPLATE_KINDS = ("form", "image", "file", "notice")
VALID_PERIODS = ("day", "week", "month")
VALID_FIELD_TYPES = ("text", "number", "date", "select")
# 任务E1：截止类型枚举（same_day=当天 / weekend=本周日 / month_end=本月最后一天）
VALID_DEADLINE_TYPES = ("same_day", "weekend", "month_end")
# 2026-11-12 下午：部门名长度上限放开到 200（官方通讯录部门名常超 30 字；
# 原 30 字上限会让「树节点加入部门选项 / 成员部门下拉」无法创建与回填）。
MAX_DEPT_NAME_LEN = 200


def _is_number_str(v: str) -> bool:
    """字符串是否可作数值看板项（2026-11-13 模块三「报数视图」判定）。"""
    try:
        float(str(v).strip())
        return True
    except (TypeError, ValueError):
        return False


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
    """驳回重交窗口。

    2026-11-12 下午（用户拍板，取代 R27 的 max(原截止+72h, 驳回+24h)）：
      · 有截止时间的模板 → 重交截止统一锁定为 **驳回时刻 + 24h**；
        超 24h 未重交 → 重交窗口自动关闭（advance 流转 expired），再打开/提交均按过期拦截；
      · 无截止时间（哨兵）的模板 → 重交窗口同样**无限期**（哨兵），
        通知文案提示「本任务无截止时间，请按审核意见尽快重新提交」。
    """
    if is_no_deadline(deadline_at):
        return NO_DEADLINE_SENTINEL
    base_s = str(reviewed_at or "").strip() or format_dt(default_now())
    return format_dt(parse_dt(base_s) + timedelta(hours=24))


def effective_instance_deadline(instance: Dict[str, Any]) -> str:
    """该实例对用户「还能不能交」真正生效的截止时刻。

    · rejected（驳回重交）：重交截止 = **驳回时刻 + 24h**（无截止模板 = 哨兵，无限期）；
    · 其余：实例自身的 deadline_at（无截止模板 = 哨兵，永不过期）。
    """
    deadline_at = str(instance.get("deadline_at") or "")
    if str(instance.get("status") or "") == "rejected":
        return calc_resubmit_deadline(deadline_at, instance.get("reviewed_at"))
    return deadline_at


def is_no_deadline(deadline_at: Optional[str]) -> bool:
    """是否是「无截止」哨兵（9999-12-31 23:59:59）。"""
    return str(deadline_at or "").strip() >= NO_DEADLINE_SENTINEL


def submission_expired_message(instance: Dict[str, Any]) -> str:
    """2026-11-12 任务E2/E3：到点后的统一提示（用户拍板，不区分链接过期与任务截止）。

    例：当前任务已过期（截止时间：2026-11-12 18:00:00），无法提交
    """
    deadline_at = effective_instance_deadline(instance)
    if is_no_deadline(deadline_at):
        deadline_at = "不限"
    return f"当前任务已过期（截止时间：{deadline_at}），无法提交"


def calc_h5_token_ttl_hours(instance: Optional[Dict[str, Any]], now_dt: Optional[datetime] = None) -> float:
    """2026-11-12 任务E1：H5 Token 动态有效期（小时）。

    用户拍板口径（保守策略）：
      · 无截止时间（哨兵）→ 720h（30 天）；
      · 有截止时间 → min(截止 + 24h, 720h)；
      · 驳回重交（2026-11-12 下午拍板）：链接有效期与 **24h 重交窗口对齐**
        （= 重交截止 − 当前时刻；驳回后立刻签发即 24h，越晚签发越短），保底 1h、封顶 720h；
    从「签发时刻」起算；已过期场景保底 1h，让用户能打开页面看到过期提示。
    """
    if not instance:
        return float(H5_TOKEN_TTL_HOURS)
    now_dt = now_dt or default_now()
    inst_status = str(instance.get("status") or "")
    deadline_at = effective_instance_deadline(instance)
    if is_no_deadline(deadline_at):
        return float(H5_TOKEN_TTL_CAP_HOURS)
    try:
        end_dt = parse_dt(deadline_at)
        # 2026-11-12 下午：rejected 的链接寿命严格对齐 24h 重交窗口（不再额外 +24h 宽限）
        if inst_status != "rejected":
            end_dt = end_dt + timedelta(hours=H5_TOKEN_TTL_GRACE_HOURS)
    except Exception:
        return float(H5_TOKEN_TTL_HOURS)
    # parse_dt 返回带 Asia/Shanghai 时区的 datetime；测试注入的 now 可能是 naive，统一对齐
    ref_dt = now_dt
    if end_dt.tzinfo is not None and ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=end_dt.tzinfo)
    elif end_dt.tzinfo is None and ref_dt.tzinfo is not None:
        end_dt = end_dt.replace(tzinfo=ref_dt.tzinfo)
    hours = (end_dt - ref_dt).total_seconds() / 3600.0
    return float(min(max(hours, H5_TOKEN_TTL_FLOOR_HOURS), H5_TOKEN_TTL_CAP_HOURS))


def format_deadline_rule(template: Optional[Dict[str, Any]]) -> str:
    """2026-11-12 下午：截止规则的**统一展示样式**（用户拍板「引用截止时间的地方统一成这样样式」）。

    本月月底 17:00 / 本周日 17:00 / 今天 17:00 / 不限 —— 模板列表徽章、编辑弹窗预览、
    H5 任务页徽章统一用规则样式；过期拦截文案仍显示已解析的具体时间戳（精确到分秒）。
    """
    if not template:
        return "不限"
    _hd = template.get("has_deadline")
    if _hd is not None and not int(_hd):
        return "不限"
    hhmm = str(template.get("deadline_time") or template.get("deadline") or "").strip()
    if not hhmm:
        return "不限"
    d_type = str(template.get("deadline_type") or "").strip() or "same_day"
    label = {"same_day": "今天", "weekend": "本周日", "month_end": "本月月底"}.get(d_type, "截止")
    return f"{label} {hhmm}"


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
    """2026-11-12 任务B：schema_json 改为可选。

    用户图一实锤：只勾「图片上传」建上传任务模板被 400「表单类型模板必须至少包含
    1 个字段」拦死 —— 但任务本来就可以只要求传图/传文件/写文字，字段报数只是
    H5 上传模块之一。空 schema（None / [] / 空串 / JSON null）一律返回 []；
    给了内容则逐项校验（非法 JSON / 非数组 / 字段项非法仍 400，守卫不放松）。
    提交侧 validate_form_submission_fields(schema=[], fields) 循环空表自然通过。
    """
    if schema_raw is None:
        return []
    if isinstance(schema_raw, str):
        if not schema_raw.strip():
            return []
        try:
            schema_raw = json.loads(schema_raw)
        except Exception:
            raise BizError(400, "VALIDATION_FAILED", "schema_json 必须是合法的 JSON 数组")
    if schema_raw is None or (isinstance(schema_raw, list) and not schema_raw):
        return []
    if not isinstance(schema_raw, list):
        raise BizError(400, "VALIDATION_FAILED", "schema_json 必须是 JSON 数组")

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


def required_h5_modules(template: Dict[str, Any]) -> set:
    """模板**显式勾选**的 H5 上传模块（spec.upload_modules）。

    2026-10-30 用户口径：管理员在模板里选了什么，用户就必须完成什么 ——
    选了「图片」就必须传图、选了「文件」就必须传文件、选了「文字说明」就必须填，
    否则提交时直接拦下来并提示，不允许"空着交上去"。

    ⚠️ 只在**显式勾选**时生效：老模板 spec 里没有 upload_modules 字段，
    保持原来行为（不额外新增拦截），避免存量任务突然交不了。
    """
    spec = template.get("spec_json") or {}
    mods = spec.get("upload_modules") if isinstance(spec, dict) else None
    if not isinstance(mods, list):
        return set()
    return {str(m).strip() for m in mods if str(m or "").strip()}


def effective_h5_modules(template: Dict[str, Any]) -> List[str]:
    """H5 页面**实际渲染**的模块清单（显式勾选优先，否则按任务类型给默认值）。

    后台详情弹窗按这份清单决定显示哪些区块 —— 管理员没勾「文件」，就不该出现文件那一栏。
    与前端预览 `resolveUploadModules` / H5 `modules` 三段推导保持一致。
    """
    mods = required_h5_modules(template)
    if mods:
        # 保持用户勾选顺序，便于前后端展示一致
        spec = template.get("spec_json") or {}
        order = [str(m) for m in (spec.get("upload_modules") or [])]
        return [m for m in order if m in mods]
    kind = str(template.get("kind") or "")
    if kind == "form":
        return ["fields", "text"]
    if kind == "image":
        return ["image", "text"]
    if kind == "file":
        return ["file", "text"]
    return ["fields", "image", "text"]


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


def _fmt_member(mid: int, role_id: str, phone: str, name: str, dept: Optional[str], enabled: Any, source: str, created_at: str, activated: Any, activations: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    item = {
        "id": int(mid), "role_id": role_id, "phone": phone, "name": name, "dept": dept,
        "enabled": bool(enabled), "source": source, "created_at": created_at, "activated": bool(activated),
    }
    if activations is not None:
        item["activations"] = {k: bool(v) for k, v in activations.items()}
    return item


def _same_day(a: str, b: str) -> bool:
    """两个 'YYYY-MM-DD HH:MM:SS' 是否同一天（只看日期部分）。"""
    return str(a or "")[:10] == str(b or "")[:10] and str(a or "")[:10] != ""


def _lt_time(a: str, b: str) -> bool:
    """a 是否早于 b（'YYYY-MM-DD HH:MM:SS' 字典序即可比）。空值返回 False。"""
    x, y = str(a or "").strip(), str(b or "").strip()
    if not x or not y:
        return False
    return x < y


class StateStore:
    def __init__(self, db_path: Path, now_fn: NowFn = default_now, platform: str = "zdxlz") -> None:
        self._now_fn = now_fn
        # 当前通道（zdxlz / wecom）：投递绑定按通道分开存，读写默认都走它。
        self.platform = (platform or "zdxlz").strip().lower()
        self.db = Database(db_path, self.now_str)
        # 2026-11-10 方案A：通讯录查询客户端（导入即激活 / 首条消息自动识别）。
        # 由 main.create_apps 在生产装配；测试/降级环境为 None → 所有自动激活路径静默跳过。
        self.contacts = None
        # 2026-11-10 企微镜像：手机号→userid 反查（官方 95402，corpid+secret，仅 HTTP，不碰长连接）
        self.wecom_contacts = None

    def _plat(self, platform: Optional[str] = None) -> str:
        """显式传了就用传的，否则用当前通道（store.platform）。

        2026-10-30 通道隔离：delivery_target 主键是 (platform, phone)，
        绑定关系必须跟着通道走，否则切到企业微信会把量子密信的 uid 当企微 userid 发。
        """
        return ((platform or "").strip().lower() or self.platform)

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
            # 2026-11-04 多管理员分级：主账号 admin 恒为超级管理员 + 双平台
            # （新建时也要兜住：迁移只保证"已存在"的 admin 行，新建路径同样要写对角色）
            if clean_user == "admin":
                self.conn.execute(
                    "UPDATE admin_user SET role_level = 'super_admin', "
                    "display_name = CASE WHEN display_name = '' THEN '超级管理员' ELSE display_name END, "
                    "allowed_platforms = '[\"zdxlz\",\"wecom\"]' WHERE id = ?",
                    (user_id,),
                )
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

    def create_admin_session(self, user_id: int, ttl_hours: int = SESSION_TTL_HOURS, parent_sid: Optional[str] = None) -> Dict[str, str]:
        """签发管理会话。parent_sid：代登录（assume）时记录原超管会话，供 unassume 一键切回。

        HttpOnly Cookie 前端拿不到 sid，所以切回必须在服务端完成：新会话上挂 parent_sid，
        unassume 用当前代管会话反查原超管会话并重新下发 Cookie。
        """
        sid, csrf = secrets.token_hex(32), secrets.token_hex(24)
        now_dt = self.now()
        created_at, expires_at = format_dt(now_dt), format_dt(now_dt + timedelta(hours=ttl_hours))
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO admin_session(sid, user_id, csrf, created_at, last_seen, expires_at, parent_sid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, user_id, csrf, created_at, created_at, expires_at, (parent_sid or None)),
            )
        return {"sid": sid, "csrf": csrf, "created_at": created_at, "expires_at": expires_at}

    def get_admin_session(self, sid: str) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute(
                # 2026-11-05：会话有效性绑定账号状态 —— 停用账号的会话一律视为无效。
                # update_admin_account 停用时已主动删会话，这里是查询口的兜底（防其它路径改库绕过）。
                "SELECT s.sid, s.user_id, s.csrf, s.parent_sid, s.created_at, s.last_seen, s.expires_at, u.username, u.must_change "
                "FROM admin_session s JOIN admin_user u ON u.id = s.user_id "
                "WHERE s.sid = ? AND u.enabled = 1",
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
                "parent_sid": row["parent_sid"],
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

    # ── 2026-11-04 多管理员分级（超级管理员 + 分级管理员）────────────────────
    # 口径（用户 2026-11-04 定）：
    #   · admin = 超级管理员：全局只读 + 管理员账号管理（不建角色/模板/成员、不改业务数据）；
    #   · 分级管理员：只读写自己名下（owner_id）的角色/成员/模板/实例，账号级限制可用办公软件平台；
    #   · 成员的「平台会话激活」（delivery_target）全平台全局共享 —— 谁先激活过就都算已激活，无需二次激活。
    DEFAULT_SUB_ADMIN_PASSWORD = "Hds22999!"
    SUPER_ADMIN = "super_admin"
    SUB_ADMIN = "sub_admin"
    ALL_PLATFORMS = ("zdxlz", "wecom")

    @staticmethod
    def normalize_admin_password(raw: str) -> str:
        """兼容中文全角感叹号：Hds22999！ → Hds22999!（用户初始密码常从中文输入法敲出）。"""
        return (raw or "").replace("！", "!")

    @staticmethod
    def parse_admin_account(row: Any) -> Dict[str, Any]:
        """admin_user 行 → 对外结构（allowed_platforms 解析成数组）。

        ⚠️ 2026-11-04：口令哈希绝不出现在任何对外响应里 —— 这里统一剔除
        （此前 list_admin_accounts 用 SELECT * 把 pwd_hash 带给了前端）。
        """
        if row is None:
            return {}
        d = dict(row)
        d.pop("pwd_hash", None)
        try:
            plats = json.loads(d.get("allowed_platforms") or "")
        except Exception:
            plats = []
        if not isinstance(plats, list):
            plats = []
        d["allowed_platforms"] = [str(p).strip().lower() for p in plats if str(p).strip()]
        d["enabled"] = int(d.get("enabled") or 0)
        d["must_change"] = int(d.get("must_change") or 0)
        d["role_level"] = str(d.get("role_level") or StateStore.SUB_ADMIN)
        return d

    def admin_display_name(self, admin_id: int) -> str:
        with self.lock:
            row = self.conn.execute(
                "SELECT username, display_name FROM admin_user WHERE id = ?", (int(admin_id),)
            ).fetchone()
        if not row:
            return ""
        return str(row["display_name"] or row["username"] or "")

    def create_admin_account(
        self,
        username: str,
        display_name: str = "",
        allowed_platforms: Optional[List[str]] = None,
        actor: str = "system",
        role_level: str = SUB_ADMIN,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """新建分级管理员：初始密码固定 Hds22999!（可覆盖），强制首登改密。"""
        clean_user = (username or "").strip()
        clean_disp = (display_name or "").strip() or clean_user
        if not clean_user:
            raise BizError(400, "VALIDATION_FAILED", "用户名不能为空")
        if not clean_user.replace("_", "").replace("-", "").isalnum():
            raise BizError(400, "VALIDATION_FAILED", "用户名只能包含字母、数字、下划线或连字符")
        if len(clean_user) > 32:
            raise BizError(400, "VALIDATION_FAILED", "用户名过长（最多 32 字符）")
        plats = [str(p).strip().lower() for p in (allowed_platforms or list(self.ALL_PLATFORMS)) if str(p).strip()]
        plats = [p for p in plats if p in self.ALL_PLATFORMS]
        if not plats:
            raise BizError(400, "VALIDATION_FAILED", "至少要授权一个办公软件平台")
        pwd = self.normalize_admin_password(password or self.DEFAULT_SUB_ADMIN_PASSWORD)
        pwd_err = validate_new_password(pwd)
        if pwd_err:
            raise BizError(400, "VALIDATION_FAILED", pwd_err)
        now_s = self.now_str()
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM admin_user WHERE username = ?", (clean_user,)).fetchone():
                raise BizError(409, "USERNAME_TAKEN", "该用户名已存在")
            cur = self.conn.execute(
                "INSERT INTO admin_user(username, pwd_hash, must_change, created_at, display_name, role_level, allowed_platforms, enabled) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, 1)",
                (clean_user, hash_password(pwd), now_s, clean_disp, str(role_level or self.SUB_ADMIN),
                 json.dumps(plats, ensure_ascii=False)),
            )
            admin_id = int(cur.lastrowid)
            self._insert_audit_unlocked(
                actor, "admin.account.create", "admin_user", str(admin_id),
                {"username": clean_user, "display_name": clean_disp, "allowed_platforms": plats,
                 "role_level": str(role_level or self.SUB_ADMIN)}, now_s,
            )
        return {"id": admin_id, "username": clean_user, "display_name": clean_disp,
                "role_level": str(role_level or self.SUB_ADMIN), "allowed_platforms": plats, "enabled": 1}

    def list_admin_accounts(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """管理员账号列表 + 各自的数据统计。

        2026-11-06 口径（用户拍板「平台视角统一」）：
          · platform 传入时，角色/成员/模板/今日实例全部按该平台过滤
            —— 超管卡 = 该平台全局汇总，分级卡 = 该管理员该平台名下（向文晗未开通企微 → 企微视角全 0）；
          · 任务实例固定只看「今天」，与实例面板默认口径一致（46/47 之争的根因：
            卡片多算了企微模板那 1 条，平台过滤后自然对齐）；
          · platform 不传时保持全平台口径（兼容旧调用/测试）；
          · 成员数按「人」去重（DISTINCT phone，一人多角色只算一次；停用也算在册）。
        """
        plat = (platform or "").strip().lower()
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM admin_user ORDER BY CASE role_level WHEN 'super_admin' THEN 0 ELSE 1 END, id ASC"
            ).fetchall()
            out: List[Dict[str, Any]] = []
            today_key = self.now().strftime("%Y-%m-%d")
            for r in rows:
                d = self.parse_admin_account(r)
                aid = int(d["id"])
                is_super = str(d["role_level"] or "") == self.SUPER_ADMIN
                scope = None if is_super else aid
                # 角色分组平台分布（跨平台全量；仅作悬停注解，不参与卡片主数值）
                if scope is None:
                    plat_rows = self.conn.execute(
                        "SELECT platform, COUNT(1) AS n FROM role GROUP BY platform").fetchall()
                else:
                    plat_rows = self.conn.execute(
                        "SELECT platform, COUNT(1) AS n FROM role WHERE owner_id = ? GROUP BY platform",
                        (scope,)).fetchall()
                d["role_platforms"] = {str(pr["platform"]): int(pr["n"]) for pr in plat_rows}

                if not plat:
                    d["role_count"] = self._count_by_owner("role", scope)
                    d["member_count"] = self._distinct_member_count(scope)
                    if scope is None:
                        d["template_count"] = int(self.conn.execute("SELECT COUNT(1) FROM task_template WHERE deleted = 0").fetchone()[0] or 0)
                    else:
                        d["template_count"] = int(self.conn.execute("SELECT COUNT(1) FROM task_template WHERE owner_id = ? AND deleted = 0", (scope,)).fetchone()[0] or 0)
                    d["instance_count"] = self._count_by_owner("task_instance", scope)
                    if scope is None:
                        d["instance_count_today"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_instance WHERE period_key = ?", (today_key,)).fetchone()[0] or 0)
                    else:
                        d["instance_count_today"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_instance WHERE owner_id = ? AND period_key = ?",
                            (scope, today_key)).fetchone()[0] or 0)
                else:
                    # ── 平台口径：模板/实例的平台由其角色归属决定（角色/成员直接按 role.platform）──
                    if scope is None:
                        d["role_count"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM role WHERE platform = ?", (plat,)).fetchone()[0] or 0)
                        d["member_count"] = int(self.conn.execute(
                            "SELECT COUNT(DISTINCT m.phone) FROM member m JOIN role r ON r.role_id = m.role_id "
                            "WHERE r.platform = ?", (plat,)).fetchone()[0] or 0)
                        d["template_count"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_template t WHERE t.deleted = 0 AND EXISTS ("
                            "SELECT 1 FROM role r WHERE r.platform = ? AND instr(t.role_ids, r.role_id) > 0)",
                            (plat,)).fetchone()[0] or 0)
                        d["instance_count"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_instance i WHERE EXISTS (SELECT 1 FROM task_template t "
                            "JOIN role r ON instr(t.role_ids, r.role_id) > 0 "
                            "WHERE t.template_id = i.template_id AND r.platform = ?)",
                            (plat,)).fetchone()[0] or 0)
                        d["instance_count_today"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_instance i WHERE i.period_key = ? AND EXISTS ("
                            "SELECT 1 FROM task_template t JOIN role r ON instr(t.role_ids, r.role_id) > 0 "
                            "WHERE t.template_id = i.template_id AND r.platform = ?)",
                            (today_key, plat)).fetchone()[0] or 0)
                    else:
                        d["role_count"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM role WHERE owner_id = ? AND platform = ?",
                            (scope, plat)).fetchone()[0] or 0)
                        d["member_count"] = int(self.conn.execute(
                            "SELECT COUNT(DISTINCT m.phone) FROM member m JOIN role r ON r.role_id = m.role_id "
                            "WHERE r.platform = ? AND m.owner_id = ?", (plat, scope)).fetchone()[0] or 0)
                        d["template_count"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_template t WHERE t.deleted = 0 AND t.owner_id = ? AND EXISTS ("
                            "SELECT 1 FROM role r WHERE r.platform = ? AND instr(t.role_ids, r.role_id) > 0)",
                            (scope, plat)).fetchone()[0] or 0)
                        d["instance_count"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_instance i WHERE i.owner_id = ? AND EXISTS ("
                            "SELECT 1 FROM task_template t JOIN role r ON instr(t.role_ids, r.role_id) > 0 "
                            "WHERE t.template_id = i.template_id AND r.platform = ?)",
                            (scope, plat)).fetchone()[0] or 0)
                        d["instance_count_today"] = int(self.conn.execute(
                            "SELECT COUNT(1) FROM task_instance i WHERE i.owner_id = ? AND i.period_key = ? AND EXISTS ("
                            "SELECT 1 FROM task_template t JOIN role r ON instr(t.role_ids, r.role_id) > 0 "
                            "WHERE t.template_id = i.template_id AND r.platform = ?)",
                            (scope, today_key, plat)).fetchone()[0] or 0)
                out.append(d)
        return out

    def _count_by_owner(self, table: str, owner_id: Optional[int]) -> int:
        if owner_id is None:
            return int(self.conn.execute(f"SELECT COUNT(1) FROM {table}").fetchone()[0] or 0)
        return int(self.conn.execute(f"SELECT COUNT(1) FROM {table} WHERE owner_id = ?", (int(owner_id),)).fetchone()[0] or 0)

    def _distinct_member_count(self, owner_id: Optional[int]) -> int:
        sql = "SELECT COUNT(DISTINCT phone) FROM member"
        params: tuple = ()
        if owner_id is not None:
            sql += " WHERE owner_id = ?"
            params = (int(owner_id),)
        return int(self.conn.execute(sql, params).fetchone()[0] or 0)

    def member_distinct_total(self, platform: Optional[str] = None, owner_id: Optional[int] = None) -> int:
        """按「人」去重的在册人数（视角/平台与角色列表同一过滤口径；停用也算在册）。"""
        sql = (
            "SELECT COUNT(DISTINCT m.phone) FROM member m JOIN role r ON r.role_id = m.role_id WHERE 1=1"
        )
        params: List[Any] = []
        plat = (platform or "").strip().lower()
        if plat:
            sql += " AND r.platform = ?"
            params.append(plat)
        if owner_id is not None:
            sql += " AND r.owner_id = ?"
            params.append(int(owner_id))
        return int(self.conn.execute(sql, params).fetchone()[0] or 0)

    # ── 全平台成员池（2026-11-05 用户需求：超管按「人」管理的全局名册）──
    # member 表按 (role_id, phone) 建行 —— 同一人在 N 个角色里是 N 行；池按 phone 聚合。
    # 主行取 MAX(id)（最近一次导入/落库的行）作为姓名/部门的默认显示值；
    # 行间不一致时给 inconsistent 标志（前端提示"该手机号在 N 个角色中，姓名/部门不一致"）。
    POOL_STATUS_ACTIVE = "active"
    POOL_STATUS_PARTIAL = "partial"
    POOL_STATUS_DISABLED = "disabled"

    def list_member_pool(self, q: str = "", size: int = 500, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """按「人」去重的成员池聚合。

        2026-11-06 口径（用户拍板「平台视角统一」）：platform 传入时，池内只统计该平台的
        名册行 —— 张三案例：密信视角下只见密信角色，跨平台部门分叉不再出现、也不再误报
        「部门不一致」；不传时保持全平台口径（兼容旧调用/测试）。主行取该口径内 MAX(id)。
        姓名内多行不同名 → name_inconsistent（同一口径内才比，提示同样保留）。
        """
        clean_q = (q or "").strip()
        plat = (platform or "").strip().lower()
        use_plat = bool(plat)

        def pf(alias: str) -> str:
            return f"AND {alias}.platform = :plat " if use_plat else ""

        sql = (
            "SELECT m.phone AS phone, COUNT(1) AS row_count, COUNT(DISTINCT m.role_id) AS role_count, "
            "COUNT(DISTINCT m.owner_id) AS admin_count, SUM(COALESCE(m.enabled, 1)) AS enabled_count, "
            "COUNT(DISTINCT m.name) AS distinct_names, COUNT(DISTINCT COALESCE(m.dept, '')) AS distinct_depts, "
            "p.name AS display_name, p.dept AS display_dept, "
            "(SELECT GROUP_CONCAT(DISTINCT u.display_name) FROM member m2 "
            " JOIN role r2 ON r2.role_id = m2.role_id JOIN admin_user u ON u.id = m2.owner_id "
            f" WHERE m2.phone = m.phone {pf('r2')}) AS owner_names, "
            # 2026-11-13 冲刺模块一 MySQL 适配：原「GROUP_CONCAT(r.name || '（' || r.platform || '）', '、')」
            # 的 || 字符串拼接是 sqlite 专属（MySQL 不认）——role_detail 改由主查询后 Python 侧聚合补齐
            # （查 role 明细 → 按 phone 分组拼「名称（平台）」），双引擎零方言依赖。
            "(SELECT u.display_name FROM member m4 JOIN role r4 ON r4.role_id = m4.role_id "
            " JOIN admin_user u ON u.id = m4.owner_id "
            f" WHERE m4.phone = m.phone {pf('r4')} ORDER BY m4.id ASC LIMIT 1) AS first_owner_name, "
            "(SELECT u.username FROM member m4 JOIN role r4 ON r4.role_id = m4.role_id "
            " JOIN admin_user u ON u.id = m4.owner_id "
            f" WHERE m4.phone = m.phone {pf('r4')} ORDER BY m4.id ASC LIMIT 1) AS first_owner_username, "
            "(SELECT GROUP_CONCAT(DISTINCT dt.platform) FROM delivery_target dt "
            " WHERE dt.phone = m.phone AND dt.usable = 1) AS activated_platforms "
            "FROM member m "
            + ("JOIN role rm ON rm.role_id = m.role_id AND rm.platform = :plat " if use_plat else "")
            + "JOIN member p ON p.id = (SELECT MAX(p2.id) FROM member p2 "
            + ("JOIN role pr ON pr.role_id = p2.role_id WHERE p2.phone = m.phone AND pr.platform = :plat "
               if use_plat else "WHERE p2.phone = m.phone ")
            + ") "
        )
        params: Dict[str, Any] = {"size": int(size)}
        if use_plat:
            params["plat"] = plat
        if clean_q:
            if use_plat:
                sql += ("WHERE EXISTS (SELECT 1 FROM member x JOIN role xr ON xr.role_id = x.role_id "
                        "WHERE x.phone = m.phone AND xr.platform = :plat "
                        "AND (x.name LIKE :qname OR x.phone LIKE :qphone)) ")
            else:
                sql += ("WHERE EXISTS (SELECT 1 FROM member x WHERE x.phone = m.phone "
                        "AND (x.name LIKE :qname OR x.phone LIKE :qphone)) ")
            params["qname"] = f"%{clean_q}%"
            params["qphone"] = f"%{clean_q}%"
        sql += "GROUP BY m.phone ORDER BY display_name ASC, m.phone ASC LIMIT :size"
        out: List[Dict[str, Any]] = []
        with self.lock:
            for r in self.conn.execute(sql, params).fetchall():
                rows_n = int(r["row_count"] or 0)
                enabled_n = int(r["enabled_count"] or 0)
                status = (
                    self.POOL_STATUS_ACTIVE if enabled_n >= rows_n
                    else (self.POOL_STATUS_DISABLED if enabled_n == 0 else self.POOL_STATUS_PARTIAL)
                )
                out.append({
                    "phone": str(r["phone"]),
                    "display_name": str(r["display_name"] or ""),
                    "display_dept": str(r["display_dept"] or ""),
                    "status": status,
                    "row_count": rows_n,
                    "role_count": int(r["role_count"] or 0),
                    "admin_count": int(r["admin_count"] or 0),
                    "role_detail": "",  # 下方 Python 聚合补齐（双引擎零方言依赖）
                    "first_owner_name": str(r["first_owner_name"] or ""),
                    "first_owner_username": str(r["first_owner_username"] or ""),
                    "activated_platforms": [p for p in str(r["activated_platforms"] or "").split(",") if p],
                    "name_inconsistent": int(r["distinct_names"] or 0) > 1,
                    "dept_inconsistent": int(r["distinct_depts"] or 0) > 1,
                })
            # role_detail Python 侧聚合：查 role 明细按 phone 分组拼「名称（平台）」，
            # 与原 sqlite 的 GROUP_CONCAT(r.name || '（' || r.platform || '）', '、') 语义一致。
            if out:
                detail_by_phone: Dict[str, List[str]] = {}
                detail_rows = self.conn.execute(
                    "SELECT m.phone AS phone, r.name AS role_name, r.platform AS platform "
                    "FROM member m JOIN role r ON r.role_id = m.role_id "
                    + ("WHERE r.platform = ? " if use_plat else "")
                    + "ORDER BY m.id ASC",
                    (plat,) if use_plat else (),
                ).fetchall()
                for dr in detail_rows:
                    detail_by_phone.setdefault(str(dr["phone"]), []).append(
                        f"{dr['role_name']}（{dr['platform']}）")
                for item in out:
                    parts = detail_by_phone.get(item["phone"]) or []
                    item["role_detail"] = "、".join(parts)
        return out

    def _pool_require_phone(self, phone: str, platform: Optional[str] = None) -> str:
        clean = (phone or "").strip()
        if not clean:
            raise BizError(400, "VALIDATION_FAILED", "缺少手机号")
        plat = (platform or "").strip().lower()
        with self.lock:
            if plat:
                hit = self.conn.execute(
                    "SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                    "WHERE m.phone = ? AND r.platform = ? LIMIT 1", (clean, plat)).fetchone()
            else:
                hit = self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", (clean,)).fetchone()
            if not hit:
                raise BizError(404, "NOT_FOUND", f"该手机号不在{'该平台' if plat else '任何'}名册中")
        return clean

    def member_pool_set_status(self, phone: str, enabled: bool, actor: str = "system", platform: Optional[str] = None) -> Dict[str, Any]:
        """启停：作用域内该手机号全部名册行统一 enabled。

        2026-11-05 用户口径：超管操作同步全部管理员；2026-11-06 补平台口径：
        platform 传入时只作用于该平台的名册行（跨管理员），其它平台不动。
        """
        clean = self._pool_require_phone(phone, platform)
        plat = (platform or "").strip().lower()
        now_s = self.now_str()
        with self.lock, self.conn:
            if plat:
                cur = self.conn.execute(
                    "UPDATE member SET enabled = ? WHERE phone = ? "
                    "AND role_id IN (SELECT role_id FROM role WHERE platform = ?)",
                    (1 if enabled else 0, clean, plat))
            else:
                cur = self.conn.execute(
                    "UPDATE member SET enabled = ? WHERE phone = ?", (1 if enabled else 0, clean))
            n = int(cur.rowcount or 0)
            self._insert_audit_unlocked(
                actor, "member_pool.set_status", "member", clean,
                {"enabled": bool(enabled), "rows": n, "platform": plat or "all"}, now_s)
        return {"ok": True, "phone": clean, "rows": n}

    def member_pool_update_profile(self, phone: str, name: Optional[str], dept: Optional[str], actor: str = "system", platform: Optional[str] = None) -> Dict[str, Any]:
        """改姓名/部门：作用域内该手机号全部名册行同步同一值。⛔ 不收手机号字段，手机号永不可改（用户红线）。"""
        clean = self._pool_require_phone(phone, platform)
        plat = (platform or "").strip().lower()
        sets, params = [], []
        if name is not None:
            nm = str(name).strip()
            if not nm or len(nm) > 20:
                raise BizError(400, "VALIDATION_FAILED", "姓名需 1~20 个字")
            sets.append("name = ?")
            params.append(nm)
        if dept is not None:
            sets.append("dept = ?")
            params.append(str(dept).strip())
        if not sets:
            raise BizError(400, "VALIDATION_FAILED", "没有要修改的字段")
        now_s = self.now_str()
        with self.lock, self.conn:
            params.append(clean)
            if plat:
                params.append(plat)
                cur = self.conn.execute(
                    f"UPDATE member SET {', '.join(sets)} WHERE phone = ? "
                    "AND role_id IN (SELECT role_id FROM role WHERE platform = ?)", params)
            else:
                cur = self.conn.execute(f"UPDATE member SET {', '.join(sets)} WHERE phone = ?", params)
            n = int(cur.rowcount or 0)
            self._insert_audit_unlocked(
                actor, "member_pool.update_profile", "member", clean,
                {"name": name, "dept": dept, "rows": n, "platform": plat or "all"}, now_s)
        return {"ok": True, "phone": clean, "rows": n}

    def member_pool_delete(self, phone: str, actor: str = "system", platform: Optional[str] = None) -> Dict[str, Any]:
        """删除：作用域内该手机号全部名册行移除；绑定清理与 delete_role_member 同口径。

        platform 传入时只删该平台的行，且仅当该手机号在该平台再无任何角色归属时
        才清除该平台的激活绑定（人在其它平台的名册/绑定不受影响）。
        """
        clean = self._pool_require_phone(phone, platform)
        plat = (platform or "").strip().lower()
        now_s = self.now_str()
        removed_bindings: List[str] = []
        with self.lock, self.conn:
            if plat:
                n = int(self.conn.execute(
                    "DELETE FROM member WHERE phone = ? AND role_id IN (SELECT role_id FROM role WHERE platform = ?)",
                    (clean, plat)).rowcount or 0)
                still = self.conn.execute(
                    "SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                    "WHERE m.phone = ? AND r.platform = ? LIMIT 1", (clean, plat)).fetchone()
                if still is None:
                    self.conn.execute(
                        "DELETE FROM delivery_target WHERE phone = ? AND platform = ?", (clean, plat))
                    removed_bindings.append(plat)
            else:
                n = int(self.conn.execute("DELETE FROM member WHERE phone = ?", (clean,)).rowcount or 0)
                if self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", (clean,)).fetchone() is None:
                    for dt in self.conn.execute(
                        "SELECT DISTINCT platform FROM delivery_target WHERE phone = ?", (clean,)
                    ).fetchall():
                        removed_bindings.append(str(dt["platform"] or ""))
                    self.conn.execute("DELETE FROM delivery_target WHERE phone = ?", (clean,))
            self._insert_audit_unlocked(
                actor, "member_pool.delete", "member", clean,
                {"rows": n, "bindings_removed": removed_bindings, "platform": plat or "all"}, now_s)
        return {"ok": True, "phone": clean, "rows": n, "bindings_removed": removed_bindings}

    def update_admin_account(
        self,
        admin_id: int,
        display_name: Optional[str] = None,
        allowed_platforms: Optional[List[str]] = None,
        enabled: Optional[bool] = None,
        actor: str = "system",
    ) -> Dict[str, Any]:
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (int(admin_id),)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "管理员账号不存在")
            if str(row["username"]) == "admin":
                if enabled is False:
                    raise BizError(400, "VALIDATION_FAILED", "超级管理员账号不能停用")
                if allowed_platforms is not None:
                    raise BizError(400, "VALIDATION_FAILED", "超级管理员固定拥有全部平台权限")
            sets: List[str] = []
            params: List[Any] = []
            if display_name is not None:
                sets.append("display_name = ?")
                params.append((display_name or "").strip())
            if allowed_platforms is not None:
                plats = [str(p).strip().lower() for p in allowed_platforms if str(p).strip()]
                plats = [p for p in plats if p in self.ALL_PLATFORMS]
                if not plats:
                    raise BizError(400, "VALIDATION_FAILED", "至少要授权一个办公软件平台")
                sets.append("allowed_platforms = ?")
                params.append(json.dumps(plats, ensure_ascii=False))
            if enabled is not None:
                sets.append("enabled = ?")
                params.append(1 if enabled else 0)
            if sets:
                self.conn.execute(
                    f"UPDATE admin_user SET {', '.join(sets)} WHERE id = ?", tuple(params + [int(admin_id)])
                )
                self._insert_audit_unlocked(
                    actor, "admin.account.update", "admin_user", str(int(admin_id)),
                    {"display_name": display_name, "allowed_platforms": allowed_platforms, "enabled": enabled}, now_s,
                )
            if enabled is False:
                self.conn.execute("DELETE FROM admin_session WHERE user_id = ?", (int(admin_id),))
            row2 = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (int(admin_id),)).fetchone()
        return self.parse_admin_account(row2)

    def reset_admin_password(self, admin_id: int, actor: str = "system") -> Dict[str, Any]:
        """重置为初始密码 Hds22999! 并强制改密；同时踢掉该账号的在线会话。"""
        now_s = self.now_str()
        pwd = self.DEFAULT_SUB_ADMIN_PASSWORD
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (int(admin_id),)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "管理员账号不存在")
            self.conn.execute(
                "UPDATE admin_user SET pwd_hash = ?, must_change = 1 WHERE id = ?",
                (hash_password(pwd), int(admin_id)),
            )
            self.conn.execute("DELETE FROM admin_session WHERE user_id = ?", (int(admin_id),))
            self._insert_audit_unlocked(
                actor, "admin.account.reset_password", "admin_user", str(int(admin_id)),
                {"username": row["username"], "must_change": True}, now_s,
            )
        return {"ok": True, "username": str(row["username"]), "must_change_password": True}

    def backup_database(self, tag: str = "manual") -> str:
        """SQLite 文件级备份（先 WAL checkpoint 保证备份完整，落在 data/ 目录），返回备份路径。

        mysql 引擎：库内备份走宝塔/云快照体系，这里返回空串（不伪造备份）。
        """
        if getattr(self.db, "engine", "sqlite") == "mysql":
            return ""
        import shutil

        with self.lock:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            ts = self.now_str().replace(":", "").replace("-", "").replace(" ", "-")
            dest = self.db.db_path.parent / f"{self.db.db_path.name}.bak-{tag}-{ts}"
            shutil.copy2(self.db.db_path, dest)
        return str(dest)

    @staticmethod
    def _purge_upload_file(uploads_dir: Path, rel_path: str) -> bool:
        """删除 uploads 目录下的一个相对路径文件；路径越界（不在 uploads_dir 内）直接跳过并视为未删。"""
        rel = str(rel_path or "").strip()
        if not rel:
            return False
        try:
            target = (uploads_dir / rel).resolve()
            target.relative_to(uploads_dir.resolve())
        except (ValueError, OSError):
            return False
        try:
            if target.is_file():
                target.unlink()
                return True
        except OSError:
            return False
        return False

    def delete_admin_account(self, admin_id: int, actor: str = "system", uploads_dir: Optional[Path] = None) -> Dict[str, Any]:
        """级联删除分级管理员（2026-11-05 用户拍板：账号与名下数据全部物理删除，不可逆）。

        范围：会话 → 账号本体 → 名下角色/成员/模板（含软删）/实例 → 实例上的提交、
        上传引用、补交 token、提醒日志 → 磁盘附件文件（路径先校验在 uploads 目录内再删）。
        绑定（激活状态）**条件删**：手机号删完全平台已无任何 member 行才删（激活全局共享，
        别的管理员名册里还有此人时保留）；用 member 表反推，delivery_target 本身无 owner 概念。
        审计日志永不删；删除动作本身记审计 admin.account.purge 并带全部明细。
        """
        now_s = self.now_str()
        aid = int(admin_id)
        detail: Dict[str, int] = {}
        removed_bindings: List[str] = []
        removed_files = 0
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (aid,)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "管理员账号不存在")
            if str(row["username"]) == "admin":
                raise BizError(400, "VALIDATION_FAILED", "超级管理员账号不能删除")

            tpl_sql = "SELECT template_id FROM task_template WHERE owner_id = ?"
            inst_sql = "SELECT id FROM task_instance WHERE owner_id = ?"
            phones = [str(r[0]) for r in self.conn.execute(
                "SELECT DISTINCT phone FROM member WHERE owner_id = ?", (aid,)).fetchall()]

            # 磁盘附件：提交上传的 rel_path + 模板通知图 attach_image_path（先收集，事务内删文件，失败只计数不回滚）
            if uploads_dir is not None:
                rels = [str(r[0]) for r in self.conn.execute(
                    f"SELECT rel_path FROM upload_ref WHERE template_id IN ({tpl_sql})", (aid,)).fetchall()]
                rels += [str(r[0]) for r in self.conn.execute(
                    "SELECT attach_image_path FROM task_template WHERE owner_id = ? "
                    "AND COALESCE(attach_image_path, '') != ''", (aid,)).fetchall()]
                for rel in rels:
                    if self._purge_upload_file(uploads_dir, rel):
                        removed_files += 1

            # 子表（挂在名下模板/实例上的记录）
            detail["submission"] = int(self.conn.execute(
                f"DELETE FROM submission WHERE instance_id IN ({inst_sql})", (aid,)).rowcount or 0)
            detail["upload_ref"] = int(self.conn.execute(
                f"DELETE FROM upload_ref WHERE template_id IN ({tpl_sql})", (aid,)).rowcount or 0)
            detail["h5_token"] = int(self.conn.execute(
                f"DELETE FROM h5_token WHERE template_id IN ({tpl_sql})", (aid,)).rowcount or 0)
            detail["reminder_log"] = int(self.conn.execute(
                f"DELETE FROM reminder_log WHERE template_id IN ({tpl_sql})", (aid,)).rowcount or 0)
            # 业务主数据
            detail["task_instance"] = int(self.conn.execute(
                "DELETE FROM task_instance WHERE owner_id = ?", (aid,)).rowcount or 0)
            detail["task_template"] = int(self.conn.execute(
                "DELETE FROM task_template WHERE owner_id = ?", (aid,)).rowcount or 0)
            detail["member"] = int(self.conn.execute(
                "DELETE FROM member WHERE owner_id = ?", (aid,)).rowcount or 0)
            detail["role"] = int(self.conn.execute(
                "DELETE FROM role WHERE owner_id = ?", (aid,)).rowcount or 0)
            # 会话 + 账号本体
            detail["admin_session"] = int(self.conn.execute(
                "DELETE FROM admin_session WHERE user_id = ?", (aid,)).rowcount or 0)
            self.conn.execute("DELETE FROM admin_user WHERE id = ?", (aid,))
            # 绑定条件删：全平台已无任何 member 行的手机号，绑定（所有平台）一并清除
            for ph in phones:
                if self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", (ph,)).fetchone() is None:
                    self.conn.execute("DELETE FROM delivery_target WHERE phone = ?", (ph,))
                    removed_bindings.append(ph)

            self._insert_audit_unlocked(
                actor, "admin.account.purge", "admin_user", str(aid),
                {"username": str(row["username"]), "deleted": detail,
                 "bindings_removed": removed_bindings, "attachments_removed": removed_files}, now_s,
            )
        return {"ok": True, "username": str(row["username"]), "deleted": detail,
                "bindings_removed": removed_bindings, "attachments_removed": removed_files}

    def transfer_owner_between(self, from_admin: int, to_admin: int, actor: str = "system") -> Dict[str, Any]:
        """定向转移：把 from 管理员名下的全部业务数据挂到 to 名下（删除前「先转移」选项用）。

        与 transfer_owner 不同：transfer_owner 是「全平台数据归并到一人」的历史迁移函数，
        不能用于账号间定向转移（会连别人的数据一起转走）。
        """
        fid, tid = int(from_admin), int(to_admin)
        now_s = self.now_str()
        with self.lock, self.conn:
            src = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (fid,)).fetchone()
            dst = self.conn.execute("SELECT * FROM admin_user WHERE id = ?", (tid,)).fetchone()
            if not src:
                raise BizError(404, "NOT_FOUND", "源管理员账号不存在")
            if not dst:
                raise BizError(404, "NOT_FOUND", "目标管理员账号不存在")
            if fid == tid:
                raise BizError(400, "VALIDATION_FAILED", "源与目标不能是同一个账号")
            if str(dst["role_level"] or "") == self.SUPER_ADMIN:
                raise BizError(400, "VALIDATION_FAILED", "业务数据不能转移到超级管理员名下（超管名下保持为空）")
            moved: Dict[str, int] = {}
            for tbl in ("role", "member", "task_template", "task_instance"):
                cur = self.conn.execute(
                    f"UPDATE {tbl} SET owner_id = ? WHERE owner_id = ?", (tid, fid))
                moved[tbl] = int(cur.rowcount or 0)
            self._insert_audit_unlocked(
                actor, "admin.account.transfer_between", "admin_user", f"{fid}->{tid}",
                {"from": str(src["username"]), "to": str(dst["username"]), "moved": moved}, now_s,
            )
        return {"ok": True, "from": str(src["username"]), "to": str(dst["username"]), "moved": moved}

    def transfer_owner(self, owner_id: int, actor: str = "system") -> Dict[str, Any]:
        """把历史业务数据（角色/成员/模板/实例）整体挂到某个管理员名下。"""
        now_s = self.now_str()
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM admin_user WHERE id = ?", (int(owner_id),)).fetchone():
                raise BizError(404, "NOT_FOUND", "目标管理员账号不存在")
            moved: Dict[str, int] = {}
            for tbl in ("role", "member", "task_template", "task_instance"):
                cur = self.conn.execute(f"UPDATE {tbl} SET owner_id = ? WHERE owner_id != ?", (int(owner_id), int(owner_id)))
                moved[tbl] = int(cur.rowcount or 0)
            self._insert_audit_unlocked(actor, "admin.account.transfer_owner", "admin_user", str(int(owner_id)),
                                        {"moved": moved}, now_s)
        return {"ok": True, "owner_id": int(owner_id), "moved": moved}

    def search_global_activated(self, q: str = "", platform: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """全局搜索**已激活**（delivery_target 有绑定）的成员 —— 跨管理员共享，免二次激活。

        2026-11-09 修正（用户口径「全量池也要做办公软件隔离」）：
        激活关系是**按平台分行**的（delivery_target.platform），所以查询主体按 platform 过滤；
        但**姓名/部门必须也从该平台的 member 行取**，否则会出现「企微视角下显示密信那条的
        姓名/部门」的串台（实测：企微视角搜到「郑佳 / 云中台」，而那条信息属于量子密信）。
        现在 name/dept 一律按 `member JOIN role ON role.platform = 平台` 取；
        按姓名搜索的 EXISTS 子查询同样按平台收口，避免搜到别的平台的名字。
        """
        plat = (platform or "").strip().lower()
        like = f"%{(q or '').strip()}%"
        where = ["dt.usable = 1", "dt.external_id IS NOT NULL", "dt.external_id != ''"]
        params: List[Any] = []
        if plat:
            where.append("dt.platform = ?")
            params.append(plat)
        if (q or "").strip():
            if plat:
                # 姓名匹配收口到本平台的名册（否则密信的名字能被企微搜到）
                where.append(
                    "(dt.phone LIKE ? OR EXISTS (SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                    "WHERE m.phone = dt.phone AND r.platform = ? AND m.name LIKE ?))"
                )
                params.extend([like, plat, like])
            else:
                where.append("(dt.phone LIKE ? OR EXISTS (SELECT 1 FROM member m WHERE m.phone = dt.phone AND m.name LIKE ?))")
                params.extend([like, like])
        sql = (
            "SELECT dt.platform, dt.phone, dt.external_id, dt.activated_at, dt.last_event_at "
            "FROM delivery_target dt WHERE " + " AND ".join(where) +
            " ORDER BY dt.last_event_at DESC LIMIT ?"
        )
        with self.lock:
            rows = self.conn.execute(sql, tuple(params + [int(limit)])).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                if plat:
                    # 姓名/部门取**本平台**的名册行（不串台到别的平台）
                    m = self.conn.execute(
                        "SELECT m.name, m.dept FROM member m JOIN role r ON r.role_id = m.role_id "
                        "WHERE m.phone = ? AND r.platform = ? ORDER BY m.id DESC LIMIT 1",
                        (r["phone"], plat),
                    ).fetchone()
                else:
                    m = self.conn.execute(
                        "SELECT name, dept FROM member WHERE phone = ? ORDER BY id DESC LIMIT 1", (r["phone"],)
                    ).fetchone()
                out.append({
                    "phone": r["phone"], "platform": r["platform"], "external_id": r["external_id"],
                    "name": (m["name"] if m else "") or "", "dept": (m["dept"] if m else "") or "",
                    "activated_at": r["activated_at"], "last_event_at": r["last_event_at"],
                })
        return out

    def is_phone_activated(self, phone: str, platform: Optional[str] = None) -> bool:
        """该手机号在（指定）平台是否已被激活（全局共享，跨管理员同样成立）。

        ⚠️ 2026-11-04 修正：platform 传 None / 空 = **任意平台已激活即可**，
        而不是回退到「当前通道」（原来走 _plat(None) → store.platform，
        于是「在企微激活过的人」在密信侧被判成未激活，与"激活关系全局共享"矛盾）。
        要限定平台时显式传 platform。
        """
        clean = (phone or "").strip()
        if not clean:
            return False
        plat = (platform or "").strip().lower()
        with self.lock:
            if plat:
                return self.conn.execute(
                    "SELECT 1 FROM delivery_target WHERE phone = ? AND platform = ? AND usable = 1 "
                    "AND external_id IS NOT NULL AND external_id != '' LIMIT 1",
                    (clean, plat),
                ).fetchone() is not None
            return self.conn.execute(
                "SELECT 1 FROM delivery_target WHERE phone = ? AND usable = 1 "
                "AND external_id IS NOT NULL AND external_id != '' LIMIT 1",
                (clean,),
            ).fetchone() is not None


    def external_id_for(self, phone: str, platform: Optional[str] = None) -> Optional[str]:
        """取该手机号在（指定 / 角色所属）平台的绑定 uid；没有则 None。

        2026-11-04：platform 传 None = 角色所属平台由调用方显式给出；
        这里只负责「取绑定」，不猜通道。
        """
        clean = (phone or "").strip()
        plat = (platform or "").strip().lower()
        if not clean or not plat:
            return None
        with self.lock:
            row = self.conn.execute(
                "SELECT external_id FROM delivery_target WHERE platform = ? AND phone = ? AND usable = 1 LIMIT 1",
                (plat, clean),
            ).fetchone()
        return (row["external_id"] if row else None) or None

    def is_phone_in_members(self, phone: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", ((phone or "").strip(),)).fetchone() is not None

    def upsert_delivery_target(self, phone: str, external_id: str, platform: Optional[str] = None,
                                activated_at: Optional[str] = None, external_type: str = "user") -> Dict[str, Any]:
        """建立/更新平台绑定。

        activated_at：**首次激活时刻**（'YYYY-MM-DD HH:MM:SS'）。
          · 不传 → 用当前时间；已存在绑定时**保留原值**（首次激活时刻不可被后续事件刷掉）
          · 传入 → 显式指定（补录历史激活记录 / 迁移历史数据时用）
        2026-11-09 新口径「当天激活的只创建激活时刻之后的任务」依赖这个字段。

        external_type：'user'（默认，IM 会话 userId）/ 'union_id'（通讯录键，官方口径可不建会话
        直接投递）。2026-11-10 方案A 起由导入/识别路径写入 'union_id'；存量行保持 'user'。
        """
        platform = self._plat(platform)
        clean_phone, clean_ext = (phone or "").strip(), (external_id or "").strip()
        type_key = str(external_type or "user").strip().lower() or "user"
        if not is_valid_phone(clean_phone):
            raise BizError(400, "PHONE_INVALID", "手机号格式不正确")
        if not clean_ext:
            raise BizError(400, "VALIDATION_FAILED", "external_id 不能为空")
        now_s = self.now_str()
        explicit_act = str(activated_at).strip() if activated_at else ""
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT activated_at FROM delivery_target WHERE platform = ? AND phone = ?", (platform, clean_phone)).fetchone()
            if existing:
                activated_at = existing["activated_at"]
                self.conn.execute(
                    "UPDATE delivery_target SET external_id = ?, external_type = ?, usable = 1, last_event_at = ? "
                    "WHERE platform = ? AND phone = ?",
                    (clean_ext, type_key, now_s, platform, clean_phone),
                )
            else:
                activated_at = explicit_act or now_s
                self.conn.execute(
                    "INSERT INTO delivery_target(platform, phone, external_id, external_type, usable, activated_at, last_event_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (platform, clean_phone, clean_ext, type_key, activated_at, now_s),
                )
            return {"platform": platform, "phone": clean_phone, "external_id": clean_ext,
                    "external_type": type_key, "usable": 1, "activated_at": activated_at, "last_event_at": now_s}

    def bind_member_with_guardrails(self, phone: str, external_id: str, path: str, platform: Optional[str] = None,
                                    external_type: str = "user") -> Dict[str, Any]:
        """v1.1 激活绑定（路径 A/B/C/U）与三条护栏：在册校验 + 防覆盖校验 + 脱敏回执与审计。
        若原记录因 500 无会话被置为 usable=0，重新绑定成功时恢复 usable=1 并保留原 activated_at。

        2026-11-10 方案A：external_type 随绑定写入（路径 U = 首条消息按 union_id 自动识别）。
        """
        platform = self._plat(platform)
        clean_phone, clean_ext, path_code = (phone or "").strip(), (external_id or "").strip(), (path or "B").strip().upper()
        type_key = str(external_type or "user").strip().lower() or "user"
        if not is_valid_phone(clean_phone):
            raise BizError(400, "PHONE_INVALID", "手机号格式不正确")
        if not clean_ext:
            raise BizError(400, "VALIDATION_FAILED", "缺少用户标识 external_id")
        now_s = self.now_str()
        with self.lock, self.conn:
            if not self.phone_in_roster(clean_phone, platform=platform):
                raise BizError(400, "VALIDATION_FAILED", "该手机号不在当前办公软件的任务名单中，请联系管理员核实")
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
                    "UPDATE delivery_target SET external_id = ?, external_type = ?, usable = 1, last_event_at = ? "
                    "WHERE platform = ? AND phone = ?",
                    (clean_ext, type_key, now_s, platform, clean_phone),
                )
            else:
                self.conn.execute(
                    "INSERT INTO delivery_target(platform, phone, external_id, external_type, usable, activated_at, last_event_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (platform, clean_phone, clean_ext, type_key, now_s, now_s),
                )

            masked = mask_phone(clean_phone)
            receipt = f"绑定成功：当前账号已关联手机号 {masked}。如非本人操作，请立即联系管理员申诉处理。"
            self._insert_audit_unlocked("system", f"target.bind_{path_code.lower()}", "delivery_target", clean_phone,
                                        {"masked_phone": masked, "external_id": clean_ext, "external_type": type_key, "path": path_code}, now_s)
            return {"ok": True, "already_same": False, "phone": clean_phone, "masked_phone": masked, "external_id": clean_ext, "receipt": receipt}

    def touch_delivery_target_by_external_id(self, external_id: str, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
        platform = self._plat(platform)
        clean_ext = (external_id or "").strip()
        if not clean_ext:
            return None
        now_s = self.now_str()
        with self.lock, self.conn:
            # 2026-10-29 补丁1：只有「usable=1 且手机号仍在有效名册中」才算已绑定成员。
            # 之前这里不看 usable、也不看 member 表，被移出/移除的人发一句话就会被静默复活成
            # usable=1，并且 return 掉绑分支，永远走不到「自动进待归纳池」那一步。
            row = self.conn.execute(
                "SELECT * FROM delivery_target WHERE platform = ? AND external_id = ? AND usable = 1",
                (platform, clean_ext),
            ).fetchone()
            if not row:
                return None
            in_roster = self.conn.execute(
                "SELECT 1 FROM member WHERE phone = ? LIMIT 1",
                (row["phone"],),
            ).fetchone()
            if not in_roster:
                return None
            self.conn.execute(
                "UPDATE delivery_target SET last_event_at = ? WHERE platform = ? AND phone = ?",
                (now_s, platform, row["phone"]),
            )
            return {"platform": row["platform"], "phone": row["phone"], "external_id": row["external_id"], "usable": 1, "activated_at": row["activated_at"], "last_event_at": now_s}

    def get_delivery_target(self, phone: str, platform: Optional[str] = None, include_unusable: bool = False) -> Optional[Dict[str, Any]]:
        platform = self._plat(platform)
        with self.lock:
            sql = "SELECT * FROM delivery_target WHERE platform = ? AND phone = ?" + ("" if include_unusable else " AND usable = 1")
            row = self.conn.execute(sql, (platform, (phone or "").strip())).fetchone()
            return dict(row) if row else None

    def get_delivery_target_by_external_id(self, external_id: str, platform: Optional[str] = None, include_unusable: bool = False) -> Optional[Dict[str, Any]]:
        platform = self._plat(platform)
        with self.lock:
            sql = "SELECT * FROM delivery_target WHERE platform = ? AND external_id = ?" + ("" if include_unusable else " AND usable = 1")
            row = self.conn.execute(sql, (platform, (external_id or "").strip())).fetchone()
            return dict(row) if row else None

    def mark_delivery_target_unusable(self, phone: str, platform: Optional[str] = None) -> None:
        """发送返回 500 无会话时标记 usable=0，保留 external_id 与 activated_at 供排障。"""
        platform = self._plat(platform)
        with self.lock, self.conn:
            self.conn.execute("UPDATE delivery_target SET usable = 0 WHERE platform = ? AND phone = ?", (platform, (phone or "").strip()))

    def delete_delivery_target(self, phone: str, platform: Optional[str] = None) -> None:
        """兼容性别名：将绑定标记为不可用 (usable=0)，不物理抹除 external_id 与 activated_at。"""
        self.mark_delivery_target_unusable(phone, platform=platform)

    def _generate_unique_id_unlocked(self, table: str, id_col: str, prefix: str) -> str:
        while True:
            candidate = f"{prefix}{secrets.token_hex(4)}"
            if not self.conn.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ?", (candidate,)).fetchone():
                return candidate

    def list_roles(self, platform: Optional[str] = None, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        plat = (platform or "").strip().lower()
        where_clauses, params = [], []
        if plat:
            where_clauses.append("r.platform = ?")
            params.append(plat)
        if owner_id is not None:
            where_clauses.append("r.owner_id = ?")
            params.append(int(owner_id))
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        with self.lock:
            rows = self.conn.execute(
                "SELECT r.role_id, r.name, r.note, r.platform, r.created_at, r.owner_id, COUNT(m.id) AS member_count, "
                "u.username AS owner_username, u.display_name AS owner_display_name "
                f"FROM role r LEFT JOIN member m ON m.role_id = r.role_id "
                f"LEFT JOIN admin_user u ON u.id = r.owner_id {where_sql} "
                "GROUP BY r.role_id ORDER BY r.created_at ASC, r.role_id ASC",
                tuple(params),
            ).fetchall()
            return [{"role_id": r["role_id"], "name": r["name"], "note": r["note"], "platform": r["platform"], "created_at": r["created_at"], "owner_id": int(r["owner_id"] or 1), "owner_username": r["owner_username"], "owner_display_name": r["owner_display_name"], "member_count": int(r["member_count"] or 0)} for r in rows]

    def role_platforms(self, role_ids: List[str]) -> set:
        """这组角色覆盖到的办公软件集合（用于判断任务归属平台）。"""
        ids = [str(r or "").strip() for r in (role_ids or []) if str(r or "").strip()]
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT DISTINCT platform FROM role WHERE role_id IN ({placeholders})", tuple(ids)
            ).fetchall()
        return {str(r["platform"]).strip().lower() for r in rows}

    def get_binding_by_external_id(self, external_id: str, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """按 external_id 反查可用绑定（2026-11-03：登记页防重复提交用）。"""
        clean_ext = (external_id or "").strip()
        if not clean_ext:
            return None
        plat = (platform or "").strip().lower()
        with self.lock:
            if plat:
                row = self.conn.execute(
                    "SELECT * FROM delivery_target WHERE platform = ? AND external_id = ? AND usable = 1",
                    (plat, clean_ext),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM delivery_target WHERE external_id = ? AND usable = 1", (clean_ext,)
                ).fetchone()
        return dict(row) if row else None

    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT r.*, (SELECT COUNT(1) FROM member m WHERE m.role_id = r.role_id) AS member_count FROM role r WHERE r.role_id = ?", ((role_id or "").strip(),)).fetchone()
            return {"role_id": row["role_id"], "name": row["name"], "note": row["note"], "platform": row["platform"], "created_at": row["created_at"], "member_count": int(row["member_count"] or 0)} if row else None

    def create_role(self, name: str, note: Optional[str] = None, actor: str = "system", platform: str = "zdxlz", owner_id: int = 1) -> Dict[str, Any]:
        clean_name = (name or "").strip()
        plat = self._plat(platform)
        if not clean_name:
            raise BizError(400, "VALIDATION_FAILED", "角色名称不能为空")
        clean_note, now_s = (str(note).strip() if note is not None and str(note).strip() else None), self.now_str()
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM role WHERE name = ? AND platform = ?", (clean_name, plat)).fetchone():
                raise BizError(409, "ROLE_NAME_TAKEN", "该办公软件下角色名称已存在")
            role_id = self._generate_unique_id_unlocked("role", "role_id", "r_")
            self.conn.execute("INSERT INTO role(role_id, name, note, platform, created_at, owner_id) VALUES (?, ?, ?, ?, ?, ?)", (role_id, clean_name, clean_note, plat, now_s, int(owner_id)))
            self._insert_audit_unlocked(actor, "role.create", "role", role_id, {"name": clean_name, "note": clean_note, "platform": plat}, now_s)
            return {"role_id": role_id, "name": clean_name, "note": clean_note, "platform": plat, "created_at": now_s, "member_count": 0, "owner_id": int(owner_id)}

    def update_role(self, role_id: str, name: str, note: Optional[str] = None, actor: str = "system") -> Dict[str, Any]:
        clean_id, clean_name = (role_id or "").strip(), (name or "").strip()
        if not clean_name:
            raise BizError(400, "VALIDATION_FAILED", "角色名称不能为空")
        clean_note, now_s = (str(note).strip() if note is not None and str(note).strip() else None), self.now_str()
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT * FROM role WHERE role_id = ?", (clean_id,)).fetchone()
            if not existing:
                raise BizError(404, "NOT_FOUND", "角色不存在")
            if self.conn.execute("SELECT 1 FROM role WHERE name = ? AND platform = ? AND role_id != ?", (clean_name, existing["platform"], clean_id)).fetchone():
                raise BizError(409, "ROLE_NAME_TAKEN", "该办公软件下角色名称已存在")
            self.conn.execute("UPDATE role SET name = ?, note = ? WHERE role_id = ?", (clean_name, clean_note, clean_id))
            cnt = int(self.conn.execute("SELECT COUNT(1) AS cnt FROM member WHERE role_id = ?", (clean_id,)).fetchone()["cnt"] or 0)
            self._insert_audit_unlocked(actor, "role.update", "role", clean_id, {"name": clean_name, "note": clean_note}, now_s)
            return {"role_id": clean_id, "name": clean_name, "note": clean_note, "platform": existing["platform"], "created_at": existing["created_at"], "member_count": cnt}

    def delete_role(self, role_id: str, actor: str = "system") -> Dict[str, Any]:
        """2026-11-12 下午（用户拍板）：删除角色分组 = **组内全部成员一并清除**。

        旧实现在「组里还有成员」时 409 拦截，用户必须逐个删成员才能删组——不符合
        「删分组即清全组」的预期（且清库迁移前这轮就是要整组清掉）。
        新口径：
        · 组内每个成员按 `delete_role_member` 同款语义处理：名册行物理删除、
          绑定条件删（该平台已无其它角色归属才删）、该模板实例按 C 口径处置、逐条审计；
        · ⛔ 若该角色仍被**任务模板引用** → 仍然 409 拦下（提示先删模板），
          避免删组连带毁掉别的角色的模板/实例（模板删除走独立的物理清除口径）。
        · 返回删除明细（成员数、绑定数、实例数），前端可提示「需重新导入」。
        """
        clean_id, now_s = (role_id or "").strip(), self.now_str()
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT * FROM role WHERE role_id = ?", (clean_id,)).fetchone()
            if not existing:
                raise BizError(404, "NOT_FOUND", "角色不存在")
            # 2026-11-12 晚 · 需求四加固：规范解析（JSON 数组 / 逗号分隔统一提取），
            # 解析不出 token 时按「原始字符串包含该 role_id」保守拦截 —— 杜绝漏判误删。
            for trow in self.conn.execute("SELECT role_ids FROM task_template WHERE deleted = 0").fetchall():
                _raw_ids = str(trow["role_ids"] or "")
                rids = parse_role_ids(_raw_ids)
                if clean_id in rids or (not rids and _raw_ids and clean_id in _raw_ids):
                    raise BizError(409, "ROLE_IN_USE", "该角色已被任务模板引用，请先删除相关任务模板")
            phones = [str(r[0]) for r in self.conn.execute(
                "SELECT phone FROM member WHERE role_id = ? ORDER BY id ASC", (clean_id,)).fetchall()]
            deleted_members = 0
            removed_bindings: List[str] = []
            cancelled_instances = 0
            removed_instances = 0
            for ph in phones:
                res = self._delete_role_member_unlocked(clean_id, ph, actor, now_s, write_audit=False)
                deleted_members += 1
                removed_bindings.extend(res.get("removed_bindings") or [])
                cancelled_instances += int(res.get("cancelled_instances") or 0)
                removed_instances += int(res.get("removed_instances") or 0)
            self.conn.execute("DELETE FROM role WHERE role_id = ?", (clean_id,))
            self._insert_audit_unlocked(
                actor, "role.purge", "role", clean_id,
                {"name": existing["name"], "deleted_members": deleted_members,
                 "bindings_removed": removed_bindings,
                 "cancelled_instances": cancelled_instances,
                 "removed_instances": removed_instances}, now_s,
            )
        return {
            "ok": True, "name": existing["name"], "deleted_members": deleted_members,
            "bindings_removed": removed_bindings,
            "cancelled_instances": cancelled_instances,
            "removed_instances": removed_instances,
        }

    def list_role_members(self, role_id: str, q: Optional[str] = None, page: int = 1, size: int = 20, platform: Optional[str] = None) -> Dict[str, Any]:
        clean_role_id = (role_id or "").strip()
        plat = self._plat(platform)
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
                f"SELECT m.*, dtz.external_id AS ext_zdxlz, dtw.external_id AS ext_wecom FROM member m "
                f"LEFT JOIN delivery_target dtz ON dtz.platform = 'zdxlz' AND dtz.phone = m.phone AND dtz.usable = 1 "
                f"LEFT JOIN delivery_target dtw ON dtw.platform = 'wecom' AND dtw.phone = m.phone AND dtw.usable = 1 "
                f"{where_sql} ORDER BY m.id ASC LIMIT ? OFFSET ?",
                tuple(params + [s, offset]),
            ).fetchall()
            items = []
            for r in rows:
                # activated 保持旧语义：跟随当前通道（兼容既有调用/测试）；
                # activations 给多通道 UI 展示「密信/企微各自是否已激活」。
                act = {
                    "zdxlz": bool(r["ext_zdxlz"]),
                    "wecom": bool(r["ext_wecom"]),
                }
                activated = act.get(plat, act["zdxlz"] or act["wecom"])
                items.append({**_fmt_member(
                    r["id"], r["role_id"], r["phone"], r["name"], r["dept"], r["enabled"],
                    r["source"], r["created_at"], activated, activations=act,
                ), "owner_id": int(r["owner_id"] or 1)})
            # 2026-11-10：附带角色归属 owner，前端据此判定「我是否为该成员的创建者」
            # （姓名/部门修改权限矩阵：超管 或 创建者可改）。
            _role_row = self.conn.execute("SELECT owner_id FROM role WHERE role_id = ?", (clean_role_id,)).fetchone()
            return {"total": total, "items": items, "role_owner_id": int(_role_row["owner_id"] or 1) if _role_row is not None else None}

    def list_roles_of_phone(self, phone: str) -> List[Dict[str, Any]]:
        """2026-10-29：某手机号当前所在的所有角色（一人多角色场景，前端用来置灰「已加入」的角色）。"""
        clean_phone = (phone or "").strip()
        if not clean_phone:
            return []
        with self.lock:
            rows = self.conn.execute(
                "SELECT m.role_id, r.name AS role_name, m.enabled FROM member m "
                "JOIN role r ON r.role_id = m.role_id WHERE m.phone = ? ORDER BY r.name",
                (clean_phone,),
            ).fetchall()
        return [
            {"role_id": r["role_id"], "role_name": r["role_name"], "enabled": bool(r["enabled"])}
            for r in rows
        ]

    def add_role_member(self, role_id: str, phone: str, name: str, dept: Optional[str] = None, source: str = "manual", actor: str = "system", owner_id: int = 1) -> Dict[str, Any]:
        """2026-11-11 用户拍板：快速添加**只收姓名+手机号**，部门由官方通讯录反查回填
        （末级部门进 dept 展示列、全路径进 dept_path 供层级统计归集）；
        显式传 dept 的旧调用方仍兼容（dept_path 反查补全）。"""
        clean_role_id, clean_phone, clean_name = (role_id or "").strip(), (phone or "").strip(), (name or "").strip()
        clean_dept = str(dept).strip() if dept is not None and str(dept).strip() else None
        clean_path: Optional[str] = None
        if not is_valid_phone(clean_phone):
            raise BizError(400, "PHONE_INVALID", "手机号格式不正确")
        if not clean_name:
            raise BizError(400, "VALIDATION_FAILED", "姓名不能为空")
        now_s = self.now_str()
        # 2026-11-11：官方通讯录反查官方部门（锁外做网络查询）——只在调用方没给部门时反查
        _role_plat_pre = self.conn.execute("SELECT platform FROM role WHERE role_id = ?", (clean_role_id,)).fetchone()
        _plat_key_pre = str((_role_plat_pre["platform"] if _role_plat_pre else "") or self.platform).lower()
        if clean_dept is None and _plat_key_pre == "zdxlz":
            zdxlz_c = getattr(self, "contacts", None)
            if zdxlz_c is not None:
                try:
                    _u = zdxlz_c.search_by_phone(clean_phone)
                except Exception:  # noqa: BLE001 - 反查失败不阻塞加人
                    _u = None
                if _u:
                    clean_dept = str(_u.get("department") or "").strip() or None
                    clean_path = str(_u.get("dept_path") or "").strip() or None
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM role WHERE role_id = ?", (clean_role_id,)).fetchone():
                raise BizError(404, "NOT_FOUND", "角色不存在")
            if self.conn.execute("SELECT 1 FROM member WHERE role_id = ? AND phone = ?", (clean_role_id, clean_phone)).fetchone():
                raise BizError(409, "DUPLICATE", "该角色下手机号已存在")
            cur = self.conn.execute("INSERT INTO member(role_id, phone, name, dept, dept_path, enabled, source, created_at, owner_id) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)", (clean_role_id, clean_phone, clean_name, clean_dept, clean_path, source, now_s, int(owner_id)))
            # 2026-11-04：这里原来跟随「当前通道」(self.platform)，导致在企微面板给密信角色
            # 加成员时取错通道 → 「激活状态」显示不对。改按**角色所属平台**判定（业务语义）。
            _role_plat = self.conn.execute("SELECT platform FROM role WHERE role_id = ?", (clean_role_id,)).fetchone()
            _plat_key = str(_role_plat["platform"] or "zdxlz").lower() if _role_plat else self.platform
            _ext = self.external_id_for(clean_phone, _plat_key)
            self._insert_audit_unlocked(actor, "member.create", "member", f"{clean_role_id}:{clean_phone}", {"name": clean_name, "dept": clean_dept, "dept_path": clean_path, "source": source}, now_s)
            member = _fmt_member(int(cur.lastrowid), clean_role_id, clean_phone, clean_name, clean_dept, True, source, now_s, _ext)

        # 2026-11-10 方案A：单个加人也自动激活（zdxlz/企微均适用；查不到/未装配时静默跳过）
        self._auto_activate_imported([(clean_phone, clean_name, clean_dept, clean_path)], platform=_plat_key, actor=actor)
        return member

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
            # 2026-11-04：同 add_role_member —— 按**角色所属平台**判定激活状态。
            _role_plat = self.conn.execute("SELECT platform FROM role WHERE role_id = ?", (clean_role_id,)).fetchone()
            _plat_key = str(_role_plat["platform"] or "zdxlz").lower() if _role_plat else self.platform
            _ext = self.external_id_for(clean_phone, _plat_key)
            self._insert_audit_unlocked(actor, "member.update", "member", f"{clean_role_id}:{clean_phone}", {"name": new_name, "dept": new_dept, "enabled": bool(new_enabled)}, now_s)
            return _fmt_member(int(row["id"]), clean_role_id, clean_phone, new_name, new_dept, new_enabled, row["source"], row["created_at"], _ext)

    def delete_role_member(self, role_id: str, phone: str, actor: str = "system") -> None:
        """从分组移除单个成员（公开入口：自带事务与审计；核心逻辑在 _delete_role_member_unlocked，
        delete_role 级联删除与批量删除复用同一份逻辑，保证口径完全一致）。"""
        clean_role_id, clean_phone, now_s = (role_id or "").strip(), (phone or "").strip(), self.now_str()
        with self.lock, self.conn:
            self._delete_role_member_unlocked(clean_role_id, clean_phone, actor, now_s)

    def _delete_role_member_unlocked(
        self, clean_role_id: str, clean_phone: str, actor: str, now_s: str, *, write_audit: bool = True
    ) -> Dict[str, Any]:
        """⚠️ 调用方必须已握 self.lock 且处于 self.conn 事务内。

        移除成员的完整语义（2026-11-03 / 11-09 / 11-12 任务C 口径的合并实现）：
        名册行删除 → 绑定条件删（该平台无其它角色归属才删）→ 该模板实例处置
        （未完成转 cancelled 留痕、已完成物理删除）。write_audit=False 供级联删除
        汇总记一条审计（role.purge / member.batch_delete）而不是逐人一条。
        """
        row = self.conn.execute("SELECT * FROM member WHERE role_id = ? AND phone = ?", (clean_role_id, clean_phone)).fetchone()
        if not row:
            raise BizError(404, "NOT_FOUND", "成员不存在")
        self.conn.execute("DELETE FROM member WHERE id = ?", (int(row["id"]),))
        # 2026-11-03 新激活模型（用户拍板：移除=彻底删除）：按「绑定所在平台」逐条判断，
        # 该平台已无任何角色归属时，**彻底删除**该平台的绑定记录（不再有停用态、也不回待归纳池），
        # 用户需重新给机器人发消息、走 H5 登记重新激活（比对新版后台名册）。
        # 人在企微角色里、只是退出密信角色时：只删密信绑定，企微绑定不动。
        removed_bindings: List[str] = []
        for dt in self.conn.execute(
            "SELECT platform, external_id FROM delivery_target WHERE phone = ? AND usable = 1",
            (clean_phone,),
        ).fetchall():
            plat = str(dt["platform"] or "").strip().lower()
            still_in_plat_role = self.conn.execute(
                "SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                "WHERE m.phone = ? AND r.platform = ? LIMIT 1",
                (clean_phone, plat),
            ).fetchone() is not None
            if still_in_plat_role:
                continue
            ext_id = str(dt["external_id"] or "").strip()
            if not ext_id:
                continue
            self.conn.execute(
                "DELETE FROM delivery_target WHERE platform = ? AND external_id = ?",
                (plat, ext_id),
            )
            removed_bindings.append(f"{plat}:{ext_id[-6:]}")

        # 2026-11-03 用户口径：从分组移除后，该用户在该分组模板下的任务实例一并清掉，
        # 避免人已移出、到点还收到残留实例的提醒（11:50 上午下班打卡实锤）。
        # 只清「该模板的所有角色都已不含此人」的实例——一人多角色时，只要还挂在同模板的其它角色，实例保留。
        #
        # 2026-11-09 用户口径（修正）：**移除要删干净**。
        # 此前只删 status IN ('pending','submitted','overdue','rejected')，
        # 已 sent/approved/expired 的实例保留 —— 结果「人已从项目经理组移出，
        # 但模板下仍有她的 sent 实例」，用户看到的即是这个不一致。
        #
        # 2026-11-12 任务C（图二实锤口径修正）：**未完成的实例不再物理删除，
        # 改标 cancelled（已取消）** —— 后台任务实例页可见「已取消」徽章（留痕可审计），
        # cancelled 不再触发提醒/逾期通知/流转。已完成/已归档实例保持 11-09「移除删干净」口径。
        cancelled_instance_ids: List[int] = []
        removed_instance_ids: List[int] = []
        open_rows = self.conn.execute(
            "SELECT id, template_id, status FROM task_instance WHERE phone = ?",
            (clean_phone,),
        ).fetchall()
        for inst in open_rows:
            trow = self.conn.execute(
                "SELECT role_ids FROM task_template WHERE template_id = ?", (inst["template_id"],)
            ).fetchone()
            # 2026-11-12 晚 · 需求四加固：同款规范解析（脏数据兜底切分）
            _raw_tpl_roles = str((trow["role_ids"] if trow else "") or "")
            tpl_roles = parse_role_ids(_raw_tpl_roles)
            if not tpl_roles:
                continue
            placeholders = ",".join("?" for _ in tpl_roles)
            still_member = self.conn.execute(
                f"SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                f"WHERE m.phone = ? AND m.role_id IN ({placeholders}) LIMIT 1",
                (clean_phone, *tpl_roles),
            ).fetchone() is not None
            if still_member:
                continue
            inst_status = str(inst["status"] or "")
            if inst_status in ("pending", "overdue", "rejected"):
                # 未完成 → 作废留痕（不再发提醒，H5 旧链接提交被拦）
                self.conn.execute(
                    "UPDATE task_instance SET status = 'cancelled' WHERE id = ?",
                    (int(inst["id"]),),
                )
                cancelled_instance_ids.append(int(inst["id"]))
            else:
                # 完成/归档 → 保持 11-09「移除删干净」
                self.conn.execute("DELETE FROM submission WHERE instance_id = ?", (int(inst["id"]),))
                self.conn.execute("DELETE FROM task_instance WHERE id = ?", (int(inst["id"]),))
                removed_instance_ids.append(int(inst["id"]))

        if write_audit:
            self._insert_audit_unlocked(
                actor, "member.delete", "member", f"{clean_role_id}:{clean_phone}",
                {"name": row["name"], "dept": row["dept"], "removed_bindings": removed_bindings,
                 "removed_instances": len(removed_instance_ids),
                 "cancelled_instances": len(cancelled_instance_ids)}, now_s,
            )
        return {
            "phone": clean_phone, "name": row["name"],
            "removed_bindings": removed_bindings,
            "cancelled_instances": len(cancelled_instance_ids),
            "removed_instances": len(removed_instance_ids),
        }

    def delete_role_members(self, role_id: str, phones: List[str], actor: str = "system") -> Dict[str, Any]:
        """2026-11-12 下午：批量移除成员（用户要求「不要一个一个删」）。

        逐个走 _delete_role_member_unlocked 同款语义（名册/绑定/实例/审计一致），
        单个失败（如手机号不在本组）不影响其余，逐条回报；整批一条审计 member.batch_delete
        汇总 + 单条审计照写（可追溯）。
        """
        clean_role_id = (role_id or "").strip()
        clean_phones: List[str] = []
        for p in phones or []:
            s = str(p or "").strip()
            if s and s not in clean_phones:
                clean_phones.append(s)
        if not clean_phones:
            raise BizError(400, "VALIDATION_FAILED", "phones 不能为空")
        if len(clean_phones) > 500:
            raise BizError(400, "VALIDATION_FAILED", "单次批量移除最多 500 人")
        now_s = self.now_str()
        deleted: List[str] = []
        failed: List[Dict[str, str]] = []
        with self.lock, self.conn:
            for ph in clean_phones:
                try:
                    self._delete_role_member_unlocked(clean_role_id, ph, actor, now_s)
                    deleted.append(ph)
                except BizError as exc:
                    failed.append({"phone": ph, "code": exc.code, "message": exc.message})
            if deleted:
                self._insert_audit_unlocked(
                    actor, "member.batch_delete", "role", clean_role_id,
                    {"deleted": len(deleted), "failed": len(failed), "phones": deleted}, now_s,
                )
        return {"ok": True, "deleted": deleted, "failed": failed}

    def import_role_members(self, role_id: str, csv_rows: List[Dict[str, Any]], dry_run: bool = True, actor: str = "system", owner_id: int = 1,
                            sync_global_profile: bool = True) -> Dict[str, Any]:
        """R43 + §9.3：批量导入成员（行解析加 try 保护，非法行进 invalid 列表而不抛 500）。

        2026-11-09 用户口径（三条）：
        ① 手机号与**已添加成员重复** → 报错提示，**待修复后才全量导入成功**
           （此前是静默跳过 already_exists，用户看不到哪些行重复了）
        ② 手机号在**全局**已存在 → 首次导入到本组时允许管理员指定姓名/部门，
           写库时**同步到全局池**（该号在所有管理员名下的行一起改）
        ③ **允许重名** —— 判据是手机号唯一，不是姓名唯一

        sync_global_profile=False 时不做全局同步（只在本组建行）。
        """
        clean_role_id = (role_id or "").strip()
        if not isinstance(csv_rows, list):
            raise BizError(400, "VALIDATION_FAILED", "csv_rows 必须是数组")
        now_s = self.now_str()
        with self.lock, self.conn:
            # 2026-11-10 方案A：顺带取角色平台（导入即激活只对 zdxlz 生效）
            role_row = self.conn.execute("SELECT platform FROM role WHERE role_id = ?", (clean_role_id,)).fetchone()
            if role_row is None:
                raise BizError(404, "NOT_FOUND", "角色不存在")
            role_platform = str(role_row["platform"] or "zdxlz").strip().lower() or "zdxlz"
            existing_phones = {r["phone"] for r in self.conn.execute("SELECT phone FROM member WHERE role_id = ?", (clean_role_id,)).fetchall()}
            # 2026-11-09：全局已有的手机号 → 记下它现有的姓名/部门（用于同步与报告）
            global_rows = self.conn.execute(
                "SELECT phone, name, dept FROM member WHERE phone IN "
                f"(SELECT DISTINCT phone FROM member) ORDER BY id ASC"
            ).fetchall()
            global_profile: Dict[str, Dict[str, Any]] = {}
            for gr in global_rows:
                gp = global_profile.setdefault(gr["phone"], {"name": None, "dept": None, "rows": 0})
                gp["rows"] = int(gp["rows"]) + 1
                if gp["name"] is None:
                    gp["name"] = gr["name"]
                if gp["dept"] is None and gr["dept"]:
                    gp["dept"] = gr["dept"]
            will_add, already_exists = 0, 0
            invalid: List[Dict[str, Any]] = []
            to_insert: List[Tuple[int, str, str, Optional[str]]] = []
            batch_seen_phones: set[str] = set()
            # 2026-11-09：本次导入会改到全局池的记录（姓名/部门与全局不一致的行）
            global_profile_updates: List[Dict[str, Any]] = []

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
                # 2026-11-04 用户口径：CSV 里的部门若「部门管理」里没有，**不拦截**——
                # 自动把该部门建进部门选项（成员表 dept 仍存文本快照），
                # 这样 H5 登记页/成员表单的下拉立刻能选到，不会出现"名册里有、下拉里没有"的死锁。
                # 2026-11-09 用户口径：重复**要报错**，不能静默跳过。
                # 「待修复后才全量导入成功」= 有任何重复行就整批不写入。
                if phone in existing_phones:
                    already_exists += 1
                    invalid.append({"row": row_num, "phone": phone,
                                    "reason": f"手机号已存在于本角色组的成员名册中（已添加成员重复），请修复后重新导入"})
                    continue
                if phone in batch_seen_phones:
                    already_exists += 1
                    invalid.append({"row": row_num, "phone": phone,
                                    "reason": "本次导入内容中手机号重复，请修复后重新导入"})
                    continue

                batch_seen_phones.add(phone)
                # 2026-11-09：部门名超长在**收集阶段**就判掉（原先在写库前才判，
                # 导致 will_add 已经累加了不该写入的行，整批拒绝语义下数字对不上）。
                if dept and len(dept) > MAX_DEPT_NAME_LEN:
                    invalid.append({"row": row_num, "phone": phone,
                                    "reason": f"部门名称过长（最多 {MAX_DEPT_NAME_LEN} 字）：{dept}"})
                    continue
                # 2026-11-09：全局已有该号 → 记录是否与全局一致（不一致的按「同步全局」处理并报告）
                gp = global_profile.get(phone)
                if gp is not None:
                    g_name = str(gp.get("name") or "").strip()
                    g_dept = str(gp.get("dept") or "").strip()
                    changed = (g_name != name) or ((dept or "") != g_dept)
                    if changed:
                        global_profile_updates.append({
                            "phone": phone, "row": row_num,
                            "from_name": g_name, "from_dept": g_dept,
                            "to_name": name, "to_dept": dept or "",
                        })
                will_add += 1
                to_insert.append((row_num, phone, name, dept))

            # 部门自动建档：本次导入用到的、部门管理里还没有的部门，导入时一并建好。
            dept_cache: Dict[str, bool] = {}
            created_depts: List[str] = []
            # 2026-11-09：本次自动建档的部门 id（整批拒绝时回滚用）
            created_dept_ids: List[int] = []
            for _row_no, _phone, _name, _dept in to_insert:
                if not _dept:
                    continue
                if _dept in dept_cache:
                    continue
                if len(_dept) > MAX_DEPT_NAME_LEN:
                    dept_cache[_dept] = False
                    continue  # 超长部门不建档（收集阶段已把该行判为 invalid）
                if self.department_exists(_dept):
                    dept_cache[_dept] = True
                    continue
                dept_cache[_dept] = True
                if not dry_run:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO dept_option(name, created_at) VALUES (?, ?)",
                        (_dept, now_s),
                    )
                    if cur.lastrowid:
                        # 2026-11-09：记录新建的部门 id —— 整批拒绝时要回滚
                        created_dept_ids.append(int(cur.lastrowid))
                        self._insert_audit_unlocked(
                            actor, "dept.create", "dept_option", str(int(cur.lastrowid)),
                            {"name": _dept, "source": "member_import"}, now_s,
                        )
                created_depts.append(_dept)

            # 2026-11-09 用户口径：「待修复后才全量导入成功」——
            # 只要有错误行（重复/冲突/格式错），**整批不写入**，把全部错误行返回给用户。
            # 无错误时才写入，并按需同步全局池的姓名/部门。
            synced_global_rows = 0
            blocked = bool(invalid)
            if not dry_run and not blocked:
                # 长度等校验已在收集阶段完成，这里不再重复判
                insert_rows: List[Tuple[int, str, str, Optional[str]]] = list(to_insert)
                if invalid:
                    blocked = True
                else:
                    for _row_no, phone, name, dept in insert_rows:
                        self.conn.execute(
                            "INSERT INTO member(role_id, phone, name, dept, enabled, source, created_at, owner_id) "
                            "VALUES (?, ?, ?, ?, 1, 'import', ?, ?)",
                            (clean_role_id, phone, name, dept, now_s, int(owner_id)),
                        )
                        # 2026-11-11：CSV/导入路径的部门由反查回填 dept_path（_auto_activate_imported 负责）
                    will_add = len(insert_rows)
                    # 2026-11-09：把该号在**所有**管理员名下的行同步成这次的姓名/部门（全局池）
                    if sync_global_profile:
                        for upd in global_profile_updates:
                            ph = upd["phone"]
                            cur = self.conn.execute(
                                "UPDATE member SET name = ?, dept = ? WHERE phone = ?", (upd["to_name"], upd["to_dept"], ph)
                            )
                            synced_global_rows += int(cur.rowcount or 0)
                            if int(cur.rowcount or 0) > 0:
                                self._insert_audit_unlocked(
                                    actor, "member.profile_sync_global", "member", ph,
                                    {"name": upd["to_name"], "dept": upd["to_dept"],
                                     "from_name": upd["from_name"], "from_dept": upd["from_dept"],
                                     "rows": int(cur.rowcount or 0), "via_role": clean_role_id},
                                    now_s,
                                )
                    self._insert_audit_unlocked(
                        actor, "member.import", "role", clean_role_id,
                        {"will_add": will_add, "already_exists": already_exists, "invalid_count": len(invalid),
                         "departments_created": created_depts, "synced_global_rows": synced_global_rows},
                        now_s,
                    )

            if blocked:
                # 整批拒绝：will_add 归零（没有任何一行被写入）
                will_add = 0
                # 回滚本次自动建档的部门 —— 否则会出现「导入没成功，部门却多了几个」
                for did in created_dept_ids:
                    self.conn.execute("DELETE FROM dept_option WHERE id = ?", (int(did),))
                if created_dept_ids:
                    created_depts = []
            result = {"ok": True, "dry_run": bool(dry_run), "will_add": will_add, "already_exists": already_exists,
                      "invalid": invalid, "departments_created": created_depts,
                      "blocked": blocked,
                      "synced_global_rows": synced_global_rows,
                      "global_profile_updates": global_profile_updates}
            # 2026-11-10 方案A：本批真实写入的成员（事务提交后再做通讯录解析，不持锁跑网络）
            imported_members = (
                [(str(row[1]).strip(), str(row[2]).strip(), row[3], None) for row in to_insert]
                if (not dry_run and not blocked) else []
            )

        # ── 导入即激活：事务外把名册成员反查成平台 id 并写绑定（fail-open，不影响导入结果）──
        result["auto_activation"] = self._auto_activate_imported(
            imported_members, platform=role_platform, actor=actor, role_id=clean_role_id)
        return result

    def _auto_activate_imported(self, members: List[Tuple[str, str, str, Optional[str]]],
                                platform: str = "zdxlz", actor: str = "system",
                                role_id: Optional[str] = None) -> Dict[str, Any]:
        """2026-11-10 方案A：导入即激活 —— 用官方通讯录把名册成员反查成平台 id 并落绑定。

        入参 members：[(phone, name, dept, dept_path), ...]
        （zdxlz 只用手机号；企微 MCP 需要 姓名+部门；dept_path 可空，反查成功会回填）。

        平台分发（两通道镜像对接，用户 2026-11-10 拍板）：
          · zdxlz → self.contacts.search_by_phone（官方响应 decryptV2 解密后取 union_id，
            external_type='union_id'，发送用 receive_type="union_id"）；
          · wecom  → self.wecom_contacts.resolve_userid（①自建应用 getuserid 手机号精确反查；
            ②MCP 通讯录按归一化姓名+部门匹配。external_type='user'，aibot 直接按 userid 投递）。

        2026-11-11 部门口径（用户拍板）：反查成功后把**官方末级部门**回填 member.dept、
        **官方全路径**回填 member.dept_path（层级统计归集：统计「云中台」要把直属与
        下级部门成员都卷进来）。role_id 提供时才回填（member 行按角色隔离）。

        约定：
        · resolver 未装配（测试/降级环境）→ enabled=False，行为与旧版完全一致；
        · 已有可用绑定的手机号跳过（不重复打接口、不覆盖绑定）；
        · usable=0 的旧行用新 id 复活（首次激活时刻保留）；
        · 查不到（不在读取范围/离职/重名消歧失败）计入 failed，不阻塞导入。
        """
        base: Dict[str, Any] = {"enabled": False, "activated": 0, "failed": 0, "skipped": 0, "failed_phones": []}
        plat = (platform or "").strip().lower()
        if plat == "zdxlz":
            resolver = getattr(self, "contacts", None)
            ext_type = "union_id"

            def _resolve_one(phone: str, name: str, dept: str, path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
                user = resolver.search_by_phone(phone) or {}
                uid = str(user.get("union_id") or "").strip() or None
                leaf = str(user.get("department") or "").strip() or None
                full = str(user.get("dept_path") or "").strip() or None
                return uid, leaf, full
        elif plat == "wecom":
            resolver = getattr(self, "wecom_contacts", None)
            ext_type = "user"

            def _resolve_one(phone: str, name: str, dept: str, path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
                info = resolver.resolve_user(phone, name, dept) or {}
                uid = str(info.get("external_id") or "").strip() or None
                leaf = str(info.get("department") or "").strip() or None
                full = str(info.get("dept_path") or "").strip() or None
                return uid, leaf, full
        else:
            return base
        if not members or resolver is None:
            return base
        base["enabled"] = True

        unique: List[Tuple[str, str, str, Optional[str]]] = []
        seen: set = set()
        for phone, name, dept, path in members:
            p = str(phone or "").strip()
            if not p or p in seen:
                continue
            seen.add(p)
            unique.append((p, str(name or "").strip(), str(dept or "").strip() if dept else "",
                           str(path or "").strip() if path else None))

        to_resolve: List[Tuple[str, str, str, Optional[str]]] = []
        for item in unique:
            p = item[0]
            existing = self.get_delivery_target(p, platform=plat)
            if existing and int(existing.get("usable") or 0) == 1:
                base["skipped"] += 1
                continue
            to_resolve.append(item)
        if not to_resolve:
            return base

        def _resolve(item: Tuple[str, str, str, Optional[str]]):
            phone, name, dept, path = item
            uid, leaf, full = _resolve_one(phone, name, dept, path)
            return phone, uid, leaf, full

        activated = failed = 0
        failed_phones: List[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(_resolve, to_resolve))
        for phone, uid, leaf, full in outcomes:
            if not uid:
                failed += 1
                failed_phones.append(phone)
                continue
            try:
                self.upsert_delivery_target(phone, uid, platform=plat, external_type=ext_type)
                # 2026-11-11 部门口径：官方末级部门 + 全路径回填 member（层级统计归集）
                if role_id and (leaf or full):
                    with self.lock, self.conn:
                        self.conn.execute(
                            "UPDATE member SET dept = COALESCE(?, dept), dept_path = COALESCE(?, dept_path) "
                            "WHERE role_id = ? AND phone = ?",
                            (leaf, full, str(role_id), phone))
                activated += 1
            except BizError:
                failed += 1
                failed_phones.append(phone)
        base.update({
            "activated": activated,
            "failed": failed,
            "failed_phones": [mask_phone(p) for p in failed_phones],
        })
        self.write_audit(actor, "target.auto_import", "delivery_target", plat, {
            "activated": activated, "failed": failed, "skipped": base["skipped"],
            "failed_phones_masked": [mask_phone(p) for p in failed_phones],
        })
        return base

    def import_members_from_org(self, role_id: str, users: List[Dict[str, Any]],
                                actor: str = "system", owner_id: int = 1) -> Dict[str, Any]:
        """2026-11-11 任务二：组织架构选人入册 + 同步激活绑定（单事务）。

        users: [{name, phone, external_id, department, dept_path}]（external_id 来自官方组织树明细：
        密信=union_id / 企微=userid；department=官方末级部门名，dept_path=官方全路径〈层级统计归集〉）。
        · 整个写入在 `with self.lock, self.conn` 一个事务内：成员行 + delivery_target 同生同死；
        · 已在该角色的手机号跳过（skipped），不重复插入、不覆盖既有绑定；
        · 绑定沿用 upsert 语义：存量行保留 activated_at、usable 恢复为 1；
        · external_id 缺失时仍入册但不绑定（activated 不计数），由后续导入/反查补。
        """
        clean_role_id = (role_id or "").strip()
        now_s = self.now_str()
        result: Dict[str, Any] = {"added": 0, "skipped": 0, "activated": 0, "invalid": []}
        with self.lock, self.conn:
            role_row = self.conn.execute(
                "SELECT platform FROM role WHERE role_id = ?", (clean_role_id,)).fetchone()
            if role_row is None:
                raise BizError(404, "NOT_FOUND", "角色不存在")
            plat = str(role_row["platform"] or "zdxlz").strip().lower() or "zdxlz"
            ext_type = "union_id" if plat == "zdxlz" else "user"
            existing = {r["phone"] for r in self.conn.execute(
                "SELECT phone FROM member WHERE role_id = ?", (clean_role_id,)).fetchall()}
            seen_batch: set = set()
            for idx, raw in enumerate(users or [], start=1):
                if not isinstance(raw, dict):
                    result["invalid"].append({"row": idx, "phone": "", "reason": "行格式错误"})
                    continue
                phone = str(raw.get("phone") or "").strip()
                name = str(raw.get("name") or "").strip()
                dept = str(raw.get("department") or raw.get("dept") or "").strip() or None
                dept_path = str(raw.get("dept_path") or "").strip() or None
                ext_id = str(raw.get("external_id") or "").strip()
                if not is_valid_phone(phone):
                    result["invalid"].append({"row": idx, "phone": phone, "reason": "手机号格式不正确"})
                    continue
                if not name:
                    result["invalid"].append({"row": idx, "phone": phone, "reason": "姓名不能为空"})
                    continue
                if dept and len(dept) > MAX_DEPT_NAME_LEN:
                    result["invalid"].append({"row": idx, "phone": phone,
                                              "reason": f"部门名称过长（最多 {MAX_DEPT_NAME_LEN} 字）"})
                    continue
                if phone in existing or phone in seen_batch:
                    result["skipped"] += 1
                    continue
                seen_batch.add(phone)
                # 部门自动建档（与 CSV 导入同口径：名册里出现、部门选项里没有 → 一并建好）
                if dept and not self.conn.execute(
                        "SELECT 1 FROM dept_option WHERE name = ? LIMIT 1", (dept,)).fetchone():
                    self.conn.execute(
                        "INSERT OR IGNORE INTO dept_option(name, created_at) VALUES (?, ?)", (dept, now_s))
                self.conn.execute(
                    "INSERT INTO member(role_id, phone, name, dept, dept_path, enabled, source, created_at, owner_id) "
                    "VALUES (?, ?, ?, ?, ?, 1, 'org', ?, ?)",
                    (clean_role_id, phone, name, dept, dept_path, now_s, int(owner_id)),
                )
                result["added"] += 1
                if not ext_id:
                    continue
                # 绑定（upsert 语义；同一事务内直接写 SQL，避免嵌套事务破坏原子性）
                old = self.conn.execute(
                    "SELECT activated_at FROM delivery_target WHERE platform = ? AND phone = ?",
                    (plat, phone)).fetchone()
                if old:
                    self.conn.execute(
                        "UPDATE delivery_target SET external_id = ?, external_type = ?, usable = 1, last_event_at = ? "
                        "WHERE platform = ? AND phone = ?",
                        (ext_id, ext_type, now_s, plat, phone))
                else:
                    self.conn.execute(
                        "INSERT INTO delivery_target(platform, phone, external_id, external_type, usable, activated_at, last_event_at) "
                        "VALUES (?, ?, ?, ?, 1, ?, ?)",
                        (plat, phone, ext_id, ext_type, now_s, now_s))
                result["activated"] += 1
            self._insert_audit_unlocked(
                actor, "member.org_import", "role", clean_role_id,
                {"added": result["added"], "skipped": result["skipped"],
                 "activated": result["activated"], "invalid": len(result["invalid"]), "platform": plat},
                now_s)
        return result

    def role_member_phones(self, role_id: str) -> set:
        """该角色下在册手机号集合（Org Picker 的「已在册」判定）。"""
        with self.lock:
            return {str(r["phone"]) for r in self.conn.execute(
                "SELECT phone FROM member WHERE role_id = ?", ((role_id or "").strip(),)).fetchall()}

    def _deserialize_template_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        # 2026-11-04 多管理员分级：模板归属（owner_id）随模板一起带出，调度建实例时继承
        d["owner_id"] = int(d.get("owner_id") or 1)
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
        # 2026-10-29 执行日：JSON 数组 → list[int]；空/坏 → None（=全周，由调度层 fail-open）
        try:
            _ew_raw = d.get("exec_weekdays")
            exec_weekdays = json.loads(_ew_raw) if _ew_raw else None
            if isinstance(exec_weekdays, list):
                exec_weekdays = [int(x) for x in exec_weekdays if str(x).strip().isdigit()]
                exec_weekdays = sorted({w for w in exec_weekdays if 1 <= w <= 7}) or None
            else:
                exec_weekdays = None
        except Exception:
            exec_weekdays = None
        # 2026-11-10 月度执行日：同口径解析 exec_dom_days（1~31）；NULL/空 = 不启用月度模式
        try:
            _ed_raw = d.get("exec_dom_days")
            exec_dom_days = json.loads(_ed_raw) if _ed_raw else None
            if isinstance(exec_dom_days, list):
                exec_dom_days = [int(x) for x in exec_dom_days if str(x).strip().isdigit()]
                exec_dom_days = sorted({w for w in exec_dom_days if 1 <= w <= 31}) or None
            else:
                exec_dom_days = None
        except Exception:
            exec_dom_days = None
        return {
            "template_id": d["template_id"], "name": d["name"], "kind": d["kind"], "role_ids": role_ids,
            "owner_id": d["owner_id"],
            # 2026-11-04：超管全局查看时要看到「创建人」（owner 名字），列表 SQL 已 JOIN admin_user
            "owner_username": d.get("owner_username"),
            "owner_display_name": d.get("owner_display_name"),
            "schema_json": schema_json, "allow_note": bool(d.get("allow_note", 0)), "text_tpl": d["text_tpl"],
            "remind_at": d.get("remind_at"), "notice_times": notice_times, "deadline": d.get("deadline"),
            "period": d["period"], "anchor_weekday": d.get("anchor_weekday"), "anchor_dom": d.get("anchor_dom"),
            "deadline_offset": int(d.get("deadline_offset") or 0), "need_review": bool(d.get("need_review", 0)),
            # 2026-11-12 任务E1：截止配置三件套（旧模板 deadline_type 为空 = 回落旧算法）
            "has_deadline": bool(int(d.get("has_deadline") if d.get("has_deadline") is not None else 1)),
            "deadline_type": (str(d.get("deadline_type") or "").strip() or None),
            "deadline_time": (str(d.get("deadline_time") or "").strip() or None),
            "max_file_mb": d.get("max_file_mb"), "enabled": bool(d.get("enabled", 1)), "deleted": bool(d.get("deleted", 0)),
            "attach_image": bool(d.get("attach_image", 0)), "attach_image_path": d.get("attach_image_path"),
            "attach_image_name": d.get("attach_image_name"),
            "exec_weekdays": exec_weekdays,
            "exec_dom_days": exec_dom_days,
            "data_keys": data_keys, "spec_json": spec_json,
            # 2026-11-12 晚 · 不定期任务（菜单自选）：True = 无推送/无截止/调度物理隔离
            "is_adhoc": bool(int(d.get("is_adhoc") if d.get("is_adhoc") is not None else 0)),
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

        # ── 2026-11-12 任务B：kind="upload" 归一 ─────────────────────────────
        # 前端重构后「上传任务」统一传 kind="upload"（不再细分 form/image/file），
        # 后端 VALID_TEMPLATE_KINDS 只认 form/image/file/notice → 创建直接 400
        # 「模板类型 kind 必须是 form/image/file/notice」，上传模板根本建不出来。
        # 真实业务形态由 spec.upload_modules 决定，这里按勾选模块映射落库 kind：
        #   · 勾了「字段报数」 → form（结构化字段 + 多附件）
        #   · 否则勾了「文件」 → file；勾了「图片」 → image
        #   · 什么都没勾（纯文字说明）→ form（空 schema，任务B 后允许）
        # form/image/file/notice 原样通过（兼容旧前端 / 编辑保存 / 既有测试直传）。
        if str(merged.get("kind") or "").strip() == "upload":
            _spec_probe = merged.get("spec") if isinstance(merged.get("spec"), dict) else (
                merged.get("spec_json") if isinstance(merged.get("spec_json"), dict) else {}
            )
            _mods_probe = [str(m).strip() for m in (_spec_probe.get("upload_modules") or []) if str(m or "").strip()]
            if "fields" in _mods_probe:
                _mapped_kind = "form"
            elif "file" in _mods_probe:
                _mapped_kind = "file"
            elif "image" in _mods_probe:
                _mapped_kind = "image"
            else:
                _mapped_kind = "form"
            payload = dict(payload or {})
            payload["kind"] = _mapped_kind
            merged["kind"] = _mapped_kind

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

        # 2026-11-02 角色按办公软件隔离：一个任务模板只能挂在同一办公软件的角色上，
        # 否则任务归属会同时出现在两个面板，调度也不知道该按哪边的名册建实例。
        plat_rows = self.conn.execute(f"SELECT role_id, platform FROM role WHERE role_id IN ({placeholders})", tuple(role_ids)).fetchall()
        role_plats = {str(r["platform"]) for r in plat_rows}
        if len(role_plats) > 1:
            raise BizError(400, "VALIDATION_FAILED", "同一任务模板只能选择同一办公软件下的角色")

        text_tpl = str(merged.get("text_tpl") or "").strip()
        if not text_tpl:
            raise BizError(400, "VALIDATION_FAILED", "提醒文案 text_tpl 不能为空")
        # 2026-10-29：把字面量 \n / \r\n 归一成真实换行再入库，
        # 避免后台编辑框和消息里一直夹着反斜杠 n 这种字符。
        text_tpl = text_tpl.replace("\\r\\n", "\n").replace("\\n", "\n").strip()

        # ── 2026-11-12 晚 · 不定期任务（菜单自选）：is_adhoc 互斥边界（需求三-4）──
        # 后台勾选「不定期任务（菜单自选）」后：推送时刻强制禁用/置空（调度链物理隔离、
        # 绝不主动催报）、截止时间强制禁用/置空（链接永不逾期、H5 永不显示截止）。
        _adhoc_raw = merged.get("is_adhoc")
        if _adhoc_raw is None:
            _adhoc_raw = (existing or {}).get("is_adhoc") or 0
        is_adhoc = bool(int(_adhoc_raw or 0))
        if is_adhoc and kind == "notice":
            raise BizError(400, "VALIDATION_FAILED", "通知类模板不支持「不定期任务（菜单自选）」模式")

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
        # 2026-11-12 任务E1：截止配置三件套默认值（notice 分支不参与，保持数据库列默认）
        has_deadline, deadline_type, deadline_time = True, None, None

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
            if is_adhoc:
                # 2026-11-12 晚 · 不定期任务：推送时刻强制置空（调度链物理隔离）+ 截止强制无。
                # remind_at=None 跳过 HH:MM 校验；has_deadline=False 走下面的无截止分支。
                remind_at = None
                has_deadline = False
            else:
                remind_at = str(merged.get("remind_at") or "").strip()
                if not is_valid_hhmm(remind_at):
                    raise BizError(400, "VALIDATION_FAILED", "remind_at 必须为合法的 HH:MM 格式")

            # ── 2026-11-12 任务E1：截止配置三件套（has_deadline / deadline_type / deadline_time）──
            # 部分更新语义：键未提交 → 保留 existing 原值（与其他可选字段一致）。
            if is_adhoc:
                has_deadline = False  # 需求三-4：不定期任务强制无截止（无视 payload，杜绝脏配置）
            elif "has_deadline" in payload:
                has_deadline = bool(payload.get("has_deadline"))
            else:
                has_deadline = bool(existing.get("has_deadline")) if existing else True

            deadline_time: Optional[str] = None
            deadline = ""
            if has_deadline:
                # deadline_time 缺省回落链：显式 deadline_time → 兼容字段 deadline → existing.deadline。
                _dt_raw = str(merged.get("deadline_time") or "").strip()
                _legacy = str(merged.get("deadline") or "").strip()
                deadline_time = _dt_raw or _legacy or str((existing or {}).get("deadline") or "").strip()
                # 旧列 deadline 同步保存为同一 HH:MM，保证任何仍读旧列的链路一致。
                deadline = deadline_time

                if "deadline_type" in payload and payload.get("deadline_type") is not None:
                    deadline_type = str(payload.get("deadline_type") or "").strip()
                    if deadline_type not in VALID_DEADLINE_TYPES:
                        raise BizError(400, "VALIDATION_FAILED",
                                       "deadline_type 必须是 same_day/weekend/month_end 之一")
                else:
                    deadline_type = str((existing or {}).get("deadline_type") or "").strip() or None
                if not is_valid_hhmm(deadline_time):
                    raise BizError(400, "VALIDATION_FAILED", "截止时间必须为合法的 HH:MM 格式")
                # 执行时刻必须早于「当天截止」（当天/旧口径语义下提醒晚于截止即配置矛盾；
                # 周末/月底截止天然在执行时刻之后，不做 HH:MM 顺序比较）。
                # 2026-11-12 下午（用户拍板，图三报错修复）：截止 **00:00 = 当天 24 点**，
                # 任何提醒时刻都早于它 → 豁免顺序比较（00:00 截止的模板应能创建）。
                if (
                    (deadline_type or "same_day") == "same_day"
                    and deadline_time != "00:00"
                    and remind_at >= deadline_time
                ):
                    raise BizError(400, "VALIDATION_FAILED", "提醒时间 remind_at 必须早于截止时间 deadline")
            else:
                # 无截止：兼容仍传 deadline 的老前端——存在时只校验格式，顺序约束不生效。
                # 旧列 deadline 一并置空（否则未升级的展示链路会错误显示「截止 HH:MM」）。
                legacy_raw = str(merged.get("deadline") or "").strip()
                if legacy_raw and not is_valid_hhmm(legacy_raw):
                    raise BizError(400, "VALIDATION_FAILED", "deadline 必须为合法的 HH:MM 格式")
                deadline = None
                deadline_time = None
                deadline_type = None

            need_review, allow_note = bool(merged.get("need_review", False)), bool(merged.get("allow_note", False))
            if kind == "form":
                # 2026-11-12 任务B：schema 来源两路——
                # 旧前端/测试直传 schema_json，优先用；重构前端只传 spec.fields（不传
                # schema_json），为空时回落到 spec.fields（字段结构与 schema_json 项一致：
                # key/label/type/required）。两路都空 → 空 schema（任务B 后允许）。
                _schema_src = merged.get("schema_json")
                _schema_empty = (
                    _schema_src is None
                    or (isinstance(_schema_src, str) and not _schema_src.strip())
                    or (isinstance(_schema_src, list) and not _schema_src)
                )
                if _schema_empty:
                    _spec_for_fields = merged.get("spec") if isinstance(merged.get("spec"), dict) else (
                        merged.get("spec_json") if isinstance(merged.get("spec_json"), dict) else {}
                    )
                    _spec_fields = _spec_for_fields.get("fields")
                    if isinstance(_spec_fields, list) and _spec_fields:
                        _schema_src = _spec_fields
                schema_json_str = json.dumps(_validate_form_schema(_schema_src), ensure_ascii=False)
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
        # 2026-10-29 H5 图文页：图片名称（描述）——用于直链文案与 H5 页面标题。
        # 前端已做必填校验；后端不强校非空，兼容老模板启停等不带名称的更新路径。
        attach_image_name = str(merged.get("attach_image_name") or "").strip() or None
        if attach_image_path:
            # 2026-10-29 P2-1：附图路径必须落在 data 目录内，挡住 ../ 与绝对路径越界读取
            if ".." in attach_image_path.replace("\\", "/"):
                raise BizError(400, "VALIDATION_FAILED", "attach_image_path 不允许包含 ..")
            _p = Path(attach_image_path)
            if _p.is_absolute():
                raise BizError(400, "VALIDATION_FAILED", "attach_image_path 必须是相对 data 目录的路径")
            _data_root = Path(self.db.db_path).parent.resolve()
            _resolved = (_data_root / attach_image_path).resolve()
            # 2026-10-29 补丁3：用 is_relative_to 替代字符串前缀比较，
            # 否则 /app/data_other 会命中 /app/data 前缀，越界判断失效。
            if not _resolved.is_relative_to(_data_root):
                raise BizError(400, "VALIDATION_FAILED", "attach_image_path 不允许指向数据目录以外的路径")
            attach_image = True  # 有图就等价于开启附图，避免前端只传了路径没传开关
        if attach_image and not data_keys_norm and not attach_image_path:
            raise BizError(
                400, "VALIDATION_FAILED",
                "勾选了「数据图片提醒」就必须先上传要推送的图片/文件，否则到点无图可发",
            )
        data_keys_str = json.dumps(data_keys_norm, ensure_ascii=False)

        # 2026-10-29 执行日（exeс_weekdays）：只有 day 周期可以配周几。
        # 未提供 → 更新时保留原值（与 need_review 同款部分更新语义）；空/坏 → 全周（调度层 fail-open）。
        if "exec_weekdays" in payload:
            raw_ew = payload.get("exec_weekdays")
            if raw_ew is None:
                exec_weekdays_norm: Optional[List[int]] = None
            else:
                if not isinstance(raw_ew, list):
                    raise BizError(400, "VALIDATION_FAILED", "exec_weekdays 必须是数组")
                clean_ew: List[int] = []
                for x in raw_ew:
                    try:
                        w = int(str(x).strip())
                    except Exception:
                        raise BizError(400, "VALIDATION_FAILED", "执行日只能是 1~7（1=周一，7=周日）")
                    if not (1 <= w <= 7):
                        raise BizError(400, "VALIDATION_FAILED", "执行日只能是 1~7（1=周一，7=周日）")
                    clean_ew.append(w)
                clean_ew = sorted(set(clean_ew))
                if not clean_ew:
                    raise BizError(400, "VALIDATION_FAILED", "至少选择一天执行日")
                if period != "day":
                    raise BizError(400, "VALIDATION_FAILED", "仅每日周期支持执行日")
                exec_weekdays_norm = clean_ew
        else:
            exec_weekdays_norm = (existing or {}).get("exec_weekdays")

        # 2026-11-10 月度执行日：同一套部分更新语义。None = 未提交（更新保留原值）；
        # [] = 显式清空（切回每周模式）；非空 = 每月几号（1~31，选中 31 含每月最后一天）。
        if "exec_dom_days" in payload:
            raw_ed = payload.get("exec_dom_days")
            if raw_ed is None:
                exec_dom_days_norm: Optional[List[int]] = None
            else:
                if not isinstance(raw_ed, list):
                    raise BizError(400, "VALIDATION_FAILED", "exec_dom_days 必须是数组")
                clean_ed: List[int] = []
                for x in raw_ed:
                    try:
                        dm = int(str(x).strip())
                    except Exception:
                        raise BizError(400, "VALIDATION_FAILED", "每月执行日只能是 1~31")
                    if not (1 <= dm <= 31):
                        raise BizError(400, "VALIDATION_FAILED", "每月执行日只能是 1~31")
                    clean_ed.append(dm)
                clean_ed = sorted(set(clean_ed))
                if not clean_ed:
                    exec_dom_days_norm = None  # 显式空数组 = 清空，回落每周模式
                else:
                    if period != "day":
                        raise BizError(400, "VALIDATION_FAILED", "仅每日周期支持执行日")
                    exec_dom_days_norm = clean_ed
        else:
            exec_dom_days_norm = (existing or {}).get("exec_dom_days")

        # 2026-11-12 晚 · 不定期任务：无执行日概念（无推送时刻 → 调度链物理隔离），强制置空。
        if is_adhoc:
            exec_weekdays_norm = None
            exec_dom_days_norm = None

        # 2026-10-29 修复：前端提交的键是 `spec`（notice_items 里带上传时的**原始文件名**
        # 与管理员填的描述），后端一直只读 `spec_json` → 这些配置从来没落库，
        # 每次重新打开模板都被前端按 attach_image_path 重建，于是名字/描述回到默认值。
        # 这里两种键都接受，优先 payload 里的 spec。
        _payload_spec = payload.get("spec")
        _spec_payload = _payload_spec if isinstance(_payload_spec, dict) else merged.get("spec_json")
        if isinstance(_spec_payload, str):
            try:
                _spec_payload = json.loads(_spec_payload)
            except Exception:  # noqa: BLE001
                _spec_payload = None

        # 2026-10-29：spec 里的正文/描述同样要把**字面量** `\n`（反斜杠+n）归一成真换行。
        # text_tpl 早就有这层归一，但 notice_items.text_content 一直漏了，
        # 结果后台卡片和预览里显示成「一、文字提醒：\n1、开晨会前…」。
        if isinstance(_spec_payload, dict):
            _items = _spec_payload.get("notice_items")
            if isinstance(_items, list):
                for _it in _items:
                    if not isinstance(_it, dict):
                        continue
                    for _k in ("text_content", "file_caption"):
                        _v = _it.get(_k)
                        if isinstance(_v, str) and "\\n" in _v:
                            _it[_k] = _v.replace("\\r\\n", "\n").replace("\\n", "\n")

        return {
            "name": name, "kind": kind, "role_ids_json": json.dumps(role_ids, ensure_ascii=False),
            "schema_json": schema_json_str, "allow_note": 1 if allow_note else 0, "text_tpl": text_tpl,
            "remind_at": remind_at, "notice_times": notice_times_str, "deadline": deadline or None, "period": period,
            "anchor_weekday": anchor_weekday, "anchor_dom": anchor_dom, "deadline_offset": deadline_offset,
            # 2026-11-12 任务E1：截止配置三件套（无截止 → has_deadline=0，deadline_time/type 置空）
            "has_deadline": 1 if has_deadline else 0,
            "deadline_type": deadline_type if has_deadline else None,
            "deadline_time": deadline_time if has_deadline else None,
            "need_review": 1 if need_review else 0, "max_file_mb": max_file_mb, "enabled": 1 if bool(merged.get("enabled", True)) else 0,
            # 2026-11-12 晚 · 不定期任务（菜单自选）：持久化 flag（调度链按它物理隔离）
            "is_adhoc": 1 if is_adhoc else 0,
            "attach_image": 1 if attach_image else 0, "attach_image_path": attach_image_path,
            "attach_image_name": attach_image_name,
            "exec_weekdays": json.dumps(exec_weekdays_norm, ensure_ascii=False) if exec_weekdays_norm else None,
            "exec_dom_days": json.dumps(exec_dom_days_norm, ensure_ascii=False) if exec_dom_days_norm else None,
            "data_keys": data_keys_str,
            "spec_json": json.dumps(_spec_payload, ensure_ascii=False) if _spec_payload not in (None, "") else None,
        }

    def create_template(self, payload: Dict[str, Any], actor: str = "system", owner_id: int = 1) -> Dict[str, Any]:
        now_s = self.now_str()
        with self.lock, self.conn:
            norm = self._validate_and_normalize_template_unlocked(payload, existing=None)
            template_id = self._generate_unique_id_unlocked("task_template", "template_id", "t_")
            self.conn.execute(
                "INSERT INTO task_template(template_id, name, kind, role_ids, schema_json, allow_note, text_tpl, remind_at, notice_times, deadline, period, anchor_weekday, anchor_dom, deadline_offset, has_deadline, deadline_type, deadline_time, need_review, max_file_mb, attach_image, attach_image_path, attach_image_name, exec_weekdays, exec_dom_days, data_keys, spec_json, enabled, is_adhoc, deleted, created_by, created_at, updated_at, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    template_id, norm["name"], norm["kind"], norm["role_ids_json"], norm["schema_json"],
                    norm["allow_note"], norm["text_tpl"], norm["remind_at"], norm["notice_times"], norm["deadline"],
                    norm["period"], norm["anchor_weekday"], norm["anchor_dom"], norm["deadline_offset"],
                    norm["has_deadline"], norm["deadline_type"], norm["deadline_time"],
                    norm["need_review"], norm["max_file_mb"], norm["attach_image"], norm["attach_image_path"],
                    norm["attach_image_name"], norm["exec_weekdays"], norm["exec_dom_days"], norm["data_keys"], norm["spec_json"],
                    norm["enabled"], norm["is_adhoc"], actor, now_s, now_s, int(owner_id),
                ),
            )
            self._insert_audit_unlocked(actor, "template.create", "task_template", template_id, {"name": norm["name"], "kind": norm["kind"], "period": norm["period"]}, now_s)
            return self._deserialize_template_row(self.conn.execute("SELECT * FROM task_template WHERE template_id = ?", (template_id,)).fetchone())

    def get_template(self, template_id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        with self.lock:
            sql = "SELECT * FROM task_template WHERE template_id = ?" + ("" if include_deleted else " AND deleted = 0")
            row = self.conn.execute(sql, ((template_id or "").strip(),)).fetchone()
            return self._deserialize_template_row(row) if row else None

    def list_templates(self, q: Optional[str] = None, kind: Optional[str] = None, enabled: Optional[bool] = None, page: int = 1, size: int = 20, platform: Optional[str] = None, owner_id: Optional[int] = None) -> Dict[str, Any]:
        p, s = _clamp_page_size(page, size)
        offset = (p - 1) * s
        where_clauses, params = ["t.deleted = 0"], []
        if q and q.strip():
            where_clauses.append("t.name LIKE ?")
            params.append(f"%{q.strip()}%")
        if kind and kind.strip():
            where_clauses.append("t.kind = ?")
            params.append(kind.strip())
        if enabled is not None:
            where_clauses.append("t.enabled = ?")
            params.append(1 if enabled else 0)
        if owner_id is not None:
            where_clauses.append("t.owner_id = ?")
            params.append(int(owner_id))
        plat = (platform or "").strip().lower()
        if plat:
            # 任务归属平台 = 模板所选角色所属平台（角色已按平台隔离）
            where_clauses.append(
                "EXISTS (SELECT 1 FROM role r WHERE r.platform = ? AND instr(t.role_ids, r.role_id) > 0)"
            )
            params.append(plat)
        where_sql = "WHERE " + " AND ".join(where_clauses)
        with self.lock:
            total = int(self.conn.execute(f"SELECT COUNT(1) AS cnt FROM task_template t {where_sql}", tuple(params)).fetchone()["cnt"] or 0)
            rows = self.conn.execute(
            f"SELECT t.*, u.username AS owner_username, u.display_name AS owner_display_name "
            f"FROM task_template t LEFT JOIN admin_user u ON u.id = t.owner_id "
            f"{where_sql} ORDER BY t.created_at DESC, t.template_id DESC LIMIT ? OFFSET ?",
            tuple(params + [s, offset]),
        ).fetchall()
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
                "has_deadline = ?, deadline_type = ?, deadline_time = ?, "
                "need_review = ?, max_file_mb = ?, attach_image = ?, attach_image_path = ?, attach_image_name = ?, exec_weekdays = ?, exec_dom_days = ?, data_keys = ?, spec_json = ?, enabled = ?, is_adhoc = ?, updated_at = ? WHERE template_id = ?",
                (
                    norm["name"], norm["kind"], norm["role_ids_json"], norm["schema_json"], norm["allow_note"],
                    norm["text_tpl"], norm["remind_at"], norm["notice_times"], norm["deadline"], norm["anchor_weekday"],
                    norm["anchor_dom"], norm["deadline_offset"],
                    norm["has_deadline"], norm["deadline_type"], norm["deadline_time"],
                    norm["need_review"], norm["max_file_mb"],
                    norm["attach_image"], norm["attach_image_path"], norm["attach_image_name"], norm["exec_weekdays"], norm["exec_dom_days"], norm["data_keys"], norm["spec_json"],
                    norm["enabled"], norm["is_adhoc"], now_s, clean_id,
                ),
            )
            # 2026-11-12 下午修复（图一实锤）：编辑模板移除角色分组后，被移除角色成员的实例
            # 还挂在「任务实例」页（旧实现只更新模板 role_ids，对实例零处理）。
            # 处置口径（用户拍板）：**物理删除** —— 模板剩余角色已不含该成员时，其在该模板下的
            # 全部实例（含未完成与已完成）连同提交/令牌一起清除，列表不再出现；
            # 仍挂在模板其它角色的成员不动（与 delete_role_member 的「一人多角色」判据同源）。
            _role_purge: Optional[Dict[str, Any]] = None
            _old_roles = parse_role_ids(row["role_ids"] or "")
            _new_roles = parse_role_ids(norm["role_ids_json"] or "")
            _removed_roles = [r for r in _old_roles if r not in _new_roles]
            if _removed_roles:
                _place = ",".join("?" for _ in _removed_roles)
                _rem_phones = [str(r[0]) for r in self.conn.execute(
                    f"SELECT DISTINCT phone FROM member WHERE role_id IN ({_place})",
                    tuple(_removed_roles)).fetchall()]
                _orphan_phones: List[str] = []
                for _ph in _rem_phones:
                    _still = False
                    if _new_roles:
                        _keep_place = ",".join("?" for _ in _new_roles)
                        _still = self.conn.execute(
                            f"SELECT 1 FROM member WHERE phone = ? AND role_id IN ({_keep_place}) LIMIT 1",
                            (_ph, *_new_roles)).fetchone() is not None
                    if _still:
                        continue
                    _orphan_phones.append(_ph)
                _deleted_instances = 0
                for _ph in _orphan_phones:
                    _ids = [int(r[0]) for r in self.conn.execute(
                        "SELECT id FROM task_instance WHERE template_id = ? AND phone = ?",
                        (clean_id, _ph)).fetchall()]
                    if not _ids:
                        continue
                    _id_place = ",".join("?" for _ in _ids)
                    self.conn.execute(f"DELETE FROM submission WHERE instance_id IN ({_id_place})", tuple(_ids))
                    # h5_token 行保留（tombstone）：旧链接点开 → 统一提示「任务已关闭」（四-4 口径）
                    _deleted_instances += int(self.conn.execute(
                        f"DELETE FROM task_instance WHERE id IN ({_id_place})", tuple(_ids)).rowcount or 0)
                _role_purge = {"removed_roles": _removed_roles, "orphan_phones": len(_orphan_phones),
                               "deleted_instances": _deleted_instances}
                if _orphan_phones:
                    self._insert_audit_unlocked(
                        actor, "template.role_removed_purge", "task_template", clean_id,
                        _role_purge, now_s,
                    )
            # 2026-11-12 下午（用户拍板，一-2）：截止规则编辑后对未完成实例**动态重算**——
            # 基于各实例原本的派发日（created_at 日期）按新规则重算 deadline_at，旧 H5 链接
            # 顶部截止时间同步更新：
            #   · 延后（含改「无截止」）→ 已逾期(overdue)实例自动恢复 pending（旧链接立即可填报），
            #     未消耗令牌寿命同步**顺延**（只延不缩），确保新截止前不提前失效；
            #   · 提前至当前时刻之前 → 立即翻 overdue（旧链接打开/提交按统一过期提示拦截）；
            #   · 已完成（submitted/approved/expired/cancelled）不受影响（历史隔离）；
            #     rejected 的重交窗口按「驳回+24h」已锁定，不随模板截止变化（三口径）。
            _deadline_sync: Dict[str, int] = {}
            if str(norm.get("kind") or "") != "notice":
                from .scheduler import calc_instance_deadline  # 延迟导入：scheduler 模块级依赖本模块，避免循环
                _fresh_tpl = self._deserialize_template_row(self.conn.execute(
                    "SELECT * FROM task_template WHERE template_id = ?", (clean_id,)).fetchone())
                for _r in self.conn.execute(
                    "SELECT id, status, deadline_at, created_at FROM task_instance "
                    "WHERE template_id = ? AND status IN ('pending','overdue')", (clean_id,)).fetchall():
                    try:
                        _anchor = datetime.strptime(str(_r["created_at"] or "")[:10], "%Y-%m-%d").date()
                        _new_dl = calc_instance_deadline(_fresh_tpl, _anchor)
                    except Exception:
                        continue
                    if _new_dl != str(_r["deadline_at"] or ""):
                        self.conn.execute("UPDATE task_instance SET deadline_at = ? WHERE id = ?", (_new_dl, int(_r["id"])))
                        _deadline_sync["recalced"] = int(_deadline_sync.get("recalced", 0)) + 1
                    if str(_r["status"]) == "pending" and _new_dl <= now_s:
                        self.conn.execute(
                            "UPDATE task_instance SET status = 'overdue' WHERE id = ? AND status = 'pending'",
                            (int(_r["id"]),))
                        _deadline_sync["to_overdue"] = int(_deadline_sync.get("to_overdue", 0)) + 1
                    elif str(_r["status"]) == "overdue" and _new_dl > now_s:
                        self.conn.execute(
                            "UPDATE task_instance SET status = 'pending' WHERE id = ? AND status = 'overdue'",
                            (int(_r["id"]),))
                        _deadline_sync["restored"] = int(_deadline_sync.get("restored", 0)) + 1
                    # 链接寿命联动（只延不缩）：无截止 → 哨兵（永不过期）
                    _new_exp_s = (NO_DEADLINE_SENTINEL if is_no_deadline(_new_dl)
                                  else format_dt(parse_dt(_new_dl) + timedelta(hours=H5_TOKEN_TTL_GRACE_HOURS)))
                    for _tk in self.conn.execute(
                        "SELECT token_id, expires_at FROM h5_token WHERE instance_id = ? AND purpose = 'submit' AND consumed = 0",
                        (int(_r["id"]),)).fetchall():
                        if str(_tk["expires_at"] or "") < _new_exp_s:
                            self.conn.execute("UPDATE h5_token SET expires_at = ? WHERE token_id = ?",
                                              (_new_exp_s, str(_tk["token_id"])))
                            _deadline_sync["tokens_extended"] = int(_deadline_sync.get("tokens_extended", 0)) + 1
            self._insert_audit_unlocked(actor, "template.update", "task_template", clean_id, {"name": norm["name"], "enabled": bool(norm["enabled"]), "role_removed_purge": _role_purge, "deadline_sync": _deadline_sync}, now_s)
            return self._deserialize_template_row(self.conn.execute("SELECT * FROM task_template WHERE template_id = ?", (clean_id,)).fetchone())

    def _purge_template_rows_unlocked(self, template_id: str, now_s: str, uploads_dir: Optional[Path] = None) -> Dict[str, int]:
        """⚠️ 调用方必须已握 self.lock 且处于 self.conn 事务内。

        2026-11-12 下午（用户拍板，为 MySQL 迁移清数据）：物理清除一个模板的全部业务痕迹。
        范围：实例 → 提交/上传引用/H5令牌/提醒日志/数据快照 → 模板行本体；磁盘附件先收集
        路径再删（越界路径跳过，删文件失败只计数不回滚——与 delete_admin_account 同款口径）。
        """
        tid = (template_id or "").strip()
        detail: Dict[str, int] = {"submission": 0, "upload_ref": 0, "h5_token": 0, "reminder_log": 0,
                                  "data_snapshot": 0, "task_instance": 0, "task_template": 0,
                                  "attachments_removed": 0}
        if not tid:
            return detail
        if uploads_dir is not None:
            rels = [str(r[0]) for r in self.conn.execute(
                "SELECT rel_path FROM upload_ref WHERE template_id = ?", (tid,)).fetchall()]
            _tpl_row = self.conn.execute(
                "SELECT attach_image_path FROM task_template WHERE template_id = ?", (tid,)).fetchone()
            _att = str(_tpl_row["attach_image_path"] or "").strip() if _tpl_row else ""
            if _att:
                rels.append(_att)
            for rel in rels:
                if self._purge_upload_file(uploads_dir, rel):
                    detail["attachments_removed"] += 1
        detail["submission"] = int(self.conn.execute(
            "DELETE FROM submission WHERE instance_id IN (SELECT id FROM task_instance WHERE template_id = ?)",
            (tid,)).rowcount or 0)
        detail["upload_ref"] = int(self.conn.execute(
            "DELETE FROM upload_ref WHERE template_id = ?", (tid,)).rowcount or 0)
        # 2026-11-12 下午（用户拍板，四-1）：h5_token 行**保留**作为轻量失效识别（tombstone）——
        # 模板物理清除后，用户点开旧链接仍能被识别为「任务已关闭」，而不是「无效令牌」技术报错。
        # 令牌行只有 token_id/phone/template_id 等无业务内容字段，不构成迁移数据负担。
        detail["reminder_log"] = int(self.conn.execute(
            "DELETE FROM reminder_log WHERE template_id = ?", (tid,)).rowcount or 0)
        # 2026-10-27 数据看板：调度器按 template_id 作为 source_key 读人工填报快照，一并清
        detail["data_snapshot"] = int(self.conn.execute(
            "DELETE FROM data_snapshot WHERE source_key = ?", (tid,)).rowcount or 0)
        detail["task_instance"] = int(self.conn.execute(
            "DELETE FROM task_instance WHERE template_id = ?", (tid,)).rowcount or 0)
        detail["task_template"] = int(self.conn.execute(
            "DELETE FROM task_template WHERE template_id = ?", (tid,)).rowcount or 0)
        return detail

    def delete_template(self, template_id: str, actor: str = "system", uploads_dir: Optional[Path] = None) -> Dict[str, Any]:
        """2026-11-12 下午（用户拍板）：删除任务模板 = **物理清除**，不再在库里留行。

        旧实现是软删（deleted=1 置位 + 未完成实例置 expired）——行永远留在本地库文件里，
        与用户「删了就不在本地数据库文件里（为 MySQL 迁移减数据量）」的口径冲突。
        新口径：模板行 + 全部实例（含未完成与已完成）+ 提交 + 令牌 + 日志 + 快照 + 磁盘附件
        全部物理删除；审计记 template.purge（永不删）。
        停用与删除就此划清：**停用 = 保留数据只停发；删除 = 彻底清空**。
        H5 旧链接因模板行不存在而失效（get_template → None → 404）。
        """
        clean_id, now_s = (template_id or "").strip(), self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT template_id, name FROM task_template WHERE template_id = ?", (clean_id,)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "任务模板不存在")
            detail = self._purge_template_rows_unlocked(clean_id, now_s, uploads_dir)
            self._insert_audit_unlocked(
                actor, "template.purge", "task_template", clean_id,
                {"name": row["name"], "deleted": detail}, now_s,
            )
        return {"ok": True, "name": row["name"], "deleted": detail}

    def match_roster_member(self, phone: str, name: str, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """2026-11-03 新激活模型：按「手机号 + 姓名」与本平台名册比对（部门不作为拦截项）。

        命中 → 返回该成员行（含 dept，供回执展示）；手机号在名册但所有行姓名都不符 →
        返回 None 并由调用方提示「信息与后台不一致」；手机号完全不在名册 → 调用方提示「管理员未导入」。

        2026-11-04（验收遗留修正）：取该手机号在本平台的**全部在册行，任一行姓名匹配即通过**。
        旧实现取 id ASC 第一条，导致"管理员后来在某角色给该人改了名"时，
        用户填新名字会被误判不一致。
        """
        clean_phone = (phone or "").strip()
        clean_name = (name or "").strip()
        plat = self._plat(platform) if platform else None
        with self.lock:
            if plat:
                rows = self.conn.execute(
                    "SELECT m.phone, m.name, m.dept FROM member m JOIN role r ON r.role_id = m.role_id "
                    "WHERE m.phone = ? AND r.platform = ? ORDER BY m.id DESC",
                    (clean_phone, plat),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT phone, name, dept FROM member WHERE phone = ? ORDER BY id DESC",
                    (clean_phone,),
                ).fetchall()
        for r in rows:
            if str(r["name"] or "").strip() == clean_name:
                return {"phone": r["phone"], "name": r["name"], "dept": r["dept"]}
        return None

    def get_dedup_enabled_members_for_roles(self, role_ids: List[str], only_activated: bool = False,
                                            platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """取去重后的在册成员。

        only_activated=True 时**只返回已激活的成员**（有可用平台绑定）。
        2026-11-09 用户口径：停用（enabled=0）与未激活（无绑定）的人都不该进任务实例 ——
        他们本来就不会收到通知，生成实例只会让「任务实例」列表出现不会实现的噪音
        （线上 UNACTIVATED 累计 155 条）。

        platform：**按哪个平台判定激活**。
          · 传 None → 用当前通道（self.platform）
          · 传具体平台 → 用该平台（如实例生成时传「模板所属角色平台」）
        ⚠️ 2026-11-09 修正：实例生成必须传「角色所属平台」，否则在密信通道下
        给企微模板建实例时，企微绑定的人会被判成未激活 → 实例数错。

        activated_at 一并带出：2026-11-09 用户口径「当天激活的只创建后面的任务，
        之前的任务实例不要，第二天才是完整一天」—— 需要用它和触发时刻比较。
        """
        clean_ids = [str(r).strip() for r in (role_ids or []) if str(r).strip()]
        if not clean_ids:
            return []
        placeholders = ",".join("?" for _ in clean_ids)
        plat = self._plat(platform)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT m.phone, m.name, m.dept, dt.external_id, dt.activated_at FROM member m "
                f"LEFT JOIN delivery_target dt ON dt.platform = ? AND dt.phone = m.phone AND dt.usable = 1 "
                f"WHERE m.role_id IN ({placeholders}) AND m.enabled = 1 ORDER BY m.id ASC",
                tuple([plat] + clean_ids),
            ).fetchall()
            seen: set[str] = set()
            deduped: List[Dict[str, Any]] = []
            for r in rows:
                ph = r["phone"]
                if ph in seen:
                    continue
                seen.add(ph)
                activated = bool(r["external_id"])
                if only_activated and not activated:
                    continue
                deduped.append({
                    "phone": ph,
                    "name": r["name"],
                    "dept": r["dept"],
                    "external_id": r["external_id"],
                    "activated": activated,
                    "activated_at": r["activated_at"],
                })
            return deduped

    def preview_template_targets(self, template_id: str) -> Dict[str, int]:
        tpl = self.get_template(template_id, include_deleted=False)
        if not tpl:
            raise BizError(404, "NOT_FOUND", "任务模板不存在")
        members = self.get_dedup_enabled_members_for_roles(tpl["role_ids"])
        member_count, activated_count = len(members), sum(1 for m in members if m["activated"])
        return {"member_count": member_count, "activated_count": activated_count, "unactivated_count": member_count - activated_count}

    def list_active_templates(self, include_notice: bool = True, include_adhoc: bool = False) -> List[Dict[str, Any]]:
        """调度链取模板的**唯一入口**（通知/实例生成/提醒/逾期流转全部从这里取）。

        2026-11-12 晚 · 不定期任务（菜单自选）物理隔离：include_adhoc 默认 False——
        定时调度器对 adhoc 模板**绝不主动发消息、绝不生成实例、绝不催报**；
        只有【菜单】取数显式带 include_adhoc=True（此时仍受启用/平台授权约束）。
        """
        with self.lock:
            # 2026-11-05 拍板口径落地：「停用管理员 = 名下模板立即停止派发」。
            # 此前只查模板自身的 enabled/deleted，管理员账号被停用后模板照常发消息（口径未实现的实测缺口）。
            # 一处补齐全调度链生效：通知/实例生成/提醒全部从这里取模板。
            # owner 缺失的历史行（owner_id=0/NULL）不拦 —— COALESCE 视为启用，避免误伤孤儿数据。
            sql = (
                "SELECT t.* FROM task_template t "
                "LEFT JOIN admin_user u ON u.id = t.owner_id "
                "WHERE t.enabled = 1 AND t.deleted = 0 AND COALESCE(u.enabled, 1) = 1"
                + (" AND t.is_adhoc = 0" if not include_adhoc else "")
                + ("" if include_notice else " AND t.kind != 'notice'")
                + " ORDER BY t.template_id ASC"
            )
            templates = [self._deserialize_template_row(r) for r in self.conn.execute(sql).fetchall()]
            if not templates:
                return []
            # 2026-11-06 拍板口径：「收回某平台的授权 = 该平台停发」（与停用账号同效，但仅限该平台）。
            # 模板归属平台由其角色决定；owner 对该平台无授权 → 不进调度列表。
            allowed_by_owner: Dict[int, set] = {}
            for au in self.conn.execute("SELECT id, allowed_platforms FROM admin_user").fetchall():
                try:
                    allowed_by_owner[int(au["id"])] = {
                        str(p).strip().lower() for p in json.loads(str(au["allowed_platforms"] or "[]"))
                        if str(p).strip()
                    }
                except Exception:
                    allowed_by_owner[int(au["id"])] = set(self.ALL_PLATFORMS)
            role_plat: Dict[str, str] = {
                str(rr["role_id"]): str(rr["platform"] or "").strip().lower()
                for rr in self.conn.execute("SELECT role_id, platform FROM role").fetchall()
            }
            out: List[Dict[str, Any]] = []
            for tpl in templates:
                plats = {
                    role_plat[str(rid)]
                    for rid in (tpl.get("role_ids") or [])
                    if str(rid) in role_plat and role_plat[str(rid)]
                }
                owner_id = int(tpl.get("owner_id") or 1)
                # owner 账号不存在（历史/测试数据）→ 与停用判定同口径（COALESCE 视为启用）：不拦
                if owner_id not in allowed_by_owner or not plats:
                    out.append(tpl)
                    continue
                if not plats & allowed_by_owner[owner_id]:
                    continue  # 该模板所属平台的授权已被收回 → 停发
                out.append(tpl)
            return out

    # ── 已激活待归纳人员（2026-10-28 方案B：用户发任意消息自动录入，管理员后续归纳角色）──
    # ── 部门选项（2026-11-03：后台配置，H5 登记页与成员表单共用）──
    def list_departments(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT id, name, created_at FROM dept_option ORDER BY id ASC").fetchall()
        return [{"id": int(r["id"]), "name": str(r["name"]), "created_at": r["created_at"]} for r in rows]

    def build_dept_tree(self) -> List[Dict[str, Any]]:
        """2026-11-12 A3：基于已导入成员的 member.dept_path 聚合部门分级树（超管视图）。

        口径：
        · 数据源 = enabled=1 且 dept_path 非空的名册行（停用成员不算，2026-11-10 拍板），
          按 (phone, dept_path) 去重 —— 同一人在多个角色/多个管理员名下的重复行不重复计数；
        · 按 "/" 分段成树，节点 {name(段名), path(全路径), count(该层级名下人数), children}；
        · count 是**人数**而非行数：含该层级及其全部下级，同一手机号在同一层级只计 1 次
          （同一人跨两条不同 dept_path 时，公共祖先只计 1 人）；
        · dept_path 为空/全空段的成员不计入（尚未反查到官方组织路径）。
        """
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT phone, dept_path FROM member "
                "WHERE enabled = 1 AND dept_path IS NOT NULL AND TRIM(dept_path) <> ''"
            ).fetchall()
        # 手机号 -> 该名下全部 dept_path 的段前缀集合（跨行合并去重，保人数口径）
        phone_prefixes: Dict[str, set] = {}
        for r in rows:
            path = str(r["dept_path"] or "").strip()
            if not path:
                continue
            segs = [s.strip() for s in path.split("/") if s.strip()]
            if not segs:
                continue
            prefixes = phone_prefixes.setdefault(str(r["phone"]), set())
            for i in range(1, len(segs) + 1):
                prefixes.add("/".join(segs[:i]))
        root: Dict[str, Any] = {"name": "", "path": "", "count": 0, "children": {}}
        nodes: Dict[str, Dict[str, Any]] = {"": root}
        for prefixes in phone_prefixes.values():
            for prefix in sorted(prefixes):
                segs = prefix.split("/")
                node = nodes.get(prefix)
                if node is None:
                    parent_path = "/".join(segs[:-1])
                    node = {"name": segs[-1], "path": prefix, "count": 0, "children": {}}
                    nodes[prefix] = node
                    nodes[parent_path]["children"][prefix] = node
                node["count"] = int(node["count"]) + 1

        def _finalize(node: Dict[str, Any]) -> Dict[str, Any]:
            children = [_finalize(node["children"][k]) for k in sorted(node["children"])]
            return {"name": node["name"], "path": node["path"], "count": int(node["count"]), "children": children}

        return [_finalize(root["children"][k]) for k in sorted(root["children"])]

    def department_exists(self, name: str) -> bool:
        clean = (name or "").strip()
        if not clean:
            return False
        with self.lock:
            return self.conn.execute("SELECT 1 FROM dept_option WHERE name = ? LIMIT 1", (clean,)).fetchone() is not None

    def create_department(self, name: str, actor: str = "system") -> Dict[str, Any]:
        clean = (name or "").strip()
        if not clean:
            raise BizError(400, "VALIDATION_FAILED", "部门名称不能为空")
        # 2026-11-12 下午：30 字 → 200 字（官方通讯录部门名常超 30 字，须能正确创建/应用）
        if len(clean) > MAX_DEPT_NAME_LEN:
            raise BizError(400, "VALIDATION_FAILED",
                           f"部门名称过长（最多 {MAX_DEPT_NAME_LEN} 字）")
        now_s = self.now_str()
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM dept_option WHERE name = ?", (clean,)).fetchone():
                raise BizError(409, "DEPT_NAME_TAKEN", "部门名称已存在")
            cur = self.conn.execute("INSERT INTO dept_option(name, created_at) VALUES (?, ?)", (clean, now_s))
            dept_id = int(cur.lastrowid)
            self._insert_audit_unlocked(actor, "dept.create", "dept_option", str(dept_id), {"name": clean}, now_s)
        return {"id": dept_id, "name": clean}

    def update_department(self, dept_id: int, name: str, actor: str = "system") -> Dict[str, Any]:
        clean = (name or "").strip()
        if not clean:
            raise BizError(400, "VALIDATION_FAILED", "部门名称不能为空")
        if len(clean) > MAX_DEPT_NAME_LEN:
            raise BizError(400, "VALIDATION_FAILED",
                           f"部门名称过长（最多 {MAX_DEPT_NAME_LEN} 字）")
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT id, name FROM dept_option WHERE id = ?", (int(dept_id),)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "部门不存在")
            if self.conn.execute("SELECT 1 FROM dept_option WHERE name = ? AND id != ?", (clean, int(dept_id))).fetchone():
                raise BizError(409, "DEPT_NAME_TAKEN", "部门名称已存在")
            self.conn.execute("UPDATE dept_option SET name = ? WHERE id = ?", (clean, int(dept_id)))
            self._insert_audit_unlocked(actor, "dept.update", "dept_option", str(int(dept_id)), {"name": clean}, now_s)
        return {"id": int(dept_id), "name": clean}

    def delete_department(self, dept_id: int, actor: str = "system") -> None:
        """删除部门选项。

        2026-11-06 修补（workbuddy，分支 1af1f3b）：删除前检查引用。
        成员表 `member.dept` 存的是**纯文本**（不是外键），所以删掉部门选项后，
        那些成员在成员表单 / H5 登记页的部门下拉里就**选不到自己当前的部门**。
        因此：仍有成员挂在该部门名下时拒绝删除，并提示先迁移。
        """
        now_s = self.now_str()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT id, name FROM dept_option WHERE id = ?", (int(dept_id),)).fetchone()
            if not row:
                raise BizError(404, "NOT_FOUND", "部门不存在")
            dept_name = str(row["name"] or "")
            # 引用检查：member.dept 是文本快照，按名字统计
            used = int(self.conn.execute(
                "SELECT COUNT(1) FROM member WHERE dept = ?", (dept_name,)).fetchone()[0] or 0)
            if used > 0:
                raise BizError(400, "DEPT_IN_USE",
                               f"该部门下还有 {used} 名成员，请先迁移到其它部门再删除")
            self.conn.execute("DELETE FROM dept_option WHERE id = ?", (int(dept_id),))
            self._insert_audit_unlocked(actor, "dept.delete", "dept_option", str(int(dept_id)), {"name": dept_name}, now_s)

    def phone_in_roster(self, phone: str, platform: Optional[str] = None) -> bool:
        """手机号是否在成员名单里。

        传 platform → 只查该办公软件下的角色名册（2026-11-03 平台隔离口径：
        密信绑定只认密信角色、企微绑定只认企微角色）；
        不传 → 查全局（历史调用兼容）。
        """
        clean = (phone or "").strip()
        if not clean:
            return False
        plat = (platform or "").strip().lower()
        with self.lock:
            if not plat:
                return self.conn.execute("SELECT 1 FROM member WHERE phone = ? LIMIT 1", (clean,)).fetchone() is not None
            return self.conn.execute(
                "SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                "WHERE m.phone = ? AND r.platform = ? LIMIT 1",
                (clean, plat),
            ).fetchone() is not None

    # ── 2026-11-03 新激活模型（后台先导入才能激活）：待归纳池整体废弃 ──
    # ⛔ 不再写入/读取 pending_member；生产历史数据由部署脚本一次性清理。

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

    def list_data_snapshots(self, source_key: Optional[str] = None, period_key: Optional[str] = None, limit: int = 50, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where_clauses, params = ["1=1"], []
        if owner_id is not None:
            # 数据快照的 source_key 即模板 id → 按模板归属过滤
            where_clauses.append("source_key IN (SELECT template_id FROM task_template WHERE owner_id = ?)")
            params.append(int(owner_id))
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
        """该实例的提交窗口是否已关闭。

        2026-11-12 任务E1/E2/E3 口径：
        · rejected（驳回重交）：按重交截止 = max(原截止 + 72h, 驳回 + 24h)（R27，保留不动）；
        · expired / 模板已删除：一律关闭；
        · 其余状态：now > deadline_at 即关闭——无截止模板的 deadline_at 是哨兵
          9999-12-31 23:59:59，字符串比较永不为真 → 永不过期（展示层特判「不限」）。
        """
        now_s = format_dt(now_dt or self.now())
        if instance["status"] == "expired":
            return True
        if instance["status"] == "rejected":
            return now_s > calc_resubmit_deadline(instance["deadline_at"], instance.get("reviewed_at"))
        return now_s > instance["deadline_at"]

    # ── 2026-11-12 任务E3：申请补交全链路下线 ──────────────────────────
    # 原「逾期补交审核制」（list_overdue_in_window / request_resubmit /
    # _load_resubmit_pending / approve_resubmit / reject_resubmit 及配套审核端点）
    # 按用户拍板彻底删除：逾期后统一提示「当前任务已过期（截止时间：…），无法提交」，
    # 不再有补交通道。⛔ 驳回重交（rejected → 窗口内原链接重交）是另一套机制，保留。
    # task_instance.resubmit_* 列保留不动（存量历史数据兼容，只是不再读写）。

    # ── 2026-11-12 晚 · 不定期任务（菜单自选）：取数与一次性链接签发 ──────────

    ADHOC_MENU_COOLDOWN_SECONDS = 60  # 同一（成员, 事项）重复数字签发的防手抖冷却

    def list_adhoc_menu_items(self, phone: str, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """【菜单】清单：当前平台下该用户**所在角色有权填报**的已启用不定期任务事项。

        需求三边界：① 平台天然隔离（角色平台 = 用户查询平台）；② 角色权限约束
        （非模板关联角色成员 → 列表不出现）；③ 菜单编号 = 清单顺序 1..N（同平台同管理员
        下天然唯一）；排序按创建时间稳定，避免两次【菜单】之间编号漂移。
        """
        clean_phone = str(phone or "").strip()
        plat = str(platform or "").strip().lower()
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM task_template WHERE deleted = 0 AND enabled = 1 AND is_adhoc = 1 "
                "AND kind != 'notice' ORDER BY created_at ASC, rowid ASC").fetchall()
            tpls = [self._deserialize_template_row(r) for r in rows]
        out: List[Dict[str, Any]] = []
        for tpl in tpls:
            roles = [str(x).strip() for x in (tpl.get("role_ids") or []) if str(x).strip()]
            if not roles:
                continue
            placeholders = ",".join("?" for _ in roles)
            with self.lock:
                row = self.conn.execute(
                    f"SELECT 1 FROM member m JOIN role r ON r.role_id = m.role_id "
                    f"WHERE m.phone = ? AND m.role_id IN ({placeholders}) AND m.enabled = 1 "
                    f"AND (? = '' OR r.platform = ?) LIMIT 1",
                    (clean_phone, *roles, plat, plat)).fetchone()
            if row is None:
                continue
            out.append({"no": len(out) + 1, "template_id": tpl["template_id"], "name": tpl["name"]})
        return out

    def get_adhoc_menu_item_by_number(self, phone: str, number: int, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """数字回复 → 菜单事项定位（越权/不存在 → None，按「未找到事项」拦截）。"""
        try:
            clean_no = int(str(number).strip())
        except Exception:
            return None
        for it in self.list_adhoc_menu_items(phone, platform=platform):
            if int(it["no"]) == clean_no:
                return it
        return None

    def create_adhoc_instance_and_token(
        self, template_id: str, phone: str, platform: Optional[str] = None, now_dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """数字回复匹配成功 → 现场创建**全新一次性实例**并签发专属 H5 链接（需求一-4 / 五-3）。

        · period_key = `adhoc-<签发时刻>-<随机后缀>`：绕开 (template_id, phone, period_key)
          唯一键 → 同一用户可对同一事项**多次上报**（每次数字回复 = 全新实例+新链接）；
        · deadline_at 固定哨兵：无截止 → 永不逾期、H5 永不显示截止；
        · 链接仅供提交 1 次：提交成功后实例转 submitted/approved → 旧链接 H5 关窗
          「您已完成本次提交」、再提交终审「已上传，请勿重复提交」；
        · 防手抖刷屏：同一（成员, 事项）冷却窗口内已有未完成/刚提交实例 → 不建新实例，
          返回 ok=False + 提示文案（零新写入，满足「重复回调不得产生重复消息」红线）。
        """
        clean_phone = str(phone or "").strip()
        clean_tid = str(template_id or "").strip()
        cur_dt = now_dt or self.now()
        now_s = format_dt(cur_dt)
        cooldown_s = format_dt(cur_dt - timedelta(seconds=self.ADHOC_MENU_COOLDOWN_SECONDS))
        item = None
        for it in self.list_adhoc_menu_items(clean_phone, platform=platform):
            if it["template_id"] == clean_tid:
                item = it
                break
        if item is None:
            raise BizError(403, "FORBIDDEN", "您没有该事项的填报权限")
        tpl = self.get_template(clean_tid, include_deleted=False)
        if not tpl or not tpl.get("is_adhoc") or not tpl.get("enabled"):
            raise BizError(404, "NOT_FOUND", "该事项不存在或已下线")
        roles = [str(x).strip() for x in (tpl.get("role_ids") or []) if str(x).strip()]
        with self.lock, self.conn:
            recent = self.conn.execute(
                "SELECT id, status FROM task_instance WHERE template_id = ? AND phone = ? AND created_at > ? "
                "AND status IN ('pending', 'submitted', 'approved') ORDER BY id DESC LIMIT 1",
                (clean_tid, clean_phone, cooldown_s)).fetchone()
            if recent is not None:
                return {
                    "ok": False, "reason": "cooldown",
                    "message": (
                        f"您刚才已获取过【{tpl['name']}】的提交链接，请先完成本次填报；"
                        f"如需再次上报，请 {self.ADHOC_MENU_COOLDOWN_SECONDS} 秒后再回复对应数字。"
                    ),
                }
            placeholders = ",".join("?" for _ in roles)
            mrow = self.conn.execute(
                f"SELECT name, dept FROM member WHERE phone = ? AND role_id IN ({placeholders}) "
                f"AND enabled = 1 ORDER BY id ASC LIMIT 1",
                (clean_phone, *roles)).fetchone()
            if mrow is None:
                raise BizError(403, "FORBIDDEN", "您没有该事项的填报权限")
            period_key = f"adhoc-{cur_dt.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
            self.conn.execute(
                "INSERT INTO task_instance(template_id, phone, name_snap, dept_snap, period_key, status, deadline_at, created_at, owner_id) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (clean_tid, clean_phone, mrow["name"], mrow["dept"], period_key,
                 NO_DEADLINE_SENTINEL, now_s, int(tpl.get("owner_id") or 1)),
            )
            inst_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        # 令牌在锁外签发（issue_h5_token 自带锁；无截止 → 720h 长链）
        raw_token = self.issue_h5_token(
            phone=clean_phone, template_id=clean_tid, period_key=period_key,
            instance_id=inst_id, purpose="submit",
            ttl_hours=calc_h5_token_ttl_hours({"status": "pending", "deadline_at": NO_DEADLINE_SENTINEL}, cur_dt),
        )
        return {"ok": True, "reason": "issued", "instance_id": inst_id, "period_key": period_key,
                "token": raw_token, "name": tpl["name"]}

    def is_member_active_for_roles(self, phone: str, role_ids: List[str]) -> bool:
        """该手机号在给定角色集合下是否仍有 enabled=1 的名册行。

        2026-11-12 下午（用户拍板）：成员账号被停用/删除 → 其名下未完成任务链接同步关闭。
        """
        clean_roles = [str(r).strip() for r in (role_ids or []) if str(r).strip()]
        clean_phone = str(phone or "").strip()
        if not clean_roles or not clean_phone:
            return False
        placeholders = ",".join("?" for _ in clean_roles)
        with self.lock:
            row = self.conn.execute(
                f"SELECT 1 FROM member WHERE phone = ? AND role_id IN ({placeholders}) AND enabled = 1 LIMIT 1",
                (clean_phone, *clean_roles)).fetchone()
        return row is not None

    def instance_close_reason(self, instance: Optional[Dict[str, Any]], template: Optional[Dict[str, Any]]) -> Optional[str]:
        """2026-11-12 下午（用户拍板）：「任务已关闭」统一拦截闸——返回 None=可用，否则返回统一文案。

        覆盖五类失效（H5 打开/上传/提交三处同口径，且**先于**过期窗口判断）：
          1) 任务模板被删除 —— 物理清除后靠保留的 h5_token 行识别（提示「任务已关闭」而非技术报错）；
          2) 任务模板被停用 —— 重新启用自动恢复；
          3) 成员被移出关联角色（单删/批量删 → 未完成实例已标 cancelled）；
          4) 编辑模板移除关联角色（role_removed_purge 已物理清实例，旧链接凭令牌识别）；
          5) 成员账号被停用（member.enabled=0，名册行还在）。
        """
        if template is None or template.get("deleted"):
            return "任务已关闭"
        if not template.get("enabled"):
            return "任务已关闭"
        if instance is None:
            return "任务已关闭"
        if str(instance.get("status") or "") == "cancelled":
            return "任务已关闭"
        if not self.is_member_active_for_roles(str(instance.get("phone") or ""), template.get("role_ids") or []):
            return "任务已关闭"
        return None

    def list_pending_todo(self, phone: str, platform: Optional[str] = None,
                          now_dt: Optional[datetime] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """2026-11-12 任务E4：【待办】指令的取数。

        查当前用户（按平台名册口径）status ∈ {pending, rejected} 且未截止的上传任务实例，
        按 deadline_at 升序取前 limit 条（封顶防刷屏）。判定口径：
        · 模板未删除且未停用（停用模板不再提醒，视为无待办；已删除模板已把实例关成 expired）；
        · 通知类（notice）不算「待提交任务」；
        · 平台过滤 = 模板所属角色平台（与实例生成同口径）；
        · rejected 也算未完成（驳回重交窗口内仍可交）；
        · 「未截止」：deadline_at > now（哨兵天然恒真）；
          rejected 用重交截止判定（超过重交截止的按 advance 会转 expired，双保险）。
        """
        clean_phone = (phone or "").strip()
        if not clean_phone:
            return []
        now_s = format_dt(now_dt or self.now())
        plat = (platform or "").strip().lower()
        plat_sql = ""
        params: List[Any] = [clean_phone]
        if plat:
            plat_sql = ("AND EXISTS (SELECT 1 FROM role r WHERE r.platform = ? "
                        "AND instr(t.role_ids, r.role_id) > 0) ")
            params.append(plat)
        with self.lock:
            rows = self.conn.execute(
                "SELECT i.*, t.name AS template_name, t.kind AS template_kind "
                "FROM task_instance i "
                "JOIN task_template t ON t.template_id = i.template_id "
                "WHERE i.phone = ? AND i.status IN ('pending', 'rejected') "
                "AND t.kind != 'notice' AND t.enabled = 1 AND t.deleted = 0 "
                + plat_sql +
                "ORDER BY i.deadline_at ASC, i.id ASC LIMIT ?",
                tuple(params + [max(1, int(limit))]),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["kind"] = d.pop("template_kind", None) or "form"
            deadline_at = effective_instance_deadline(d)
            if is_no_deadline(deadline_at) or deadline_at > now_s:
                out.append(d)
        return out

    def submit_h5_task(
        self, *, raw_token: str, fields: Optional[Dict[str, Any]] = None, file_ref: Optional[str] = None,
        note: Optional[str] = None, refs: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        tok = self.get_h5_token(raw_token)
        # 2026-11-12 任务E3：purpose 收窄为 submit（原「补交链接」机制已下线）
        if not tok or tok.get("purpose") != "submit":
            raise BizError(404, "NOT_FOUND", "提交链接无效或不存在")
        cur_dt = self.now()
        now_s = format_dt(cur_dt)
        self.advance_instance_statuses(cur_dt)

        inst = self.get_instance(int(tok["instance_id"])) if tok.get("instance_id") else None
        tpl = self.get_template(inst["template_id"], include_deleted=True) if inst else None
        # 2026-11-12 下午（用户拍板）：五类失效统一拦截「任务已关闭」，先于过期窗口判断；
        # 模板被物理删除/实例被清除时，靠保留的 h5_token 行识别出「任务已关闭」而非技术报错。
        close_reason = self.instance_close_reason(inst, tpl)
        if close_reason:
            raise BizError(403, "TASK_CLOSED", close_reason)
        # 2026-11-12 下午（用户拍板）：H5 页面对已提交/已审核通过的实例**打开放行**（正常渲染表单），
        # 仅在最后一步提交时终审拦截，统一提示「已上传，请勿重复提交」（不动已有历史提交记录）。
        if inst["status"] in ("submitted", "approved"):
            raise BizError(400, "ALREADY_SUBMITTED", "已上传，请勿重复提交")
        # 旧链接令牌已消耗（提交过一次）→ 同口径按重复提交拦截
        if bool(tok["consumed"]):
            raise BizError(400, "ALREADY_SUBMITTED", "已上传，请勿重复提交")
        # 2026-11-12 任务E2：链接过期统一口径（用户拍板，与任务截止不作区分）
        if tok["expires_at"] < now_s:
            raise BizError(400, "TOKEN_EXPIRED", submission_expired_message(inst))
        if inst["status"] == "expired":
            # 提前流转（模板删除/72h 过期等）也已关窗，统一提示
            raise BizError(400, "SUBMISSION_CLOSED", submission_expired_message(inst))
        # 2026-11-12 任务E2/E3：窗口硬拦截——到点后统一提示（错误码 SUBMISSION_CLOSED）。
        # 注意 rejected 走 is_instance_window_closed 的重交截止分支（保留）。
        if self.is_instance_window_closed(inst, cur_dt):
            raise BizError(400, "SUBMISSION_CLOSED", submission_expired_message(inst))

        fields_json_str, up_row = None, None
        # 勾了「文字说明」模块就必须把 note 存下来（否则下面校验必填时会永远判空）
        _note_allowed = bool(tpl.get("allow_note")) or tpl["kind"] == "form" or "text" in required_h5_modules(tpl)
        clean_note = str(note).strip()[:500] if (_note_allowed and note is not None and str(note).strip()) else None

        # 上报类（form）可以同时带多个附件：文字说明走 note，图片/文件走 refs
        clean_refs = [str(r).strip() for r in (refs or []) if str(r or "").strip()] if isinstance(refs, list) else []
        if not clean_refs and file_ref:
            clean_refs = [str(file_ref).strip()]
        ref_rows: List[Dict[str, Any]] = []

        if tpl["kind"] == "form":
            # 2026-11-12 任务B：字段校验与 H5 实际渲染对齐。
            # H5 只在 upload_modules 含 "fields"（或老模板无 upload_modules 时 form 默认渲染
            # fields）时才渲染字段输入框；此前这里无条件校验 schema_json 的 required 字段，
            # 「schema 有字段但 H5 没渲染」的老模板会把提交死锁（页面没有输入源，服务端却要必填）。
            # effective_h5_modules 与 task.html 的 modules 推导同一口径。
            if "fields" in effective_h5_modules(tpl):
                fields_json_str = json.dumps(validate_form_submission_fields(tpl.get("schema_json") or [], fields), ensure_ascii=False)
            else:
                fields_json_str = None  # 未渲染字段模块：不校验、不落 fields（H5 本来就没有输入源）
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
        # 2026-10-30：管理员勾选的模块，用户必须真的完成（服务端硬校验，H5 前端只是先提示一遍）
        _mods = required_h5_modules(tpl)
        if _mods:
            _imgs = [r for r in ref_rows if str(r.get("mime") or "").startswith("image/")]
            _others = [r for r in ref_rows if not str(r.get("mime") or "").startswith("image/")]
            if "image" in _mods and not _imgs:
                raise BizError(400, "VALIDATION_FAILED", "请上传图片后再提交")
            if "file" in _mods and not _others:
                raise BizError(400, "VALIDATION_FAILED", "请上传文件后再提交")
            if "text" in _mods and not clean_note:
                raise BizError(400, "VALIDATION_FAILED", "请填写文字说明后再提交")
        new_status = "submitted" if tpl["need_review"] else "approved"
        late_submit = 1 if (now_s > inst["deadline_at"] or int(inst.get("late_submit") or 0) == 1) else 0

        with self.lock, self.conn:
            # 2026-11-13 冲刺模块四压测实锤修复：「读状态→检查→写」跨锁段组合在多线程并发下
            # 存在竞态（同一令牌并发提交可全部通过前置闸门），同一 H5 链接出现双写。
            # 修复 = **原子 CAS 消费令牌**：UPDATE ... WHERE consumed = 0，抢不到即已被
            # 并发请求消费 → 立即拒绝（幂等红线：提交事务内的互斥，双引擎下均原子）。
            _cas = self.conn.execute(
                "UPDATE h5_token SET consumed = 1 WHERE token_id = ? AND consumed = 0",
                (tok["token_id"],),
            )
            if int(_cas.rowcount or 0) == 0:
                raise BizError(400, "ALREADY_SUBMITTED", "已上传，请勿重复提交")
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
            self.conn.execute(
                "UPDATE task_instance SET status = ?, submitted_at = ?, late_submit = ?, reviewed_at = NULL, reviewer = NULL WHERE id = ?",
                (new_status, now_s, late_submit, int(inst["id"])),
            )

        return {"ok": True, "submission_id": sub_id, "status": new_status, "late_submit": bool(late_submit), "message": "已提交，等待审核" if tpl["need_review"] else "已完成"}

    def review_instance(self, instance_id: int, action: str, reviewer: str, reason: Optional[str] = None) -> Dict[str, Any]:
        act, clean_reason = (action or "").strip().lower(), (reason or "").strip()
        if act not in ("approve", "reject"):
            raise BizError(400, "VALIDATION_FAILED", "action 必须是 approve 或 reject")
        if act == "reject" and len(clean_reason) < 2:
            raise BizError(400, "VALIDATION_FAILED", "驳回原因不少于 2 个字")
        now_s = self.now_str()
        with self.lock, self.conn:
            inst_row = self.conn.execute(
                "SELECT status FROM task_instance WHERE id = ?", (int(instance_id),)
            ).fetchone()
            if not inst_row:
                raise BizError(404, "NOT_FOUND", "任务实例不存在")
            # 2026-10-29 P1-2：只有「已提交、待审核」的实例可以审核。
            # 之前对 pending/approved/expired 也能调，会把已完成的实例改回 approved 或覆盖结论。
            if inst_row["status"] != "submitted":
                raise BizError(
                    400, "VALIDATION_FAILED",
                    f"当前状态为 {inst_row['status']}，仅已提交（submitted）的实例可审核",
                )
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
            # 2026-11-04 多管理员分级：实例归属继承模板的 owner_id（调度器自动建实例时也要带上）
            tpl_owner = self.conn.execute("SELECT owner_id FROM task_template WHERE template_id = ?", (template_id,)).fetchone()
            owner_val = int(tpl_owner["owner_id"]) if tpl_owner else 1
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO task_instance(template_id, phone, name_snap, dept_snap, period_key, status, deadline_at, created_at, owner_id) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (template_id, phone, name_snap, dept_snap, period_key, deadline_at, now_s, owner_val),
            )
            created = cur.rowcount > 0
            res = dict(self.conn.execute("SELECT * FROM task_instance WHERE template_id = ? AND phone = ? AND period_key = ?", (template_id, phone, period_key)).fetchone())
            res["created"] = created
            return res

    def _template_owner_enabled(self, owner_id: int) -> bool:
        """模板所属管理员账号是否处于启用状态。

        2026-11-06（workbuddy，分支 1af1f3b）：账号不存在（历史行 owner_id 指向已删账号）
        时按**启用**处理，避免误伤孤儿数据 —— 与 list_active_templates 里
        COALESCE(u.enabled, 1) 的口径一致。
        """
        try:
            aid = int(owner_id or 1)
        except Exception:
            return True
        with self.lock:
            row = self.conn.execute("SELECT enabled FROM admin_user WHERE id = ?", (aid,)).fetchone()
        if row is None:
            return True
        # ⚠ 不能写 `int(row["enabled"] or 1)`：0（停用）会被 `or` 短路成 1，停用判定整个失效。
        v = row["enabled"]
        return True if v is None else int(v) == 1

    def _owner_allowed_platforms(self, owner_id: int) -> set:
        """管理员账号被授权的办公软件平台集合（账号不存在/授权数据坏 → 全平台，fail-open 不误伤）。"""
        try:
            aid = int(owner_id or 1)
        except Exception:
            return set(self.ALL_PLATFORMS)
        with self.lock:
            row = self.conn.execute("SELECT allowed_platforms FROM admin_user WHERE id = ?", (aid,)).fetchone()
        if row is None:
            return set(self.ALL_PLATFORMS)
        try:
            plats = json.loads(str(row["allowed_platforms"] or "[]"))
        except Exception:
            return set(self.ALL_PLATFORMS)
        return {str(p).strip().lower() for p in plats if str(p).strip()}

    def _template_platform_allowed(self, tpl: Dict[str, Any]) -> bool:
        """2026-11-06 拍板口径：模板所属平台的授权被收回 → 该平台停发（与停用账号同效，但仅限该平台）。"""
        plats = self.role_platforms(tpl.get("role_ids") or [])
        if not plats:
            return True
        return bool(plats & self._owner_allowed_platforms(int(tpl.get("owner_id") or 1)))

    def ensure_template_instances(self, template_id: str, period_key: str, deadline_at: str,
                                  now_str: Optional[str] = None,
                                  trigger_at: Optional[str] = None) -> List[Dict[str, Any]]:
        """为模板生成当期实例。

        2026-11-09 用户口径（三条）：
        ① 停用成员不生成（enabled=0，由取数层过滤）
        ② 未激活成员不生成（无平台绑定 → 本来就收不到通知，生成只是噪音）
        ③ 当天激活的成员**只创建激活时刻之后**的任务实例，之前的不要；
           第二天才是完整一天。
           → 用 delivery_target.activated_at 与「本次触发时刻」比较：
             trigger_at 是这次任务的实际触发时刻（notice 用通知时刻，form 用生成时刻）。

        now_str：当前时间（默认取系统时间），用于判定激活时刻是否落在当天。
        trigger_at：本次触发时刻（默认等于 now_str）。
        """
        tpl = self.get_template(template_id, include_deleted=False)
        # 2026-10-28 方案A：notice 也建实例（pending → sent），不再在这里拦掉
        if not tpl or not tpl["enabled"]:
            return []
        # 2026-11-06 纵深防御（workbuddy）：模板所属管理员被停用 → 也不生成实例。
        # 此前只在 list_active_templates 拦（真实调度链只经那里，业务上不漏发），
        # 但直接调用本函数可绕过 → 这里补一道，避免将来新增调用点时静默漏发。
        if not self._template_owner_enabled(int(tpl.get("owner_id") or 1)):
            return []
        # 2026-11-06 拍板口径：模板所属平台的授权被收回 → 同样不生成（仅停该平台，其它平台不受影响）。
        if not self._template_platform_allowed(tpl):
            return []
        # 2026-11-09：只取已激活成员（未激活不建实例）。
        # 平台判据 = **模板所属角色平台**（不是当前通道）——
        # 否则在密信通道下给企微模板建实例时，企微绑定的人会被判成未激活。
        # 2026-11-09：激活判定用**模板所属角色平台**，不是当前通道。
        # 跨平台模板 → 各平台分别取已激活成员，按手机号去重（任一平台已激活即可）。
        tpl_plats = sorted(self.role_platforms(tpl.get("role_ids") or [])) or [None]
        members: List[Dict[str, Any]] = []
        for _p in tpl_plats:
            for _m in self.get_dedup_enabled_members_for_roles(
                    tpl["role_ids"], only_activated=True, platform=_p):
                if not any(x["phone"] == _m["phone"] for x in members):
                    members.append(_m)
        now_s = now_str or self.now_str()
        trigger = trigger_at or now_s
        with self.lock, self.conn:
            # 2026-11-09：只有「本周期就是今天」时才按激活时刻切分。
            # period_key 是历史日期（补跑）或跨天周期时不做这个过滤 ——
            #   · 历史周期：那天该不该有实例由当时的状态决定，现在补跑就补上
            #   · 第二天：period_key 变了，激活时刻已在前一天 → 完整一天都建
            #     （对应用户口径「第二天才是刷新完整一天」）
            period_is_today = _same_day(period_key, now_s)
            for m in members:
                # 2026-11-09：当天激活的成员只建「激活时刻之后」的实例
                # 判定：激活时刻**晚于**本次触发时刻 → 这次的任务还没轮到他 → 跳过
                #   （10:00 激活的人：收不到 09:00 那条，收得到 12:00 那条）
                act_at = str(m.get("activated_at") or "").strip()
                if act_at and period_is_today:
                    if _same_day(act_at, now_s) and _lt_time(trigger, act_at):
                        continue
                self.conn.execute(
                    "INSERT OR IGNORE INTO task_instance(template_id, phone, name_snap, dept_snap, period_key, status, deadline_at, created_at, owner_id) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (template_id, m["phone"], m["name"], m["dept"], period_key, deadline_at, now_s, int(tpl.get("owner_id") or 1)),
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
            # 通知类：当天 23:59 过了还没发出去的，标成 expired（未送达），避免永久 pending
            c_n = self.conn.execute(
                "UPDATE task_instance SET status = 'expired' WHERE status = 'pending' AND deadline_at < ? "
                "AND template_id IN (SELECT template_id FROM task_template WHERE kind = 'notice')",
                (now_s,),
            )
            c2 = self.conn.execute(
                f"UPDATE task_instance SET status = 'expired' WHERE status = 'overdue' AND deadline_at < ? AND {not_notice}",
                (cutoff_72h,),
            )
            to_expired = int(c2.rowcount or 0) + int(c_n.rowcount or 0)
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

    MANUAL_PUSH_COOLDOWN_SECONDS = 120  # 2026-11-12 任务D：同模板一键推送冷却窗口（防手抖）

    def check_manual_push_cooldown(
        self, template_id: str, actor: str = "system", now_dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """2026-11-12 任务D：一键推送软护栏——同模板 2 分钟内拒绝重复推送（429）。

        判定口径：reminder_log 中该模板 kind='manual_push' 最近一次记录（任意成员、任意结果
        ——failed/missed 也算「点过」，防手抖优先），attempted_at 距今 < 120s 即拒绝。
        通过后**当场记一条 marker**（无实例、phone=操作人、idem 唯一），空名单模板也计入冷却，
        不给「连点 10 次只有 1 次真发出去」留缝。返回时间基线给调用方审计与幂等键。
        """
        now_s = format_dt(now_dt or self.now())
        clean_id = (template_id or "").strip()
        # 读判 + 占位记账放同一个锁事务内：并发双击只放一个过（防抖护栏不许有窗口）
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT attempted_at FROM reminder_log WHERE template_id = ? AND kind = 'manual_push' ORDER BY id DESC LIMIT 1",
                (clean_id,),
            ).fetchone()
            if row is not None:
                last_ts = str(row["attempted_at"] or "").strip()
                if last_ts:
                    elapsed = (parse_dt(now_s) - parse_dt(last_ts)).total_seconds()
                    if 0 <= elapsed < self.MANUAL_PUSH_COOLDOWN_SECONDS:
                        raise BizError(
                            429, "PUSH_COOLDOWN",
                            f"该模板 2 分钟内已推送过一次，请稍后再试（上次推送：{last_ts[11:16]}）",
                        )
            self.conn.execute(
                "INSERT OR IGNORE INTO reminder_log("
                "template_id, instance_id, phone, kind, planned_at, attempted_at, sent_at, result, error, retry, idem_key"
                ") VALUES (?, NULL, ?, 'manual_push', ?, ?, NULL, 'sent', 'push_marker', 0, ?)",
                (clean_id, (actor or "system").strip()[:60], now_s, now_s,
                 f"push_marker|{clean_id}|{now_s}|{secrets.token_hex(6)}"),
            )
        return {"attempted_at": now_s}

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

    def list_reminder_logs(self, template_id: Optional[str] = None, instance_id: Optional[int] = None, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        if owner_id is not None:
            # 2026-11-04 多管理员分级：提醒日志按模板归属过滤（reminder_log 自身无 owner 列）
            where.append("EXISTS (SELECT 1 FROM task_template t WHERE t.template_id = reminder_log.template_id AND t.owner_id = ?)")
            params.append(int(owner_id))
        if template_id is not None:
            where.append("template_id = ?")
            params.append(template_id)
        if instance_id is not None:
            where.append("instance_id = ?")
            params.append(int(instance_id))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with self.lock:
            return [dict(r) for r in self.conn.execute(f"SELECT * FROM reminder_log {where_sql} ORDER BY id ASC", tuple(params)).fetchall()]

    # ==================================================================
    # 2026-11-13 冲刺模块三：多维自适应统计看板（四 KPI / 报数-文本-附件三视图 /
    # 督办红榜 / 超管只读全局 + 分级管理员按 owner_id 隔离）
    # ==================================================================
    def stats_dashboard(
        self,
        owner_id: Optional[int] = None,
        template_id: Optional[str] = None,
        period_key: Optional[str] = None,
        red_list_limit: int = 50,
    ) -> Dict[str, Any]:
        """统计看板聚合（单次取数，三段：kpis / views / red_list）。

        · KPI 四件：应交总数 / 已交数 / 完成率 / 按时率（含平均提交耗时分钟数）；
        · 三视图：numbers（数值字段）/ texts（文字说明）/ files（附件）——
          数据来自 submission（fields_json / note / refs_json），按提交人聚合；
        · 督办红榜：form 类已过期仍未交（pending/overdue 且 deadline 已过）按滞留时间倒序；
        · owner_id：分级管理员数据隔离（None = 超管全局只读口径）。
        """
        where, params = [], []
        if owner_id is not None:
            where.append("i.owner_id = ?")
            params.append(int(owner_id))
        if template_id:
            where.append("i.template_id = ?")
            params.append(template_id)
        if period_key:
            where.append("i.period_key = ?")
            params.append(period_key)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        kpis: Dict[str, Any] = {
            "should_submit": 0, "submitted": 0, "approved": 0, "rejected": 0,
            "pending": 0, "overdue": 0, "late_submit": 0,
            "completion_rate": 0.0, "on_time_rate": 0.0, "avg_submit_minutes": None,
        }
        numbers: List[Dict[str, Any]] = []
        texts: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        red_list: List[Dict[str, Any]] = []
        now_s = self.now_str()

        with self.lock:
            rows = self.conn.execute(
                f"SELECT i.status AS status, COUNT(1) AS cnt, "
                f"SUM(CASE WHEN i.late_submit = 1 THEN 1 ELSE 0 END) AS late_cnt "
                f"FROM task_instance i {where_sql} GROUP BY i.status",
                tuple(params),
            ).fetchall()
            total = done = late_total = 0
            for r in rows:
                st, cnt = str(r["status"]), int(r["cnt"] or 0)
                total += cnt
                late_total += int(r["late_cnt"] or 0)
                if st in ("submitted", "approved"):
                    done += cnt
                if st in kpis:
                    kpis[st] = cnt
            kpis["should_submit"] = total
            kpis["submitted"] = done
            kpis["late_submit"] = late_total
            if total:
                kpis["completion_rate"] = round(done / total * 100.0, 1)
                kpis["on_time_rate"] = round((total - late_total) / total * 100.0, 1)

            # 平均提交耗时：双引擎兼容（不用 julianday/sqlite 专属函数），Python 侧相减
            avg_rows = self.conn.execute(
                f"SELECT i.created_at AS created_at, i.submitted_at AS submitted_at "
                f"FROM task_instance i {where_sql} AND i.submitted_at IS NOT NULL",
                tuple(params) if where else (),
            ).fetchall() if where else self.conn.execute(
                "SELECT i.created_at AS created_at, i.submitted_at AS submitted_at "
                "FROM task_instance i WHERE i.submitted_at IS NOT NULL"
            ).fetchall()
            minutes: List[float] = []
            for r in avg_rows:
                try:
                    created = parse_dt(str(r["created_at"]))
                    submitted = parse_dt(str(r["submitted_at"]))
                    delta_min = (submitted - created).total_seconds() / 60.0
                    if delta_min >= 0:
                        minutes.append(delta_min)
                except Exception:  # noqa: BLE001
                    continue
            if minutes:
                kpis["avg_submit_minutes"] = round(sum(minutes) / len(minutes), 1)

            # 三视图：报数（fields_json 数值）/ 文本（note）/ 附件（refs_json）
            sub_rows = self.conn.execute(
                f"SELECT i.phone AS phone, i.name_snap AS name, i.dept_snap AS dept, "
                f"i.template_id AS template_id, i.period_key AS period_key, i.status AS status, "
                f"s.fields_json AS fields_json, s.note AS note, s.refs_json AS refs_json, s.mime AS mime "
                f"FROM task_instance i JOIN submission s ON s.instance_id = i.id "
                f"WHERE s.id = (SELECT MAX(s2.id) FROM submission s2 WHERE s2.instance_id = i.id) "
                f"AND ({' AND '.join(['1=1'] + where) if where else '1=1'})",
                tuple(params),
            ).fetchall()
            for r in sub_rows:
                base = {
                    "phone": r["phone"], "name": r["name"], "dept": r["dept"] or "",
                    "template_id": r["template_id"], "period_key": r["period_key"],
                    "status": r["status"],
                }
                fjson = str(r["fields_json"] or "")
                if fjson:
                    try:
                        fvals = json.loads(fjson)
                    except Exception:  # noqa: BLE001
                        fvals = {}
                    for k, v in (fvals or {}).items():
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            numbers.append({**base, "field": k, "value": v})
                        elif isinstance(v, str) and _is_number_str(v):
                            numbers.append({**base, "field": k, "value": float(v)})
                        elif isinstance(v, str) and v.strip():
                            texts.append({**base, "text": v.strip()})
                if r["note"] and str(r["note"]).strip():
                    texts.append({**base, "text": str(r["note"]).strip()})
                rjson = str(r["refs_json"] or "")
                if rjson:
                    try:
                        refs = json.loads(rjson)
                    except Exception:  # noqa: BLE001
                        refs = []
                    if refs:
                        files.append({**base, "file_count": len(refs), "refs": refs})

            # 督办红榜：form 类已过截止仍未交（pending/overdue），滞留小时 Python 侧算（双引擎兼容）
            red_where = [w for w in where]
            red_params = list(params)
            red_where.append("t.kind = 'form'")
            red_where.append("i.status IN ('pending', 'overdue')")
            red_where.append("i.deadline_at < ?")
            red_params.append(now_s)
            red_where.append("i.deadline_at < '9999-12-31 23:59:59'")  # 哨兵截止（不限）不进红榜
            red_rows = self.conn.execute(
                f"SELECT i.phone AS phone, i.name_snap AS name, i.dept_snap AS dept, "
                f"t.name AS template_name, i.deadline_at AS deadline_at "
                f"FROM task_instance i JOIN task_template t ON t.template_id = i.template_id "
                f"WHERE {' AND '.join(red_where)} "
                f"ORDER BY i.deadline_at ASC LIMIT ?",
                tuple(red_params + [int(red_list_limit)]),
            ).fetchall()
            try:
                now_dt = parse_dt(now_s)
            except Exception:  # noqa: BLE001
                now_dt = self.now()
            for r in red_rows:
                overdue_hours = 0
                try:
                    overdue_hours = int((now_dt - parse_dt(str(r["deadline_at"]))).total_seconds() // 3600)
                except Exception:  # noqa: BLE001
                    pass
                red_list.append({
                    "phone": r["phone"], "name": r["name"], "dept": r["dept"] or "",
                    "template_name": r["template_name"], "deadline_at": r["deadline_at"],
                    "overdue_hours": overdue_hours,
                })
            red_list.sort(key=lambda x: -x["overdue_hours"])

        return {
            "kpis": kpis,
            "views": {"numbers": numbers, "texts": texts, "files": files},
            "red_list": red_list,
        }
