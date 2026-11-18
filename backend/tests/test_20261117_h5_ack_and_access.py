from __future__ import annotations

import asyncio
import re
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.h5 import create_h5_router
from app.h5log import H5AccessLogMiddleware
from app.scheduler import FakeNotifier
from test_submit_feedback_dedupe import _instance, _store


def _app(store, notifier=None):
    app = FastAPI()
    app.include_router(create_h5_router(store, store.data_dir / "uploads", notifier=notifier))
    return app


def _token(store, inst):
    return store.issue_h5_token(inst["phone"], inst["template_id"], inst["period_key"], inst["id"])


def test_completed_upload_with_header_is_rejected_without_reading_body():
    store = _store()
    try:
        inst = _instance(store, "zdxlz")
        token = _token(store, inst)
        store.submit_h5_task(raw_token=token, fields={"n": 1})

        async def send_upload():
            async def unread_body():
                raise AssertionError("已完成的任务不应读取附件请求体")
                yield b""

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(store)), base_url="http://test") as client:
                return await client.post("/api/h5/task/upload", content=unread_body(), headers={
                    "Content-Type": "multipart/form-data; boundary=preview",
                    "X-H5-Token": token,
                })

        response = asyncio.run(send_upload())
        assert (response.status_code, response.json()["code"]) == (400, "ALREADY_SUBMITTED")
        assert "已提交完成" in response.json()["message"]
        with TestClient(_app(store)) as client:
            legacy = client.post("/api/h5/upload", data={"token": token},
                                 files={"file": ("photo.png", b"unused", "image/png")})
        assert (legacy.status_code, legacy.json()["code"]) == (400, "ALREADY_SUBMITTED")
    finally:
        store.close()


@contextmanager
def _http_server(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                assert worker.is_alive() and time.monotonic() < deadline, "测试 HTTP 服务未启动"
                time.sleep(0.01)
            with httpx.Client(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=5) as client:
                yield client
        finally:
            server.should_exit = True
            worker.join(timeout=6)
            assert not worker.is_alive()


def test_submit_ack_slow_sender_returns_http_before_send_and_keeps_dedupe(tmp_path):
    class SlowNotifier(FakeNotifier):
        def send_text(self, *args, **kwargs):
            time.sleep(2)
            return super().send_text(*args, **kwargs)

    store = _store()
    notifier = SlowNotifier()
    mw = H5AccessLogMiddleware(_app(store, notifier), log_dir=tmp_path, slow_ms=500)
    try:
        inst = _instance(store, "zdxlz")
        token = _token(store, inst)
        path = "/api/h5/submit"
        payload = {"token": token, "fields": {"n": 1}}
        with _http_server(mw) as client:
            assert client.post("/api/h5/task/info", json={"token": token}).status_code == 200
            started = time.perf_counter()
            response = client.post(path, json=payload)
            duration = time.perf_counter() - started
            assert response.status_code == 200, response.text
            assert duration < 0.2, f"HTTP 200 耗时 {duration * 1000:.1f}ms"
            assert store.get_h5_token(token)["consumed"]
            assert len(store.list_submissions_for_instance(inst["id"])) == 1
            assert not notifier.sent_messages
            for _ in range(3):
                duplicate = client.post(path, json=payload)
                assert (duplicate.status_code, duplicate.json()["code"]) == (400, "ALREADY_SUBMITTED")
            deadline = time.monotonic() + 5
            while not (logs := store.list_reminder_logs(instance_id=inst["id"])):
                assert time.monotonic() < deadline, "后台回执未记录"
                time.sleep(0.025)
            assert len(notifier.sent_messages) == len(logs) == 1
            assert (logs[0]["kind"], logs[0]["result"]) == ("submit_ack", "sent")
            print(f"HTTP 200 {duration * 1000:.1f}ms；2s 下游回执 1 条；重复请求 3 次均拒绝")
        lines = next(tmp_path.glob("h5_access-*.log")).read_text(encoding="utf-8").splitlines()
        success = next(line for line in lines if f"POST {path} | 200 |" in line)
        assert "SLOW" not in success and "inst=" in success, success
    finally:
        mw.close()
        store.close()


def test_upload_rejection_logs_metadata_and_same_instance_without_favicon(tmp_path, monkeypatch):
    store = _store()
    mw = H5AccessLogMiddleware(_app(store), log_dir=tmp_path)
    try:
        inst = _instance(store, "zdxlz")
        store.update_template(inst["template_id"], {"spec": {"upload_modules": ["fields", "image"]}})
        monkeypatch.setattr("app.api.h5.task_upload_max_mb", lambda template: 1)
        token = _token(store, inst)
        with TestClient(mw) as client:
            assert client.post("/api/h5/task/info", json={"token": token}).status_code == 200
            upload = client.post("/api/h5/upload", data={"token": token},
                                 files={"file": ("private-photo.heic", b"x" * 1536, "image/heic")})
            assert (upload.status_code, upload.json()["code"]) == (400, "HEIC_NOT_SUPPORTED")
            large = client.post("/api/h5/task/upload", data={"token": token},
                                files={"file": ("private-photo.png", b"x" * (1024**2 + 512), "image/png")})
            assert (large.status_code, large.json()["code"]) == (400, "FILE_TOO_LARGE")
            reject = client.post("/api/h5/task/submit", json={"token": token, "fields": {"n": 1}})
            assert reject.status_code == 400
            assert client.get("/favicon.ico").status_code == 404
        lines = next(tmp_path.glob("h5_access-*.log")).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4, lines
        ids = [re.search(r"\binst=([0-9a-f]{16})\b", line).group(1) for line in lines]
        assert len(set(ids)) == 1
        assert "err=HEIC_NOT_SUPPORTED" in lines[1] and "ext=heic" in lines[1] and "kb=1.5" in lines[1]
        assert "err=FILE_TOO_LARGE" in lines[2] and "ext=png" in lines[2] and "kb=1024.5" in lines[2]
        assert "err=VALIDATION_FAILED" in lines[3]
        assert all(value not in "\n".join(lines) for value in (token, inst["phone"], "private-photo"))
        print(lines[1])
    finally:
        mw.close()
        store.close()
