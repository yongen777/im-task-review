# -*- coding: utf-8 -*-
"""量子密信 (zdxlz) open-apis 通道集成（严格对齐规格书 §7.1 ~ §7.4 与实测口径）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import subprocess
import tempfile
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

    def send_text(self, receive_id: str, text: str, idem_key: str, receive_type: str = "user") -> SendResult:
        """§7.2 + §16 P4 发送单聊文本消息：
        - POST /open-apis/v1/messages/v1/send
        - 头同时带 Authorization: Bearer <token> 与 X-Client-Id: <AppID>
        - body: receive_id / msg_type=text / content(JSON 字符串) / idem_key
        - HTTP 200 且 (success==true || code==0 || code==200) -> SendResult(True, "sent", 200, ...)

        2026-11-10 方案A（union_id 直连）：receive_type 支持 "user"（默认，IM userId）
        与 "union_id"（通讯录 union_id，官方口径：不要求用户先给机器人发过消息）。
        receive_type="user" 时**不写该字段**（保持历史 wire 格式逐字节一致，存量 636 条
        发送记录不受影响）；"union_id" 时显式传 —— 官方文档：省略时默认按 user 解析。
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
            # 2026-11-10 方案A：union_id 绑定显式声明 receive_type；user 保持旧格式不带该字段
            if (receive_type or "user").strip().lower() == "union_id":
                body["receive_type"] = "union_id"

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

    @staticmethod
    def _upload_data(resp, stage: str) -> dict:
        if resp.status_code == 200:
            try:
                envelope = resp.json()
            except ValueError:
                envelope = None
            if isinstance(envelope, dict):
                code = envelope.get("code")
                if envelope.get("success") is True or (not isinstance(code, bool) and code in (0, 200)):
                    data = envelope.get("data")
                    if isinstance(data, dict):
                        return data
        raise ChannelError(f"upload {stage} 失败: HTTP {resp.status_code} {resp.text[:300]}")

    def upload_media(self, file_path: str, category: int = 1, *, filename: Optional[str] = None,
                     mime_type: Optional[str] = None) -> str:
        """媒体三步分片上传，成功返回 fileKey（= 消息 content.fileId）。

        流程（官方《媒体文件上传与下载》）：
        1) POST /files/upload/init          -> parts[].uploadUrl（预签名）+ uploadId
        2) PUT  uploadUrl（application/octet-stream）-> 响应头 ETag
        3) POST /files/upload/{uploadId}/complete -> data.fileKey

        init 和 complete 使用同一个原始文件 MD5，complete 保留 ETag 引号。
        """
        if not self.configured:
            raise ChannelError("消息通道未配置 (IMT_APP_ID / IMT_APP_SECRET 缺失)")

        path = Path(file_path)
        if not path.is_file():
            raise ChannelError(f"上传文件不存在: {file_path}")

        file_size = path.stat().st_size
        if not file_size:
            raise ChannelError("上传文件为空")
        with path.open("rb") as source:
            digest = hashlib.md5()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        file_hash = digest.hexdigest()
        ext = path.suffix.lower()
        mime = (mime_type or self._MIME_BY_EXT.get(ext) or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream")

        token = self.get_access_token()
        base_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

        # 1) 初始化上传（chunkSize 先传整文件大小，服务端返回实际采用的 chunkSize 与 parts）
        init_url = f"{self.config.api_base}/open-apis/v1/files/upload/init"
        init_body = {"fileName": filename or path.name, "fileSize": file_size, "mimeType": mime,
                     "chunkSize": file_size, "category": int(category), "fileHash": file_hash}
        resp = self._client.post(init_url, headers={**base_headers}, json=init_body, timeout=60.0)
        init_data = self._upload_data(resp, "init")
        if init_data.get("deduplicatedHit") is True and init_data.get("uploadComplete") is True:
            file_key = str(init_data.get("fileKey") or "").strip()
            if file_key:
                return file_key
        parts = init_data.get("parts") or []
        upload_id = str(init_data.get("uploadId") or "").strip()
        if not upload_id or not parts:
            raise ChannelError(f"upload init 响应缺失 uploadId/parts: {resp.text}")

        chunk_size = int(init_data.get("chunkSize") or file_size)
        if chunk_size <= 0:
            raise ChannelError("upload init 响应包含无效 chunkSize")
        completed = []
        with path.open("rb") as source:
            for part in parts:
                part_no = int(part["partNumber"])
                start = (part_no - 1) * chunk_size
                if not 0 <= start < file_size:
                    raise ChannelError("upload init 响应包含无效 partNumber")
                source.seek(start)
                chunk = source.read(min(chunk_size, file_size - start))
                put_resp = self._client.put(
                    str(part["uploadUrl"]),
                    content=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=120.0,
                )
                if put_resp.status_code not in (200, 201, 204):
                    raise ChannelError(f"分片 {part_no} 上传失败: HTTP {put_resp.status_code}")
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
            json={"parts": completed, "fileHash": file_hash},
            timeout=60.0,
        )
        data3 = self._upload_data(resp3, "complete")
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
        receive_type: str = "user",
    ) -> SendResult:
        """按量子密信协议发送原生图片。保留旧调用方的可选参数。"""
        if not width or not height:
            return SendResult(False, "failed", None, False, False, error="IMAGE_SIZE_REQUIRED")
        ext = (file_ext or mime_type.rsplit("/", 1)[-1]).strip().lstrip(".").lower()
        content = {"fileId": file_key, "width": int(width), "height": int(height),
                   "mimeType": "jpg" if ext == "jpeg" else ext, "altText": alt_text}
        return self._send_media_message(receive_id, "image", content, idem_key, receive_type, push_content)

    def send_file(self, receive_id: str, file_key: str, *, file_name: str, size: int,
                  mime_type: str, ext: str, idem_key: str, receive_type: str = "user") -> SendResult:
        content = {"fileId": file_key, "fileName": file_name, "size": int(size),
                   "mimeType": mime_type, "ext": ext.lstrip(".").lower()}
        return self._send_media_message(receive_id, "file", content, idem_key, receive_type, file_name)

    def send_video(self, receive_id: str, file_key: str, *, duration: int, width: int, height: int,
                   mime_type: str, ext: str, thumbnail: str, idem_key: str,
                   receive_type: str = "user") -> SendResult:
        content = {"fileId": file_key, "duration": int(duration), "width": int(width), "height": int(height),
                   "mimeType": mime_type, "ext": ext.lstrip(".").lower(), "thumbnail": thumbnail}
        return self._send_media_message(receive_id, "video", content, idem_key, receive_type, "你收到一条视频")

    def prepare_notice_attachment(self, file_path: str, asset: dict) -> dict | SendResult:
        kind = asset["kind"]
        name = asset["file_name"]
        if kind == "image":
            width, height = self._read_image_size(file_path)
            if not width or not height:
                raise ChannelError("IMAGE_SIZE_REQUIRED")
            fields = {"width": width, "height": height, "alt_text": name}
        elif kind == "video":
            fields = self._prepare_video(file_path)
            if isinstance(fields, SendResult):
                return fields
        else:
            fields = {"file_name": name, "size": asset["size"]}
        mime = asset.get("mime") or self._MIME_BY_EXT.get(Path(file_path).suffix.lower()) or "application/octet-stream"
        fields["mime_type"] = mime.split(";", 1)[0]
        fields["file_key"] = self.upload_media(file_path, filename=name, mime_type=fields["mime_type"])
        if kind != "image":
            fields["ext"] = Path(file_path).suffix.lstrip(".").lower()
        return fields

    def _prepare_video(self, file_path: str) -> dict | SendResult:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,duration:format=duration", "-of", "json", file_path],
                capture_output=True, check=True, timeout=20,
            )
            info = json.loads(probe.stdout)
            stream = info["streams"][0]
            duration = float(info.get("format", {}).get("duration") or stream["duration"])
            width, height = int(stream["width"]), int(stream["height"])
            if not (0 < duration < float("inf") and width > 0 and height > 0):
                raise ValueError("Invalid video duration or dimensions")
            with tempfile.TemporaryDirectory(prefix="imtask_video_") as directory:
                poster = str(Path(directory) / "thumbnail.jpg")
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-i", file_path,
                     "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=480:-2", poster],
                    capture_output=True, check=True, timeout=30,
                )
                thumbnail = self.upload_media(poster)
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError, TypeError, KeyError, IndexError) as exc:
            return SendResult(ok=False, result="failed", retryable=False,
                              error=f"VIDEO_PREPARE_FAILED:{type(exc).__name__}:{exc}")
        return {"duration": max(1, round(duration)), "width": width, "height": height, "thumbnail": thumbnail}

    def send_prepared_notice(self, receive_id: str, kind: str, fields: dict, idem_key: str,
                             receive_type: str) -> SendResult:
        send = {"image": self.send_image, "file": self.send_file, "video": self.send_video}[kind]
        return send(receive_id, **fields, idem_key=idem_key, receive_type=receive_type)

    def _send_media_message(self, receive_id: str, kind: str, content: dict, idem_key: str,
                            receive_type: str, push_content: str) -> SendResult:
        if not self.configured:
            return SendResult(False, "failed", None, False, False, error="CHANNEL_UNCONFIGURED")
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
            body = {
                "receive_id": str(receive_id),
                "msg_type": kind,
                "content": json.dumps(content, ensure_ascii=False),
                "push_content": str(push_content),
                "idem_key": str(idem_key),
            }
            if (receive_type or "user").strip().lower() == "union_id":
                body["receive_type"] = "union_id"

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
