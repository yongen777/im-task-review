# -*- coding: utf-8 -*-
"""2026-11-10 方案A专项用例：union_id 直连激活（导入即激活）。

口径（用户 2026-11-10 拍板，基于当日实测）：
1. 通讯录响应可用官方 decryptV2 算法在本地解密（不依赖 KMS/密码设备）；
2. 导入成员 / 单个加成员时，自动查通讯录 phone→union_id 并写绑定
   （external_type='union_id'），zdxlz 平台专属；contacts 不可用时静默回退旧行为；
3. 发送时按绑定的 external_type 传 receive_type（union_id 行不再依赖 IM userId/会话）；
4. 回调事件带 union_id：未绑定用户发消息可按 union_id 反查通讯录自动绑定（路径 U）；
5. H5 登记保留为兜底；查得到通讯录时优先改写为 union_id 绑定。

⚠️ 全部用 FakeContacts / MockTransport，测试绝不发起真实网络请求。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.api.events import handle_inbound_text  # noqa: E402
from app.api.h5 import process_h5_bind  # noqa: E402
from app.channels.zdxlz_contacts import ZdxlzContacts  # noqa: E402
from app.channels.zdxlz_crypto import (  # noqa: E402
    ResponseCryptoError,
    build_response_envelope_for_test,
    decrypt_response_payload,
    verify_response_sign,
)
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402

P_A = "13800001001"
P_B = "13800001002"
P_C = "13800001003"
NAME_A = "测试甲"
NAME_B = "测试乙"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_unionid_")) / "im.db")


class FakeContacts:
    """内存版通讯录：只映射显式登记过的手机号。"""

    def __init__(self, phone_to_user: dict | None = None, union_to_user: dict | None = None):
        self.phone_to_user = phone_to_user or {}
        self.union_to_user = union_to_user or {}
        self.calls = 0

    def search_by_phone(self, phone: str):
        self.calls += 1
        return self.phone_to_user.get((phone or "").strip())

    def search_by_name(self, name: str):
        self.calls += 1
        return [u for u in self.phone_to_user.values() if u.get("name") == name]

    def get_by_union_id(self, union_id: str):
        self.calls += 1
        return self.union_to_user.get((union_id or "").strip())


def _user(union_id: str, phone: str, name: str) -> dict:
    return {"customer_id": "", "union_id": union_id, "name": name, "phone": phone,
            "extension": {}, "user_departments": []}


# ── 1. 官方解密算法 ─────────────────────────────────────────────────
def test_decrypt_v2_roundtrip_and_sign_guard() -> None:
    """信封构建→验签→解密往返一致；篡改 sign 必须拒绝；错误密钥必须失败。"""
    app_id, app_secret = "appid_001", "sec_001"
    payload = {"users": [_user("u1", P_A, NAME_A)]}
    env = build_response_envelope_for_test(app_id, app_secret, payload)
    assert verify_response_sign(app_id, env) is True
    import json as _json
    plain = decrypt_response_payload(app_id, app_secret, env)
    assert _json.loads(plain)["users"][0]["union_id"] == "u1"

    # 篡改 sign → 拒绝
    bad = dict(env)
    bad["sign"] = ("0" if env["sign"][0] != "0" else "1") + env["sign"][1:]
    assert verify_response_sign(app_id, bad) is False
    try:
        decrypt_response_payload(app_id, app_secret, bad)
        raise AssertionError("签名不符必须抛 ResponseCryptoError")
    except ResponseCryptoError:
        pass
    # 错误密钥 → 解不开（填充非法）
    try:
        decrypt_response_payload(app_id, "wrong_secret", env)
        raise AssertionError("错误密钥必须抛 ResponseCryptoError")
    except ResponseCryptoError:
        pass


def test_contacts_client_parses_envelope() -> None:
    """ZdxlzContacts：密文响应解出用户；错误码透传不解密；明文 data 直接返回。"""
    app_id, app_secret = "appid_002", "sec_002"
    users = {"users": [_user("u2", P_A, NAME_A)]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/open/v2/user/search"):
            env = build_response_envelope_for_test(app_id, app_secret, users)
            return httpx.Response(200, json={"code": 200, "msg": "OK", "data": env})
        return httpx.Response(200, json={"code": 400001, "msg": "参数错误", "data": None})

    class _Chan:
        def get_access_token(self, force_refresh: bool = False) -> str:  # noqa: ARG002
            return "tok"

    class _Cfg:
        def __init__(self) -> None:
            self.app_id = app_id
            self.app_secret = app_secret

    contacts = ZdxlzContacts(_Cfg(), _Chan(), http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    got = contacts.search_by_phone(P_A)
    assert got and got["union_id"] == "u2" and got["phone"] == P_A
    assert contacts.search_by_phone("13900000000") is None  # 信封里查无此人 → None


# ── 2. 导入即激活 ───────────────────────────────────────────────────
def _csv(phone: str, name: str) -> dict:
    return {"phone": phone, "name": name, "dept": "一部"}


def test_import_auto_binds_union_id() -> None:
    """导入 2 人 → 2 条 union_id 绑定；结果带 auto_activation 统计。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.contacts = FakeContacts(
            phone_to_user={P_A: _user("union_A", P_A, NAME_A), P_B: _user("union_B", P_B, NAME_B)},
            union_to_user={"union_A": _user("union_A", P_A, NAME_A), "union_B": _user("union_B", P_B, NAME_B)},
        )
        res = store.import_role_members(rid, [_csv(P_A, NAME_A), _csv(P_B, NAME_B)], dry_run=False, actor="t")
        assert res["ok"] and res["will_add"] == 2, res
        auto = res["auto_activation"]
        assert auto["enabled"] is True and auto["activated"] == 2 and auto["failed"] == 0, auto
        ra = store.get_delivery_target(P_A, platform="zdxlz")
        assert ra["external_id"] == "union_A" and ra["external_type"] == "union_id", ra
        rb = store.get_delivery_target(P_B, platform="zdxlz")
        assert rb["external_id"] == "union_B" and rb["external_type"] == "union_id", rb
    finally:
        store.close()


def test_import_rebind_reactivates_unusable_row() -> None:
    """usable=0 的旧绑定（历史上发 500 被标记）：导入时用 union_id 复活，保留首次激活时刻。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.upsert_delivery_target(P_A, "im_old", platform="zdxlz", activated_at="2026-10-01 09:00:00")
        store.mark_delivery_target_unusable(P_A, platform="zdxlz")
        store.contacts = FakeContacts(phone_to_user={P_A: _user("union_A", P_A, NAME_A)})
        res = store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res["auto_activation"]["activated"] == 1, res
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "union_A" and row["external_type"] == "union_id"
        assert row["usable"] == 1
        assert row["activated_at"] == "2026-10-01 09:00:00", "首次激活时刻不可被覆盖"
    finally:
        store.close()


def test_import_skips_already_bound_and_counts_calls() -> None:
    """再导入（第二个角色）：已绑定的人不重复调通讯录、不覆盖绑定。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        rid2 = store.create_role("B组", actor="t", platform="zdxlz")["role_id"]
        fake = FakeContacts(phone_to_user={P_A: _user("union_A", P_A, NAME_A)})
        store.contacts = fake
        store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert fake.calls == 1
        res2 = store.import_role_members(rid2, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res2["auto_activation"]["activated"] == 0 and res2["auto_activation"]["skipped"] == 1, res2
        assert fake.calls == 1, "已绑定成员不得再打通讯录"
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "union_A"
    finally:
        store.close()


def test_import_blocked_batch_binds_nothing() -> None:
    """整批拒绝（重复行）→ 一条绑定都不产生。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        fake = FakeContacts(phone_to_user={P_A: _user("union_A", P_A, NAME_A)})
        store.contacts = fake
        res = store.import_role_members(
            rid, [_csv(P_A, NAME_A), _csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res["blocked"] is True and res["will_add"] == 0
        assert fake.calls == 0, "整批拒绝不得打通讯录"
        assert store.get_delivery_target(P_A, platform="zdxlz") is None
    finally:
        store.close()


def test_import_without_contacts_falls_back_silently() -> None:
    """contacts 未装配（测试/降级环境）：导入行为与旧版完全一致。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        res = store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res["ok"] and res["will_add"] == 1
        assert res["auto_activation"]["enabled"] is False
        assert store.get_delivery_target(P_A, platform="zdxlz") is None
    finally:
        store.close()


def test_import_wecom_platform_never_calls_contacts() -> None:
    """企微角色：union_id 机制只属于量子密信，企微导入不得打通讯录。"""
    store = _store()
    try:
        rid = store.create_role("W组", actor="t", platform="wecom")["role_id"]
        fake = FakeContacts(phone_to_user={P_A: _user("union_A", P_A, NAME_A)})
        store.contacts = fake
        res = store.import_role_members(rid, [_csv(P_A, NAME_A)], dry_run=False, actor="t")
        assert res["auto_activation"]["enabled"] is False
        assert fake.calls == 0
        assert store.get_delivery_target(P_A, platform="zdxlz") is None
        assert store.get_delivery_target(P_A, platform="wecom") is None
    finally:
        store.close()


def test_add_role_member_auto_binds_single() -> None:
    """单个加成员同样自动激活。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.contacts = FakeContacts(phone_to_user={P_A: _user("union_A", P_A, NAME_A)})
        store.add_role_member(rid, P_A, NAME_A, "一部", actor="t")
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row and row["external_id"] == "union_A" and row["external_type"] == "union_id", row
    finally:
        store.close()


# ── 3. 发送按 external_type 传 receive_type ────────────────────────
def _notice_payload(rid: str) -> dict:
    return {"name": "通知T", "kind": "notice", "role_ids": [rid],
            "text_tpl": "正文", "notice_times": ["09:00"], "period": "day"}


def test_scheduler_sends_receive_type_union_id_and_user() -> None:
    """union_id 绑定 → receive_type='union_id'；旧 IM 绑定 → 'user'；且 3 tick 幂等只发 1 条。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.upsert_delivery_target(P_A, "union_A", platform="zdxlz", external_type="union_id")
        store.upsert_delivery_target(P_B, "im_legacy_B", platform="zdxlz", external_type="user")
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        store.add_role_member(rid, P_B, NAME_B, actor="t")
        tpl = store.create_template(_notice_payload(rid), actor="t")

        ntf = FakeNotifier()
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=ntf,
                          now_fn=lambda: datetime(2026, 11, 10, 9, 0, 30))
        sched.run_once()
        by_id = {m.receive_id: m for m in ntf.sent_messages}
        assert by_id["union_A"].receive_type == "union_id", by_id
        assert by_id["im_legacy_B"].receive_type == "user", by_id
        # 连跑 3 个 tick：同一消息同一接收人有且仅有一条（幂等红线）
        sched.run_once()
        sched.run_once()
        assert len(ntf.sent_messages) == 2, [ (m.receive_id, m.text[:10]) for m in ntf.sent_messages ]
        assert len(store.list_instances_for_period(tpl["template_id"], "2026-11-10")) == 2
    finally:
        store.close()


# ── 4. 首条消息自动识别（路径 U）────────────────────────────────────
def test_events_auto_bind_by_union_id() -> None:
    """未绑定用户发消息：union_id 反查通讯录 → 名册比对 → 自动绑定（union_id 形态）。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        fake = FakeContacts(union_to_user={"union_X": _user("union_X", P_A, NAME_A)})
        sent: list[tuple[str, str]] = []

        code, body = handle_inbound_text(
            state=store, user_id="im_user_X", msg_text="你好",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
            user_union_id="union_X", contacts=fake,
        )
        assert code == 200 and body.get("path") == "U" and body.get("bound") is True, body
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "union_X" and row["external_type"] == "union_id", row
        assert any("绑定成功" in t for t, _ in sent), sent

        # 第二条消息：已绑定 → 走 ack（不再重复绑定）
        sent.clear()
        code2, body2 = handle_inbound_text(
            state=store, user_id="im_user_X", msg_text="再发一句",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
            user_union_id="union_X", contacts=fake,
        )
        assert code2 == 200 and body2.get("bound") is True and body2.get("path") != "U", body2
        assert any("消息已收到" in t for t, _ in sent), sent
    finally:
        store.close()


def test_events_union_id_not_in_roster_falls_back_to_link() -> None:
    """union_id 反查到的人不在名册 → 仍然只回登记链接（白名单语义不变）。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        fake = FakeContacts(union_to_user={"union_Y": _user("union_Y", P_C, "路人")})
        sent: list[tuple[str, str]] = []
        code, body = handle_inbound_text(
            state=store, user_id="im_user_Y", msg_text="你好",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
            user_union_id="union_Y", contacts=fake,
        )
        assert code == 200 and body.get("bound") is False, body
        assert body.get("bind_url", "").startswith("http://e.com/h5/bind"), body
        assert store.get_delivery_target(P_C, platform="zdxlz") is None
    finally:
        store.close()


def test_events_without_contacts_keeps_register_prompt() -> None:
    """旧环境（无 contacts）：未绑定用户照旧回登记链接。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        sent: list[tuple[str, str]] = []
        code, body = handle_inbound_text(
            state=store, user_id="im_user_Z", msg_text="你好",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
            user_union_id="union_Z",
        )
        assert code == 200 and body.get("bound") is False and body.get("bind_url"), body
    finally:
        store.close()


# ── 5. H5 兜底：查得到通讯录时改写为 union_id 绑定 ──────────────────
def test_h5_bind_prefers_union_id_when_contacts_available() -> None:
    """H5 登记成功后若通讯录可查 → 绑定改写为 union_id（发送不再依赖会话）。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        fake = FakeContacts(phone_to_user={P_A: _user("union_A", P_A, NAME_A)})
        code, body = process_h5_bind(
            state=store,
            data={"phone": P_A, "external_id": "im_from_link", "platform": "zdxlz",
                  "name": NAME_A, "dept": "一部"},
            contacts=fake,
        )
        assert code == 200 and body.get("bound") is True, body
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "union_A" and row["external_type"] == "union_id", row
    finally:
        store.close()


def test_h5_bind_without_contacts_keeps_im_binding() -> None:
    """查不到通讯录（无 contacts / 外部人员）→ 保持 IM userId 绑定，行为同旧版。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        code, body = process_h5_bind(
            state=store,
            data={"phone": P_A, "external_id": "im_from_link", "platform": "zdxlz",
                  "name": NAME_A, "dept": "一部"},
        )
        assert code == 200 and body.get("bound") is True, body
        row = store.get_delivery_target(P_A, platform="zdxlz")
        assert row["external_id"] == "im_from_link" and row["external_type"] == "user", row
    finally:
        store.close()
