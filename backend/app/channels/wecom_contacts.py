# -*- coding: utf-8 -*-
"""企业微信「手机号 → userid」反查适配器（2026-11-10 企微通道对齐方案A）。

两代通道（按凭证自动择优）：
  ① 自建应用凭证（corpid + secret，通讯录权限）→ POST /cgi-bin/user/getuserid
     —— **手机号精确反查**（语义最强；凭证当前缺，用户在补）
  ② 智能机器人 MCP 通讯录（apikey）→ contact_users_search
     —— **姓名+部门匹配**（2026-11-10 实测：MCP 不支持手机号搜索，只匹配人名/别名；
        userid 与 aibot 事件 userid 同一体系，郑佳实例证）
匹配规则（②，保守）：归一化姓名（剥括号/下划线后缀）精确相等；唯一命中才绑定；
重名时用归一化部门段消歧；查不到 / 消歧失败 → None（导入 failed 清单 + H5 兜底）。

⚠️ 与 aibot 长连接的关系：
  · 两条通道都只走 HTTPS，**绝不建立/触碰 WebSocket 长连接**；
  · 因此本地验证不会踢掉生产正在跑的智能机器人连接（用户 2026-11-10 安全红线）。

fail-open 约定：凭证缺失 / 无权限 / 网络异常 → 返回 None + 打码日志，调用方静默回退旧行为。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .wecom import WecomClient
from .wecom_mcp import WecomMcpContacts

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?<!\d)1\d{10}(?!\d)")


def _mask(text: str) -> str:
    return _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], str(text or ""))


class WecomContacts:
    """企微 userid 反查门面：按凭证择优（getuserid 手机号精确 → MCP 姓名匹配）。"""

    def __init__(self, cfg: Any, client: Optional[Any] = None,
                 mcp: Optional[Any] = None) -> None:
        self.cfg = cfg
        self.corpid = str(getattr(cfg, "wecom_corpid", "") or "").strip()
        self.secret = str(getattr(cfg, "wecom_secret", "") or "").strip()
        agentid = str(getattr(cfg, "wecom_agentid", "") or "").strip()
        # agentid 对 getuserid 不是必需（只有 message/send 需要）；传空串不报错，只是不可发消息
        self.client = client or WecomClient(self.corpid, agentid, self.secret)
        apikey = str(getattr(cfg, "wecom_mcp_apikey", "") or "").strip()
        self.mcp = mcp or (WecomMcpContacts(apikey) if apikey else None)

    def is_configured(self) -> bool:
        """反查是否具备最小凭证（自建应用 corpid+secret，或 MCP apikey）。"""
        return bool((self.corpid and self.secret) or self.mcp is not None)

    def resolve_userid(self, phone: str, name: str = "", dept: str = "") -> Optional[str]:
        """名册成员 → 企微 userid。查不到/无凭证/异常 → None，绝不抛异常。"""
        clean_phone = str(phone or "").strip()
        if not clean_phone:
            return None
        # ① 手机号精确反查（有自建应用凭证时优先）
        if self.corpid and self.secret:
            try:
                uid = self.client.get_userid_by_mobile(clean_phone)
                if uid:
                    return str(uid).strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning("企微 userid 反查（getuserid）异常 phone=%s: %s",
                               _mask(clean_phone), _mask(str(exc))[:160])
        # ② MCP 姓名匹配（apikey 授权时兜底；查不到返回 None）
        if self.mcp is not None:
            return self.mcp.resolve_userid(clean_phone, name, dept)
        return None

    def resolve_userid_by_phone(self, phone: str) -> Optional[str]:
        """兼容旧签名（等价于不带姓名/部门的反查：只有 getuserid 路径生效）。"""
        return self.resolve_userid(phone, name="", dept="")

    # ── 组织架构（2026-11-11 任务二：Org Picker 后端数据源）──────────────
    def org_available(self) -> bool:
        """组织树数据源是否可用（目前仅自建应用凭证形态；MCP 无部门树能力）。"""
        return bool(self.corpid and self.secret)

    def org_departments(self, parent_id: str = "") -> List[Dict[str, Any]]:
        """指定父部门下的子部门列表（含 has_children 计算）。

        parent_id 空 → 返回根部门。WeCom department/list 一次拉全量，本地过滤 parentid，
        has_children 由「是否存在以其为 parentid 的条目」直接得出（零额外请求）。
        """
        if not self.org_available():
            return []
        all_depts = self.client.department_list()
        if not all_depts:
            return []
        pid = str(parent_id or "0").strip() or "0"
        if pid == "0":
            # 企微树根约定 parentid=0；上层可能给根 dept_id，此处兼容
            return [{"dept_id": d["dept_id"], "name": d["name"],
                     "has_children": any(x["parent_id"] == d["dept_id"] for x in all_depts),
                     "user_count": 0}
                    for d in all_depts if d["parent_id"] == "0"]
        children = [d for d in all_depts if d["parent_id"] == pid]
        if not children:
            return []
        ids = {d["dept_id"] for d in all_depts}
        return [{"dept_id": d["dept_id"], "name": d["name"],
                 "has_children": d["dept_id"] in ids,
                 "user_count": 0}
                for d in children]

    def org_department_users(self, dept_id: str) -> List[Dict[str, Any]]:
        """部门**直属**成员明细（与量子密信 dept/users/list 同口径）。"""
        if not self.org_available():
            return []
        return self.client.department_users(str(dept_id or "").strip())