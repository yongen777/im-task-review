# -*- coding: utf-8 -*-
"""量子密信开放接口「响应信封」官方解密（decryptV2 规则，2026-11-10 从官方文档取得并实测验证）。

来源：开放平台文档《API 回参解密说明》（api-response-decryption.md），算法原文：

  签名密钥 = AppId 的 UTF-8 字节做 SHA-512，再标准 Base64；使用**该字符串**的 UTF-8 字节
  签名输入 = timeStamp + nonce + encrypt（直接连接，无分隔符）
  签名     = HMAC-SHA256 → 小写十六进制；校验失败停止
  AES 密钥 = SHA-256( UTF-8( Base64(SHA-512(AppSecret)) + ":" + timeStamp + ":" + nonce ) )
  密文     = encrypt 为 URL-safe Base64，支持末尾缺失填充
  IV       = 解码结果前 16 字节；其余为 AES-256-CBC 密文；PKCS7 去填充

文档同时明确：本算法**不依赖密服账号、量子 SDK 或密码设备**（2026-10-27 工单
「密钥疑由密服平台托管、本地解不开」的结论由此作废——当日实测两类真实信封全部解开）。

⚠️ 三个加密环节不要混：本模块只管「服务端 API 响应信封」；
   回调传输层验签解密在 zdxlz.py；量子消息正文信封走官方 SDK。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict


class ResponseCryptoError(Exception):
    """响应信封验签 / 解密失败（签名不符、填充非法、密文异常）。"""


def _b64_sha512(text: str) -> str:
    """SHA-512 后标准 Base64（文档规定：用这串字符串本身的 UTF-8 字节做后续运算）。"""
    return base64.b64encode(hashlib.sha512((text or "").encode("utf-8")).digest()).decode("ascii")


def verify_response_sign(app_id: str, data: Dict[str, Any]) -> bool:
    """校验信封 sign：HMAC-SHA256(sign_key, timeStamp+nonce+encrypt) == sign（小写 hex）。"""
    try:
        ts = str(data["timeStamp"])
        nonce = str(data["nonce"])
        enc = str(data["encrypt"])
        sign = str(data["sign"]).strip().lower()
    except (KeyError, TypeError):
        return False
    key = _b64_sha512(app_id).encode("utf-8")
    calc = hmac.new(key, (ts + nonce + enc).encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, sign)


def decrypt_response_payload(app_id: str, app_secret: str, data: Dict[str, Any]) -> str:
    """解密信封，返回业务明文 JSON 字符串。验签不过 / 填充非法一律抛 ResponseCryptoError。"""
    if not verify_response_sign(app_id, data):
        raise ResponseCryptoError("response sign mismatch")
    try:
        ts = str(data["timeStamp"])
        nonce = str(data["nonce"])
        enc = str(data["encrypt"])
    except (KeyError, TypeError) as exc:
        raise ResponseCryptoError(f"envelope field missing: {exc}") from exc

    from Crypto.Cipher import AES  # noqa: PLC0415 - pycryptodome（requirements 已含）

    material = _b64_sha512(app_secret) + ":" + ts + ":" + nonce
    key = hashlib.sha256(material.encode("utf-8")).digest()
    raw = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4))
    if len(raw) < 32 or len(raw) % 16:
        raise ResponseCryptoError(f"cipher length invalid: {len(raw)}")
    iv, body = raw[:16], raw[16:]
    plain = AES.new(key, AES.MODE_CBC, iv).decrypt(body)
    pad = plain[-1] if plain else 0
    if not (1 <= pad <= 16) or plain[-pad:] != bytes([pad]) * pad:
        raise ResponseCryptoError("pkcs7 padding invalid")
    return plain[:-pad].decode("utf-8")


def decrypt_response_json(app_id: str, app_secret: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """解密并解析为 dict（解析失败抛 ResponseCryptoError）。"""
    text = decrypt_response_payload(app_id, app_secret, data)
    try:
        obj = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ResponseCryptoError(f"plaintext not json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ResponseCryptoError("plaintext not a json object")
    return obj


# ── 测试辅助（也是算法的镜像自证）：按同一套规则构建信封 ────────────────
def build_response_envelope_for_test(app_id: str, app_secret: str, payload: Dict[str, Any],
                                     time_stamp: str = "", nonce: str = "") -> Dict[str, str]:
    """按官方规则反向构建信封，供单测做往返自证。⛔ 仅测试用，生产代码不得调用。"""
    import secrets
    from Crypto.Cipher import AES  # noqa: PLC0415

    ts = time_stamp or str(secrets.randbelow(10 ** 12) + 10 ** 12)
    nc = nonce or secrets.token_hex(16)

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    pad = 16 - (len(raw) % 16)
    raw += bytes([pad]) * pad
    material = _b64_sha512(app_secret) + ":" + ts + ":" + nc
    key = hashlib.sha256(material.encode("utf-8")).digest()
    iv = secrets.token_bytes(16)
    cipher = AES.new(key, AES.MODE_CBC, iv).encrypt(raw)
    enc = base64.urlsafe_b64encode(iv + cipher).decode("ascii").rstrip("=")

    sign_key = _b64_sha512(app_id).encode("utf-8")
    sign = hmac.new(sign_key, (ts + nc + enc).encode("utf-8"), hashlib.sha256).hexdigest()
    return {"sign": sign, "timeStamp": ts, "nonce": nc, "encrypt": enc}
