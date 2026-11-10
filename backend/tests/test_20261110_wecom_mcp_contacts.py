# -*- coding: utf-8 -*-
"""2026-11-10 企微 MCP 通讯录反查专项（姓名+部门 → userid 匹配规则）。

依据 2026-11-10 实测（用户提供 apikey）：
  · MCP contact_users_search 只支持人名关键词（手机号/部门名都搜不到）；
  · 返回 users:[{userid,name,departments:[全路径]}]，userid 与 aibot 投递同一体系；
  · 组织里存在「林波（云中台）」类展示后缀 → 归一化必须剥括号。

匹配口径（保守）：
  1) 归一化姓名精确相等（剥「（…）」/「_后缀」/空白）；
  2) 唯一命中 → 绑定；重名 → 归一化部门段消歧，唯一才绑定；
  3) 查不到 / 消歧失败 → None（绝不猜）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.channels.wecom_contacts import WecomContacts  # noqa: E402
from app.channels.wecom_mcp import WecomMcpContacts, normalize_name  # noqa: E402

PATH_CS = "中国电信德阳分公司/德阳市公司本部/云中台（天翼云四川分公司德阳分部、电信数智四川分公司德阳分部）/产数支撑中心"
PATH_CQ = "中国电信德阳分公司/罗江区/罗江分公司/鄢家支局"


def _mcp(users: list[dict]) -> WecomMcpContacts:
    """MockTransport 版 MCP：tools/call 返回给定 users；initialize 正常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"),
                                             "result": {"protocolVersion": "2025-03-26",
                                                        "capabilities": {},
                                                        "serverInfo": {"name": "t", "version": "1"}}})
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if body.get("method") == "tools/call":
            payload = {"security_notice": "x", "extra_identity_context": "y",
                       "users": users, "users_count": len(users), "errcode": 0, "errmsg": "ok"}
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": body.get("id"),
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
            })
        return httpx.Response(404)

    return WecomMcpContacts("test_key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


class _CfgOnlyKey:
    wecom_corpid = ""
    wecom_secret = ""
    wecom_agentid = ""
    wecom_mcp_apikey = "test_key"


# ── 1. 归一化 ───────────────────────────────────────────────────────
def test_normalize_name_variants() -> None:
    assert normalize_name("林波（云中台）") == "林波"
    assert normalize_name("简睿骁_德阳市") == "简睿骁"
    assert normalize_name("  郑佳 ") == "郑佳"
    assert normalize_name("郑佳超") == "郑佳超", "⛔ 剥后缀不得把真名截短"
    assert normalize_name("张三（德阳产数支撑中心）") == "张三"


# ── 2. 匹配规则 ─────────────────────────────────────────────────────
def test_resolve_unique_exact_name() -> None:
    """唯一精确姓名命中 → 直接返回 userid（部门不做强约束，名册部门可能是管理员自拟值）。"""
    mcp = _mcp([{"userid": "wo_1", "name": "郑佳", "departments": [PATH_CS]}])
    assert mcp.resolve_userid("13800000001", "郑佳", "市场部") == "wo_1"


def test_resolve_duplicate_name_disambiguated_by_dept() -> None:
    """重名：归一化部门段消歧（「云中台（…）」段剥括号后 == 名册「云中台」）。"""
    mcp = _mcp([
        {"userid": "wo_a", "name": "林波（云中台）", "departments": [PATH_CS]},
        {"userid": "wo_b", "name": "林波", "departments": [PATH_CQ]},
    ])
    assert mcp.resolve_userid("13800000001", "林波", "云中台") == "wo_a"
    assert mcp.resolve_userid("13800000001", "林波", "鄢家支局") == "wo_b"


def test_resolve_ambiguous_without_dept_returns_none() -> None:
    """重名且无部门可用 → None（绝不猜）。"""
    mcp = _mcp([
        {"userid": "wo_a", "name": "李四", "departments": [PATH_CS]},
        {"userid": "wo_b", "name": "李四", "departments": [PATH_CQ]},
    ])
    assert mcp.resolve_userid("13800000001", "李四", "") is None
    assert mcp.resolve_userid("13800000001", "李四", "不存在的部门") is None


def test_resolve_no_exact_match_returns_none() -> None:
    """搜索只回模糊命中（如「郑佳超」）而名册是「郑佳」→ 不绑定。"""
    mcp = _mcp([{"userid": "wo_c", "name": "郑佳超", "departments": [PATH_CQ]}])
    assert mcp.resolve_userid("13800000001", "郑佳", "一部") is None


def test_mcp_transport_error_returns_none() -> None:
    """MCP 挂了 → None，不抛异常（fail-open）。"""
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500)

    mcp = WecomMcpContacts("test_key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert mcp.resolve_userid("13800000001", "郑佳", "") is None
    assert mcp.search_users("郑佳") == []


# ── 3. WecomContacts 门面：双通道择优 ───────────────────────────────
def test_facade_prefers_phone_lookup_when_secret_configured() -> None:
    """有 corpid+secret 时先走 getuserid（手机号精确）；失败再落到 MCP。"""

    class _PhoneClient:
        def __init__(self) -> None:
            self.called = 0

        def get_userid_by_mobile(self, phone: str) -> str | None:
            self.called += 1
            return "uid_by_phone" if phone == "13800000001" else None

    class _CfgBoth:
        wecom_corpid = "ww_x"
        wecom_secret = "sec"
        wecom_agentid = ""
        wecom_mcp_apikey = ""

    pc = _PhoneClient()
    facade = WecomContacts(_CfgBoth(), client=pc, mcp=_mcp([{"userid": "wo_mcp", "name": "郑佳", "departments": []}]))
    assert facade.resolve_userid("13800000001", "郑佳", "") == "uid_by_phone"
    assert pc.called == 1
    # 手机号查不到 → 落 MCP
    assert facade.resolve_userid("13800000002", "郑佳", "") == "wo_mcp"


def test_facade_apikey_only_uses_mcp() -> None:
    """只有 apikey（当前生产形态）→ 直接 MCP。"""
    facade = WecomContacts(_CfgOnlyKey(), client=None, mcp=_mcp([{"userid": "wo_x", "name": "郑佳", "departments": []}]))
    assert facade.is_configured() is True
    assert facade.resolve_userid("13800000001", "郑佳", "") == "wo_x"


def test_facade_no_credentials_returns_none() -> None:
    class _CfgNone:
        wecom_corpid = ""
        wecom_secret = ""
        wecom_agentid = ""
        wecom_mcp_apikey = ""

    facade = WecomContacts(_CfgNone())
    assert facade.is_configured() is False
    assert facade.resolve_userid("13800000001", "郑佳", "") is None
