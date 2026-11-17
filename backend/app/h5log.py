# -*- coding: utf-8 -*-
"""H5 纯净访问日志（2026-11-13 冲刺模块二）。

规范（用户指令原文）：
  · 记录文件 `logs/h5_access.log`，**按天切割**（`logs/h5_access-YYYYMMDD.log`，单行、无 JSON）；
  · 每条 HTTP 请求一行：毫秒时间戳 | 客户端 IP | 方法 路径 | 状态码 | 耗时毫秒；
  · 耗时超过阈值标 `SLOW`；
  · token/手机号等敏感参数**脱敏**（不落原文；不记录请求体与请求头）；
  · 只挂在**业务端口（H5 入口）**，不记录后台管理端口。

纯 ASGI 中间件在发送完响应后计时落日志，错误 JSON 只收集有限的字节用于提取错误码。
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from .config import SHANGHAI_TZ

SLOW_MS = 500.0
H5_ACCESS_META = "imtask.h5_access"
_ERROR_JSON_LIMIT = 4096
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SENSITIVE_KEYS = {"token", "raw", "phone", "mobile", "idem", "sig", "sign", "key"}


def _mask_qs(query: str) -> str:
    """query 敏感参数脱敏；其余原样保留（截断超长值，单行可读）。"""
    if not query:
        return ""
    try:
        pairs = parse_qs(query, keep_blank_values=True)
    except ValueError:
        return "[invalid]"
    out = []
    for k, vals in pairs.items():
        v = (vals or [""])[0]
        lk = k.lower()
        if lk == "phone" or "phone" in lk:
            if len(v) > 7:
                v = v[:3] + "****" + v[-2:]
            elif v:
                v = v[:2] + "***"
        elif lk in _SENSITIVE_KEYS or "token" in lk:
            v = (v[:4] + "***") if v else ""
        elif len(v) > 64:
            v = v[:64] + "…"
        out.append((k, v))
    # 手工拼接保留 *** / … 等可读标记（urlencode 会把它们转义成 %2A，日志难读）
    return "&".join(f"{k}={v}" for k, v in out)


def _client_ip(scope: Dict[str, Any]) -> str:
    for name, val in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            first = val.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    return str(client[0]) if client else "-"


def _log_field(value: str) -> str:
    return value.replace("\r", "%0D").replace("\n", "%0A").replace("|", "%7C")


class H5AccessLogMiddleware:
    """单行访问日志中间件（按天文件自动切换；写失败绝不打断请求）。"""

    def __init__(self, app, log_dir: Optional[os.PathLike | str] = None, slow_ms: float = SLOW_MS) -> None:
        self.app = app
        self.log_dir = Path(log_dir) if log_dir else Path(os.environ.get("IMT_H5_LOG_DIR", "logs"))
        self.slow_ms = slow_ms
        self._lock = threading.Lock()
        self._current_day: Optional[str] = None
        self._fh = None
        self._instance_key = secrets.token_bytes(32)

    def _write_line(self, line: str) -> None:
        try:
            day = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
            with self._lock:
                if day != self._current_day or self._fh is None:
                    if self._fh is not None:
                        try:
                            self._fh.close()
                        except Exception:  # noqa: BLE001
                            pass
                    self.log_dir.mkdir(parents=True, exist_ok=True)
                    self._fh = open(self.log_dir / f"h5_access-{day}.log", "a", encoding="utf-8")
                    self._current_day = day
                self._fh.write(line + "\n")
                self._fh.flush()
        except Exception:  # noqa: BLE001 - 日志绝不打断业务
            pass

    def close(self) -> None:
        """显式关闭当前日志句柄（测试/优雅停机用；文件按天懒重开）。"""
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    pass
                self._fh = None
                self._current_day = None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        status = 0
        written = False
        error_json: Optional[bytearray] = None
        metadata = scope.setdefault(H5_ACCESS_META, {})
        path = str(scope.get("path") or "/")
        tz = SHANGHAI_TZ
        started_at = datetime.now(tz)
        ts = started_at.strftime("%Y-%m-%d %H:%M:%S.") + f"{started_at.microsecond // 1000:03d}"

        def write_access_line():
            if path == "/favicon.ico" and status == 404:
                return
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            raw_qs = scope.get("query_string") or b""
            qs = _mask_qs(bytes(raw_qs).decode("latin-1") if isinstance(raw_qs, (bytes, bytearray)) else str(raw_qs))
            target = f"{path}?{qs}" if qs else path
            line = (
                f"{ts} | {_log_field(_client_ip(scope))} | {str(scope.get('method') or 'GET')} {_log_field(target)} | "
                f"{status} | {elapsed_ms:.0f}ms"
            )
            if status >= 400:
                code = None
                if error_json:
                    try:
                        payload = json.loads(error_json)
                        error = payload.get("error") if isinstance(payload, dict) else None
                        code = payload.get("code") if isinstance(payload, dict) else None
                        if code is None and isinstance(error, dict):
                            code = error.get("code")
                    except (ValueError, RecursionError):
                        pass
                if not isinstance(code, str) or not _ERROR_CODE.fullmatch(code):
                    code = "VALIDATION_FAILED" if status == 422 else f"HTTP_{status}"
                line += f" | err={code}"
            if path in ("/api/h5/task/upload", "/api/h5/upload"):
                size = metadata.get("upload_bytes")
                kb = f"{size / 1024:.1f}" if size is not None else "-"
                line += f" | ext={metadata.get('upload_ext', '-')} | kb={kb}"
            instance_id = metadata.get("instance_id")
            if instance_id is not None:
                opaque = hmac.digest(self._instance_key, str(instance_id).encode("ascii"), "sha256").hex()[:16]
                line += f" | inst={opaque}"
            if elapsed_ms >= self.slow_ms:
                line += " | SLOW"
            self._write_line(line)

        async def send_wrapper(message):
            nonlocal status, error_json, written
            message_type = message.get("type")
            if message_type == "http.response.start":
                status = int(message.get("status", 0))
                if status >= 400 and any(
                    name.lower() == b"content-type" and b"json" in value.lower()
                    for name, value in message.get("headers", [])
                ):
                    error_json = bytearray()
            if message_type == "http.response.body" and error_json is not None:
                body = message.get("body", b"")
                if len(error_json) + len(body) <= _ERROR_JSON_LIMIT:
                    error_json.extend(body)
                else:
                    error_json = None
            await send(message)
            if message_type == "http.response.body" and not message.get("more_body", False) and not written:
                written = True
                write_access_line()

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if not written:
                write_access_line()
