# -*- coding: utf-8 -*-
"""量子密信原生图片回归，覆盖旧调用方式和官方消息内容结构。"""
from __future__ import annotations

import json

from app.channels.zdxlz import ZdxlzChannel
from app.config import Config


def _mk_cfg() -> Config:
    return Config(
        public_port=9020,
        admin_port=9013,
        public_base_url="http://203.0.113.10:9020",
        app_id="test_app_id",
        app_secret="test_app_secret",
    )


class _StubResp:
    status_code = 200

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload if payload is not None else {"success": True, "code": 200}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _StubClient:
    """只记录请求体，不真正发网络；按 URL 分流 token / 消息两个接口。"""

    def __init__(self) -> None:
        self.last_json: dict | None = None

    def post(self, url, headers=None, json=None, **kwargs):  # noqa: A002
        self.last_json = json
        if "/auth/token" in url:
            return _StubResp(payload={"access_token": "stub_token", "expires_in": 7200})
        return _StubResp()


def test_image_content_omits_legacy_metadata():
    """旧调用方仍可传附件参数，消息体采用官方图片字段。"""
    stub = _StubClient()
    ch = ZdxlzChannel(_mk_cfg(), http_client=stub)
    res = ch.send_image(
        "uid_x",
        "fk_123",
        width=650,
        height=434,
        mime_type="image/jpeg",
        file_name="数据看板.jpg",
        file_size=36484,
        file_ext="jpg",
    )
    assert res.ok is True
    content = json.loads(stub.last_json["content"])  # type: ignore[index]
    assert content == {
        "fileId": "fk_123", "width": 650, "height": 434, "mimeType": "jpg", "altText": "数据看板",
    }


def test_image_content_mime_type_normalizes_extension():
    """历史调用方的 .PNG 后缀归一到消息的 mimeType。"""
    stub = _StubClient()
    ch = ZdxlzChannel(_mk_cfg(), http_client=stub)
    ch.send_image(
        "uid_x", "fk_123", width=650, height=434,
        mime_type="image/png", file_name="a.png", file_size=100, file_ext=".PNG",
    )
    content = json.loads(stub.last_json["content"])  # type: ignore[index]
    assert content["mimeType"] == "png"
    assert "ext" not in content


def test_image_content_without_optional_attachment_metadata():
    stub = _StubClient()
    ch = ZdxlzChannel(_mk_cfg(), http_client=stub)
    ch.send_image("uid_x", "fk_123", width=650, height=434, mime_type="png")
    content = json.loads(stub.last_json["content"])  # type: ignore[index]
    assert set(content.keys()) == {"fileId", "width", "height", "mimeType", "altText"}
    assert content["mimeType"] == "png"
