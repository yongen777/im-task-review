# -*- coding: utf-8 -*-
"""企业微信自建应用回调（/api/wecom/callback）的验收用例。

覆盖用户要求的四项：
1. GET 验证：构造密文 echostr → 验签 + 解密 → 返回明文
2. POST：构造加密 XML → 解密出 text 事件 → 提取 userid/Content → 走「申请补交」分支
3. 错误密文 / 错 Token → 401/400
4. 未配置 → 503；重复投递（企微 5 秒超时会重试 3 次）只处理一次

加解密走官方 WXBizMsgCrypt（Python3 移植），这里用同一套构造报文，属于「自洽闭环」验证；
与企微服务端的互通性要等 corpid/agentid/secret/Token/AESKey 到位后联调确认。
"""
from __future__ import annotations

import base64
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.wecom_callback import create_wecom_router, parse_wecom_xml, reset_dedupe_cache
from app.channels.wecom_crypto import (
    SHA1,
    WXBizMsgCrypt_ValidateSignature_Error,
    Prpcrypt,
    WXBizMsgCrypt,
)
from app.config import SHANGHAI_TZ, format_dt
from app.state import StateStore

TOKEN = "TESTTOKEN123456"
AES_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode().rstrip("=")  # 43 位
CORPID = "corp_test_x"
PHONE = "13900009301"
USERID = "zhangsan"


def _key() -> bytes:
    return base64.b64decode(AES_KEY + "=")


def _encrypt(plain: str) -> str:
    ret, enc = Prpcrypt(_key()).encrypt(plain.encode("utf-8"), CORPID.encode("utf-8"))
    assert ret == 0 and enc is not None
    return enc.decode("utf-8")


def _sign(ts: str, nonce: str, encrypt: str) -> str:
    ret, sig = SHA1().getSHA1(TOKEN, ts, nonce, encrypt)
    assert ret == 0 and sig
    return sig


def _post_body(encrypt: str) -> str:
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{CORPID}]]></ToUserName>"
        "<AgentID><![CDATA[1000002]]></AgentID>"
        f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        "</xml>"
    )


def _text_plain(content: str, userid: str = USERID, msg_id: str = "1000001") -> str:
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{CORPID}]]></ToUserName>"
        f"<FromUserName><![CDATA[{userid}]]></FromUserName>"
        "<CreateTime>1600000000</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        f"<MsgId>{msg_id}</MsgId>"
        "<AgentID>1000002</AgentID>"
        "</xml>"
    )


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: List[tuple] = []

    def send_text(self, receive_id: str, text: str, idem_key: str):
        self.sent.append((receive_id, text, idem_key))

        class _R:
            ok = True
            error = None

        return _R()


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp()
    return StateStore(Path(tmp) / "im.db")


def _mk_overdue(store: StateStore, *, hours_late: float = 2.0) -> Dict[str, Any]:
    """建一个已逾期实例（用于验证「申请补交」分支）。"""
    rid = store.create_role("执行组", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    store.upsert_delivery_target(PHONE, USERID)  # 企微 userid 作为推送 ID
    tpl = store.create_template(
        {
            "name": "每日上报", "kind": "form", "role_ids": [rid], "text_tpl": "上报今日数据",
            "remind_at": "09:00", "deadline": "18:00", "period": "day", "need_review": True,
            "schema_json": [{"key": "summary", "label": "摘要", "type": "text"}],
        },
        actor="t",
    )
    base = store.now().replace(tzinfo=None, microsecond=0)
    deadline = base - timedelta(hours=hours_late)
    pkey = format_dt(deadline)[:10]
    inst = store.ensure_template_instances(tpl["template_id"], pkey, format_dt(deadline))[0]
    store.advance_instance_statuses(base)
    return store.get_instance(inst["id"])


def _client(store: StateStore, notifier: Any, *, configured: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_wecom_router(
            store, notifier,
            token=TOKEN if configured else "",
            aes_key=AES_KEY if configured else "",
            corpid=CORPID if configured else "",
        )
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_dedupe():
    reset_dedupe_cache()
    yield
    reset_dedupe_cache()


# ── 1. GET URL 验证 ─────────────────────────────────────────────────
def test_verify_url_returns_decrypted_plaintext() -> None:
    store = _mk_store()
    try:
        c = _client(store, FakeNotifier())
        ts, nonce, echo_plain = "1600000000", "nonce123", "1234567890abcdef"
        enc = _encrypt(echo_plain)
        sig = _sign(ts, nonce, enc)
        r = c.get(
            "/api/wecom/callback",
            params={"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": enc},
        )
        assert r.status_code == 200, r.text
        # ⛔ 必须是原样明文：不带引号、不带换行
        assert r.text == echo_plain
    finally:
        store.close()


def test_verify_url_rejects_bad_signature() -> None:
    store = _mk_store()
    try:
        c = _client(store, FakeNotifier())
        enc = _encrypt("hello")
        r = c.get(
            "/api/wecom/callback",
            params={"msg_signature": "deadbeef", "timestamp": "1600000000", "nonce": "n", "echostr": enc},
        )
        assert r.status_code == 401
    finally:
        store.close()


def test_verify_url_503_when_not_configured() -> None:
    store = _mk_store()
    try:
        c = _client(store, FakeNotifier(), configured=False)
        r = c.get("/api/wecom/callback", params={"msg_signature": "x", "timestamp": "1", "nonce": "n", "echostr": "e"})
        assert r.status_code == 503
    finally:
        store.close()


# ── 2. POST 业务回调 ────────────────────────────────────────────────
def test_post_text_message_triggers_resubmit_branch() -> None:
    """企微回调 → 解出 text → 「申请补交」→ 走现有业务分支并回执。"""
    store = _mk_store()
    try:
        inst = _mk_overdue(store)
        notifier = FakeNotifier()
        c = _client(store, notifier)

        ts, nonce = "1600000001", "nonce_abc"
        enc = _encrypt(_text_plain("申请补交", msg_id="2000001"))
        body = _post_body(enc)
        sig = _sign(ts, nonce, enc)

        r = c.post("/api/wecom/callback", params={"msg_signature": sig, "timestamp": ts, "nonce": nonce}, content=body)
        assert r.status_code == 200, r.text
        assert r.text == "success"  # 先应答（5 秒硬约束）

        # 业务在 BackgroundTasks 里同步执行完（TestClient 行为）
        assert store.get_instance(inst["id"])["resubmit_status"] == "pending"
        assert notifier.sent, "应有回执"
        assert "已收到补交申请" in notifier.sent[0][1]
        assert notifier.sent[0][0] == USERID
    finally:
        store.close()


def test_post_unbound_user_goes_to_pending_pool() -> None:
    """未绑定用户发任意文本 → 自动落「已激活待归纳」池（与密信同一套规则）。"""
    store = _mk_store()
    try:
        notifier = FakeNotifier()
        c = _client(store, notifier)
        ts, nonce = "1600000002", "n2"
        enc = _encrypt(_text_plain("你好", userid="lisi", msg_id="3000001"))
        sig = _sign(ts, nonce, enc)
        r = c.post("/api/wecom/callback", params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
                   content=_post_body(enc))
        assert r.status_code == 200
        assert store.get_pending_member("lisi") is not None
        assert notifier.sent and "尚未完成身份绑定" in notifier.sent[0][1]
    finally:
        store.close()


def test_post_duplicate_msgid_handled_once() -> None:
    """企微 5 秒超时会重试 3 次：同一 MsgId 只能处理一次，避免给用户回两遍。"""
    store = _mk_store()
    try:
        _mk_overdue(store)
        notifier = FakeNotifier()
        c = _client(store, notifier)
        ts, nonce = "1600000003", "n3"
        enc = _encrypt(_text_plain("申请补交", msg_id="4000001"))
        sig = _sign(ts, nonce, enc)
        params = {"msg_signature": sig, "timestamp": ts, "nonce": nonce}
        body = _post_body(enc)

        assert c.post("/api/wecom/callback", params=params, content=body).status_code == 200
        assert c.post("/api/wecom/callback", params=params, content=body).status_code == 200
        assert len(notifier.sent) == 1, f"重复投递被回了 {len(notifier.sent)} 次"
    finally:
        store.close()


# ── 3. 错误报文 ────────────────────────────────────────────────────
def test_post_bad_signature_401() -> None:
    store = _mk_store()
    try:
        c = _client(store, FakeNotifier())
        enc = _encrypt(_text_plain("hi"))
        r = c.post("/api/wecom/callback",
                   params={"msg_signature": "bad", "timestamp": "1", "nonce": "n"},
                   content=_post_body(enc))
        assert r.status_code == 401
    finally:
        store.close()


def test_post_malformed_xml_401() -> None:
    store = _mk_store()
    try:
        c = _client(store, FakeNotifier())
        r = c.post("/api/wecom/callback",
                   params={"msg_signature": "bad", "timestamp": "1", "nonce": "n"},
                   content="not-xml-at-all")
        assert r.status_code in (400, 401)
    finally:
        store.close()


def test_post_wrong_corpid_fails_decrypt() -> None:
    """receiveid 用 corpid 校验：拿别的 corpid 加密，解密应失败。"""
    store = _mk_store()
    try:
        c = _client(store, FakeNotifier())
        ret, enc = Prpcrypt(_key()).encrypt(_text_plain("hi").encode(), b"other_corp")
        assert ret == 0
        enc_s = enc.decode()
        ts, nonce = "1600000004", "n4"
        sig = _sign(ts, nonce, enc_s)
        r = c.post("/api/wecom/callback", params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
                   content=_post_body(enc_s))
        assert r.status_code == 401
    finally:
        store.close()


# ── 4. 加解密自洽 ──────────────────────────────────────────────────
def test_crypto_roundtrip_encrypt_then_decrypt() -> None:
    crypt = WXBizMsgCrypt(TOKEN, AES_KEY, CORPID)
    plain = _text_plain("申请补交")
    ret, enc_xml = crypt.EncryptMsg(plain, "nonce_x")
    assert ret == 0 and enc_xml
    from app.channels.wecom_crypto import XMLParse

    ret, enc = XMLParse().extract(enc_xml)
    assert ret == 0 and enc
    ts = "1600000005"
    sig = _sign(ts, "nonce_x", enc)
    ret, out = crypt.DecryptMsg(enc_xml, sig, ts, "nonce_x")
    assert ret == 0
    assert out.decode("utf-8") == plain


def test_crypto_rejects_tampered_ciphertext() -> None:
    crypt = WXBizMsgCrypt(TOKEN, AES_KEY, CORPID)
    ret, enc_xml = crypt.EncryptMsg(_text_plain("hello"), "nonce_y")
    assert ret == 0
    from app.channels.wecom_crypto import XMLParse

    _, enc = XMLParse().extract(enc_xml)
    ts = "1600000006"
    good_sig = _sign(ts, "nonce_y", enc)
    tampered = enc[:-4] + ("AAAA" if not enc.endswith("AAAA") else "BBBB")
    ret, _ = crypt.DecryptMsg(_post_body(tampered), good_sig, ts, "nonce_y")
    assert ret == WXBizMsgCrypt_ValidateSignature_Error


def test_parse_wecom_xml_flattens_fields() -> None:
    fields = parse_wecom_xml(_text_plain("abc", userid="u9", msg_id="777"))
    assert fields["FromUserName"] == "u9"
    assert fields["Content"] == "abc"
    assert fields["MsgType"] == "text"
    assert fields["MsgId"] == "777"
