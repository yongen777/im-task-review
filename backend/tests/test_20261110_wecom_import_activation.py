# -*- coding: utf-8 -*-
"""2026-11-10 企微通道镜像方案A专项：导入即激活（手机号 → userid → 绑定）。

口径（用户 2026-11-10 拍板，与量子密信镜像）：
1. 官方 95402：POST /cgi-bin/user/getuserid（corpid+secret，通讯录权限），明文返回 userid；
2. 导入/加成员（平台=wecom）时自动反查并写 delivery_target(platform='wecom',
   external_id=userid, external_type='user')，用户 0 操作；
3. 与 zdxlz 的 resolver 严格隔离：企微导入不得误用 union_id 解析器，反之亦然；
4. resolver 未装配 / 查不到 → 静默回退旧行为（H5 兜底仍可用）；
5. 调度发送：external_id(userid) 原样进入 aibot 发送路径。

⚠️ 测试全部 MockTransport / Fake，**绝不建立 WebSocket 长连接**（用户安全红线）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.channels.wecom import WecomClient  # noqa: E402
from app.channels.wecom_contacts import WecomContacts  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402

P_A = "13800002001"
P_B = "13800002002"
NAME_A = "企微甲"
NAME_B = "企微乙"


class FakeWecomContacts:
    """内存版企微反查（手机号→userid，便于 state 插桩测试；MCP 姓名+部门语义在新文件覆盖）。"""

    def __init__(self, mapping: dict | None = None):
        self.mapping = mapping or {}  # phone → userid
        self.calls = 0

    def resolve_userid_by_phone(self, phone: str):
        self.calls += 1
        return self.mapping.get((phone or "").strip())

    def resolve_userid(self, phone: str, name: str = "", dept: str = ""):
        # 2026-11-10 方案A 后 state 走本签名；假件保持 phone 键以便导入流程测试
        self.calls += 1
        return self.mapping.get((phone or "").strip())

    def resolve_user(self, phone: str, name: str = "", dept: str = ""):
        # 2026-11-11 部门口径：state 现走 resolve_user（uid+部门回填）；假件仍按 phone 键
        self.calls += 1
        uid = self.mapping.get((phone or "").strip())
        return {"external_id": uid, "department": "", "dept_path": ""} if uid else None


def _store() -> StateStore:
    # 企微通道：单通道调度按 store.platform 查绑定
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_wecom_")) / "im.db", platform="wecom")


def _csv(phone: str, name: str) -> dict:
    return {"phone": phone, "name": name, "dept": "一部"}


# ── 1. 官方 95402 接口（WecomClient）────────────────────────────────
def _client(handler) -> WecomClient:
    return WecomClient("corp_x", "1000002", "sec_x",
                       http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_wecom_client_get_userid_success() -> None:
    """POST /cgi-bin/user/getuserid：入参 mobile、token 取查询参数、明文取 userid。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"errcode": 0, "access_token": "tok_1", "expires_in": 7200})
        if request.url.path == "/cgi-bin/user/getuserid":
            seen["token"] = request.url.params.get("access_token")
            seen["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "userid": "zhangsan"})
        return httpx.Response(404)

    got = _client(handler).get_userid_by_mobile(P_A)
    assert got == "zhangsan"
    assert seen["token"] == "tok_1"
    assert seen["body"] == {"mobile": P_A}


def test_wecom_client_get_userid_errcode_returns_none() -> None:
    """60011（无通讯录权限）等错误码 → None（fail-open）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"errcode": 0, "access_token": "tok", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 60011, "errmsg": "no privilege"})

    assert _client(handler).get_userid_by_mobile(P_A) is None


def test_wecom_client_get_userid_token_retry() -> None:
    """token 失效（42001）→ 强制刷新后重试一次成功。"""
    calls = {"getuserid": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"errcode": 0, "access_token": "tok_ok", "expires_in": 7200})
        calls["getuserid"] += 1
        if calls["getuserid"] == 1:
            return httpx.Response(200, json={"errcode": 42001, "errmsg": "token expired"})
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "userid": "lisi"})

    assert _client(handler).get_userid_by_mobile(P_A) == "lisi"
    assert calls["getuserid"] == 2


def test_wecom_contacts_adapter_fail_open() -> None:
    """适配器：无凭证/异常/查不到 → None，绝不抛异常。"""
    class _Cfg:
        wecom_corpid = ""
        wecom_secret = ""
        wecom_agentid = ""

    class _Boom:
        def get_userid_by_mobile(self, phone: str):  # noqa: ARG002
            raise RuntimeError("boom")

    assert WecomContacts(_Cfg()).resolve_userid_by_phone(P_A) is None  # 无凭证

    class _CfgOk:
        wecom_corpid = "corp"
        wecom_secret = "sec"
        wecom_agentid = ""

    assert WecomContacts(_CfgOk(), client=_Boom()).resolve_userid_by_phone(P_A) is None  # 异常吞掉

    class _Ok:
        def get_userid_by_mobile(self, phone: str):
            return "u_1"

    assert WecomContacts(_CfgOk(), client=_Ok()).resolve_userid_by_phone(P_A) == "u_1"


# ── 2. 导入即激活（state 层）────────────────────────────────────────
def test_import_wecom_auto_binds_userid() -> None:
    """wecom 角色导入 → 自动反查 userid 并绑定；zdxlz resolver 一次都不调。"""
    store = _store()
    try:
        rid = store.create_role("W组", actor="t", platform="wecom")["role_id"]

        class _ZdxFake:
            calls = 0

            def search_by_phone(self, phone: str):  # noqa: ARG002
                _ZdxFake.calls += 1
                return None

        store.contacts = _ZdxFake()  # 隔离验证：即使装错了也不得被企微导入使用
        fake = FakeWecomContacts({P_A: "wx_uid_A", P_B: "wx_uid_B"})
        store.wecom_contacts = fake
        res = store.import_role_members(rid, [_csv(P_A, NAME_A), _csv(P_B, NAME_B)], dry_run=False, actor="t")
        assert res["will_add"] == 2, res
        auto = res["auto_activation"]
        assert auto["enabled"] is True and auto["activated"] == 2 and auto["failed"] == 0, auto
        ra = store.get_delivery_target(P_A, platform="wecom")
        assert ra["external_id"] == "wx_uid_A" and ra["external_type"] == "user", ra
        assert store.get_delivery_target(P_A, platform="zdxlz") is None, "企微绑定不得落到 zdxlz 平台"
        assert _ZdxFake.calls == 0, "企微导入不得误用 union_id 解析器"
    finally:
        store.close()


def test_import_wecom_skips_already_bound() -> None:
    """重复导入（另一角色）：已绑定者跳过，不重复打接口。"""
    store = _store()
    try:
        rid = store.create_role("W组", actor="t", platform="wecom")["role_id"]
        rid2 = store.create_role("W2组", actor="t", platform="wecom")["role_id"]
        fake = FakeWecomContacts({P_A: "wx_uid_A"})
        store.wecom_contacts = fake
        store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert fake.calls == 1
        res2 = store.import_role_members(rid2, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res2["auto_activation"]["activated"] == 0 and res2["auto_activation"]["skipped"] == 1, res2
        assert fake.calls == 1, "已绑定成员不得再打接口"
    finally:
        store.close()


def test_import_wecom_without_resolver_falls_back() -> None:
    """wecom_contacts 未装配（测试/降级）→ 与旧版一致，无绑定、不报错。"""
    store = _store()
    try:
        rid = store.create_role("W组", actor="t", platform="wecom")["role_id"]
        res = store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res["ok"] and res["will_add"] == 1
        assert res["auto_activation"]["enabled"] is False
        assert store.get_delivery_target(P_A, platform="wecom") is None
    finally:
        store.close()


def test_add_role_member_wecom_auto_binds() -> None:
    store = _store()
    try:
        rid = store.create_role("W组", actor="t", platform="wecom")["role_id"]
        store.wecom_contacts = FakeWecomContacts({P_A: "wx_uid_A"})
        store.add_role_member(rid, P_A, NAME_A, "一部", actor="t")
        row = store.get_delivery_target(P_A, platform="wecom")
        assert row and row["external_id"] == "wx_uid_A" and row["external_type"] == "user", row
    finally:
        store.close()


def test_import_zdxlz_never_uses_wecom_resolver() -> None:
    """反向隔离：zdxlz 平台导入只走 union_id 解析器，不得碰企微反查。"""
    store = _store()
    try:
        rid = store.create_role("Z组", actor="t", platform="zdxlz")["role_id"]

        class _Zdx:
            def search_by_phone(self, phone: str):
                return {"union_id": "union_A", "phone": phone, "name": NAME_A}

        fake_wx = FakeWecomContacts({P_A: "wx_uid_A"})
        store.contacts = _Zdx()
        store.wecom_contacts = fake_wx
        res = store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res["auto_activation"]["activated"] == 1, res
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "union_A" and row["external_type"] == "union_id", row
        assert fake_wx.calls == 0, "zdxlz 导入不得误用企微反查"
    finally:
        store.close()


# ── 3. 调度投递：userid 原样进发送路径 ────────────────────────────
def test_scheduler_delivers_to_wecom_userid() -> None:
    """企微绑定 external_id(userid) 直接作为接收人；3 tick 幂等只发 1 条。"""
    store = _store()
    try:
        rid = store.create_role("W组", actor="t", platform="wecom")["role_id"]
        store.upsert_delivery_target(P_A, "wx_uid_A", platform="wecom")
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        tpl = store.create_template(
            {"name": "企微通知T", "kind": "notice", "role_ids": [rid],
             "text_tpl": "正文", "notice_times": ["09:00"], "period": "day"},
            actor="t",
        )
        ntf = FakeNotifier()
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=ntf,
                          now_fn=lambda: datetime(2026, 11, 10, 9, 0, 30))
        sched.run_once()
        assert len(ntf.sent_messages) == 1
        assert ntf.sent_messages[0].receive_id == "wx_uid_A"
        sched.run_once()
        sched.run_once()
        assert len(ntf.sent_messages) == 1, "幂等红线：同一消息同一接收人只发一条"
        assert len(store.list_instances_for_period(tpl["template_id"], "2026-11-10")) == 1
    finally:
        store.close()