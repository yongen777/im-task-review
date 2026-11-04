# -*- coding: utf-8 -*-
"""阶段 2B + 2A 评审必修项（必修 1~3 + 4 小项）完整单元测试。"""
from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import time
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.api.events import process_app_event
from app.api.h5 import load_h5_html, process_h5_bind, process_h5_task_info, process_h5_task_submit, process_h5_task_upload, reset_bind_rate
from app.channels.zdxlz import ZdxlzChannel, encrypt_event_payload
from app.config import SHANGHAI_TZ, Config
from app.scheduler import FakeNotifier, Scheduler, calc_resubmit_deadline, make_idem_key
from app.state import BizError, StateStore


def make_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI_TZ)


def build_encrypted_event(encrypt_key: str, verify_token: str, timestamp: str, nonce: str, payload: dict) -> tuple[str, str]:
    body_b64 = encrypt_event_payload(encrypt_key, timestamp, nonce, payload)
    sig = hmac.new(verify_token.encode("utf-8"), (timestamp + nonce + body_b64).encode("utf-8"), hashlib.sha256).hexdigest()
    return body_b64, sig


@dataclass
class _DirectResponse:
    status_code: int
    _data: Any
    text: str = ""

    def json(self) -> Any:
        return self._data


class _DirectPublicClient:
    """在未安装 fastapi/httpx 的裸 Python 环境中直接驱动路由处理函数；若已安装则优先用 TestClient。"""

    def __init__(self, state: StateStore, channel: ZdxlzChannel, cfg: Config, notifier: FakeNotifier) -> None:
        self.state = state
        self.channel = channel
        self.cfg = cfg
        self.notifier = notifier

    def get(self, path: str) -> _DirectResponse:
        if path in ("/h5/bind", "/h5/task"):
            return _DirectResponse(200, None, load_h5_html("bind.html" if path == "/h5/bind" else "task.html"))
        return _DirectResponse(404, {"code": "NOT_FOUND"})

    def post(
        self,
        path: str,
        *,
        content: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> _DirectResponse:
        hdrs = headers or {}
        if path == "/api/app-events":
            code, body = process_app_event(
                state=self.state, channel=self.channel, public_base_url=self.cfg.public_base_url,
                timestamp=hdrs.get("X-CTQ-Timestamp", ""), nonce=hdrs.get("X-CTQ-Nonce", ""),
                signature=hdrs.get("X-CTQ-Signature", ""), body_text=content or "", notifier=self.notifier,
            )
            return _DirectResponse(code, body)
        if path == "/api/h5/bind":
            code, body = process_h5_bind(state=self.state, data=json or {}, notifier=self.notifier)
            return _DirectResponse(code, body)
        if path == "/api/h5/task/info":
            raw_token = (json or {}).get("token") or hdrs.get("X-H5-Token", "")
            code, body = process_h5_task_info(state=self.state, raw_token=raw_token)
            return _DirectResponse(code, body)
        if path == "/api/h5/task/upload":
            raw_token = (data or {}).get("token") or hdrs.get("X-H5-Token", "")
            filename, file_bytes = (files or {})["file"][0], (files or {})["file"][1]
            code, body = process_h5_task_upload(
                state=self.state, uploads_dir=self.cfg.uploads_dir, raw_token=raw_token, filename=filename, content_bytes=file_bytes
            )
            return _DirectResponse(code, body)
        if path == "/api/h5/task/submit":
            payload_data = json or {}
            raw_token = payload_data.get("token") or hdrs.get("X-H5-Token", "")
            code, body = process_h5_task_submit(state=self.state, raw_token=raw_token, data=payload_data)
            return _DirectResponse(code, body)
        return _DirectResponse(404, {"code": "NOT_FOUND"})


class TestStage2B(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.current_dt = make_dt("2026-10-25 09:00:00")

        self.cfg = Config(
            app_id="test_app_id",
            app_secret="test_app_secret",
            api_base="https://open.work.example.com",
            verify_token="test_verify_token_123",
            encrypt_key="test_encrypt_key_456",
            public_base_url="https://task.example.com",
            public_port=21001,
            admin_port=21002,
            data_dir=self.tmp_path / "data",
        )
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.uploads_dir.mkdir(parents=True, exist_ok=True)

        self.notifier = FakeNotifier(now_fn=lambda: self.current_dt)
        self.store = StateStore(self.cfg.db_path, now_fn=lambda: self.current_dt)
        self.channel = ZdxlzChannel(self.cfg)
        self.scheduler = Scheduler(
            state=self.store, public_base_url=self.cfg.public_base_url, notifier=self.notifier, now_fn=lambda: self.current_dt
        )

        try:
            from fastapi.testclient import TestClient
            from app.main import create_apps

            pub_app, _, _, _ = create_apps(
                config=self.cfg, state=self.store, channel=self.channel, notifier=self.notifier, now_fn=lambda: self.current_dt
            )
            self.pub_client: Any = TestClient(pub_app)
        except ImportError:
            self.pub_client = _DirectPublicClient(self.store, self.channel, self.cfg, self.notifier)
        # 2026-11-03：登记表单测试增多，每个用例前清空绑定接口的 IP 限流，避免跨用例误伤
        reset_bind_rate()

    def tearDown(self) -> None:
        try:
            self.channel.close()
            self.store.close()
            self._tmp.cleanup()
        finally:
            os.environ.clear()
            os.environ.update(self._orig_env)

    def test_fix1_and_fix2_retry_after_failure_and_real_h5_token(self) -> None:
        with self.assertRaises(ValueError):
            Scheduler(state=self.store, public_base_url="")

        rid = self.store.create_role("运维组", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13800000001", "王一", "运维部", actor="admin")
        self.store.add_role_member(rid, "13800000002", "赵二", "运维部", actor="admin")
        self.store.upsert_delivery_target("13800000001", "ext_user_1")
        self.store.upsert_delivery_target("13800000002", "ext_user_2")

        tpl = self.store.create_template(
            {
                "name": "机房巡检", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "temp", "label": "温度", "type": "text", "required": True}],
                "text_tpl": "{姓名}请提交{任务名}", "remind_at": "09:00", "deadline": "12:00", "period": "day",
            },
            actor="admin",
        )
        tid = tpl["template_id"]

        self.notifier.fail_receive_ids.add("ext_user_1")
        self.notifier.no_session_receive_ids.add("ext_user_2")

        self.current_dt = make_dt("2026-10-25 09:00:00")
        s1 = self.scheduler.run_once()
        self.assertEqual(s1["failed"], 2)
        self.assertEqual(s1["sent"], 0)
        self.assertIsNone(self.store.get_delivery_target("13800000002"))

        # 2026-10-28：幂等键已加入「计划时刻」，否则同一天内一次 missed 会永久挡住改到稍后的发送
        idem1 = make_idem_key(tid, "13800000001", "2026-10-25", "remind", 1, "2026-10-25 09:00:00")
        self.assertIsNone(self.store.get_reminder_by_idem_key(idem1))
        self.assertFalse(self.store.has_sent_reminder(idem1))
        attempts_before = self.store.list_reminder_attempts(idem1)
        self.assertEqual(len(attempts_before), 1)
        self.assertEqual(attempts_before[0]["result"], "failed")
        self.assertEqual(attempts_before[0]["retry"], 0)

        self.notifier.fail_receive_ids.clear()
        self.current_dt = make_dt("2026-10-25 09:00:30")
        s2 = self.scheduler.run_once()
        self.assertEqual(s2["sent"], 1)
        self.assertTrue(self.store.has_sent_reminder(idem1))

        attempts_after = self.store.list_reminder_attempts(idem1)
        self.assertEqual(len(attempts_after), 2)
        self.assertEqual((attempts_after[0]["retry"], attempts_after[0]["result"]), (0, "failed"))
        self.assertEqual((attempts_after[1]["retry"], attempts_after[1]["result"]), (1, "sent"))

        sent_msg = self.notifier.sent_messages[-1].text
        self.assertIn("https://task.example.com/h5/task#t=", sent_msg)
        raw_token = sent_msg.split("#t=", 1)[1].strip()
        tok_record = self.store.get_h5_token(raw_token)
        self.assertIsNotNone(tok_record)
        self.assertEqual(tok_record["phone"], "13800000001")
        self.assertEqual(tok_record["token_id"], hashlib.sha256(raw_token.encode("utf-8")).hexdigest())

    def test_fix3_r14_overdue_notice_r27_resubmit_and_rejected_expired(self) -> None:
        dl = "2026-10-25 18:00:00"
        self.assertEqual(calc_resubmit_deadline(dl, "2026-10-26 04:00:00"), "2026-10-28 18:00:00")
        self.assertEqual(calc_resubmit_deadline(dl, "2026-10-28 06:00:00"), "2026-10-29 06:00:00")

        rid = self.store.create_role("日报组", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13900000011", "甲", "一部", actor="admin")
        self.store.add_role_member(rid, "13900000012", "乙", "二部", actor="admin")
        self.store.upsert_delivery_target("13900000011", "uid_jia")
        self.store.upsert_delivery_target("13900000012", "uid_yi")

        tpl = self.store.create_template(
            {
                "name": "工作日报", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
                "text_tpl": "{姓名}请交{任务名}", "remind_at": "17:30", "deadline": "18:00", "period": "day", "need_review": True,
            },
            actor="admin",
        )
        tid = tpl["template_id"]

        self.current_dt = make_dt("2026-10-25 17:30:00")
        self.scheduler.run_once()
        self.assertEqual(len(self.notifier.sent_messages), 2)

        inst_yi = self.store.get_instance_by_key(tid, "13900000012", "2026-10-25")
        tok_yi = self.store.issue_h5_token("13900000012", tid, "2026-10-25", inst_yi["id"])
        self.current_dt = make_dt("2026-10-25 17:40:00")
        self.store.submit_h5_task(raw_token=tok_yi, fields={"summary": "初稿"})

        self.current_dt = make_dt("2026-10-25 18:05:00")
        s_1805 = self.scheduler.run_once()
        self.assertEqual((s_1805["to_overdue"], s_1805["sent"]), (1, 0))

        self.current_dt = make_dt("2026-10-25 18:10:00")
        s_1810 = self.scheduler.run_once()
        self.assertEqual(s_1810["sent"], 1)
        self.assertIn("【工作日报】已逾期", self.notifier.sent_messages[-1].text)

        self.current_dt = make_dt("2026-10-25 18:15:00")
        self.assertEqual(self.scheduler.run_once()["sent"], 0)

        self.current_dt = make_dt("2026-10-28 06:00:00")
        rejected_yi = self.store.review_instance(inst_yi["id"], action="reject", reason="内容不完整", reviewer="admin")
        self.assertEqual((rejected_yi["status"], rejected_yi["resubmit_deadline"]), ("rejected", "2026-10-29 06:00:00"))

        self.current_dt = make_dt("2026-10-29 06:00:01")
        adv = self.store.advance_instance_statuses(self.current_dt)
        self.assertGreaterEqual(adv["to_expired"], 1)
        self.assertEqual(self.store.get_instance(inst_yi["id"])["status"], "expired")

    def test_rejected_instance_can_resubmit_inside_window(self) -> None:
        """2026-10-28 回归：驳回后重新提交的链接必须可用（页面不能锁死、接口要放行）。

        场景：18:00 截止 -> 次日 06:00 驳回（重交截止 = max(截止+72h, 驳回+24h)）。
        窗口内：h5 info 给出 window_open=True 且带 resubmit_deadline；提交成功并回到 submitted。
        窗口外：window_open=False，提交被拒。
        """
        from app.api.h5 import process_h5_task_info

        rid = self.store.create_role("重交组", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13900000021", "甲", "一部", actor="admin")
        self.store.upsert_delivery_target("13900000021", "uid_resubmit")
        tpl = self.store.create_template(
            {
                "name": "重交验证", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
                "text_tpl": "{姓名}请交{任务名}", "remind_at": "17:30", "deadline": "18:00",
                "period": "day", "need_review": True,
            },
            actor="admin",
        )
        tid = tpl["template_id"]
        self.current_dt = make_dt("2026-10-25 17:30:00")
        self.scheduler.run_once()
        inst = self.store.get_instance_by_key(tid, "13900000021", "2026-10-25")
        tok1 = self.store.issue_h5_token("13900000021", tid, "2026-10-25", inst["id"])
        self.current_dt = make_dt("2026-10-25 17:40:00")
        self.store.submit_h5_task(raw_token=tok1, fields={"summary": "初稿"})

        self.current_dt = make_dt("2026-10-26 06:00:00")
        rej = self.store.review_instance(inst["id"], action="reject", reason="图片太糊", reviewer="admin")
        self.assertEqual(rej["status"], "rejected")

        # 窗口内：重交链接可用
        tok2 = self.store.issue_h5_token("13900000021", tid, "2026-10-25", inst["id"])
        self.current_dt = make_dt("2026-10-26 09:00:00")
        code, info = process_h5_task_info(state=self.store, raw_token=tok2)
        self.assertEqual(code, 200)
        self.assertTrue(info["window_open"], info.get("window_reason"))
        self.assertEqual(info["instance"]["resubmit_deadline"], rej["resubmit_deadline"])

        res = self.store.submit_h5_task(raw_token=tok2, fields={"summary": "改好的"})
        self.assertEqual(res["status"], "submitted")
        fresh = self.store.get_instance(inst["id"])
        self.assertEqual((fresh["status"], fresh["reviewed_at"]), ("submitted", None))

        # 窗口外（超过重交截止）：拒绝提交
        self.store.review_instance(inst["id"], action="reject", reason="还是不行", reviewer="admin")
        self.current_dt = make_dt("2026-11-05 09:00:00")
        tok3 = self.store.issue_h5_token("13900000021", tid, "2026-10-25", inst["id"])
        code2, info2 = process_h5_task_info(state=self.store, raw_token=tok3)
        self.assertEqual(code2, 200)
        self.assertFalse(info2["window_open"])
        with self.assertRaises(BizError):
            self.store.submit_h5_task(raw_token=tok3, fields={"summary": "超期了"})

    def test_app_events_three_paths_and_three_guardrails(self) -> None:
        rid = self.store.create_role("全员角色", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13800008001", "张A", "研发", actor="admin")
        self.store.add_role_member(rid, "13800008002", "李B", "产品", actor="admin")
        self.store.add_role_member(rid, "13800008003", "王C", "设计", actor="admin")
        # 2026-11-03：H5 登记表单必填部门，先配置部门选项
        self.store.create_department("研发", actor="admin")
        self.store.create_department("产品", actor="admin")
        self.store.create_department("设计", actor="admin")
        ts, nonce = str(int(time.time())), "nonce_abc"

        body_b64, _ = build_encrypted_event(self.cfg.encrypt_key, self.cfg.verify_token, ts, nonce, {"userId": "uid_hacker", "phone": "13800008001"})
        resp_401 = self.pub_client.post(
            "/api/app-events", content=body_b64,
            headers={"X-CTQ-Timestamp": ts, "X-CTQ-Nonce": nonce, "X-CTQ-Signature": "deadbeef_bad_signature"},
        )
        self.assertEqual(resp_401.status_code, 401)
        self.assertIsNone(self.store.get_delivery_target("13800008001"))

        body_a, sig_a = build_encrypted_event(self.cfg.encrypt_key, self.cfg.verify_token, ts, nonce, {"userId": "uid_zhang_a", "phone": "13800008001"})
        resp_a = self.pub_client.post("/api/app-events", content=body_a, headers={"X-CTQ-Timestamp": ts, "X-CTQ-Nonce": nonce, "X-CTQ-Signature": sig_a})
        self.assertEqual((resp_a.status_code, resp_a.json()["path"]), (200, "A"))
        self.assertEqual(self.store.get_delivery_target("13800008001")["external_id"], "uid_zhang_a")

        # ⭐ 2026-11-03 新激活模型：后台先导入才能激活。
        #   未绑定用户发任何文本（含手机号）→ 统一回登记链接，不解析手机号、不落待归纳池。
        body_stranger, sig_str = build_encrypted_event(self.cfg.encrypt_key, self.cfg.verify_token, ts, nonce, {"userId": "uid_stranger", "text": "13999999999"})
        resp_str = self.pub_client.post("/api/app-events", content=body_stranger, headers={"X-CTQ-Timestamp": ts, "X-CTQ-Nonce": nonce, "X-CTQ-Signature": sig_str})
        self.assertEqual(resp_str.status_code, 200)
        self.assertIn("你还未完成信息登记", resp_str.json()["message"])
        self.assertIn("/h5/bind#uid=uid_stranger&plat=zdxlz", resp_str.json()["bind_url"])
        with self.store.lock:
            pending_cnt = self.store.conn.execute("SELECT COUNT(*) FROM pending_member").fetchone()[0]
        self.assertEqual(pending_cnt, 0, "⛔ 不再有待归纳池")
        self.assertIsNone(self.store.get_delivery_target("13999999999"))

        self.assertEqual(self.pub_client.get("/h5/bind").status_code, 200)
        # 护栏：已绑定的手机号不能被别的账号覆盖（409）
        resp_c_dup = self.pub_client.post("/api/h5/bind", json={"phone": "13800008001", "external_id": "uid_attacker", "name": "攻", "dept": "研发"})
        self.assertEqual((resp_c_dup.status_code, resp_c_dup.json()["code"]), (409, "DUPLICATE"))
        self.assertEqual(self.store.get_delivery_target("13800008001")["external_id"], "uid_zhang_a")

        # 名册内：H5 登记表单（姓名+手机号与后台一致）提交即绑定
        resp_c_ok = self.pub_client.post("/api/h5/bind", json={"phone": "13800008003", "external_id": "uid_wang_c", "name": "王C", "dept": "设计"})
        self.assertEqual((resp_c_ok.status_code, resp_c_ok.json()["phone_masked"]), (200, "138****8003"))
        self.assertEqual(self.store.get_delivery_target("13800008003")["external_id"], "uid_wang_c")

        # 姓名与后台不一致 → 拒绝（2026-11-03 新模型比对规则）
        resp_name_bad = self.pub_client.post("/api/h5/bind", json={"phone": "13800008002", "external_id": "uid_li_b", "name": "假李B", "dept": "产品"})
        self.assertEqual((resp_name_bad.status_code, resp_name_bad.json().get("code")), (400, "ROSTER_MISMATCH"))
        # 登记表单字段校验：缺部门 / 缺姓名直接 400
        resp_no_dept = self.pub_client.post("/api/h5/bind", json={"phone": "13800008002", "external_id": "uid_li_b", "name": "李B", "dept": ""})
        self.assertEqual(resp_no_dept.status_code, 400)
        resp_no_name = self.pub_client.post("/api/h5/bind", json={"phone": "13800008002", "external_id": "uid_li_b", "name": "", "dept": "产品"})
        self.assertEqual(resp_no_name.status_code, 400)

        audits = self.store.conn.execute("SELECT * FROM admin_audit WHERE action LIKE 'target.bind_%' ORDER BY id ASC").fetchall()
        self.assertEqual(len(audits), 2)

    def test_rebind_same_phone_returns_already_same(self) -> None:
        """同一账号重复提交登记表单：返回「已登记」，不再抛 409（2026-11-03 改为 H5 登记表单）。"""
        reset_bind_rate()
        rid = self.store.create_role("重复绑定角色", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13800007001", "重复测", "研发", actor="admin")
        self.store.create_department("研发", actor="admin")

        first = self.pub_client.post("/api/h5/bind", json={"phone": "13800007001", "external_id": "uid_repeat", "name": "重复测", "dept": "研发"})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["bound"])
        self.assertFalse(first.json().get("already_same", False))

        second = self.pub_client.post("/api/h5/bind", json={"phone": "13800007001", "external_id": "uid_repeat", "name": "重复测", "dept": "研发"})
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json().get("already_same"), "重复登记应返回已登记而不是 409")

    def test_h5_registration_not_imported_rejected(self) -> None:
        """2026-11-03 新激活模型：手机号不在本平台名册 → 400 提示未导入，不绑定、不落任何池。"""
        reset_bind_rate()
        self.store.create_department("市场部", actor="admin")
        for phone, name in (("13800009001", "张三"), ("13800009002", "李四")):
            resp = self.pub_client.post("/api/h5/bind", json={"phone": phone, "external_id": "uid_np_" + name, "name": name, "dept": "市场部"})
            self.assertEqual(resp.status_code, 400, name)
            self.assertEqual(resp.json().get("code"), "NOT_IN_ROSTER", name)
            self.assertIsNone(self.store.get_delivery_target(phone), name)
        with self.store.lock:
            cnt = self.store.conn.execute("SELECT COUNT(*) FROM pending_member").fetchone()[0]
        self.assertEqual(cnt, 0)

    def test_missed_slot_does_not_block_rescheduled_send(self) -> None:
        """2026-10-28 缺陷回归：同一天内「错过一次」不能把「改到稍后」的发送永久堵死。

        线上真实场景：09:30 的提醒因服务重启时已超出 30 分钟补偿窗口被记为 missed，
        管理员随后把执行时间改到 14:18 —— 旧幂等键不含计划时刻，14:18 仍被判「已处理」而不再发送，
        表现就是「改了时间却收不到」。加入 planned_at 后各时刻独立幂等。
        """
        rid = self.store.create_role("片长", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13800000011", "王一", "运维部", actor="admin")
        self.store.upsert_delivery_target("13800000011", "ext_missed_1")
        tpl = self.store.create_template(
            {
                "name": "外出上门通报", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "visit_count", "label": "今日累计上门户数", "type": "number", "required": True}],
                "text_tpl": "{姓名}请提交{任务名}", "remind_at": "09:30", "deadline": "18:00", "period": "day",
            },
            actor="admin",
        )
        tid = tpl["template_id"]

        self.current_dt = make_dt("2026-10-25 11:00:00")  # 距 09:30 已超 30 分钟补偿窗口
        s1 = self.scheduler.run_once()
        self.assertEqual(s1["sent"], 0)
        rows = [dict(r) for r in self.store.conn.execute("SELECT result FROM reminder_log").fetchall()]
        self.assertEqual([r["result"] for r in rows], ["missed"])

        self.store.update_template(tid, {"remind_at": "14:18"}, actor="admin")
        self.current_dt = make_dt("2026-10-25 14:18:10")
        s2 = self.scheduler.run_once()
        self.assertEqual(s2["sent"], 1)
        rows = [
            dict(r)
            for r in self.store.conn.execute("SELECT kind, result, planned_at FROM reminder_log ORDER BY id").fetchall()
        ]
        self.assertEqual((rows[1]["kind"], rows[1]["result"], rows[1]["planned_at"]), ("remind", "sent", "2026-10-25 14:18:00"))

    def test_instance_filters_kind_role_keyword_and_name(self) -> None:
        """2026-10-28 缺陷回归：任务实例列表的「任务类型 / 所属角色 / 关键字 / 任务名称」必须真过滤。

        线上现象：选「纯通知提醒」时上传任务仍在列表里（后端 `/api/admin/instances` 只认
        template_id/period_key/status/q，前端传的 kind、role_id、phone 被 FastAPI 静默丢弃）；
        「所属角色」列一律退化成「业务角色」（行里没有 role_id）。这里同时锁住「过滤生效」
        与「行内补齐 kind / role_id」两件事。
        """
        from app.api.admin import _query_instances

        rid = self.store.create_role("片长", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13800000021", "甲一", "一部", actor="admin")
        tpl = self.store.create_template(
            {
                "name": "机房巡检", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "temp", "label": "温度", "type": "text", "required": True}],
                "text_tpl": "{姓名}请提交{任务名}", "remind_at": "09:30", "deadline": "18:00", "period": "day",
            },
            actor="admin",
        )
        self.store.ensure_template_instances(tpl["template_id"], "2026-10-25", "2026-10-25 18:00:00")

        up = _query_instances(self.store, kind="upload")
        self.assertEqual(up["total"], 1)
        self.assertEqual(up["items"][0]["kind"], "form")
        self.assertEqual(up["items"][0]["role_id"], rid)

        self.assertEqual(_query_instances(self.store, kind="notice")["total"], 0)
        self.assertEqual(_query_instances(self.store, kind="form")["total"], 1)
        self.assertEqual(_query_instances(self.store, role_id=rid)["total"], 1)
        self.assertEqual(_query_instances(self.store, role_id="r_not_exist")["total"], 0)
        self.assertEqual(_query_instances(self.store, q="甲一")["total"], 1)
        self.assertEqual(_query_instances(self.store, q="13800000021")["total"], 1)
        self.assertEqual(_query_instances(self.store, q="不存在的人")["total"], 0)
        self.assertEqual(_query_instances(self.store, template_name="巡检")["total"], 1)
        self.assertEqual(_query_instances(self.store, template_name="外出")["total"], 0)
        self.assertEqual(_query_instances(self.store, template_id=tpl["template_id"])["total"], 1)
        # 组合条件：类型对但名称不匹配 -> 空
        self.assertEqual(_query_instances(self.store, kind="upload", template_name="外出")["total"], 0)

    def test_h5_submissions_upload_security_and_late_window(self) -> None:
        rid = self.store.create_role("执行组", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13600000001", "陈一", "技术部", actor="admin")
        self.store.upsert_delivery_target("13600000001", "uid_chen_1")

        tpl_img = self.store.create_template(
            {
                "name": "工位照片", "kind": "image", "role_ids": [rid], "text_tpl": "请拍工位照片",
                "remind_at": "09:00", "deadline": "18:00", "period": "day", "max_file_mb": 1, "allow_note": True, "need_review": True,
            },
            actor="admin",
        )
        tpl_file = self.store.create_template(
            {
                "name": "周报文档", "kind": "file", "role_ids": [rid], "text_tpl": "请交周报 PDF",
                "remind_at": "09:00", "deadline": "18:00", "period": "day",
            },
            actor="admin",
        )

        self.current_dt = make_dt("2026-10-25 09:00:00")
        self.scheduler.run_once()

        inst_img = self.store.get_instance_by_key(tpl_img["template_id"], "13600000001", "2026-10-25")
        inst_file = self.store.get_instance_by_key(tpl_file["template_id"], "13600000001", "2026-10-25")
        tok_img = self.store.issue_h5_token("13600000001", tpl_img["template_id"], "2026-10-25", inst_img["id"])
        tok_file = self.store.issue_h5_token("13600000001", tpl_file["template_id"], "2026-10-25", inst_file["id"])

        r_md = self.pub_client.post("/api/h5/task/upload", data={"token": tok_file}, files={"file": ("readme.md", b"# Hello Markdown", "text/markdown")})
        self.assertEqual((r_md.status_code, r_md.json()["code"]), (400, "FILE_TYPE_REJECTED"))

        r_fake_magic = self.pub_client.post("/api/h5/task/upload", data={"token": tok_img}, files={"file": ("fake.jpg", b"not a real jpeg header", "image/jpeg")})
        self.assertEqual((r_fake_magic.status_code, r_fake_magic.json()["code"]), (400, "FILE_TYPE_REJECTED"))

        big_png = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (1024 * 1024 + 100))
        r_big = self.pub_client.post("/api/h5/task/upload", data={"token": tok_img}, files={"file": ("too_big.png", big_png, "image/png")})
        self.assertEqual((r_big.status_code, r_big.json()["code"]), (400, "FILE_TOO_LARGE"))

        residual_files = [p for p in self.cfg.uploads_dir.rglob("*") if p.is_file()]
        self.assertEqual(len(residual_files), 0)

        valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        r_ok_png = self.pub_client.post("/api/h5/task/upload", data={"token": tok_img}, files={"file": ("desk.png", valid_png, "image/png")})
        self.assertEqual(r_ok_png.status_code, 200)
        ref_png = r_ok_png.json()["ref"]

        # 2026-10-29 逾期补交审核制：逾期后普通链接不放行，
        # 必须「申请补交 → 管理员通过 → 30 分钟新链接」才能提交（申请窗口截止后 24h 内）。
        self.current_dt = make_dt("2026-10-26 12:00:00")
        self.store.advance_instance_statuses(self.current_dt)
        r_sub_old = self.pub_client.post("/api/h5/task/submit", json={"token": tok_img, "payload": {"refs": [ref_png]}, "note": "出差补交"})
        self.assertEqual((r_sub_old.status_code, r_sub_old.json()["code"]), (400, "WINDOW_CLOSED"))
        self.assertEqual(self.store.request_resubmit("13600000001")["result"], "applied")
        approved = self.store.approve_resubmit(int(inst_img["id"]), actor="admin")
        r_sub_late = self.pub_client.post("/api/h5/task/submit", json={"token": approved["raw_token"], "payload": {"refs": [ref_png]}, "note": "出差补交"})
        self.assertEqual(r_sub_late.status_code, 200)
        self.assertTrue(r_sub_late.json()["late_submit"])
        self.assertEqual(r_sub_late.json()["instance"]["late_submit"], 1)

        self.current_dt = make_dt("2026-10-28 18:30:00")
        tok_file_late = self.store.issue_h5_token("13600000001", tpl_file["template_id"], "2026-10-25", inst_file["id"])
        self.current_dt = make_dt("2026-10-28 19:00:00")
        r_closed = self.pub_client.post("/api/h5/task/submit", json={"token": tok_file_late, "payload": {"refs": ["dummy"]}})
        self.assertEqual((r_closed.status_code, r_closed.json()["code"]), (400, "WINDOW_CLOSED"))


if __name__ == "__main__":
    unittest.main()
