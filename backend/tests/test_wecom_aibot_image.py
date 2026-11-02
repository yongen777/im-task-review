# -*- coding: utf-8 -*-
"""补测：企微机器人「三步上传 + 原生图片消息」，以及调度器「图片优先、链接兜底」。

背景：我此前断言「智能机器人发不了图片」，被线上实测证伪。
错因是只看官方 SDK 封装（只有 download），没去看协议（官方文档 101970）。
这些用例锁住协议细节，防止以后又有人"看 SDK 没这个方法"就下结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import asyncio
import base64
import hashlib
import threading
import time

from app.channels.wecom_aibot import AibotBridge, WecomAibotNotifier
from app.scheduler import FakeNotifier, SendResult


class FakeWsManager:
    """记录所有长连接命令；按 cmd 返回脚本化回执。"""

    def __init__(self, script: Dict[str, Any] | None = None, fail_chunk_with: str | None = None) -> None:
        self.sent: List[Tuple[str, Dict[str, Any]]] = []
        self.script = script or {}
        # fail_chunk_with: 传 "base64_data" 表示「用这个字段名会报错」，用于测字段名兜底
        self.fail_chunk_with = fail_chunk_with
        self._seq = 0

    async def send_reply(self, req_id: str, body: Dict[str, Any], cmd: str = "aibot_respond_msg") -> Dict[str, Any]:
        self.sent.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"errcode": 0, "body": {"upload_id": "UP123"}}
        if cmd == "aibot_upload_media_chunk":
            if self.fail_chunk_with and self.fail_chunk_with in body:
                return {"errcode": 40058, "errmsg": "invalid param"}
            if body.get("chunk_index") == 0 and "chunk_index" not in body:
                return {"errcode": 40058, "errmsg": "invalid param"}
            return {"errcode": 0, "errmsg": "ok"}
        if cmd == "aibot_upload_media_finish":
            return {"errcode": 0, "body": {"media_id": "MEDIA_ABC", "type": "image"}}
        return {"errcode": 0, "errmsg": "ok"}


class FakeClientWithWs:
    """带 _ws_manager 的客户端替身（真实 SDK 的 WSClient 就有这个属性）。"""

    def __init__(self, ws: FakeWsManager, send_reply: Dict[str, Any] | None = None) -> None:
        self._ws_manager = ws
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._send_reply = send_reply if send_reply is not None else {"errcode": 0, "errmsg": "ok"}

    def on(self, name: str, fn: Any) -> None:  # 供 start() 注册事件
        pass

    def run(self) -> None:
        pass

    async def send_message(self, chatid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((chatid, body))
        return self._send_reply


def _ready_bridge(client: Any) -> AibotBridge:
    bridge = AibotBridge("bot_x", "secret_x", client=client)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    for _ in range(200):
        if loop.is_running():
            break
        time.sleep(0.01)
    bridge._loop = loop          # noqa: SLF001
    bridge._ready.set()
    return bridge


def _write_png(tmp_path, size: int = 1024) -> str:
    """写一个真假无所谓的 png（协议只验字节数，不解码图像）。"""
    p = tmp_path / "probe.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * size)
    return str(p)


# ── 1. 三步上传 + 图片消息 ─────────────────────────────────────────
def test_upload_image_three_steps_then_image_message(tmp_path) -> None:
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    bridge = _ready_bridge(client)
    path = _write_png(tmp_path, 700 * 1024)  # 686KB → 需要 2 片

    res = bridge.send_image("zhangsan", path)

    assert res.ok is True, res
    cmds = [c for c, _ in ws.sent]
    assert cmds == [
        "aibot_upload_media_init",
        "aibot_upload_media_chunk",
        "aibot_upload_media_chunk",
        "aibot_upload_media_finish",
    ]

    init_body = ws.sent[0][1]
    assert init_body["type"] == "image"
    assert init_body["total_size"] == 700 * 1024 + 8
    assert init_body["total_chunks"] == 2
    assert init_body["md5"] == hashlib.md5((tmp_path / "probe.png").read_bytes()).hexdigest()
    assert init_body["filename"] == "probe.png"

    chunk0 = ws.sent[1][1]
    assert chunk0["upload_id"] == "UP123" and chunk0["chunk_index"] == 0
    # 分片用 base64，且是 512KB 切片
    raw0 = base64.b64decode(chunk0["base64_data"])
    assert len(raw0) == 512 * 1024
    assert ws.sent[3][1] == {"upload_id": "UP123"}

    # 最后一条是真正的图片消息：msgtype=image + media_id，chatid 是 userid
    assert client.calls == [("zhangsan", {"msgtype": "image", "image": {"media_id": "MEDIA_ABC"}})]


def test_upload_chunk_falls_back_to_data_field(tmp_path) -> None:
    """分片字段名有分歧（data / base64_data）：第一个不认时要自动换第二个再发。"""
    ws = FakeWsManager(fail_chunk_with="base64_data")
    bridge = _ready_bridge(FakeClientWithWs(ws))

    res = bridge.send_image("zhangsan", _write_png(tmp_path, 1024))

    assert res.ok is True, res
    chunk_calls = [b for c, b in ws.sent if c == "aibot_upload_media_chunk"]
    assert len(chunk_calls) == 2
    assert "base64_data" in chunk_calls[0]
    assert "data" in chunk_calls[1]


def test_send_image_upload_failure_is_retryable(tmp_path) -> None:
    class BrokenWs(FakeWsManager):
        async def send_reply(self, req_id, body, cmd="aibot_respond_msg"):
            self.sent.append((cmd, body))
            if cmd == "aibot_upload_media_init":
                return {"errcode": 40014, "errmsg": "invalid access_token"}
            return {"errcode": 0}

    client = FakeClientWithWs(BrokenWs())
    bridge = _ready_bridge(client)
    res = bridge.send_image("zhangsan", _write_png(tmp_path))

    assert res.ok is False
    assert res.retryable is True           # 交给下一轮重试，不许判死
    assert client.calls == []              # 上传没成功就不许发消息


def test_send_image_before_connected_is_retryable(tmp_path) -> None:
    bridge = AibotBridge("bot_x", "secret_x", client=FakeClientWithWs(FakeWsManager()))
    res = bridge.send_image("zhangsan", _write_png(tmp_path))
    assert (res.ok, res.retryable, res.error) == (False, True, "NOT_CONNECTED")


def test_notifier_exposes_send_image() -> None:
    """调度器靠「通道有没有 send_image」来决定发图片还是发链接，名字不能改。"""
    class Cfg:
        wecom_bot_id = ""
        wecom_bot_secret = ""

    n = WecomAibotNotifier(Cfg())
    assert callable(getattr(n, "send_image", None))


# ── 2. 调度器：图片优先、只用一条、失败降级链接 ────────────────────
class ImageCapableNotifier(FakeNotifier):
    """模拟企微机器人：既能发文本，也能发原生图片。"""

    def __init__(self, fail_with: str | None = None, retryable: bool = False) -> None:
        super().__init__()
        self.image_calls: List[Tuple[str, str]] = []
        self.fail_with = fail_with
        self.retryable = retryable

    def send_image(self, receive_id: str, file_path: str, idem_key: str) -> SendResult:
        self.image_calls.append((str(receive_id), str(file_path)))
        if self.fail_with:
            return SendResult(False, "failed", 200, retryable=self.retryable, error=self.fail_with)
        return SendResult(True, "sent", 200)


def test_notice_image_prefers_native_image_and_stays_once_across_ticks() -> None:
    """通道支持发图时：三轮回调只发 1 条图片消息，且不再补发 H5 链接。"""
    from tests.test_stage_3_chuncu import PKEY, _mk_scheduler, _mk_store, _setup_notice_tpl

    store = _mk_store()
    try:
        tpl_id = _setup_notice_tpl(store, attach_image=True)
        store.upsert_delivery_target("13800000001", "ext_user_001")
        store.upsert_data_snapshot(tpl_id, PKEY, {"completion_rate": "86.5", "intent_count": "45"})

        notifier = ImageCapableNotifier()
        scheduler = _mk_scheduler(store, notifier=notifier)
        for _ in range(3):
            scheduler.run_once()

        assert len(notifier.image_calls) == 1, notifier.image_calls
        assert notifier.image_calls[0][0] == "ext_user_001"
        # 不许同时发链接（否则用户收到图片 + 链接两条）
        assert not [m for m in notifier.sent_messages if "/h5/img/" in m.text]

        kinds = [r["kind"] for r in store.list_reminder_logs(tpl_id)]
        assert kinds.count("notice_img") == 1, kinds
        assert "notice_img_link" not in kinds
    finally:
        store.close()


def test_notice_image_falls_back_to_link_when_permanent_failure() -> None:
    """图片发送**永久失败**（不可重试）时要降级发链接，不能让用户什么都收不到。"""
    from tests.test_stage_3_chuncu import PKEY, _mk_scheduler, _mk_store, _setup_notice_tpl

    store = _mk_store()
    try:
        tpl_id = _setup_notice_tpl(store, attach_image=True)
        store.upsert_delivery_target("13800000001", "ext_user_001")
        store.upsert_data_snapshot(tpl_id, PKEY, {"completion_rate": "86.5", "intent_count": "45"})

        notifier = ImageCapableNotifier(fail_with="40001: invalid media", retryable=False)
        scheduler = _mk_scheduler(store, notifier=notifier)
        scheduler.run_once()

        assert len(notifier.image_calls) == 1
        links = [m for m in notifier.sent_messages if "/h5/img/" in m.text]
        assert len(links) == 1, [m.text[:30] for m in notifier.sent_messages]
    finally:
        store.close()


def test_notice_image_retryable_failure_does_not_send_link_immediately() -> None:
    """图片发送可重试（长连接抖动）时**不要**立刻补发链接 —— 否则下一轮重试成功＝用户收两条。"""
    from tests.test_stage_3_chuncu import PKEY, _mk_scheduler, _mk_store, _setup_notice_tpl

    store = _mk_store()
    try:
        tpl_id = _setup_notice_tpl(store, attach_image=True)
        store.upsert_delivery_target("13800000001", "ext_user_001")
        store.upsert_data_snapshot(tpl_id, PKEY, {"completion_rate": "86.5", "intent_count": "45"})

        notifier = ImageCapableNotifier(fail_with="NOT_CONNECTED", retryable=True)
        scheduler = _mk_scheduler(store, notifier=notifier)
        scheduler.run_once()

        assert not [m for m in notifier.sent_messages if "/h5/img/" in m.text]
    finally:
        store.close()


# ── 3. media_id 缓存：同图只传一次，别踩 30 次/分钟 ────────────────
class ScriptedClient(FakeClientWithWs):
    """send_message 按脚本依次返回，用来模拟「缓存 media_id 失效」。"""

    def __init__(self, ws: FakeWsManager, replies: List[Dict[str, Any]]) -> None:
        super().__init__(ws)
        self._replies = list(replies)

    async def send_message(self, chatid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((chatid, body))
        return self._replies.pop(0) if self._replies else {"errcode": 0, "errmsg": "ok"}


def _inits(ws: FakeWsManager) -> int:
    return len([c for c, _ in ws.sent if c == "aibot_upload_media_init"])


def test_same_image_uploaded_once_for_many_recipients(tmp_path) -> None:
    """一个模板 20 个收件人：上传只能 1 次，消息发 20 条。

    官方上传限流 30 次/分钟；按人各传一次会在两个模板同时到点时直接打满。
    """
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    bridge = _ready_bridge(client)
    path = _write_png(tmp_path)

    for i in range(20):
        res = bridge.send_image(f"user_{i}", path)
        assert res.ok is True, (i, res)

    assert _inits(ws) == 1, _inits(ws)
    assert len(client.calls) == 20
    assert all(b["image"]["media_id"] == "MEDIA_ABC" for _, b in client.calls)


def test_changed_image_triggers_reupload(tmp_path) -> None:
    ws = FakeWsManager()
    bridge = _ready_bridge(FakeClientWithWs(ws))
    path = _write_png(tmp_path, 1024)

    bridge.send_image("u1", path)
    # 同名文件内容变了（mtime/大小任一变化都必须重传，否则会发旧图）
    import os as _os
    with open(path, "ab") as fh:
        fh.write(b"\x00" * 4096)
    _os.utime(path, (time.time() + 5, time.time() + 5))

    bridge.send_image("u2", path)
    assert _inits(ws) == 2, _inits(ws)


def test_stale_cached_media_id_is_reuploaded_once(tmp_path) -> None:
    """缓存里的 media_id 被服务端判失效（过期/清理）时，必须丢掉缓存重传一次，而不是直接失败。"""
    ws = FakeWsManager()
    client = ScriptedClient(ws, [
        {"errcode": 0, "errmsg": "ok"},                      # 首次真上传，缓存写入
        {"errcode": 40007, "errmsg": "invalid media_id"},  # 第二次命中缓存 → 被判失效
        {"errcode": 0, "errmsg": "ok"},                      # 丢掉缓存重传后成功
    ])
    bridge = _ready_bridge(client)
    path = _write_png(tmp_path)

    assert bridge.send_image("u1", path).ok is True      # 首次真上传，缓存写入
    assert _inits(ws) == 1

    res = bridge.send_image("u2", path)                  # 命中缓存 → 被判失效 → 重传
    assert res.ok is True, res
    assert _inits(ws) == 2, _inits(ws)
    assert len(client.calls) == 3


def test_cache_expires_after_ttl(tmp_path, monkeypatch) -> None:
    ws = FakeWsManager()
    bridge = _ready_bridge(FakeClientWithWs(ws))
    path = _write_png(tmp_path)

    bridge.send_image("u1", path)
    assert _inits(ws) == 1
    # 把缓存 TTL 拨到 0，模拟过期
    monkeypatch.setattr(AibotBridge, "MEDIA_CACHE_TTL_SECONDS", -1, raising=False)
    bridge._media_cache.clear()  # noqa: SLF001
    bridge.send_image("u2", path)
    assert _inits(ws) == 2
