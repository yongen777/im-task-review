# -*- coding: utf-8 -*-
"""通道配置存储（2026-10-30）。

目标：把「凭证从哪来」从 .env 挪到数据库，换一家公司部署时后台填表即可，不改代码。

三条硬约束（用户红线）：
1. ⛔ 凭证明文入库 —— payload 一律 AES-256-GCM 加密后再落库；
2. ⛔ 密钥与密文分离 —— 密钥在 env `IMT_ENC_KEY` 或 `data/config_key.hex`（chmod 600），
   数据库里只有密文；别人只拿到 im_task.db 解不开；
3. ⛔ 不改调度/发送/回调逻辑 —— 本模块只负责「读出来拼成一个 Config」，
   上层拿到的东西和从 env 读出来的完全同构。

现网零行为变化：DB 无该通道记录时返回 None，调用方继续用 env 值。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Crypto.Cipher import AES

from .config import Config

logger = logging.getLogger(__name__)

KEY_ENV = "IMT_ENC_KEY"
KEY_FILENAME = "config_key.hex"
BLOB_PREFIX = "v1:"
META_CHANNEL = "__meta__"  # 保留行：存当前启用通道等非凭证元数据

# 每个通道的字段定义：前台按这个渲染表单，后台按这个校验必填、决定哪些要打码。
# secret=True 的字段：接口只回掩码，⛔ 永不回完整值。
CHANNEL_SCHEMA: Dict[str, Dict[str, Any]] = {
    "zdxlz": {
        "label": "量子密信",
        "callback_path": "/api/app-events",
        "fields": [
            {"key": "app_id", "label": "AppID", "required": True, "secret": True,
             "hint": "量子密信开放平台分配的应用 ID"},
            {"key": "app_secret", "label": "AppSecret", "required": True, "secret": True,
             "hint": "应用密钥，仅首次可复制"},
            {"key": "verify_token", "label": "回调 Token", "required": False, "secret": True,
             "hint": "与平台「事件订阅」里配置的 Token 一致，用于事件验签"},
            {"key": "encrypt_key", "label": "回调 EncryptKey", "required": False, "secret": True,
             "hint": "事件加密密钥（原样填写，不做 base64 解码）"},
            {"key": "api_base", "label": "API 基础地址", "required": False, "secret": False,
             "hint": "留空用默认 https://imtwo.zdxlz.com"},
        ],
    },
    "wecom": {
        "label": "企业微信（自建应用）",
        "callback_path": "/api/wecom/callback",
        "fields": [
            {"key": "corpid", "label": "企业 ID（corpid）", "required": True, "secret": True,
             "hint": "管理后台 → 我的企业 → 企业信息 → 企业 ID"},
            {"key": "agentid", "label": "应用 AgentId", "required": True, "secret": False,
             "hint": "必须是整数，例如 1000002"},
            {"key": "secret", "label": "应用 Secret", "required": True, "secret": True,
             "hint": "应用详情页的 Secret"},
            {"key": "token", "label": "回调 Token", "required": False, "secret": True,
             "hint": "应用「接收消息」里自己填的 Token"},
            {"key": "aes_key", "label": "回调 EncodingAESKey", "required": False, "secret": True,
             "hint": "43 位，应用「接收消息」里随机生成"},
        ],
    },
}

# DB payload 字段 → Config 字段（None 表示需要特殊处理）
_FIELD_MAP: Dict[str, Dict[str, str]] = {
    "zdxlz": {
        "app_id": "app_id",
        "app_secret": "app_secret",
        "verify_token": "verify_token",
        "encrypt_key": "encrypt_key",
        "api_base": "api_base",
    },
    "wecom": {
        "corpid": "wecom_corpid",
        "agentid": "wecom_agentid",
        "secret": "wecom_secret",
        "token": "wecom_token",
        "aes_key": "wecom_aes_key",
    },
}


# ══════════════════════════════════════════════════════════════════
# 加密密钥
# ══════════════════════════════════════════════════════════════════
def _parse_key(raw: str) -> Optional[bytes]:
    """支持 64 位 hex、标准 base64、urlsafe base64 三种形态，长度必须 32 字节。"""
    s = (raw or "").strip()
    if not s:
        return None
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            pad = "=" * (-len(s) % 4)
            key = decoder(s + pad)
            if len(key) == 32:
                return key
        except Exception:  # noqa: BLE001 - 格式非法就走下一种
            continue
    return None


def load_or_create_key(data_dir: Path) -> bytes:
    """取加密密钥：env 优先 → 文件 → 首次生成。

    ⛔ 密钥绝不写进数据库：DB 泄漏但密钥文件不在，密文就是一堆随机字节。
    """
    env_raw = os.environ.get(KEY_ENV, "").strip()
    if env_raw:
        key = _parse_key(env_raw)
        if key is not None:
            return key
        logger.error("%s 格式无法识别（需 64 位 hex 或 32 字节 base64），改用文件密钥", KEY_ENV)

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    key_file = data_dir / KEY_FILENAME
    if key_file.is_file():
        try:
            key = _parse_key(key_file.read_text(encoding="utf-8"))
            if key is not None:
                return key
            logger.error("密钥文件 %s 内容损坏，将重新生成（旧密文将无法解密）", key_file)
        except OSError as exc:
            logger.error("读取密钥文件失败 %s: %s", key_file, exc)

    key = secrets.token_bytes(32)
    key_file.write_text(key.hex(), encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)  # Windows 上是 no-op，Linux 上生效
    except OSError:
        pass
    logger.warning("首次启动：已生成通道配置加密密钥 %s（请与数据库分开备份）", key_file)
    return key


# ══════════════════════════════════════════════════════════════════
# AES-256-GCM
# ══════════════════════════════════════════════════════════════════
def encrypt_json(key: bytes, data: Dict[str, Any], *, aad: str = "") -> str:
    """加密成 `v1:base64(nonce|tag|ciphertext)`。

    AAD 绑定通道名：把 wecom 的密文抄到 zdxlz 行上会解密失败，防错位。
    """
    plain = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    nonce = secrets.token_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    if aad:
        cipher.update(aad.encode("utf-8"))
    ct, tag = cipher.encrypt_and_digest(plain)
    return BLOB_PREFIX + base64.b64encode(nonce + tag + ct).decode("ascii")


def decrypt_json(key: bytes, blob: str, *, aad: str = "") -> Dict[str, Any]:
    raw = (blob or "").strip()
    if not raw.startswith(BLOB_PREFIX):
        raise ValueError("密文格式不认识（缺少版本前缀）")
    payload = base64.b64decode(raw[len(BLOB_PREFIX):])
    if len(payload) < 28:
        raise ValueError("密文长度不足")
    nonce, tag, ct = payload[:12], payload[12:28], payload[28:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    if aad:
        cipher.update(aad.encode("utf-8"))
    plain = cipher.decrypt_and_verify(ct, tag)  # 篡改/密钥不对都会抛异常
    data = json.loads(plain.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("解密结果不是对象")
    return data


# ══════════════════════════════════════════════════════════════════
# 掩码与校验
# ══════════════════════════════════════════════════════════════════
def mask_value(value: str) -> str:
    """`143e4c0b...e8bb` → `143e****e8bb`；太短一律 `****`，⛔ 绝不回完整值。"""
    s = str(value or "")
    if not s:
        return ""
    if len(s) >= 12:
        return f"{s[:4]}****{s[-4:]}"
    return "****"


def normalize_payload(channel: str, raw: Dict[str, Any]) -> Dict[str, str]:
    """只保留 schema 里声明过的字段，去空白；未知字段直接丢弃（防塞垃圾进库）。"""
    schema = CHANNEL_SCHEMA.get(channel)
    if schema is None:
        raise ValueError(f"不支持的通道：{channel}")
    out: Dict[str, str] = {}
    for f in schema["fields"]:
        val = raw.get(f["key"])
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out[f["key"]] = text
    return out


def validate_payload(channel: str, payload: Dict[str, str]) -> Optional[str]:
    """返回可读错误信息；None 表示通过。错误文案要能直接给填表的人看。"""
    schema = CHANNEL_SCHEMA.get(channel)
    if schema is None:
        return f"不支持的通道：{channel}"
    for f in schema["fields"]:
        if f["required"] and not payload.get(f["key"]):
            return f"缺少必填项：{f['label']}"
    if channel == "wecom":
        agentid = payload.get("agentid", "")
        if agentid and not agentid.isdigit():
            return "agentid 必须是整数（例如 1000002）"
        aes_key = payload.get("aes_key", "")
        if aes_key and len(aes_key) != 43:
            return f"EncodingAESKey 必须是 43 位（当前 {len(aes_key)} 位）"
    if channel == "zdxlz":
        api_base = payload.get("api_base", "")
        if api_base and not api_base.startswith(("http://", "https://")):
            return "API 基础地址必须以 http:// 或 https:// 开头"
    return None


def masked_view(channel: str, payload: Dict[str, str]) -> Dict[str, str]:
    """给前端看的字段视图：secret 字段只回掩码，其余原样。"""
    schema = CHANNEL_SCHEMA.get(channel) or {"fields": []}
    out: Dict[str, str] = {}
    for f in schema["fields"]:
        val = str(payload.get(f["key"]) or "")
        if not val:
            continue
        out[f["key"]] = mask_value(val) if f["secret"] else val
    return out


def channel_configured(channel: str, payload: Optional[Dict[str, str]]) -> bool:
    """必填项齐全才算已配置（和 Config.channel_configured 同口径）。"""
    if not payload:
        return False
    return validate_payload(channel, payload) is None


# ══════════════════════════════════════════════════════════════════
# 存取
# ══════════════════════════════════════════════════════════════════
class ChannelConfigStore:
    """channel_config 表的读写门面。所有写入都走这里，保证永远加密。"""

    def __init__(self, conn, lock, data_dir: Path) -> None:
        self.conn = conn
        self.lock = lock
        self.data_dir = Path(data_dir)
        self._key: Optional[bytes] = None

    # ── 密钥（懒加载，避免每次读都碰文件） ──────────────────────────
    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = load_or_create_key(self.data_dir)
        return self._key

    def _row(self, channel: str):
        with self.lock:
            return self.conn.execute(
                "SELECT channel, payload, updated_at FROM channel_config WHERE channel = ?",
                (channel,),
            ).fetchone()

    def read(self, channel: str) -> Optional[Dict[str, str]]:
        """解出一条通道的凭证；不存在或解不开都返回 None（调用方退回 env）。"""
        row = self._row(channel)
        if row is None:
            return None
        try:
            data = decrypt_json(self.key, row["payload"], aad=channel)
        except Exception as exc:  # noqa: BLE001 - 密钥换过/密文损坏
            logger.error("通道 %s 配置解密失败（密钥是否变更过？）: %s", channel, exc)
            return None
        return {k: str(v) for k, v in data.items() if str(v).strip()}

    def read_all(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for name in CHANNEL_SCHEMA:
            data = self.read(name)
            if data:
                out[name] = data
        return out

    def save(self, channel: str, payload: Dict[str, str], *, now_str: str) -> Dict[str, str]:
        """合并保存：没传的字段保留原值（编辑时不必重复填 secret）。"""
        if channel not in CHANNEL_SCHEMA:
            raise ValueError(f"不支持的通道：{channel}")
        existing = self.read(channel) or {}
        merged = {**existing, **normalize_payload(channel, payload)}
        err = validate_payload(channel, merged)
        if err:
            raise ValueError(err)
        blob = encrypt_json(self.key, merged, aad=channel)
        with self.lock:
            self.conn.execute(
                "INSERT INTO channel_config(channel, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(channel) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (channel, blob, now_str),
            )
            self.conn.commit()
        return merged

    # ── 当前启用通道（元数据行，不含凭证） ─────────────────────────
    def read_active_channel(self) -> Optional[str]:
        row = self._row(META_CHANNEL)
        if row is None:
            return None
        try:
            data = decrypt_json(self.key, row["payload"], aad=META_CHANNEL)
        except Exception:  # noqa: BLE001
            return None
        val = str(data.get("active_channel") or "").strip().lower()
        return val if val in CHANNEL_SCHEMA else None

    def save_active_channel(self, channel: str, *, now_str: str) -> None:
        if channel not in CHANNEL_SCHEMA:
            raise ValueError(f"不支持的通道：{channel}")
        blob = encrypt_json(self.key, {"active_channel": channel}, aad=META_CHANNEL)
        with self.lock:
            self.conn.execute(
                "INSERT INTO channel_config(channel, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(channel) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (META_CHANNEL, blob, now_str),
            )
            self.conn.commit()


# ══════════════════════════════════════════════════════════════════
# 启动期合并：DB 优先，env 兜底
# ══════════════════════════════════════════════════════════════════
def apply_db_channel_config(cfg: Config, store=None) -> Config:
    """把库里已配置的通道合并进 Config（DB 覆盖 env）。

    · DB 空 → 原样返回 cfg（现网行为零变化）；
    · 只覆盖「库里真实存在且非空」的字段，避免把 env 里配好的字段冲成空；
    · 返回新对象（dataclasses.replace），不改原 cfg，便于测试与回滚。
    """
    from dataclasses import replace

    data_dir = cfg.data_dir
    try:
        store_conn = getattr(store, "conn", None) if store is not None else None
        if store_conn is None:
            import sqlite3

            conn = sqlite3.connect(str(cfg.db_path))
            conn.row_factory = sqlite3.Row
            lock = __import__("threading").RLock()
            own = True
        else:
            conn, lock, own = store_conn, getattr(store, "lock"), False

        cc = ChannelConfigStore(conn, lock, data_dir)
        stored = cc.read_all()
        active = cc.read_active_channel()
        if own:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 配置读不出来不能拖垮启动
        logger.error("读取数据库通道配置失败，全部退回 env：%s", exc)
        return cfg

    if not stored and not active:
        return cfg

    changes: Dict[str, Any] = {}
    for channel, payload in stored.items():
        for src, dst in _FIELD_MAP.get(channel, {}).items():
            val = str(payload.get(src) or "").strip()
            if val:
                changes[dst] = val
        logger.info("通道配置来源：数据库（%s，%d 个字段）", channel, len(payload))

    # 通道选择：env 里显式给了 IMT_CHANNEL 就尊重 env（现网/运维可控），否则用库里记住的
    env_channel = os.environ.get("IMT_CHANNEL", "").strip().lower()
    if not env_channel and active:
        changes["channel"] = active
        logger.info("启用通道：%s（来自数据库）", active)

    if not changes:
        return cfg
    try:
        return replace(cfg, **changes)
    except Exception as exc:  # noqa: BLE001
        logger.error("合并通道配置失败，退回 env：%s", exc)
        return cfg


def build_callback_url(public_base_url: str, channel: str) -> str:
    path = (CHANNEL_SCHEMA.get(channel) or {}).get("callback_path", "")
    base = (public_base_url or "").rstrip("/")
    return f"{base}{path}" if base and path else ""


def field_map(channel: str) -> Dict[str, str]:
    """DB payload 字段名 → Config 字段名（供 admin 侧读 env 现状用）。"""
    return dict(_FIELD_MAP.get(channel, {}))


def schema_for_api() -> List[Dict[str, Any]]:
    """给前端渲染表单用的完整 schema（不含任何凭证）。"""
    return [
        {
            "channel": name,
            "label": meta["label"],
            "callback_path": meta["callback_path"],
            "fields": meta["fields"],
        }
        for name, meta in CHANNEL_SCHEMA.items()
    ]


def mask_all(payload: Optional[Dict[str, str]], channel: str) -> Dict[str, str]:
    return masked_view(channel, payload or {})


def key_source() -> Tuple[str, str]:
    """(来源, 说明)；来源 ∈ env / file / none —— 用于后台显示「密钥在哪」。"""
    if os.environ.get(KEY_ENV, "").strip():
        return "env", f"环境变量 {KEY_ENV}"
    return "file", KEY_FILENAME
