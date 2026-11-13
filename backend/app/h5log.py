# -*- coding: utf-8 -*-
"""H5 纯净访问日志（2026-11-13 冲刺模块二）。

规范（用户指令原文）：
  · 记录文件 `logs/h5_access.log`，**按天切割**（`logs/h5_access-YYYYMMDD.log`，单行、无 JSON）；
  · 每条 HTTP 请求一行：毫秒时间戳 | 客户端 IP | 方法 路径 | 状态码 | 耗时毫秒；
  · 耗时超过阈值标 `SLOW`；
  · token/手机号等敏感参数**脱敏**（不落原文；不记录请求体与请求头）；
  · 只挂在**业务端口（H5 入口）**，不记录后台管理端口。

实现为纯 ASGI 中间件（不缓冲响应体、不属于 Starlette 的 BaseHTTPMiddleware），
单行写入用 O_APPEND（POSIX 下单行原子），异常中断也会落一行（status 记 0）。
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode

from .config import SHANGHAI_TZ

SLOW_MS = 500.0
_SENSITIVE_KEYS = {"token", "raw", "phone", "mobile", "idem", "sig", "sign", "key"}
_TOKEN_RE = re.compile(r"^(.{4})(.*)$")


def _mask_qs(query: str) -> str:
    """query 敏感参数脱敏；其余原样保留（截断超长值，单行可读）。"""
    if not query:
        return ""
    try:
        pairs = parse_qs(query, keep_blank_values=True)
    except Exception:  # noqa: BLE001 - 畸形 query 原样截断返回
        return query[:200]
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


class H5AccessLogMiddleware:
    """单行访问日志中间件（按天文件自动切换；写失败绝不打断请求）。"""

    def __init__(self, app, log_dir: Optional[os.PathLike | str] = None, slow_ms: float = SLOW_MS) -> None:
        self.app = app
        self.log_dir = Path(log_dir) if log_dir else Path(os.environ.get("IMT_H5_LOG_DIR", "logs"))
        self.slow_ms = slow_ms
        self._lock = threading.Lock()
        self._current_day: Optional[str] = None
        self._fh = None

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
        status_holder: Dict[str, int] = {}
        tz = SHANGHAI_TZ
        started_at = datetime.now(tz)
        ts = started_at.strftime("%Y-%m-%d %H:%M:%S.") + f"{started_at.microsecond // 1000:03d}"

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            status = status_holder.get("status", 0)
            path = str(scope.get("path") or "/")
            raw_qs = scope.get("query_string") or b""
            qs = _mask_qs(bytes(raw_qs).decode("latin-1") if isinstance(raw_qs, (bytes, bytearray)) else str(raw_qs))
            target = f"{path}?{qs}" if qs else path
            line = (
                f"{ts} | {_client_ip(scope)} | {str(scope.get('method') or 'GET')} {target} | "
                f"{status} | {elapsed_ms:.0f}ms"
            )
            if elapsed_ms >= self.slow_ms:
                line += " | SLOW"
            self._write_line(line)