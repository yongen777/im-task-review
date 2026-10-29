# -*- coding: utf-8 -*-
"""量子密信 (zdxlz) open-apis 通道集成（严格对齐规格书 §7.1 ~ §7.4 与实测口径）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError:  # pragma: no cover
    AES = None  # type: ignore[assignment]
    pad = None  # type: ignore[assignment]
    unpad = None  # type: ignore[assignment]

from ..config import Config

TOKEN_REFRESH_MARGIN_SECONDS = 300


class ChannelError(Exception):
    """通道调用异常。"""


class SignatureVerificationError(Exception):
    """事件回调验签失败（对应 HTTP 401）。"""


@dataclass
class SendResult:
    ok: bool
    result: str
    status_code: Optional[int] = None
    need_activate: bool = False
    retryable: bool = False
    error: Optional[str] = None


def verify_event_signature(
    verify_token: str,
    timestamp: str,
    nonce: str,
    body_text: str,
    signature: str,
) -> bool:
    """§7.3 验签：HMAC-SHA256(verify_token, timestamp + nonce + body_text)，64 位 hex。"""
    if not verify_token or not timestamp or not nonce or not signature:
        return False
    expected = hmac.new(
        verify_token.encode("utf-8"),
        (timestamp + nonce + body_text).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected.lower(), signature.strip().lower())


def is_event_timestamp_fresh(timestamp: str, window_sec: int = 300, now_ms: Optional[int] = None) -> bool:
    """2026-10-29 P2-2：回调时间戳新鲜度校验，挡住重放旧报文。

    - 平台可能下发秒级或毫秒级时间戳，这里按量级归一（小于 1e12 视为秒）；
    - window_sec <= 0 表示关闭校验（联调/排障时可临时放宽）。
    """
    if window_sec is None or window_sec <= 0:
        return True
    raw = str(timestamp or "").strip()
    if not raw:
        return False
    try:
        ts = int(float(raw))
    except (TypeError, ValueError):
        return False
    ts_ms = ts * 1000 if ts < 10 ** 12 else ts
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return abs(current_ms - ts_ms) <= int(window_sec) * 1000


def _aes256_cbc_crypt(key: bytes, iv: bytes, data: bytes, encrypt: bool) -> bytes:
    """当环境未安装 pycryptodome 时使用 OpenSSL EVP_Cipher 执行 AES-256-CBC + PKCS7。"""
    import ctypes
    import ctypes.util

    lib_name = ctypes.util.find_library("crypto") or "libcrypto.so.3"
    lib = ctypes.CDLL(lib_name)

    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_aes_256_cbc.restype = ctypes.c_void_p
    lib.EVP_CipherInit_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    lib.EVP_CipherUpdate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    lib.EVP_CipherFinal_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
    ]

    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise ValueError("EVP_CIPHER_CTX_new failed")
    try:
        enc_flag = 1 if encrypt else 0
        if lib.EVP_CipherInit_ex(ctx, lib.EVP_aes_256_cbc(), None, key, iv, enc_flag) != 1:
            raise ValueError("EVP_CipherInit_ex failed")
        out_buf = ctypes.create_string_buffer(len(data) + 32)
        out_len1 = ctypes.c_int(0)
        if lib.EVP_CipherUpdate(ctx, out_buf, ctypes.byref(out_len1), data, len(data)) != 1:
            raise ValueError("EVP_CipherUpdate failed")
        tail_buf = ctypes.create_string_buffer(32)
        out_len2 = ctypes.c_int(0)
        if lib.EVP_CipherFinal_ex(ctx, tail_buf, ctypes.byref(out_len2)) != 1:
            raise ValueError("AES-256-CBC padding or key error")
        return out_buf.raw[: out_len1.value] + tail_buf.raw[: out_len2.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def encrypt_event_payload(
    encrypt_key: str,
    timestamp: str,
    nonce: str,
    payload: Dict[str, Any],
    iv: bytes = b"0123456789abcdef",
) -> str:
    """测试辅助：将明文 dict 按实测算法反向加密为纯 base64 密文字符串。"""
    key = hashlib.sha256(f"{encrypt_key}:{timestamp}:{nonce}".encode("utf-8")).digest()
    plain = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if AES is not None and pad is not None:
        ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plain, 16))
    else:
        ct = _aes256_cbc_crypt(key, iv, plain, encrypt=True)
    return base64.b64encode(iv + ct).decode("utf-8")


def decrypt_event_payload(
    encrypt_key: str,
    timestamp: str,
    nonce: str,
    body_text: str,
) -> Dict[str, Any]:
    """§7.3 回调解密（已实测口径）：
    - body_text 是纯 base64 密文，不是 {"data": ...} 包裹
    - 会话密钥 = SHA256(encrypt_key + ":" + timestamp + ":" + nonce)
    - IV = base64decode(body_text)[:16]，其余为密文；AES-256-CBC + PKCS7
    - encrypt_key 用后台原样字符串参与，不做 base64 解码
    """
    key = hashlib.sha256(f"{encrypt_key}:{timestamp}:{nonce}".encode("utf-8")).digest()
    raw = base64.b64decode((body_text or "").strip())
    if len(raw) <= 16:
        raise ValueError("cipher payload too short")
    iv, ct = raw[:16], raw[16:]
    if AES is not None and unpad is not None:
        plain = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ct), 16)
    else:
        plain = _aes256_cbc_crypt(key, iv, ct, encrypt=False)
    res = json.loads(plain.decode("utf-8"))
    if not isinstance(res, dict):
        raise ValueError("decrypted payload is not a JSON object")
    return res


class ZdxlzChannel:
    """量子密信单聊发送与事件验签解密客户端。"""

    def __init__(
        self,
        config: Config,
        http_client: Optional[httpx.Client] = None,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._client = http_client or (httpx.Client(timeout=10.0) if httpx is not None else None)
        self._owns_client = http_client is None and self._client is not None
        self._clock_fn = clock_fn
        self._token_lock = threading.RLock()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return self.config.channel_configured

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def invalidate_token(self) -> None:
        with self._token_lock:
            self._access_token = None
            self._token_expires_at = 0.0

    def get_access_token(self, force_refresh: bool = False) -> str:
        """§7.1 获取并缓存 access_token，剩余有效期 <300 秒时刷新。
        ⛔ 严格调用 POST /open-apis/v1/auth/token，绝不使用 /seal/oauth2/token。
        """
        if not self.configured:
            raise ChannelError("消息通道未配置 (IMT_APP_ID / IMT_APP_SECRET 缺失)")

        with self._token_lock:
            now_mono = self._clock_fn()
            if (
                not force_refresh
                and self._access_token
                and (self._token_expires_at - now_mono) >= TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._access_token

            url = f"{self.config.api_base}/open-apis/v1/auth/token"
            resp = self._client.post(
                url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.config.app_id,
                    "client_secret": self.config.app_secret,
                },
            )
            if resp.status_code != 200:
                raise ChannelError(f"获取 token 失败: HTTP {resp.status_code} {resp.text}")

            payload = resp.json()
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise ChannelError(f"获取 token 响应缺失 access_token: {resp.text}")

            expires_in = int(payload.get("expires_in") or 7200)
            self._access_token = token
            self._token_expires_at = now_mono + max(expires_in, 60)
            return token

    def send_text(self, receive_id: str, text: str, idem_key: str) -> SendResult:
        """§7.2 + §16 P4 发送单聊文本消息：
        - POST /open-apis/v1/messages/v1/send
        - 头同时带 Authorization: Bearer <token> 与 X-Client-Id: <AppID>
        - body: receive_id / msg_type=text / content(JSON 字符串) / idem_key
        - HTTP 200 且 (success==true || code==0 || code==200) -> SendResult(True, "sent", 200, ...)
        - HTTP 500（无会话） -> SendResult(False, "failed", 500, need_activate=True, retryable=False, error="NO_SESSION_500: ...")
        """
        if not self.configured:
            return SendResult(
                ok=False,
                result="failed",
                status_code=None,
                need_activate=False,
                retryable=False,
                error="CHANNEL_UNCONFIGURED",
            )

        for attempt in range(2):
            try:
                token = self.get_access_token(force_refresh=(attempt > 0))
            except Exception as exc:
                return SendResult(
                    ok=False,
                    result="failed",
                    status_code=None,
                    need_activate=False,
                    retryable=True,
                    error=str(exc),
                )

            url = f"{self.config.api_base}/open-apis/v1/messages/v1/send"
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Client-Id": self.config.app_id,
                "Content-Type": "application/json; charset=utf-8",
            }
            body = {
                "receive_id": str(receive_id),
                "msg_type": "text",
                "content": json.dumps({"content": text}, ensure_ascii=False),
                "idem_key": str(idem_key),
            }

            try:
                resp = self._client.post(url, headers=headers, json=body)
            except Exception as exc:
                return SendResult(
                    ok=False,
                    result="failed",
                    status_code=None,
                    need_activate=False,
                    retryable=True,
                    error=f"NETWORK_ERROR: {exc}",
                )

            if resp.status_code == 401 and attempt == 0:
                self.invalidate_token()
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if isinstance(data, dict):
                    code_val = data.get("code")
                    code_ok = (not isinstance(code_val, bool)) and (code_val in (0, 200))
                    if data.get("success") is True or code_ok:
                        return SendResult(
                            ok=True,
                            result="sent",
                            status_code=200,
                            need_activate=False,
                            retryable=False,
                            error=None,
                        )
                return SendResult(
                    ok=False,
                    result="failed",
                    status_code=200,
                    need_activate=False,
                    retryable=False,
                    error=resp.text or "HTTP_200_WITHOUT_SUCCESS_FLAG",
                )

            if resp.status_code == 500:
                return SendResult(
                    ok=False,
                    result="failed",
                    status_code=500,
                    need_activate=True,
                    retryable=False,
                    error=f"NO_SESSION_500: {resp.text}",
                )

            if 400 <= resp.status_code < 500:
                return SendResult(
                    ok=False,
                    result="failed",
                    status_code=resp.status_code,
                    need_activate=False,
                    retryable=False,
                    error=resp.text,
                )

            return SendResult(
                ok=False,
                result="failed",
                status_code=resp.status_code,
                need_activate=False,
                retryable=True,
                error=resp.text,
            )

        return SendResult(
            ok=False,
            result="failed",
            status_code=401,
            need_activate=False,
            retryable=False,
            error="UNAUTHORIZED_AFTER_TOKEN_REFRESH",
        )

    def parse_callback_event(
        self,
        timestamp: str = "",
        nonce: str = "",
        signature: str = "",
        body_text: str = "",
    ) -> Dict[str, Any]:
        """§7.3 验签并解密回调事件。验签失败抛 SignatureVerificationError；解密失败抛异常。"""
        if not verify_event_signature(
            self.config.verify_token, timestamp, nonce, body_text, signature
        ):
            raise SignatureVerificationError("invalid callback signature")
        return decrypt_event_payload(
            self.config.encrypt_key, timestamp, nonce, body_text
        )

    # ── 媒体上传与图片消息（2026-10-27 春促数据看板，对齐官方《媒体文件上传与下载》三步流程）──

    _MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}

    @staticmethod
    def _read_image_size(file_path: str) -> Tuple[int, int]:
        """读取图片宽高（Pillow 缺失时返回 (0, 0)，由调用方兜底）。"""
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(file_path) as im:
                return int(im.width), int(im.height)
        except Exception:
            return 0, 0

    def upload_media(self, file_path: str, category: int = 1) -> str:
        """媒体三步分片上传，成功返回 fileKey（= 消息 content.fileId）。

        流程（官方《媒体文件上传与下载》）：
        1) POST /files/upload/init          -> parts[].uploadUrl（预签名）+ uploadId
        2) PUT  uploadUrl（Content-Type 传真实 mime，⛔ 不带 Authorization）-> 响应头 ETag
        3) POST /files/upload/{uploadId}/complete -> data.fileKey

        2026-10-29 修复：预签名 PUT 的 Content-Type 会被 S3 存成对象元数据，
        下载时原样返回 ——之前写死 octet-stream，手机端图片组件因
        Content-Type=application/octet-stream 无法识别而转圈重试。
        现在与 init 的 mimeType 保持一致（_MIME_BY_EXT）。
        注意：complete 提交的 etag 必须保留响应头中的引号。
        """
        if not self.configured:
            raise ChannelError("消息通道未配置 (IMT_APP_ID / IMT_APP_SECRET 缺失)")

        path = Path(file_path)
        if not path.is_file():
            raise ChannelError(f"上传文件不存在: {file_path}")

        file_bytes = path.read_bytes()
        file_size = len(file_bytes)
        ext = path.suffix.lower()
        mime = self._MIME_BY_EXT.get(ext, "application/octet-stream")

        token = self.get_access_token()
        base_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

        # 1) 初始化上传（chunkSize 先传整文件大小，服务端返回实际采用的 chunkSize 与 parts）
        init_url = f"{self.config.api_base}/open-apis/v1/files/upload/init"
        init_body = {"fileName": path.name, "fileSize": file_size, "mimeType": mime, "chunkSize": file_size, "category": int(category)}
        resp = self._client.post(init_url, headers={**base_headers}, json=init_body, timeout=60.0)
        if resp.status_code != 200:
            raise ChannelError(f"upload init 失败: HTTP {resp.status_code} {resp.text}")
        init_data = (resp.json() or {}).get("data") or {}
        parts = init_data.get("parts") or []
        upload_id = str(init_data.get("uploadId") or "").strip()
        if not upload_id or not parts:
            raise ChannelError(f"upload init 响应缺失 uploadId/parts: {resp.text}")

        # 2) 按分片上传（预签名地址，Content-Type=真实 mime，不带开放平台 Authorization）
        #    实测：S3 会把这个头存为对象元数据，下载 Content-Type 由此决定（A/B 实验已验证）。
        completed: list = []
        for part in parts:
            part_no = int(part["partNumber"])
            upload_url = str(part["uploadUrl"])
            chunk_size = int(init_data.get("chunkSize") or 0) or file_size
            start = (part_no - 1) * chunk_size
            end = min(part_no * chunk_size, file_size)
            chunk = file_bytes[start:end]
            put_resp = self._client.put(
                upload_url,
                content=chunk,
                headers={"Content-Type": mime},
                timeout=120.0,
            )
            if put_resp.status_code not in (200, 201, 204):
                raise ChannelError(f"分片 {part_no} 上传失败: HTTP {put_resp.status_code} {put_resp.text[:200]}")
            etag = put_resp.headers.get("ETag") or put_resp.headers.get("etag")
            if not etag:
                raise ChannelError(f"分片 {part_no} 上传成功但响应头缺失 ETag")
            completed.append({"partNumber": part_no, "etag": etag, "size": len(chunk)})

        # 3) 完成上传（合并分片）
        complete_url = f"{self.config.api_base}/open-apis/v1/files/upload/{upload_id}/complete"
        token2 = self.get_access_token()
        resp3 = self._client.post(
            complete_url,
            headers={"Authorization": f"Bearer {token2}", "Content-Type": "application/json; charset=utf-8"},
            json={"parts": completed, "fileHash": ""},
            timeout=60.0,
        )
        if resp3.status_code != 200:
            raise ChannelError(f"upload complete 失败: HTTP {resp3.status_code} {resp3.text}")
        data3 = (resp3.json() or {}).get("data") or {}
        file_key = str(data3.get("fileKey") or "").strip()
        if not file_key:
            raise ChannelError(f"upload complete 响应缺失 fileKey: {resp3.text}")
        return file_key

    def send_image(
        self,
        receive_id: str,
        file_key: str,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        mime_type: str = "png",
        alt_text: str = "数据看板",
        push_content: str = "你收到一条数据看板",
        idem_key: str = "",
        file_name: Optional[str] = None,
        file_size: Optional[int] = None,
        file_ext: Optional[str] = None,
    ) -> SendResult:
        """发送单聊图片消息（官方《机器人消息发送和接收类型》image 消息）。

        - content: {"fileId", "width", "height", "mimeType", "altText", "name", "size", "ext"}
        - 成功判定与 send_text 一致（success==true || code==0 || code==200）
        - HTTP 500（无会话）同样置 need_activate=True

        2026-10-29 实测修复（D/E/F 对照实验）：content 里显式携带的 name/size/ext
        会被服务端原样透传给客户端，客户端凭 ext/size 识别图片；
        只发 mimeType 时客户端收到 ext=""、size=0、name="fileId."，
        手机端无法识别为图片（点击转圈），PC 端走「接收文件」下载流程。
        因此 image 消息务必带全 name/size/ext 三个字段。
        """
        if not self.configured:
            return SendResult(False, "failed", None, False, False, error="CHANNEL_UNCONFIGURED")

        if (not width) or (not height):
            return SendResult(False, "failed", None, False, False, error="IMAGE_SIZE_REQUIRED (width/height 必填，可用 _read_image_size 读取)")

        for attempt in range(2):
            try:
                token = self.get_access_token(force_refresh=(attempt > 0))
            except Exception as exc:
                return SendResult(False, "failed", None, False, True, error=str(exc))

            url = f"{self.config.api_base}/open-apis/v1/messages/v1/send"
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Client-Id": self.config.app_id,
                "Content-Type": "application/json; charset=utf-8",
            }
            content: Dict[str, Any] = {
                "fileId": str(file_key),
                "width": int(width),
                "height": int(height),
                "mimeType": str(mime_type),
                "altText": str(alt_text),
            }
            # D/E/F 实验结论：这三个字段是客户端识别图片的关键（服务端原样透传）。
            # 未传时回退旧口径，保证兼容；生产链路 ZdxlzImageSender 总是会传齐。
            if file_name is not None:
                content["name"] = str(file_name)
            if file_size is not None:
                content["size"] = int(file_size)
            if file_ext is not None and str(file_ext).strip():
                content["ext"] = str(file_ext).strip().lstrip(".").lower()
            body = {
                "receive_id": str(receive_id),
                "msg_type": "image",
                "content": json.dumps(content, ensure_ascii=False),
                "push_content": str(push_content),
                "idem_key": str(idem_key),
            }

            try:
                resp = self._client.post(url, headers=headers, json=body)
            except Exception as exc:
                return SendResult(False, "failed", None, False, True, error=f"NETWORK_ERROR: {exc}")

            if resp.status_code == 401 and attempt == 0:
                self.invalidate_token()
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if isinstance(data, dict):
                    code_val = data.get("code")
                    code_ok = (not isinstance(code_val, bool)) and (code_val in (0, 200))
                    if data.get("success") is True or code_ok:
                        return SendResult(True, "sent", 200, False, False, error=None)
                return SendResult(False, "failed", 200, False, False, error=resp.text or "HTTP_200_WITHOUT_SUCCESS_FLAG")

            if resp.status_code == 500:
                return SendResult(False, "failed", 500, True, False, error=f"NO_SESSION_500: {resp.text}")

            if 400 <= resp.status_code < 500:
                return SendResult(False, "failed", resp.status_code, False, False, error=resp.text)

            return SendResult(False, "failed", resp.status_code, False, True, error=resp.text)

        return SendResult(False, "failed", 401, False, False, error="UNAUTHORIZED_AFTER_TOKEN_REFRESH")
