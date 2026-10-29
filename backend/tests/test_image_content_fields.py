# -*- coding: utf-8 -*-
"""2026-10-29 图片消息修复回归：content 必须带 name/size/ext（D/E/F 对照实验结论）。

实验背景：机器人发的 image 消息，客户端收到 ext=""、size=0、name="<fileId>."，
手机端无法识别为图片（点击转圈），PC 端走「接收文件」下载流程。
实测（对照组 D/E/F，2026-10-29 11:33）：content 里显式携带的 name/size/ext
被服务端原样透传给客户端；mimeType 无论何种格式都不影响这三个字段。
"""
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


def test_image_content_includes_name_size_ext():
    """带上 name/size/ext 时，content 必须完整透传这三个字段。"""
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
    assert content["fileId"] == "fk_123"
    assert content["width"] == 650 and content["height"] == 434
    assert content["name"] == "数据看板.jpg"
    assert content["size"] == 36484
    assert content["ext"] == "jpg"


def test_image_content_ext_normalized():
    """ext 传 ".PNG" 这类脏值时归一成 "png"。"""
    stub = _StubClient()
    ch = ZdxlzChannel(_mk_cfg(), http_client=stub)
    ch.send_image(
        "uid_x", "fk_123", width=650, height=434,
        mime_type="image/png", file_name="a.png", file_size=100, file_ext=".PNG",
    )
    content = json.loads(stub.last_json["content"])  # type: ignore[index]
    assert content["ext"] == "png"


def test_image_content_fallback_keeps_old_shape():
    """不传可选字段时 content 保持旧结构（不新增空字段），确保兼容。"""
    stub = _StubClient()
    ch = ZdxlzChannel(_mk_cfg(), http_client=stub)
    ch.send_image("uid_x", "fk_123", width=650, height=434, mime_type="png")
    content = json.loads(stub.last_json["content"])  # type: ignore[index]
    assert set(content.keys()) == {"fileId", "width", "height", "mimeType", "altText"}
