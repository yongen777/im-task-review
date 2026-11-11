# -*- coding: utf-8 -*-
"""量子密信开放平台「通讯录」客户端（2026-11-10 方案A 基石）。

用途：把「手机号 → union_id」的官方映射查出来，供「导入即激活」使用。
依据（当日实测 + 官方文档）：
  · POST /open/v2/user/search  {"phone": "..."} → data 为密文信封，解密后 users[{union_id,name,phone,...}]
  · POST /open/v2/user/get     {"union_id": "..."} → 同上（单用户对象）
  · POST /open/v2/dept/users/list {"dept_id":...,"page_num":...,"page_size":...} → 部门成员分页
  · 响应解密见 zdxlz_crypto.decrypt_response_json（官方 decryptV2 规则）
  · 接口使用**应用级** access_token（与发消息同一个 token）

安全口径：
  · 解密后的手机号/姓名**不落日志**，日志只出现打码手机号；
  · 查询失败不抛异常给调用方（导入流程要 fail-open），只返回 None + 打码日志；
  · 本客户端只在「导入/加人/首条消息识别」等低频动作使用，不参与 30s 调度循环。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from .zdxlz_crypto import ResponseCryptoError, decrypt_response_json

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?<!\d)1\d{10}(?!\d)")


def _mask(text: str) -> str:
    return _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], str(text or ""))


class ZdxlzContacts:
    """通讯录查询门面。所有方法失败一律返回 None/[]，不向业务层抛异常。"""

    def __init__(self, cfg: Any, channel: Any, http_client: Optional[httpx.Client] = None) -> None:
        self.cfg = cfg
        self.channel = channel  # 复用其 get_access_token（应用级 token，线程安全）
        self._client = http_client or (httpx.Client(timeout=12.0) if httpx is not None else None)

    # ── 内部 ────────────────────────────────────────────────────────
    def _call(self, path: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._client is None:
            return None
        try:
            token = self.channel.get_access_token()
        except Exception as exc:  # noqa: BLE001 - 未配置/取 token 失败 → 降级
            logger.warning("通讯录查询跳过（取 token 失败）：%s", _mask(str(exc))[:160])
            return None
        url = str(getattr(self.cfg, "api_base", "") or "https://imtwo.zdxlz.com").rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            resp = self._client.post(url, json=body, headers=headers)
        except Exception as exc:  # noqa: BLE001 - 网络异常不拖垮导入
            logger.warning("通讯录请求失败 %s: %s", path, _mask(str(exc))[:160])
            return None
        try:
            outer = resp.json()
        except Exception:  # noqa: BLE001
            logger.warning("通讯录响应非 JSON %s: HTTP %s", path, resp.status_code)
            return None
        if not isinstance(outer, dict):
            return None
        code = outer.get("code")
        if code is not None and int(code) != 200:
            logger.warning("通讯录接口错误 %s: code=%s msg=%s", path, code, _mask(str(outer.get("msg") or ""))[:120])
            return None
        data = outer.get("data")
        if not isinstance(data, dict):
            return None
        if data.get("encrypt"):
            try:
                return decrypt_response_json(self.cfg.app_id, self.cfg.app_secret, data)
            except ResponseCryptoError as exc:
                logger.warning("通讯录响应解密失败 %s: %s", path, exc)
                return None
        return data  # 个别接口/环境可能直接给明文 JSON

    @staticmethod
    def _pick_user(row: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        union_id = str(row.get("union_id") or "").strip()
        phone = str(row.get("phone") or "").strip()
        if not union_id and not phone:
            return None
        return {
            "union_id": union_id,
            "phone": phone,
            "name": str(row.get("name") or "").strip(),
            "customer_id": str(row.get("customer_id") or "").strip(),
            "departments": row.get("user_departments") or [],
        }

    # ── 对外 ────────────────────────────────────────────────────────
    def search_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """按手机号精确查用户（官方 search 的 phone 参数已实测可用）。"""
        clean = str(phone or "").strip()
        if not clean:
            return None
        obj = self._call("/open/v2/user/search", {"phone": clean})
        if not obj:
            return None
        for row in obj.get("users") or []:
            if str(row.get("phone") or "").strip() == clean:
                return self._pick_user(row)
        return None

    def search_by_name(self, name: str) -> List[Dict[str, Any]]:
        clean = str(name or "").strip()
        if not clean:
            return []
        obj = self._call("/open/v2/user/search", {"name": clean})
        out = []
        for row in (obj or {}).get("users") or []:
            picked = self._pick_user(row)
            if picked:
                out.append(picked)
        return out

    def get_by_union_id(self, union_id: str) -> Optional[Dict[str, Any]]:
        """按 union_id 查单个用户（回调事件里直接带 union_id，用于首条消息自动识别）。"""
        clean = str(union_id or "").strip()
        if not clean:
            return None
        obj = self._call("/open/v2/user/get", {"union_id": clean})
        if not obj:
            return None
        return self._pick_user(obj) if obj.get("union_id") else self._pick_user(
            (obj.get("users") or [{}])[0] if isinstance(obj.get("users"), list) else {}
        )

    def dept_users_list(self, dept_id: str, page_num: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """部门成员分页（名册对账/批量兜底用；失败返回空结构）。"""
        obj = self._call("/open/v2/dept/users/list", {
            "dept_id": str(dept_id or "").strip(),
            "page_num": int(page_num),
            "page_size": int(page_size),
        })
        if not obj:
            return {"total": 0, "total_pages": 0, "page_num": page_num, "users": []}
        users = []
        for row in obj.get("users") or []:
            picked = self._pick_user(row)
            if picked:
                picked["title"] = str((row.get("extension") or {}).get("job_level") or "").strip()
                users.append(picked)
        return {"total": int(obj.get("total") or 0), "total_pages": int(obj.get("total_pages") or 0),
                "page_num": int(obj.get("page_num") or page_num), "users": users}

    # ── 部门树（2026-11-11 任务二：后台组织架构选择器）───────────────────
    # 实测定档（2026-11-11 探针）：
    #   dept/get      {dept_id,parent_id,name,full_name,count(子树人数),index,hide_count}
    #   dept/children {total,departments:[{dept_id,name,count(子树人数),index}]}（参数名 dept_id；0/空=400001）
    #   dept/parents  {parents:[直接父 … 根]}（根的 parent_id="0"）
    #   dept/users/list 只含**直属**成员（非子树）；count 才是子树总数
    def get_department(self, dept_id: str) -> Optional[Dict[str, Any]]:
        obj = self._call("/open/v2/dept/get", {"dept_id": str(dept_id or "").strip()})
        if not obj or not str(obj.get("dept_id") or "").strip():
            return None
        return {
            "dept_id": str(obj.get("dept_id") or "").strip(),
            "parent_id": str(obj.get("parent_id") or "").strip(),
            "name": str(obj.get("name") or "").strip(),
            "full_name": str(obj.get("full_name") or "").strip(),
            "user_count": int(obj.get("count") or 0),  # 官方口径：含全部下级的子树人数
        }

    def department_children(self, dept_id: str) -> List[Dict[str, Any]]:
        """子部门列表（条目自带 count=子树人数；失败/无子级返回空）。"""
        obj = self._call("/open/v2/dept/children", {"dept_id": str(dept_id or "").strip()})
        out: List[Dict[str, Any]] = []
        for row in (obj or {}).get("departments") or []:
            dept_id_clean = str(row.get("dept_id") or "").strip()
            if not dept_id_clean:
                continue
            out.append({
                "dept_id": dept_id_clean,
                "name": str(row.get("name") or "").strip(),
                "user_count": int(row.get("count") or 0),
                "index": int(row.get("index") or 0),
            })
        out.sort(key=lambda d: d["index"])
        return out

    def department_parents(self, dept_id: str) -> List[Dict[str, Any]]:
        """祖先链（官方返回顺序：直接父 → … → 根；失败返回空）。"""
        obj = self._call("/open/v2/dept/parents", {"dept_id": str(dept_id or "").strip()})
        out: List[Dict[str, Any]] = []
        for row in (obj or {}).get("parents") or []:
            dept_id_clean = str(row.get("dept_id") or "").strip()
            if not dept_id_clean:
                continue
            out.append({
                "dept_id": dept_id_clean,
                "parent_id": str(row.get("parent_id") or "").strip(),
                "name": str(row.get("name") or "").strip(),
                "full_name": str(row.get("full_name") or "").strip(),
            })
        return out

    def dept_users_all(self, dept_id: str, page_size: int = 100, max_pages: int = 50) -> List[Dict[str, Any]]:
        """某部门**直属**成员全量（自动翻页；失败/超页上限返回已取部分）。"""
        all_users: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = self.dept_users_list(dept_id, page_num=page, page_size=page_size)
            all_users.extend(batch.get("users") or [])
            if page >= max(1, int(batch.get("total_pages") or 1)):
                break
        return all_users