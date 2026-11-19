import hashlib
import io
import json
import shutil
import subprocess
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from PIL import Image

from app.channels.zdxlz import SendResult, ZdxlzChannel
from app.scheduler import FakeNotifier
from test_20261117_manual_mark_submitted import NOW, ctx
from test_wecom_aibot_image import FakeClientWithWs, FakeWsManager, _seam_bridge


def quantum_client(handler, monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    channel = ZdxlzChannel(SimpleNamespace(channel_configured=True, api_base="http://fake", app_id="test-app"), client)
    monkeypatch.setattr(channel, "get_access_token", lambda **_: "test-token")
    return channel, client


@pytest.mark.parametrize("hit", [False, True])
def test_quantum_upload_md5_etag_and_instant_hit(tmp_path, monkeypatch, hit):
    source = tmp_path / "picture.png"
    payload = b"\x89PNG\r\n\x1a\n12345678"
    source.write_bytes(payload)
    request_log = []

    def handle(request):
        request_log.append(request)
        if request.url.path.endswith("/init"):
            if hit:
                return httpx.Response(200, json={"success": True, "data": {
                    "deduplicatedHit": True, "uploadComplete": True, "fileKey": "existing",
                }})
            return httpx.Response(200, json={"code": 0, "data": {
                "uploadId": "upload", "chunkSize": 8, "parts": [
                    {"partNumber": 1, "uploadUrl": "http://object/part/1"},
                    {"partNumber": 2, "uploadUrl": "http://object/part/2"},
                ],
            }})
        if request.method == "PUT":
            return httpx.Response(200, headers={"ETag": f'"etag-{request.url.path[-1]}"'})
        return httpx.Response(200, json={"code": 200, "data": {"fileKey": "uploaded"}})

    channel, client = quantum_client(handle, monkeypatch)
    with client:
        assert channel.upload_media(str(source)) == ("existing" if hit else "uploaded")
    digest = hashlib.md5(payload).hexdigest()
    init = json.loads(request_log[0].content)
    assert init["fileHash"] == digest and init["category"] == 1 and init["mimeType"] == "image/png"
    if hit:
        assert len(request_log) == 1
        return
    assert [request.method for request in request_log] == ["POST", "PUT", "PUT", "POST"]
    for offset, request in enumerate(request_log[1:3]):
        assert request.headers["Content-Type"] == "application/octet-stream"
        assert "authorization" not in request.headers
        assert request.content == payload[offset * 8:(offset + 1) * 8]
    assert json.loads(request_log[-1].content) == {"fileHash": digest, "parts": [
        {"partNumber": 1, "etag": '"etag-1"', "size": 8},
        {"partNumber": 2, "etag": '"etag-2"', "size": 8},
    ]}


def test_quantum_native_message_formats(monkeypatch):
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    channel, client = quantum_client(handle, monkeypatch)
    with client:
        assert channel.send_image("uid", "picture", width=22, height=18, mime_type="image/JPEG",
                                  alt_text="图", file_name="old", file_size=123,
                                  receive_type="union_id", idem_key="picture-idem").ok
        assert channel.send_file("uid", "document", file_name="通知.pdf", size=100, mime_type="application/pdf",
                                 ext=".PDF", receive_type="union_id", idem_key="file-idem").ok
        assert channel.send_video("uid", "movie", duration=4, width=64, height=32, mime_type="video/mp4",
                                  ext="mp4", thumbnail="poster", receive_type="union_id", idem_key="video-idem").ok
    assert [body["msg_type"] for body in sent] == ["image", "file", "video"]
    assert all(body["receive_type"] == "union_id" and body["receive_id"] == "uid" for body in sent)
    assert [body["idem_key"] for body in sent] == ["picture-idem", "file-idem", "video-idem"]
    assert [json.loads(body["content"]) for body in sent] == [
        {"fileId": "picture", "width": 22, "height": 18, "mimeType": "jpg", "altText": "图"},
        {"fileId": "document", "fileName": "通知.pdf", "size": 100, "mimeType": "application/pdf", "ext": "pdf"},
        {"fileId": "movie", "duration": 4, "width": 64, "height": 32, "mimeType": "video/mp4", "ext": "mp4",
         "thumbnail": "poster"},
    ]


@pytest.mark.parametrize("kind, filename", [("file", "通知.txt"), ("video", "培训.mp4")])
def test_wecom_native_attachment_uses_media_upload_without_websocket(tmp_path, kind, filename):
    ws = FakeWsManager()
    client = FakeClientWithWs(ws)
    bridge = _seam_bridge(client)
    path = tmp_path / filename
    path.write_bytes(b"example bytes")
    asset = {"kind": kind, "file_name": filename}
    assert bridge.send_notice_attachment("mock-member", str(path), asset).ok
    assert bridge.send_notice_attachment("mock-member-2", str(path), asset).ok
    initial = [body for name, body in ws.sent if name == "aibot_upload_media_init"]
    assert len(initial) == 1 and initial[0]["type"] == kind and initial[0]["filename"] == filename
    content = {"media_id": "MEDIA_ABC"}
    if kind == "video":
        content["title"] = filename
    assert [body for _, body in client.calls] == [{"msgtype": kind, kind: content}] * 2


def test_wecom_native_attachment_without_ack_keeps_pending_claim(tmp_path):
    client = FakeClientWithWs(FakeWsManager(), reply={})
    bridge = _seam_bridge(client)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video payload")
    result = bridge.send_notice_attachment("mock-member", str(path), {"kind": "video", "file_name": path.name})
    assert not result.ok and result.retryable and result.result == "pending"
    assert len(client.calls) == 1


def test_wecom_native_attachment_handles_sdk_rejection(tmp_path):
    class RejectingClient(FakeClientWithWs):
        async def send_message(self, chatid, body):
            raise RuntimeError("Reply ack error: errcode=40009, errmsg=invalid media")

    path = tmp_path / "test.mp4"
    path.write_bytes(b"media bytes")
    bridge = _seam_bridge(RejectingClient(FakeWsManager()))
    result = bridge.send_notice_attachment("mock-member", str(path), {"kind": "video", "file_name": path.name})
    assert not result.ok and not result.retryable and result.error == "40009: invalid media"


@pytest.fixture
def movie(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg 未安装")
    output = tmp_path / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x32:d=1",
                    "-c:v", "libx264", "-movflags", "+faststart", str(output)], check=True, timeout=20)
    return output


def test_quantum_prepares_real_video_and_uploads_poster(movie, monkeypatch):
    uploaded = []

    def upload(path, **kwargs):
        from pathlib import Path

        media = Path(path)
        uploaded.append(media.read_bytes())
        return f"file-{len(uploaded)}"

    channel, client = quantum_client(lambda _: pytest.fail("unexpected HTTP"), monkeypatch)
    monkeypatch.setattr(channel, "upload_media", upload)
    with client:
        prepared = channel.prepare_notice_attachment(str(movie), {
            "kind": "video", "file_name": "示例.mp4", "mime": "video/mp4",
        })
    assert prepared == {"duration": 1, "width": 64, "height": 32, "thumbnail": "file-1",
                        "file_key": "file-2", "mime_type": "video/mp4", "ext": "mp4"}
    assert uploaded[0].startswith(b"\xff\xd8") and uploaded[1] == movie.read_bytes()


def fixture_video():
    def box(kind, data):
        return (8 + len(data)).to_bytes(4, "big") + kind + data

    return box(b"ftyp", b"isom\0\0\0\0") + box(b"moov", b"test") + box(b"mdat", b"test")


def upload(owner, kind, filename, data, mime):
    response = owner.post(f"/api/admin/notice-assets/{kind}", files={"file": (filename, data, mime)})
    assert response.status_code == 200, response.text
    return response.json()


class NativeNoticeTester(FakeNotifier):
    def __init__(self, fail_video=False):
        super().__init__(now_fn=lambda: NOW)
        self.native = []
        self.fail_video = fail_video

    def prepare_notice_attachment(self, source, asset):
        return asset

    def send_prepared_notice(self, receive_id, kind, asset, idem, receive_type):
        self.native.append((kind, asset["file_name"], idem, receive_type))
        return SendResult(False, "failed", retryable=False, error="TEST_REJECTED" * 50) if self.fail_video and kind == "video" \
            else SendResult(True, "sent", 200)


def make_template(owner, tpl, items, is_manual, text=""):
    response = owner.post("/api/admin/templates", json={
        "name": "媒体通知", "kind": "notice", "period": "day", "is_manual": is_manual,
        "role_ids": tpl["role_ids"], "text_tpl": text, "notice_times": [] if is_manual else ["10:00"],
        "spec": {"notice_items": [{"format": item["kind"], "asset_id": item["asset_id"]} for item in items]},
    })
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


@pytest.mark.parametrize("is_manual", [False, True])
def test_save_and_send_each_attachment_once_across_three_ticks(ctx, is_manual):
    store, scheduler, _, _, owner, create_owner, tpl, member = ctx
    images = io.BytesIO()
    Image.new("RGB", (12, 8), "blue").save(images, "PNG")
    asset_uploads = [
        ("image", "png", images.getvalue(), "image/png"),
        ("file", "txt", "培训通知".encode(), "text/plain"),
        ("video", "mp4", fixture_video(), "video/mp4"),
    ]
    assets = [upload(owner, kind, f"{kind}-{num}.{ext}", data, mime)
              for kind, ext, data, mime in asset_uploads for num in range(2)]
    _, outsider = create_owner("other_notice_owner")
    assert outsider.get(f"/api/admin/notice-assets/{assets[0]['asset_id']}").status_code == 404
    assert owner.post("/api/admin/notice-assets/video", files={"file": ("bad.mp4", b"bad", "video/mp4")}).status_code == 400
    template_id = make_template(owner, tpl, assets, is_manual)
    persisted = store.get_template(template_id)["spec_json"]["notice_items"]
    assert {item["asset_id"] for item in persisted} == {item["asset_id"] for item in assets}
    store.upsert_delivery_target(member["phone"], "mock-member", external_type="union_id",
                                 activated_at="2026-11-16 08:00:00")
    notifier = NativeNoticeTester()
    scheduler.notifiers = {"zdxlz": notifier}
    if is_manual:
        for _ in range(3):
            scheduler.push_now(template_id)
    else:
        for tick in range(3):
            scheduler.run_once(NOW + timedelta(seconds=tick * 30))
    assert [(kind, name) for kind, name, _, _ in notifier.native] == [(item["kind"], item["file_name"]) for item in assets]
    assert len({idem for _, _, idem, _ in notifier.native}) == 6
    assert all(receive_type == "union_id" for _, _, _, receive_type in notifier.native)
    assert not any("/h5/notice-media" in message.text or "/h5/img/" in message.text for message in notifier.sent_messages)


def test_definite_native_video_failure_gets_one_playback_and_download_link(ctx, movie):
    store, scheduler, _, _, owner, _, tpl, member = ctx
    movie_data = movie.read_bytes()
    asset = upload(owner, "video", "培训视频.mp4", movie_data, "video/mp4")
    template_id = make_template(owner, tpl, [asset], False)
    notifier = NativeNoticeTester(fail_video=True)
    scheduler.notifiers = {"zdxlz": notifier}
    for tick in range(3):
        scheduler.run_once(NOW + timedelta(seconds=tick * 30))
    links = [message.text.splitlines()[-1] for message in notifier.sent_messages if "/h5/notice-media" in message.text]
    assert len(notifier.native) == len(links) == 1
    assert any(row["kind"] == "notice_vid_link" and row["result"] == "sent"
               for row in store.list_reminder_logs(template_id=template_id))
    url = urlsplit(links[0])
    path = f"{url.path}?{url.query}"
    page = owner.get(path)
    assert page.status_code == 200 and "<video controls playsinline" in page.text and "下载原视频文件" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    part = owner.get(path + "&raw=1", headers={"Range": "bytes=0-15"})
    assert part.status_code == 206 and part.content == movie_data[:16]
    download = owner.get(path + "&download=1")
    assert download.content == movie_data and download.headers["content-disposition"].startswith("attachment;")
    assert owner.get(url.path).status_code == 401
    inst = store.get_instance_by_key(template_id, member["phone"], NOW.date().isoformat())
    expired = store.issue_h5_token(member["phone"], template_id, asset["asset_id"], inst["id"],
                                   purpose="notice_asset", ttl_hours=-1)
    assert owner.get(url.path, params={"token": expired}).status_code == 401
