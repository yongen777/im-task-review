# -*- coding: utf-8 -*-
"""企微机器人「三步上传 + 原生图片消息」与调度器选路。

2026-11-02 稳定性改造（验收反馈：另一台 Windows 上 6 条用例全部超时）：
原写法用 `for _ in range(200): if loop.is_running(): break` **猜**事件循环有没有起来，
猜不中就 `run_coroutine_threadsafe(...).result(60)` 一直等，表现成「6 条同时超时」。

现在的分工：

1. **协议层用例**：直接 `asyncio.run()` 驱动 async 内部方法，请求体 / 顺序 / 字段兜底
   全在同一个线程里验证，天然没有跨线程时序；
2. **同步外壳用例**：注入两个既定接缝 `_transport_ready` / `_run_sync`
   （2026-11-02 从生产代码抽出来的，纯结构性抽取，⛔ 不含任何操作系统判断），
   让测试**完全不碰线程**；
3. 只保留**一条**真线程集成用例（`test_real_event_loop_thread_smoke`），
   用来证明真实 `run_coroutine_threadsafe` 路径没被接缝改坏。它失败时只红 1 条，
   能直接指认「环境问题」而不是让整个文件看起来全坏了。

"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import threading
import time
from typing import Any, Dict, List, Tuple

from app.channels.wecom_aibot import AibotBridge, WecomAibotNotifier
from app.scheduler import FakeNotifier, SendResult

# 测试里所有同步调用都用这个超时：环境有问题 2 秒内红，绝不挂 60 秒
T = 2.0


class FakeWsManager:
    """记录所有长连接命令；按 cmd 返回脚本化回执。"""

    def __init__(self, fail_chunk_with: str | None = None) -> None:
        self.sent: List[Tuple[str, Dict[str, Any]]] = []
        # fail_chunk_with: 传 "base64_data" 表示「用这个字段名会报错」，用于测字段名兜底
        self.fail_chunk_with = fail_chunk_with

    async def send_reply(self, req_id: str, body: Dict[str, Any], cmd: str = "aibot_respond_msg") -> Dict[str, Any]:
        self.sent.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"errcode": 0, "body": {"upload_id": "UP123"}}
        if cmd == "aibot_upload_media_chunk":
            if self.fail_chunk_with and self.fail_chunk_with in body:
                return {"errcode": 40058, "errmsg": "invalid param"}
            return {"errcode": 0, "errmsg": "ok"}
        if cmd == "aibot_upload_media_finish":
            return {"errcode": 0, "body": {"media_id": "MEDIA_ABC", "type": "image"}}
        return {"errcode": 0, "errmsg": "ok"}


class FakeClientWithWs:
    """带 _ws_manager 的客户端替身（真实 SDK 的 WSClient 就有这个属性）。"""

    def __init__(self, ws: FakeWsManager, reply: Dict[str, Any] | None = None) -> None:
        self._ws_manager = ws
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.reply = reply if reply is not None else {"errcode": 0, "errmsg": "ok"}

    def on(self, name: str, fn: Any) -> None:  # 供 start() 注册事件
        pass

    def run(self) -> None:
        pass

    async def send_message(self, chatid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((chatid, body))
        return self.reply


class ScriptedClient(FakeClientWithWs):
    """send_message 按脚本依次返回，用来模拟「缓存 media_id 失效」。"""

    def __init__(self, ws: FakeWsManager, replies: List[Dict[str, Any]]) -> None:
        super().__init__(ws)
        self._replies = list(replies)

    async def send_message(self, chatid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((chatid, body))
        return self._replies.pop(0) if self._replies else {"errcode": 0, "errmsg": "ok"}


class _LoopThread:
    """确定性地起一个事件循环线程。

    关键：`call_soon(set)` 在 `run_forever()` **之前**注册，循环一跑必然先回调它，
    所以 `started.wait()` 是「确实在跑」的证据，不是 sleep 猜出来的。
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self.loop.call_soon(self._started.set)
        self._thread = threading.Thread(target=self._run, name="test-aibot-loop", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def __enter__(self) -> asyncio.AbstractEventLoop:
        self._thread.start()
        if not self._started.wait(5):
            raise AssertionError("测试事件循环未能启动（线程池或 loop 策略异常）")
        return self.loop

    def __exit__(self, *exc: Any) -> bool:
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(5)
        finally:
            if not self.loop.is_running():
                self.loop.close()
        return False


def _ready_bridge(client: Any, loop: asyncio.AbstractEventLoop) -> AibotBridge:
    bridge = AibotBridge("bot_x", "secret_x", client=client)
    bridge._loop = loop          # noqa: SLF001 - 测试直接注入
    bridge._ready.set()
    return bridge


def _seam_bridge(client: Any) -> AibotBridge:
    """注入接缝的 bridge：不起线程、不依赖事件循环时序。

    `_run_sync` 用 `asyncio.run` 在当前线程跑完协程 —— 与生产行为等价
    （生产是投递到后台 loop 再等结果），但它不依赖任何线程调度。
    """
    bridge = AibotBridge("bot_x", "secret_x", client=client)
    bridge._transport_ready = lambda: True   # noqa: SLF001 - 注入接缝
    bridge._run_sync = lambda coro, timeout: asyncio.run(coro)  # noqa: SLF001
    return bridge


def _write_png(tmp_path, size: int = 1024) -> str:
    """写一个真假无所谓的 png（协议只验字节数，不解码图像）。"""
    p = tmp_path / "probe.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * size)
    return str(p)


def _inits(ws: FakeWsManager) -> int:
    return len([c for c, _ in ws.sent if c == "aibot_upload_media_init"])


# ── 1. 协议层：不碰线程，直接 asyncio.run ──────────────────────────
def test_upload_image_three_steps_bodies(tmp_path) -> None:
    """三步顺序与请求体：init 带 md5/大小/片数，chunk 512KB 切片 base64，finish 只带 upload_id。"""
    ws = FakeWsManager()
    bridge = AibotBridge("bot_x", "secret_x", client=FakeClientWithWs(ws))
    path = _write_png(tmp_path, 700 * 1024)  # 686KB → 2 片

    with open(path, "rb") as fh:
        media_id, err = asyncio.run(bridge._upload_image_async(fh.read(), "probe.png"))  # noqa: SLF001

    assert (media_id, err) == ("MEDIA_ABC", "")
    assert [c for c, _ in ws.sent] == [
        "aibot_upload_media_init",
        "aibot_upload_media_chunk",
        "aibot_upload_media_chunk",
        "aibot_upload_media_finish",
    ]

    init_body = ws.sent[0][1]
    assert init_body["type"] == "image"
    assert init_body["total_size"] == 700 * 1024 + 8
    assert init_body["total_chunks"] == 2
    with open(path, "rb") as fh:
        assert init_body["md5"] == hashlib.md5(fh.read()).hexdigest()
    assert init_body["filename"] == "probe.png"

    chunk0 = ws.sent[1][1]
    assert chunk0["upload_id"] == "UP123" and chunk0["chunk_index"] == 0
    assert len(base64.b64decode(chunk0["base64_data"])) == 512 * 1024
    assert ws.sent[2][1]["chunk_index"] == 1
    assert ws.sent[3][1] == {"upload_id": "UP123"}


def test_upload_chunk_falls_back_to_data_field() -> None:
    """分片字段名有分歧（base64_data / data）：第一个不认时要自动换第二个再发。"""
    ws = FakeWsManager(fail_chunk_with="base64_data")
    bridge = AibotBridge("bot_x", "secret_x", client=FakeClientWithWs(ws))

    media_id, err = asyncio.run(bridge._upload_image_async(b"\x89PNG" + b"\x00" * 100, "p.png"))  # noqa: SLF001

    assert (media_id, err) == ("MEDIA_ABC", "")
    chunk_calls = [b for c, b in ws.sent if c == "aibot_upload_media_chunk"]
    assert len(chunk_calls) == 2
    assert "base64_data" in chunk_calls[0]
    assert "data" in chunk_calls[1]


def test_upload_init_failure_returns_error() -> None:
    class BrokenWs(FakeWsManager):
        async def send_reply(self, req_id, body, cmd="aibot_respond_msg"):
            self.sent.append((cmd, body))
            if cmd == "aibot_upload_media_init":
                return {"errcode": 40014, "errmsg": "invalid access_token"}
            return {"errcode": 0}

    bridge = AibotBridge("bot_x", "secret_x", client=FakeClientWithWs(BrokenWs()))
    media_id, err = asyncio.run(bridge._upload_image_async(b"\x89PNG", "p.png"))  # noqa: SLF001

    assert media_id is None
    assert "INIT_FAILED" in err and "40014" in err


# ── 2. 同步外壳：用确定性的 loop（Event 确认 + 必定回收）────────────
def test_send_image_posts_image_message(tmp_path) -> None:
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    res = _seam_bridge(client).send_image("zhangsan", _write_png(tmp_path), timeout=T)

    assert res.ok is True, res
    # chatid 传的是 userid，消息体是 msgtype=image + media_id
    assert client.calls == [("zhangsan", {"msgtype": "image", "image": {"media_id": "MEDIA_ABC"}})]


def test_send_image_upload_failure_is_retryable(tmp_path) -> None:
    class BrokenWs(FakeWsManager):
        async def send_reply(self, req_id, body, cmd="aibot_respond_msg"):
            self.sent.append((cmd, body))
            if cmd == "aibot_upload_media_init":
                return {"errcode": 40014, "errmsg": "invalid access_token"}
            return {"errcode": 0}

    client = FakeClientWithWs(BrokenWs())
    res = _seam_bridge(client).send_image("zhangsan", _write_png(tmp_path), timeout=T)

    assert res.ok is False
    assert res.retryable is True           # 交给下一轮重试，不许判死
    assert client.calls == []              # 上传没成功就不许发消息


def test_send_image_before_connected_is_retryable(tmp_path) -> None:
    bridge = AibotBridge("bot_x", "secret_x", client=FakeClientWithWs(FakeWsManager()))
    res = bridge.send_image("zhangsan", _write_png(tmp_path), timeout=T)
    assert (res.ok, res.retryable, res.error) == (False, True, "NOT_CONNECTED")


def test_notifier_exposes_send_image() -> None:
    """调度器靠「通道有没有 send_image」决定发图片还是发链接，名字不能改。"""
    class Cfg:
        wecom_bot_id = ""
        wecom_bot_secret = ""

    assert callable(getattr(WecomAibotNotifier(Cfg()), "send_image", None))


# ── 3. media_id 缓存：同图只传一次，别踩 30 次/分钟 ────────────────
def test_same_image_uploaded_once_for_many_recipients(tmp_path) -> None:
    """一个模板 20 个收件人：上传只能 1 次，消息发 20 条。

    官方上传限流 30 次/分钟；按人各传一次会在两个模板同时到点时直接打满。
    """
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    path = _write_png(tmp_path)

    bridge = _seam_bridge(client)
    for i in range(20):
        res = bridge.send_image(f"user_{i}", path, timeout=T)
        assert res.ok is True, (i, res)

    assert _inits(ws) == 1, _inits(ws)
    assert len(client.calls) == 20
    assert all(b["image"]["media_id"] == "MEDIA_ABC" for _, b in client.calls)


def test_changed_image_triggers_reupload(tmp_path) -> None:
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    path = _write_png(tmp_path, 1024)

    bridge = _seam_bridge(client)
    bridge.send_image("u1", path, timeout=T)
    # 同名文件内容变了（mtime/大小任一变化都必须重传，否则会发旧图）
    with open(path, "ab") as fh:
        fh.write(b"\x00" * 4096)
    import os as _os
    later = time.time() + 5
    _os.utime(path, (later, later))
    bridge.send_image("u2", path, timeout=T)

    assert _inits(ws) == 2, _inits(ws)


def test_stale_cached_media_id_is_reuploaded_once(tmp_path) -> None:
    """缓存里的 media_id 被服务端判失效（40007）时，丢缓存重传一次，而不是直接失败。"""
    ws = FakeWsManager()
    client = ScriptedClient(ws, [
        {"errcode": 0, "errmsg": "ok"},                      # 首次真上传，缓存写入
        {"errcode": 40007, "errmsg": "invalid media_id"},    # 第二次命中缓存 → 被判失效
        {"errcode": 0, "errmsg": "ok"},                      # 丢缓存重传后成功
    ])
    path = _write_png(tmp_path)

    bridge = _seam_bridge(client)
    assert bridge.send_image("u1", path, timeout=T).ok is True
    assert _inits(ws) == 1
    res = bridge.send_image("u2", path, timeout=T)

    assert res.ok is True, res
    assert _inits(ws) == 2, _inits(ws)
    assert len(client.calls) == 3


def test_cache_dropped_triggers_reupload(tmp_path) -> None:
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    path = _write_png(tmp_path)

    bridge = _seam_bridge(client)
    bridge.send_image("u1", path, timeout=T)
    assert _inits(ws) == 1
    bridge._cache_drop(path)  # noqa: SLF001 - 等价于 TTL 到期
    bridge.send_image("u2", path, timeout=T)

    assert _inits(ws) == 2, _inits(ws)


# ── 4. 调度器：图片优先、只用一条、失败降级链接 ────────────────────
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
    """通道支持发图时：三轮 tick 只发 1 条图片消息，且不再补发 H5 链接。"""
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
        assert not [m for m in notifier.sent_messages if "/h5/img/" in m.text]

        kinds = [r["kind"] for r in store.list_reminder_logs(tpl_id)]
        assert kinds.count("notice_img") == 1, kinds
        assert "notice_img_link" not in kinds
    finally:
        store.close()


def test_notice_image_falls_back_to_link_when_permanent_failure() -> None:
    """图片发送**永久失败**（不可重试）要降级发链接，不能让用户什么都收不到。"""
    from tests.test_stage_3_chuncu import PKEY, _mk_scheduler, _mk_store, _setup_notice_tpl

    store = _mk_store()
    try:
        tpl_id = _setup_notice_tpl(store, attach_image=True)
        store.upsert_delivery_target("13800000001", "ext_user_001")
        store.upsert_data_snapshot(tpl_id, PKEY, {"completion_rate": "86.5", "intent_count": "45"})

        notifier = ImageCapableNotifier(fail_with="40001: invalid media", retryable=False)
        _mk_scheduler(store, notifier=notifier).run_once()

        assert len(notifier.image_calls) == 1
        links = [m for m in notifier.sent_messages if "/h5/img/" in m.text]
        assert len(links) == 1, [m.text[:30] for m in notifier.sent_messages]
    finally:
        store.close()


def test_notice_image_retryable_failure_does_not_send_link_immediately() -> None:
    """可重试失败（长连接抖动）**不要**立刻补链接 —— 否则下一轮重试成功＝收两条。"""
    from tests.test_stage_3_chuncu import PKEY, _mk_scheduler, _mk_store, _setup_notice_tpl

    store = _mk_store()
    try:
        tpl_id = _setup_notice_tpl(store, attach_image=True)
        store.upsert_delivery_target("13800000001", "ext_user_001")
        store.upsert_data_snapshot(tpl_id, PKEY, {"completion_rate": "86.5", "intent_count": "45"})

        notifier = ImageCapableNotifier(fail_with="NOT_CONNECTED", retryable=True)
        _mk_scheduler(store, notifier=notifier).run_once()

        assert not [m for m in notifier.sent_messages if "/h5/img/" in m.text]
    finally:
        store.close()


# ── 5. 唯一保留的真线程集成用例 ────────────────────────────────────
def test_real_event_loop_thread_smoke(tmp_path) -> None:
    """真实路径冒烟：后台事件循环 + run_coroutine_threadsafe。

    其余用例都用注入接缝，这条专门守住「真实跨线程链路本身没坏」。
    它红了只红 1 条，能直接指认是环境（loop/线程）问题，而不是让 6 条一起超时。
    """
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)

    with _LoopThread() as loop:
        bridge = _ready_bridge(client, loop)
        res = bridge.send_image("zhangsan", _write_png(tmp_path), timeout=T)

    assert res.ok is True, res
    assert ws.sent[0][0] == "aibot_upload_media_init"
    assert client.calls and client.calls[0][1]["msgtype"] == "image"
