# -*- coding: utf-8 -*-
"""企业微信「手机号 → userid」反查适配器（2026-11-10 企微通道对齐方案A）。

背景：量子密信侧已用官方通讯录解密实现「导入即激活」；企微侧镜像同一能力：
  自建应用凭证（corpid + secret，需通讯录读取权限）调
  POST /cgi-bin/user/getuserid {"mobile": "..."} → 明文返回 userid，
  导入成员时自动写 delivery_target(platform='wecom', external_id=userid)。

⚠️ 与 aibot 长连接的关系：
  · 本适配器只走 HTTP（gettoken / getuserid），**绝不建立/触碰 WebSocket 长连接**；
  · 因此本地验证不会踢掉生产正在跑的智能机器人连接（用户 2026-11-10 安全红线）；
  · 同一机器人的长连接唯一性约束与本模块无关。

fail-open 约定：凭证缺失 / 无权限 / 网络异常 → 返回 None + 打码日志，调用方静默回退旧行为。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .wecom import WecomClient

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?<!\d)1\d{10}(?!\d)")


def _mask(text: str) -> str:
    return _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], str(text or ""))


class WecomContacts:
    """通讯录反查门面：只做 phone → userid，失败一律 None（不向业务层抛异常）。"""

    def __init__(self, cfg: Any, client: Optional[Any] = None) -> None:
        self.cfg = cfg
        self.corpid = str(getattr(cfg, "wecom_corpid", "") or "").strip()
        self.secret = str(getattr(cfg, "wecom_secret", "") or "").strip()
        agentid = str(getattr(cfg, "wecom_agentid", "") or "").strip()
        # agentid 对 getuserid 不是必需（只有 message/send 需要）；传空串不报错，只是不可发消息
        self.client = client or WecomClient(self.corpid, agentid, self.secret)

    def is_configured(self) -> bool:
        """通讯录反查是否具备最小凭证（corpid + secret；agentid 不参与该接口）。"""
        return bool(self.corpid and self.secret)

    def resolve_userid_by_phone(self, phone: str) -> Optional[str]:
        """手机号 → 企业微信成员 userid；查不到/无权限/异常 → None。"""
        clean = str(phone or "").strip()
        if not clean:
            return None
        if not self.is_configured():
            return None
        try:
            return self.client.get_userid_by_mobile(clean)
        except Exception as exc:  # noqa: BLE001 - 反查失败绝不拖垮导入
            logger.warning("企微 userid 反查异常 phone=%s: %s", _mask(clean), _mask(str(exc))[:160])
            return None