# -*- coding: utf-8 -*-
"""2026-11-10 方案A专项用例：union_id 直连激活（导入即激活）。

口径（用户 2026-11-10 拍板，基于当日实测）：
1. 通讯录响应可用官方 decryptV2 算法在本地解密（不依赖 KMS/密码设备）；
2. 导入成员 / 单个加成员时，自动查通讯录 phone→union_id 并写绑定
   （external_type='union_id'），zdxlz 平台专属；contacts 不可用时静默回退旧行为；
3. 发送时按绑定的 external_type 传 receive_type（union_id 行不再依赖 IM userId/会话）；
4. **私聊零激活（2026-11-11 任务一收口）**：未绑定用户发消息 → 统一提醒「尚未录入业务权限」
   + 零落库；唯一激活入口 = 管理端导入/选人；已绑定用户的补交/ack 不受影响；
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
    """resolver 隔离：企微角色导入**不得误用 union_id 解析器**（store.contacts）。

    2026-11-10 企微镜像后：wecom 走独立的 store.wecom_contacts（见
    test_20261110_wecom_import_activation.py）；本用例守住「跨平台解析器不串用」。
    """
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


# ── 4. 私聊自激活已废除（2026-11-11 任务一：收归管理权）──────────────
def test_events_unbound_gets_guide_and_zero_writes() -> None:
    """未绑定用户（含带 union_id）发任意文本：统一提醒 + 零落库零绑定。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        sent: list[tuple[str, str]] = []

        code, body = handle_inbound_text(
            state=store, user_id="im_user_X", msg_text="你好",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
            user_union_id="union_X",
        )
        assert code == 200 and body.get("bound") is False, body
        assert "尚未录入您的业务权限" in body["message"], body
        assert "bind_url" not in body, "⛔ 不再发登记链接（激活唯一入口=管理端录入）"
        assert any("尚未录入" in t and tag == "not_registered" for t, tag in sent), sent
        # ⛔ 私聊零落库：不建绑定、不写名册、不落待归纳池
        assert store.get_delivery_target(P_A, platform="zdxlz") is None
        with store.lock:
            pending = store.conn.execute("SELECT COUNT(*) FROM pending_member").fetchone()[0]
        assert pending == 0

        # 在册但未绑定的人发消息 → 同样拦截（激活只能来自管理端导入/选人）
        code2, body2 = handle_inbound_text(
            state=store, user_id="im_user_X2", msg_text=f"{NAME_A} {P_A}",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
        )
        assert code2 == 200 and body2.get("bound") is False, body2
        assert "尚未录入您的业务权限" in body2["message"], body2
        assert store.get_delivery_target(P_A, platform="zdxlz") is None, "⛔ 在册未绑定不得经私聊补绑"
    finally:
        store.close()


def test_events_bound_user_still_gets_resubmit_and_ack() -> None:
    """已绑定用户不受影响：union_id 绑定（管理端导入）消息可正常命中 → 补交/ack 照旧。"""
    store = _store()
    try:
        rid = store.create_role("A组", actor="t", platform="zdxlz")["role_id"]
        store.upsert_delivery_target(P_A, "union_X", platform="zdxlz", external_type="union_id")
        store.add_role_member(rid, P_A, NAME_A, actor="t")
        sent: list[tuple[str, str]] = []
        code, body = handle_inbound_text(
            state=store, user_id="im_user_X", msg_text="再发一句",
            send=lambda text, tag: sent.append((text, tag)),
            platform="zdxlz", base_url="http://e.com",
            user_union_id="union_X",
        )
        assert code == 200 and body.get("bound") is True, body
        assert any("消息已收到" in t for t, _ in sent), sent
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
