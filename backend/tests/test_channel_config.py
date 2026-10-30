# -*- coding: utf-8 -*-
"""2026-10-30 通道配置向导：加密入库、DB 优先 / env 兜底、掩码、连通性自检。

红线对照（用户指令）：
1. 凭证必须 AES-GCM 加密入库，库内不得出现明文；
2. 密钥与密文分离（env IMT_ENC_KEY 或 data/config_key.hex）；
3. DB 空表时全量用例仍绿（现网零行为变化）—— 本文件不碰 data/im_task.db，全部临时库；
4. 接口永远只回掩码。

约定：所有用例自建临时目录 + 临时库，跑完即弃。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router
from app.channel_config import (
    BLOB_PREFIX,
    KEY_FILENAME,
    ChannelConfigStore,
    apply_db_channel_config,
    build_callback_url,
    channel_configured,
    decrypt_json,
    encrypt_json,
    load_or_create_key,
    mask_value,
    masked_view,
    validate_payload,
)
from app.config import Config
from app.state import StateStore

SECRET = "SuperSecretValue_9f2c1d"  # 明文特征串，用于断言"库里查不到"


# ── 夹具 ────────────────────────────────────────────────────────────
def _mk_store(tmp: Path) -> StateStore:
    return StateStore(tmp / "im_task.db")


def _mk_config(tmp: Path, **kw: Any) -> Config:
    return Config(
        public_port=21001,
        admin_port=21002,
        public_base_url="https://task.example.com",
        data_dir=tmp,
        **kw,
    )


def _cc(store: StateStore, tmp: Path) -> ChannelConfigStore:
    return ChannelConfigStore(store.conn, store.lock, tmp)


def _ciphertext(store: StateStore, channel: str) -> str:
    with store.lock:
        row = store.conn.execute(
            "SELECT payload FROM channel_config WHERE channel = ?", (channel,)
        ).fetchone()
    assert row is not None, f"{channel} 没有落库记录"
    return str(row["payload"])


# ── 1. 加密 roundtrip + 库内无明文 ──────────────────────────────────
def test_encrypt_roundtrip_and_no_plaintext_in_db() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        cc = _cc(store, tmp)
        merged = cc.save(
            "zdxlz",
            {"app_id": "appid_143e4c0b", "app_secret": SECRET, "verify_token": "vt_001"},
            now_str=store.now_str(),
        )
        assert merged["app_secret"] == SECRET

        # 回读解密一致
        back = cc.read("zdxlz")
        assert back is not None
        assert back["app_secret"] == SECRET and back["app_id"] == "appid_143e4c0b"

        # 库里只有密文
        blob = _ciphertext(store, "zdxlz")
        assert blob.startswith(BLOB_PREFIX)
        assert SECRET not in blob
        assert "appid_143e4c0b" not in blob

        # 密钥文件单独落盘，且是 64 位 hex（32 字节）
        key_file = tmp / KEY_FILENAME
        assert key_file.is_file()
        assert len(key_file.read_text(encoding="utf-8").strip()) == 64
    finally:
        store.close()


def test_wrong_key_cannot_decrypt() -> None:
    """密钥换了（或换成别的机器的）→ 解不开，read 返回 None，调用方自动退回 env。"""
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        cc = _cc(store, tmp)
        cc.save("zdxlz", {"app_id": "a1", "app_secret": SECRET}, now_str=store.now_str())
        assert cc.read("zdxlz") is not None

        other = ChannelConfigStore(store.conn, store.lock, tmp)
        other._key = b"\x00" * 32  # 模拟换机/换密钥
        assert other.read("zdxlz") is None
    finally:
        store.close()


def test_key_can_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = "ab" * 32  # 64 位 hex
    monkeypatch.setenv("IMT_ENC_KEY", fixed)
    key = load_or_create_key(tmp_path)
    assert key.hex() == fixed
    # env 给了密钥时不应再生成密钥文件
    assert not (tmp_path / KEY_FILENAME).exists()
    # 用同一把密钥能解开（证明存/取走的是 env 密钥）
    assert decrypt_json(key, encrypt_json(key, {"x": "1"}, aad="wecom"), aad="wecom") == {"x": "1"}


# ── 2. DB 优先 / env 兜底 ──────────────────────────────────────────
def test_empty_db_keeps_env_config_untouched(tmp_path: Path) -> None:
    """现网口径：DB 空表 → Config 原样返回（不是复制、不是改字段）。"""
    cfg = _mk_config(tmp_path, app_id="ENV_APP", app_secret="ENV_SECRET")
    store = _mk_store(tmp_path)
    try:
        got = apply_db_channel_config(cfg, store)
        assert got.app_id == "ENV_APP"
        assert got.app_secret == "ENV_SECRET"
        assert got is cfg  # 同一对象，说明确实"一个字都没动"
    finally:
        store.close()


def test_db_overrides_env(tmp_path: Path) -> None:
    cfg = _mk_config(tmp_path, app_id="ENV_APP", app_secret="ENV_SECRET", wecom_corpid="ENV_CORP")
    store = _mk_store(tmp_path)
    try:
        _cc(store, tmp_path).save(
            "wecom",
            {"corpid": "DB_CORP", "agentid": "1000002", "secret": "DB_SECRET"},
            now_str=store.now_str(),
        )
        got = apply_db_channel_config(cfg, store)
        assert got.wecom_corpid == "DB_CORP"
        assert got.wecom_secret == "DB_SECRET"
        # 库里没配的通道不受影响
        assert got.app_id == "ENV_APP"
    finally:
        store.close()


def test_db_active_channel_used_only_when_env_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMT_CHANNEL", raising=False)
    cfg = _mk_config(tmp_path)
    store = _mk_store(tmp_path)
    try:
        cc = _cc(store, tmp_path)
        cc.save("wecom", {"corpid": "c", "agentid": "1000002", "secret": SECRET}, now_str=store.now_str())
        cc.save_active_channel("wecom", now_str=store.now_str())
        assert apply_db_channel_config(cfg, store).channel == "wecom"

        # env 显式指定就尊重 env（运维仍可控）
        monkeypatch.setenv("IMT_CHANNEL", "zdxlz")
        assert apply_db_channel_config(cfg, store).channel == "zdxlz"
    finally:
        store.close()


# ── 3. 掩码 / 校验 / 回调地址 ─────────────────────────────────────
def test_mask_value_never_leaks_full_secret() -> None:
    assert mask_value("143e4c0b1234abcd5678e8bb") == "143e****e8bb"
    assert mask_value("short") == "****"
    assert mask_value("") == ""
    view = masked_view("zdxlz", {"app_id": "appid_1234567890", "app_secret": SECRET})
    assert SECRET not in str(view)
    assert view["app_secret"].startswith("Supe") and view["app_secret"].endswith("2c1d")


def test_validate_payload_readable_errors() -> None:
    assert validate_payload("wecom", {"corpid": "c", "agentid": "1000002", "secret": "s"}) is None
    assert "缺少必填项" in (validate_payload("wecom", {"corpid": "c"}) or "")
    err = validate_payload("wecom", {"corpid": "c", "agentid": "abc", "secret": "s"}) or ""
    assert "agentid 必须是整数" in err
    err2 = validate_payload("wecom", {"corpid": "c", "agentid": "1", "secret": "s", "aes_key": "tooShort"}) or ""
    assert "43 位" in err2
    assert "不支持" in (validate_payload("nope", {}) or "")
    # channel_configured 与必填口径一致
    assert channel_configured("zdxlz", {"app_id": "a", "app_secret": "b"}) is True
    assert channel_configured("zdxlz", {"app_id": "a"}) is False


def test_callback_url_built_from_public_base() -> None:
    assert build_callback_url("https://task.example.com", "zdxlz") == "https://task.example.com/api/app-events"
    assert build_callback_url("https://task.example.com/", "wecom") == "https://task.example.com/api/wecom/callback"
    assert build_callback_url("", "wecom") == ""


# ── 4. 接口层 ──────────────────────────────────────────────────────
def _client(store: StateStore, public_base: str = "https://task.example.com") -> TestClient:
    app = FastAPI()
    app.include_router(
        create_admin_router(store, notifier=None, public_base_url=public_base)
    )
    return TestClient(app)


def _login(store: StateStore) -> Dict[str, Any]:
    uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
    return store.create_admin_session(uid)


def test_get_channel_config_reports_env_source_and_masks() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        sess = _login(store)
        app = FastAPI()
        app.include_router(create_admin_router(store, notifier=None, public_base_url="https://task.example.com"))
        # 模拟现网：凭证在 env（DB 空表）
        app.state.config = SimpleNamespace(
            app_id="appid_143e4c0b7788", app_secret=SECRET, verify_token="vt", encrypt_key="ek",
            api_base="https://imtwo.zdxlz.com", channel="zdxlz",
            wecom_corpid="", wecom_agentid="", wecom_secret="", wecom_token="", wecom_aes_key="",
        )
        client = TestClient(app)
        r = client.get("/api/admin/channel-config", cookies={"sid": sess["sid"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["any_configured"] is True
        zdx = next(c for c in body["channels"] if c["channel"] == "zdxlz")
        assert zdx["source"] == "env" and zdx["configured"] is True
        assert zdx["callback_url"] == "https://task.example.com/api/app-events"
        # ⛔ 响应里绝不能出现完整 secret
        assert SECRET not in r.text
        wecom = next(c for c in body["channels"] if c["channel"] == "wecom")
        assert wecom["configured"] is False and wecom["source"] == "none"
        assert wecom["callback_url"] == "https://task.example.com/api/wecom/callback"
        assert body["enc_key_source"] in ("env", "file")
    finally:
        store.close()


def test_get_requires_session() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        client = _client(store)
        assert client.get("/api/admin/channel-config").status_code == 401
    finally:
        store.close()


def test_save_requires_csrf_and_encrypts() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        sess = _login(store)
        client = _client(store)
        payload = {"channel": "zdxlz", "payload": {"app_id": "appid_143e4c0b", "app_secret": SECRET}}

        # 缺 CSRF
        r0 = client.post("/api/admin/channel-config", json=payload, cookies={"sid": sess["sid"]})
        assert (r0.status_code, r0.json().get("code")) == (403, "CSRF_INVALID")

        r1 = client.post(
            "/api/admin/channel-config", json=payload,
            cookies={"sid": sess["sid"]}, headers={"X-CSRF-Token": sess["csrf"]},
        )
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["restart_required"] is True
        assert SECRET not in r1.text
        assert body["masked"]["app_secret"].startswith("Supe")

        # 落库是密文
        blob = _ciphertext(store, "zdxlz")
        assert SECRET not in blob and blob.startswith(BLOB_PREFIX)

        # GET 现在显示 db 来源
        r2 = client.get("/api/admin/channel-config", cookies={"sid": sess["sid"]})
        zdx = next(c for c in r2.json()["channels"] if c["channel"] == "zdxlz")
        assert zdx["source"] == "db" and zdx["configured"] is True
        assert SECRET not in r2.text
    finally:
        store.close()


def test_save_rejects_missing_required_with_readable_message() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        sess = _login(store)
        client = _client(store)
        r = client.post(
            "/api/admin/channel-config",
            json={"channel": "wecom", "payload": {"corpid": "corp_only"}},
            cookies={"sid": sess["sid"]}, headers={"X-CSRF-Token": sess["csrf"]},
        )
        assert r.status_code == 400
        assert "缺少必填项" in r.json()["message"]
    finally:
        store.close()


def test_second_save_keeps_untouched_fields() -> None:
    """只改 app_id 不重填 secret：secret 必须保留（否则编辑一次凭证就废了）。"""
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        cc = _cc(store, tmp)
        cc.save("zdxlz", {"app_id": "old_id", "app_secret": SECRET}, now_str=store.now_str())
        cc.save("zdxlz", {"app_id": "new_id"}, now_str=store.now_str())
        back = cc.read("zdxlz") or {}
        assert back["app_id"] == "new_id"
        assert back["app_secret"] == SECRET
    finally:
        store.close()


# ── 5. 连通性自检（临时凭证不入库） ────────────────────────────────
def test_probe_ok_does_not_touch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import admin as admin_mod

    class _FakeOk:
        def __init__(self, cfg):  # noqa: ANN001 - 模拟 ZdxlzChannel(config)
            self.cfg = cfg

        def get_access_token(self, force_refresh: bool = False) -> str:  # noqa: ARG002
            return "token_abcdefgh12345"

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.channels.zdxlz.ZdxlzChannel", _FakeOk)
    ok, detail = admin_mod._probe_channel("zdxlz", {"app_id": "a", "app_secret": "b"})
    assert ok is True
    assert "连接成功" in detail and "token_" not in detail  # 只回掩码


def test_probe_bad_credential_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import admin as admin_mod
    from app.channels.zdxlz import ChannelError

    class _Fake401:
        def __init__(self, cfg):  # noqa: ANN001
            pass

        def get_access_token(self, force_refresh: bool = False) -> str:  # noqa: ARG002
            raise ChannelError("获取 token 失败: HTTP 401 unauthorized")

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.channels.zdxlz.ZdxlzChannel", _Fake401)
    ok, detail = admin_mod._probe_channel("zdxlz", {"app_id": "a", "app_secret": "bad"})
    assert ok is False
    assert "AppID 或 AppSecret 无效" in detail


def test_probe_wecom_errcode_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import admin as admin_mod
    from app.channels.wecom import WecomError

    class _FakeWecom:
        def __init__(self, corpid, agentid, secret):  # noqa: ANN001
            pass

        def get_token(self, force: bool = False) -> str:  # noqa: ARG002
            raise WecomError(40001, "invalid credential")

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.channels.wecom.WecomClient", _FakeWecom)
    ok, detail = admin_mod._probe_channel(
        "wecom", {"corpid": "c", "agentid": "1000002", "secret": "bad"}
    )
    assert ok is False
    assert "40001" in detail and "Secret 无效" in detail


def test_test_endpoint_returns_detail_and_never_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels.wecom import WecomError

    class _Boom:
        def __init__(self, corpid, agentid, secret):  # noqa: ANN001
            pass

        def get_token(self, force: bool = False) -> str:  # noqa: ARG002
            raise WecomError(60020, "not allow to access from your ip")

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.channels.wecom.WecomClient", _Boom)
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        sess = _login(store)
        client = _client(store)
        r = client.post(
            "/api/admin/channel-config/test",
            json={"channel": "wecom", "payload": {"corpid": "c", "agentid": "1000002", "secret": SECRET}},
            cookies={"sid": sess["sid"]}, headers={"X-CSRF-Token": sess["csrf"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert "白名单" in body["detail"]  # 60020 → 可信 IP 提示
        # ⛔ 测试不写库
        with store.lock:
            n = store.conn.execute("SELECT COUNT(1) AS n FROM channel_config").fetchone()["n"]
        assert int(n) == 0
    finally:
        store.close()


def test_test_endpoint_rejects_bad_agentid_before_calling_api() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = _mk_store(tmp)
    try:
        sess = _login(store)
        client = _client(store)
        r = client.post(
            "/api/admin/channel-config/test",
            json={"channel": "wecom", "payload": {"corpid": "c", "agentid": "not-int", "secret": "s"}},
            cookies={"sid": sess["sid"]}, headers={"X-CSRF-Token": sess["csrf"]},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "agentid 必须是整数" in r.json()["detail"]
    finally:
        store.close()
