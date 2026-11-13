# -*- coding: utf-8 -*-
"""阶段 2A + v1.2 §16 (P9~P11) 单元测试。"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.config import MISSING_PORT_MSG, SAME_PORT_MSG, SHANGHAI_TZ, Config
from app.scheduler import FakeNotifier, Scheduler, calc_deadline_at, effective_month_dom, is_anchor_day, period_key
from app.state import BizError, StateStore


def bind_activated_at(store, phone: str, external_id: str, activated_at: str = "2000-01-01 00:00:00", platform: str = "zdxlz") -> None:
    """建立平台绑定并**指定首次激活时刻**。

    2026-11-09 新口径「当天激活的只创建激活时刻之后的任务，第二天才是刷新完整一天」：
    实例生成会拿 delivery_target.activated_at 与 period_key 比 ——
      · 激活时刻早于 period_key 那天 → 建实例
      · 激活时刻晚于 period_key 那天 → 不建（那天他还没激活）

    历史 period_key 的测试场景必须把激活时刻设在那天之前，
    否则语义上「那天他还没激活」→ 不该有实例，断言就会对不上。
    """
    store.upsert_delivery_target(phone, external_id, platform=platform, activated_at=activated_at)

def make_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI_TZ)


class TestStage2A(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.current_dt = make_dt("2026-10-25 09:00:00")
        self.store = StateStore(self.tmp_path / "im_task.db", now_fn=lambda: self.current_dt)

    def tearDown(self) -> None:
        try:
            self.store.close()
            self._tmp.cleanup()
        finally:
            os.environ.clear()
            os.environ.update(self._orig_env)

    def test_p9_p10_p11_config_validation(self) -> None:
        empty_dotenv = self.tmp_path / ".env"
        empty_dotenv.write_text("", encoding="utf-8")

        saved_env = dict(os.environ)
        try:
            for k in ("IMT_PUBLIC_PORT", "IMT_ADMIN_PORT", "IMT_PUBLIC_BASE_URL"):
                os.environ.pop(k, None)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    Config.from_env(dotenv_path=empty_dotenv)
            self.assertEqual(str(cm.exception), MISSING_PORT_MSG)
            self.assertEqual(buf.getvalue(), "")

            for bad_url in ("task.example.com", "ftp://task.example.com", "http://", "https:///path"):
                os.environ["IMT_PUBLIC_PORT"] = "21001"
                os.environ["IMT_ADMIN_PORT"] = "21002"
                os.environ["IMT_PUBLIC_BASE_URL"] = bad_url
                with self.assertRaises(SystemExit) as cm:
                    Config.from_env(dotenv_path=empty_dotenv)
                self.assertEqual(str(cm.exception), MISSING_PORT_MSG)

            os.environ["IMT_PUBLIC_PORT"] = "21001"
            os.environ["IMT_ADMIN_PORT"] = "21001"
            os.environ["IMT_PUBLIC_BASE_URL"] = "https://task.example.com"
            with self.assertRaises(SystemExit) as cm:
                Config.from_env(dotenv_path=empty_dotenv)
            self.assertEqual(str(cm.exception), SAME_PORT_MSG)

            os.environ["IMT_ADMIN_PORT"] = "21002"
            cfg = Config.from_env(dotenv_path=empty_dotenv)
            self.assertEqual((cfg.public_port, cfg.admin_port, cfg.public_base_url), (21001, 21002, "https://task.example.com"))
        finally:
            os.environ.clear()
            os.environ.update(saved_env)

    def test_period_key_and_iso_week(self) -> None:
        self.assertEqual(period_key("day", "2026-10-25"), "2026-10-25")
        self.assertEqual(period_key("month", "2026-10-25"), "2026-10")
        self.assertEqual(period_key("week", "2026-10-25"), "2026-W43")
        self.assertEqual(period_key("week", "2027-01-01"), "2026-W53")
        self.assertEqual(period_key(make_dt("2027-01-01 08:00:00"), "week"), "2026-W53")

    def test_r48_anchor_day_and_deadline_offset(self) -> None:
        self.assertTrue(is_anchor_day("day", "2026-10-25"))
        self.assertTrue(is_anchor_day("week", "2026-10-19", anchor_weekday=1))
        self.assertFalse(is_anchor_day("week", "2026-10-20", anchor_weekday=1))
        self.assertTrue(is_anchor_day("week", "2026-10-23", anchor_weekday=5))

        self.assertEqual(effective_month_dom(2026, 2, 31), 28)
        self.assertTrue(is_anchor_day("month", "2026-02-28", anchor_dom=31))
        self.assertFalse(is_anchor_day("month", "2026-02-27", anchor_dom=31))

        self.assertEqual(effective_month_dom(2024, 2, 31), 29)
        self.assertTrue(is_anchor_day("month", "2024-02-29", anchor_dom=31))
        self.assertFalse(is_anchor_day("month", "2024-02-28", anchor_dom=31))

        self.assertTrue(is_anchor_day("month", "2026-04-30", anchor_dom=31))
        self.assertFalse(is_anchor_day("month", "2026-04-29", anchor_dom=31))

        self.assertEqual(calc_deadline_at("2026-10-19", "18:00", deadline_offset=0, period="week"), "2026-10-19 18:00:00")
        self.assertEqual(calc_deadline_at("2026-10-19", "18:00", deadline_offset=4, period="week"), "2026-10-23 18:00:00")
        self.assertEqual(calc_deadline_at("2026-02-28", "17:30", deadline_offset=2, period="month"), "2026-03-02 17:30:00")

    def test_r10_remind_before_deadline_and_template_crud(self) -> None:
        rid = self.store.create_role("销售角色", "一线销售", actor="admin")["role_id"]

        for bad_remind, bad_deadline in (("18:00", "18:00"), ("18:30", "18:00")):
            with self.assertRaises(BizError) as cm:
                self.store.create_template(
                    {
                        "name": "日总结", "kind": "image", "role_ids": [rid],
                        "text_tpl": "{姓名}请交{任务名}", "remind_at": bad_remind, "deadline": bad_deadline, "period": "day",
                    },
                    actor="admin",
                )
            self.assertEqual((cm.exception.status_code, cm.exception.code), (400, "VALIDATION_FAILED"))

        with self.assertRaises(BizError) as cm:
            self.store.create_template(
                {"name": "周会通知", "kind": "notice", "role_ids": [rid], "text_tpl": "请准时参会", "notice_times": ["09:00"], "period": "week"},
                actor="admin",
            )
        self.assertEqual((cm.exception.status_code, cm.exception.code), (400, "VALIDATION_FAILED"))

        notice_tpl = self.store.create_template(
            {"name": "晨会通知", "kind": "notice", "role_ids": [rid], "text_tpl": "请准时参加晨会", "notice_times": ["09:00"]},
            actor="admin",
        )
        self.assertEqual(notice_tpl["period"], "day")

        tpl = self.store.create_template(
            {
                "name": "日总结", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "done_work", "label": "今日完成", "type": "text", "required": True, "max_len": 200}],
                "text_tpl": "{姓名}，今天的{任务名}还没提交，截止{截止时间}。",
                "remind_at": "17:30", "deadline": "18:00", "period": "day", "need_review": True, "enabled": True,
            },
            actor="admin",
        )
        tid = tpl["template_id"]
        self.assertTrue(tid.startswith("t_"))

        with self.assertRaises(BizError) as cm:
            self.store.update_template(tid, {"period": "week"}, actor="admin")
        self.assertEqual((cm.exception.status_code, cm.exception.code), (400, "PERIOD_IMMUTABLE"))

        with self.assertRaises(BizError) as cm:
            self.store.update_template(tid, {"kind": "image"}, actor="admin")
        self.assertEqual((cm.exception.status_code, cm.exception.code), (400, "KIND_IMMUTABLE"))

        with self.assertRaises(BizError) as cm:
            self.store.update_template(tid, {"remind_at": "19:00", "deadline": "18:00"}, actor="admin")
        self.assertEqual(cm.exception.status_code, 400)

        self.store.delete_template(tid, actor="admin")
        self.assertIsNone(self.store.get_template(tid, include_deleted=False))
        # 2026-11-12 下午口径（用户拍板）：删除 = 物理清除，库里不再保留软删行
        self.assertIsNone(self.store.get_template(tid, include_deleted=True))
        self.assertEqual(self.store.list_templates()["total"], 1)

    def test_r44_partial_template_update_keeps_kind_and_notice_remind_at(self) -> None:
        """2026-10-28 缺陷回归：编辑弹窗只改执行时间时，不能被判「模板类型创建后不可修改」。

        前端 PUT 只发 name/role_ids/remind_at/deadline/text_tpl/spec/enabled —— 界面里 kind、period
        是锁定项，永远不发。而后端一旦在 payload 里看到 kind/period 就当作「要改类型/周期」。
        所以前端映射层不得再无脑补 kind='notice'、period='day'（旧实现正是这样，导致上传类
        模板一编辑就 400）。通知类模板则必须把界面上唯一的「执行时间」映射进 notice_times。
        """
        rid = self.store.create_role("片长/分支局长", actor="admin")["role_id"]
        upload_tpl = self.store.create_template(
            {
                "name": "外出上门通报", "kind": "form", "role_ids": [rid],
                "schema_json": [{"key": "visit_count", "label": "今日累计上门户数", "type": "number", "required": True}],
                "text_tpl": "请完成今日上门数据", "remind_at": "09:30", "deadline": "18:00",
                "period": "day", "need_review": True, "enabled": True,
                "data_keys": [{"key": "kpi_1", "label": "当日关键指标", "unit": "户"}],
            },
            actor="admin",
        )
        updated = self.store.update_template(
            upload_tpl["template_id"],
            {
                "name": "外出上门通报", "role_ids": [rid], "remind_at": "14:20", "deadline": "18:00",
                "text_tpl": "请完成今日上门数据", "enabled": True,
            },
            actor="admin",
        )
        self.assertEqual((updated["kind"], updated["period"], updated["remind_at"]), ("form", "day", "14:20"))
        self.assertEqual(updated["data_keys"], [{"key": "kpi_1", "label": "当日关键指标", "unit": "户"}])

        notice_tpl = self.store.create_template(
            {"name": "春促·晨会安排", "kind": "notice", "role_ids": [rid],
             "text_tpl": "今日晨会 08:15 开始", "notice_times": ["08:15"]},
            actor="admin",
        )
        self.assertEqual((notice_tpl["notice_times"], notice_tpl["remind_at"]), (["08:15"], "08:15"))
        edited = self.store.update_template(
            notice_tpl["template_id"],
            {"name": "春促·晨会安排", "role_ids": [rid], "remind_at": "09:05",
             "text_tpl": "今日晨会 09:05 开始", "notice_times": ["09:05"], "enabled": True},
            actor="admin",
        )
        self.assertEqual((edited["notice_times"], edited["remind_at"]), (["09:05"], "09:05"))

    def test_r43_member_import_dry_run_and_commit(self) -> None:
        rid = self.store.create_role("渠道经理", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13800138001", "张三", "一部", actor="admin")
        bind_activated_at(self.store, "13800138001", "13800138001")
        # 2026-11-03：部门必须命中后台配置的部门选项
        for dept in ("一部", "二部", "三部", "四部", "五部"):
            self.store.create_department(dept, actor="admin")

        csv_rows = [
            {"name": "张三", "phone": "13800138001", "dept": "一部"},
            {"name": "李四", "phone": "13800138002", "dept": "二部"},
            {"name": "王五", "phone": "13800138003", "dept": "三部"},
            {"name": "赵六", "phone": "12345", "dept": "四部"},
            {"name": "", "phone": "13800138005", "dept": "五部"},
            {"name": "李四重复", "phone": "13800138002", "dept": "二部"},
            None,
        ]

        diff = self.store.import_role_members(rid, csv_rows, dry_run=True, actor="admin")
        # 2026-11-09 新口径：与已添加成员重复的手机号**要报错**（不再静默跳过），
        # 且「待修复后才全量导入成功」= 有错误行就整批不写入。
        self.assertEqual((diff["ok"], diff["dry_run"]), (True, True))
        self.assertEqual(diff["will_add"], 0, diff)          # 2026-11-09 新口径：整批拒绝 → will_add 归零
        self.assertEqual(diff["already_exists"], 2, diff)     # 张三（名册已有）+ 李四重复（批次内重复）
        self.assertEqual(len(diff["invalid"]), 5, diff)       # 与名册重复 + 手机号格式 + 姓名空 + 批次内重复 + None 行
        # 2026-11-09：invalid 列表的**顺序变了**（新增的「与名册重复」检测在收集阶段，
        # 排在格式错误之前）→ 按**行号**取断言，不依赖下标顺序。
        _by_row = {x["row"]: x for x in diff["invalid"]}
        self.assertEqual((_by_row[4]["phone"], _by_row[4]["reason"]), ("12345", "手机号格式不正确"))
        self.assertEqual((_by_row[5]["row"], _by_row[7]["row"]), (5, 7))
        self.assertTrue(any(x["row"] == 1 and "重复" in x["reason"] for x in diff["invalid"]), diff["invalid"])
        self.assertEqual(self.store.list_role_members(rid)["total"], 1)

        # 有错误行 → 整批不写入（待修复后再导）
        commit_res = self.store.import_role_members(rid, csv_rows, dry_run=False, actor="admin")
        self.assertEqual((commit_res["dry_run"], commit_res["will_add"]), (False, 0))
        self.assertTrue(commit_res["blocked"], commit_res)
        self.assertEqual(self.store.list_role_members(rid)["total"], 1)  # 一行都没写入

    def test_r19_unique_instance_across_roles_and_scheduler(self) -> None:
        r1 = self.store.create_role("部门长", actor="admin")["role_id"]
        r2 = self.store.create_role("项目组长", actor="admin")["role_id"]

        self.store.add_role_member(r1, "13900139001", "陈一", "研发部", actor="admin")
        self.store.add_role_member(r2, "13900139001", "陈一", "研发部", actor="admin")
        self.store.add_role_member(r2, "13900139002", "林二", "产品部", actor="admin")
        bind_activated_at(self.store, "13900139001", "3000000000000000001")

        tpl = self.store.create_template(
            {
                "name": "日报提交", "kind": "image", "role_ids": [r1, r2], "allow_note": True,
                "text_tpl": "{姓名}（{部门}），请提交{周期}{任务名}，截止{截止时间}。",
                "remind_at": "17:30", "deadline": "18:00", "period": "day", "need_review": False, "enabled": True,
            },
            actor="admin",
        )
        tid = tpl["template_id"]
        self.assertEqual(self.store.preview_template_targets(tid), {"member_count": 2, "activated_count": 1, "unactivated_count": 1})

        notifier = FakeNotifier(now_fn=lambda: self.current_dt)
        scheduler = Scheduler(state=self.store, public_base_url="https://task.example.com", notifier=notifier, now_fn=lambda: self.current_dt)

        self.current_dt = make_dt("2026-10-25 17:00:00")
        # 2026-10-29：上传任务也改为「当天一进来就按人预生成实例」，未到点只建行不发送
        self.assertEqual(scheduler.run_once()["generated_instances"], 1)  # 2026-11-09 新口径：未激活成员不生成实例
        self.assertEqual(len(notifier.sent_messages), 0)

        self.current_dt = make_dt("2026-10-25 17:30:00")
        s2 = scheduler.run_once()
        self.assertEqual((s2["generated_instances"], s2["sent"], s2["skipped"]), (0, 1, 0))  # 2026-11-09 新口径：未激活不建实例 → 无 skipped
        self.assertEqual(len(notifier.sent_messages), 1)
        self.assertIn("陈一（研发部）", notifier.sent_messages[0].text)
        # 2026-11-13：截止文案统一具体日期（当天也不再显示「今天」）
        self.assertIn("10月25日 18:00", notifier.sent_messages[0].text)
        self.assertIn("https://task.example.com/h5/task#t=", notifier.sent_messages[0].text)
        self.assertNotIn("#t=preview", notifier.sent_messages[0].text)
        self.assertEqual(len(self.store.list_instances_for_period(tid, "2026-10-25")), 1)  # 2026-11-09 新口径

        self.current_dt = make_dt("2026-10-25 17:31:00")
        self.assertEqual(scheduler.run_once()["sent"], 0)
        self.assertEqual(len(notifier.sent_messages), 1)

    def test_weekly_and_monthly_anchor_schedule(self) -> None:
        rid = self.store.create_role("月报组", actor="admin")["role_id"]
        self.store.add_role_member(rid, "13700137001", "周三", "财务部", actor="admin")
        self.store.upsert_delivery_target("13700137001", "9000000000000000001")

        tpl = self.store.create_template(
            {
                "name": "月度结算表", "kind": "file", "role_ids": [rid],
                "text_tpl": "{姓名}请交{任务名}，截止{截止时间}",
                "remind_at": "09:00", "deadline": "18:00", "period": "month", "anchor_dom": 31, "deadline_offset": 2,
            },
            actor="admin",
        )
        notifier = FakeNotifier(now_fn=lambda: self.current_dt)
        scheduler = Scheduler(state=self.store, public_base_url="https://task.example.com", notifier=notifier, now_fn=lambda: self.current_dt)

        self.current_dt = make_dt("2026-02-27 09:00:00")
        self.assertEqual(scheduler.run_once()["generated_instances"], 0)

        self.current_dt = make_dt("2026-02-28 09:00:00")
        res_anchor = scheduler.run_once()
        self.assertEqual((res_anchor["generated_instances"], res_anchor["sent"]), (1, 1))

        insts = self.store.list_instances_for_period(tpl["template_id"], "2026-02")
        self.assertEqual(len(insts), 1)
        self.assertEqual(insts[0]["deadline_at"], "2026-03-02 18:00:00")


if __name__ == "__main__":
    unittest.main()
