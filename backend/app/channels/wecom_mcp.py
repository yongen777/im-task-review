# -*- coding: utf-8 -*-
"""企业微信智能机器人 MCP 通讯录客户端（2026-11-10 实测定档）。

实测事实（2026-11-10，用户提供的 MCP apikey，生产同体系验证）：
  · 端点  POST https://qyapi.weixin.qq.com/mcp/v2/bot/contact?apikey=…
  · 协议  MCP Streamable HTTP（JSON-RPC；initialize → notifications/initialized → tools/call）
  · 工具  contact_users_search(keywords=[...]) → 明文 JSON：
         {users:[{userid,name,alias,position,departments:[全路径]},...],users_count,errcode}
  · userid 与 aibot 长连接的 from.userid **同一体系**（郑佳实测完全一致）
  · ⚠️ **关键词只匹配人名/别名**：手机号、部门名都搜不到（users_count=0）

因此企微「导入即激活」的反查规则是「姓名+部门 → userid」（非手机号）：
  1) 名册姓名与 MCP 姓名都做归一化（剥「（…）」后缀、剥「_后缀」、去空白）后精确相等；
  2) 唯一命中 → 绑定；重名 → 用归一化部门段（路径按 / 拆段、剥括号）消歧，唯一才绑定；
  3) 查不到 / 消歧失败 → 返回 None（导入 failed 清单 + H5 兜底），绝不猜。

安全：apikey 关联机器人+授权用户个人信息，不落日志；只走 HTTPS，绝不建 WebSocket。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
_PAREN_SUFFIX_RE = re.compile(r"[（(][^（）()]*[）)]\s*$")


def _mask(text: Any) -> str:
    return _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], str(text or ""))


def _strip_paren_suffix(s: str) -> str:
    """剥掉结尾的「（德阳产数支撑中心）」类展示后缀（可叠加多层）。"""
    out = str(s or "").strip()
    while True:
        stripped = _PAREN_SUFFIX_RE.sub("", out).strip()
        if stripped == out:
            return out
        out = stripped


def normalize_name(name: Any) -> str:
    """姓名归一化：去空白 + 剥「（…）」后缀 + 剥「_德阳市」类下划线后缀。"""
    out = _strip_paren_suffix(name)
    if "_" in out:
        out = out.split("_", 1)[0].strip()
    return out


def normalize_dept(dept: Any) -> str:
    """部门归一化：与姓名同规则（「云中台（天翼云…）」→「云中台」）。"""
    return _strip_paren_suffix(dept)


def _dept_hit(paths: List[Any], dept: str) -> bool:
    """部门路径里是否有任一段（归一化后）与名册部门一致；或整条路径以 /部门 结尾。"""
    target = normalize_dept(dept)
    if not target:
        return False
    for raw in paths or []:
        path = str(raw or "").strip()
        if not path:
            continue
        if path.endswith("/" + target) or path == target:
            return True
        for seg in path.split("/"):
            if normalize_dept(seg) == target:
                return True
    return False


class WecomMcpError(Exception):
    """MCP 调用失败（网络/协议层）。"""


class WecomMcpContacts:
    """智能机器人 MCP 通讯录门面。所有失败一律空结果/None，不向业务层抛异常。"""

    TOOL_NAME = "contact_users_search"
    # 2026-11-11 实测：list 模式返回上限约 50 条（「张」在全公司命中远超 50，
    # 返回被截断且无 total 字段可判全量）—— 做选择器时必须按关键词分批拉，不能假设列表完整。
    LIST_MODE_TRUNCATION = 50

    def __init__(self, apikey: str, *, http_client: Optional[Any] = None,
                 timeout: float = 12.0, base_url: str = "https://qyapi.weixin.qq.com") -> None:
        self.apikey = (apikey or "").strip()
        self._url = f"{(base_url or 'https://qyapi.weixin.qq.com').rstrip('/')}/mcp/v2/bot/contact?apikey={self.apikey}"
        self._client = http_client or httpx.Client(timeout=timeout)
        self._ready = False
        self._lock = threading.Lock()
        self._next_id = 0

    # ── 协议层 ──────────────────────────────────────────────────────
    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
             id_: Optional[int] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if id_ is not None:
            body["id"] = id_
        resp = self._client.post(
            self._url, json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        ct = (resp.headers.get("content-type") or "").lower()
        text = resp.text or ""
        if "event-stream" in ct:
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except Exception:  # noqa: BLE001 - 逐行找 JSON
                        continue
            raise WecomMcpError("SSE 响应无数据行")
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise WecomMcpError(f"非 JSON 响应 HTTP {resp.status_code}") from exc

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            self._next_id += 1
            resp = self._rpc(
                "initialize",
                {"protocolVersion": "2025-03-26", "capabilities": {},
                 "clientInfo": {"name": "imtask", "version": "1.0"}},
                id_=self._next_id,
            )
            if isinstance(resp, dict) and resp.get("error"):
                raise WecomMcpError(f"initialize 失败: {resp['error']}")
            try:
                self._rpc("notifications/initialized")
            except Exception:  # noqa: BLE001 - 202/空响应体属正常
                pass
            self._ready = True

    # ── 业务层 ──────────────────────────────────────────────────────
    def search_users(self, keyword: str) -> List[Dict[str, Any]]:
        clean = str(keyword or "").strip()
        if not clean:
            return []
        try:
            self._ensure_ready()
            self._next_id += 1
            resp = self._rpc(
                "tools/call",
                {"name": self.TOOL_NAME, "arguments": {"keywords": [clean]}},
                id_=self._next_id,
            )
        except Exception as exc:  # noqa: BLE001 - 查询失败不拖垮导入
            logger.warning("MCP contact_users_search 异常 keyword=%s: %s", _mask(clean), str(exc)[:160])
            return []
        if isinstance(resp, dict) and resp.get("error"):
            logger.warning("MCP search 错误: %s", str(resp.get("error"))[:160])
            return []
        text = ""
        for c in ((resp or {}).get("result") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                text += str(c.get("text") or "")
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001
            logger.warning("MCP 响应正文非 JSON（截断）：%s", _mask(text)[:160])
            return []
        if not isinstance(data, dict):
            return []
        users = data.get("users") or []
        return [u for u in users if isinstance(u, dict) and str(u.get("userid") or "").strip()]

    def resolve_userid(self, phone: str, name: str, dept: str = "") -> Optional[str]:
        """名册（姓名+部门）→ 企微 userid。唯一命中才返回，其余一律 None。"""
        del phone  # MCP 不支持手机号搜索（保留参数与双通道接口对齐）
        target = normalize_name(name)
        if not target:
            return None
        users = self.search_users(target)
        exact = [u for u in users if normalize_name(u.get("name")) == target]
        if not exact:
            return None
        if len(exact) == 1:
            return str(exact[0].get("userid") or "").strip() or None
        clean_dept = str(dept or "").strip()
        if clean_dept:
            hits = [u for u in exact if _dept_hit(u.get("departments") or [], clean_dept)]
            if len(hits) == 1:
                return str(hits[0].get("userid") or "").strip() or None
        logger.warning("MCP 重名且部门消歧失败 name=%s dept=%s 候选=%d",
                       target, clean_dept, len(exact))
        return None
