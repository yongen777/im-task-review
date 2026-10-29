# -*- coding: utf-8 -*-
"""阶段 3 修复（2026-10-27）：平台「URL 测试」事件兼容。

背景（实抓样本 D:/tmp/last_event.json，平台点击测试下发）：
    {"msgUid": "http-callback-test", "userId": "", "content": {...}, "extra": {"test": true}}
    平台测试事件的 userId 为空，此前 process_app_event 返回 400，
    导致开发者后台「URL 测试」判定「回调地址不可达或未按预期返回 200」。

修复后行为：
  - 平台测试事件（msgUid=http-callback-test 或 extra.test=true 且无 userId）→ 200 确认；
  - 缺 userId 的非测试事件 → 仍 400（真实业务事件保持严格校验）；
  - 正常绑定流程（路径 A/B/C）不受影响（由 test_stage_2b 覆盖）。
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.events import process_app_event  # noqa: E402
from app.channels.zdxlz import ZdxlzChannel, encrypt_event_payload  # noqa: E402
from app.config import Config  # noqa: E402
from app.scheduler import FakeNotifier  # noqa: E402
from app.state import StateStore  # noqa: E402

import hashlib  # noqa: E402
import hmac  # noqa: E402

PLATFORM_TEST_PAYLOAD = {
    "appId": 63474,
    "eventType": "callback:direct",
    "userId": "",
    "groupId": "",
    "toUserId": "",
    "msgUid": "http-callback-test",
    "type": "text",
    "content": {"content": "HTTP callback test message"},
    "extra": {"test": True},
    "extraContent": "",
}

NON_TEST_PAYLOAD_NO_USERID = {
    "appId": 63474,
    "eventType": "callback:direct",
    "userId": "",
    "msgUid": "M-12345",
    "type": "text",
    "content": {"content": "hello"},
    "extra": {},
    "extraContent": "",
}


class TestPlatformUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self.cfg = Config(
            app_id="test_app_id",
            app_secret="test_app_secret",
            api_base="https://open.work.example.com",
            verify_token="test_verify_token_123",
            encrypt_key="test_encrypt_key_456",
            public_base_url="https://task.example.com",
            public_port=21001,
            admin_port=21002,
            data_dir=tmp_path / "data",
        )
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.cfg.db_path)
        self.channel = ZdxlzChannel(self.cfg)
        self.notifier = FakeNotifier()

    def tearDown(self) -> None:
        try:
            self.store.close()
            self.channel.close()
        finally:
            self._tmp.cleanup()

    def _post_event(self, payload: dict) -> tuple[int, dict]:
        ts, nonce = str(int(time.time() * 1000)), "xFWXh8zvUcRDJmm5"
        body_b64 = encrypt_event_payload(self.cfg.encrypt_key, ts, nonce, payload)
        sig = hmac.new(self.cfg.verify_token.encode(), (ts + nonce + body_b64).encode(), hashlib.sha256).hexdigest()
        return process_app_event(
            state=self.store, channel=self.channel, public_base_url=self.cfg.public_base_url,
            timestamp=ts, nonce=nonce, signature=sig, body_text=body_b64, notifier=self.notifier,
        )

    def test_platform_url_test_event_returns_200(self):
        """平台「URL 测试」事件（实抓样本同构）：返回 200 确认，不进绑定流程。"""
        code, body = self._post_event(PLATFORM_TEST_PAYLOAD)
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("test"))
        # 测试事件绝不触发机器人回复
        self.assertEqual(len(self.notifier.sent_messages), 0)

    def test_missing_userid_non_test_event_still_400(self):
        """缺 userId 且非平台测试特征的事件：保持 400 严格校验。"""
        code, body = self._post_event(NON_TEST_PAYLOAD_NO_USERID)
        self.assertEqual(code, 400)
        self.assertEqual(body.get("code"), "VALIDATION_FAILED")

    def test_bound_user_nonphone_gets_ack_reply(self):
        """已绑定用户发送非手机号消息：机器人固定回复确认（不再静默）。"""
        role = self.store.create_role(name="ack测试角色")
        self.store.add_role_member(role["role_id"], phone="13800000001", name="测试成员")
        self.store.upsert_delivery_target("13800000001", "ext_ack_user")

        payload = {
            "appId": 63474, "eventType": "callback:direct",
            "userId": "ext_ack_user", "groupId": "", "toUserId": "",
            "msgUid": "M-ACK-1", "type": "text",
            "content": {"content": "你好"}, "extra": {}, "extraContent": "",
        }
        code, body = self._post_event(payload)
        self.assertEqual(code, 200)
        self.assertTrue(body.get("bound"))
        self.assertEqual(len(self.notifier.sent_messages), 1)
        self.assertIn("【OPS任务调度助手】", self.notifier.sent_messages[0].text)
        self.assertIn("消息已收到", self.notifier.sent_messages[0].text)
        self.assertIn("暂时看不懂聊天和其他文字指令", self.notifier.sent_messages[0].text)


if __name__ == "__main__":
    unittest.main()
