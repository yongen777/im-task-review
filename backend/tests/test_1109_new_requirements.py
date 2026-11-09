# -*- coding: utf-8 -*-
"""2026-11-09 用户需求的专项用例（pytest 标准写法：test_ 函数 + assert）。

覆盖四条需求：
① 未激活成员不生成实例；停用成员不生成实例
② 当天激活只建激活时刻之后的实例；第二天才是完整一天
③ 删成员时本组模板下的实例全删（含已发送/已完成）；其它组不动
④ 导入重复/冲突整批拒绝；首次导入可改姓名部门并同步全局
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.state import StateStore  # noqa: E402


def _store() -> StateStore:
    s = StateStore(Path(tempfile.mkdtemp()) / "t.db")
    s.upsert_admin_user("admin", "Admin@2026", must_change=False)
    return s


def _role(s: StateStore, name: str, platform: str = "zdxlz") -> str:
    return s.create_role(name, actor="t", platform=platform)["role_id"]


def _notice_tpl(s: StateStore, role_ids, name: str = "通知") -> str:
    tpl = s.create_template({
        "name": name, "kind": "notice", "role_ids": list(role_ids),
        "text_tpl": "干活", "notice_times": ["09:00", "12:00"], "period": "day", "enabled": True,
    }, actor="t")
    return tpl["template_id"]


# ── ① 未激活 / 停用成员不生成实例 ─────────────────────────────
def test_unactivated_member_gets_no_instance():
    s = _store()
    try:
        rid = _role(s, "组")
        s.add_role_member(rid, "13700000001", "已激活", "市场部", actor="t")
        s.add_role_member(rid, "13700000002", "未激活", "市场部", actor="t")
        s.upsert_delivery_target("13700000001", "uid_1", platform="zdxlz")
        tpl = _notice_tpl(s, [rid])
        insts = s.ensure_template_instances(tpl, "2026-11-09", "2026-11-09 23:59:00")
        assert len(insts) == 1, insts
        assert insts[0]["phone"] == "13700000001"
    finally:
        s.close()


def test_disabled_member_gets_no_instance():
    s = _store()
    try:
        rid = _role(s, "组")
        s.add_role_member(rid, "13700000001", "已激活", "市场部", actor="t")
        s.upsert_delivery_target("13700000001", "uid_1", platform="zdxlz")
        s.conn.execute("UPDATE member SET enabled = 0 WHERE phone = '13700000001'")
        s.conn.commit()
        tpl = _notice_tpl(s, [rid])
        insts = s.ensure_template_instances(tpl, "2026-11-09", "2026-11-09 23:59:00")
        assert insts == [], insts
    finally:
        s.close()


# ── ② 当天激活只建激活时刻之后的实例；第二天完整 ───────────────
def test_same_day_activation_only_builds_after_trigger():
    s = _store()
    try:
        rid = _role(s, "组")
        s.add_role_member(rid, "13700000003", "当天激活", "市场部", actor="t")
        s.upsert_delivery_target("13700000003", "uid_3", platform="zdxlz",
                                 activated_at="2026-11-09 10:00:00")
        tpl = _notice_tpl(s, [rid])
        # 09:00 触发（激活前）→ 不建
        i9 = s.ensure_template_instances(tpl, "2026-11-09", "2026-11-09 09:00:00",
                                         now_str="2026-11-09 09:00:00", trigger_at="2026-11-09 09:00:00")
        assert i9 == [], i9
        # 12:00 触发（激活后）→ 建
        i12 = s.ensure_template_instances(tpl, "2026-11-09", "2026-11-09 12:00:00",
                                          now_str="2026-11-09 12:00:00", trigger_at="2026-11-09 12:00:00")
        assert len(i12) == 1, i12
    finally:
        s.close()


def test_next_day_gets_full_day():
    s = _store()
    try:
        rid = _role(s, "组")
        s.add_role_member(rid, "13700000003", "当天激活", "市场部", actor="t")
        s.upsert_delivery_target("13700000003", "uid_3", platform="zdxlz",
                                 activated_at="2026-11-09 10:00:00")
        tpl = _notice_tpl(s, [rid])
        s.conn.execute("DELETE FROM task_instance")
        s.conn.commit()
        # 第二天 → 09:00 也建（完整一天）
        i2 = s.ensure_template_instances(tpl, "2026-11-10", "2026-11-10 09:00:00",
                                         now_str="2026-11-10 09:00:00", trigger_at="2026-11-10 09:00:00")
        assert len(i2) == 1, i2
    finally:
        s.close()


# ── ③ 删成员 → 本组模板下实例全删（含已发送）；其它组不动 ──────
def test_remove_member_deletes_terminal_instances_of_that_role():
    s = _store()
    try:
        ra = _role(s, "A组")
        rb = _role(s, "B组")
        s.add_role_member(ra, "13700000004", "人", "部", actor="t")
        s.add_role_member(rb, "13700000004", "人", "部", actor="t")
        s.upsert_delivery_target("13700000004", "uid_4", platform="zdxlz")
        tpl_a = _notice_tpl(s, [ra], "仅A")
        tpl_ab = _notice_tpl(s, [ra, rb], "A+B")
        s.ensure_template_instances(tpl_a, "2026-11-09", "2026-11-09 23:59:00")
        s.ensure_template_instances(tpl_ab, "2026-11-09", "2026-11-09 23:59:00")
        s.conn.execute("UPDATE task_instance SET status = 'sent' WHERE template_id = ?", (tpl_a,))
        s.conn.commit()

        s.delete_role_member(ra, "13700000004", actor="t")
        left_a = s.conn.execute("SELECT COUNT(*) FROM task_instance WHERE template_id = ?", (tpl_a,)).fetchone()[0]
        assert left_a == 0, "本组模板下的已发送实例也应删除"
        left_ab = s.conn.execute("SELECT COUNT(*) FROM task_instance WHERE template_id = ?", (tpl_ab,)).fetchone()[0]
        assert left_ab == 1, "还在其它组的共用模板实例要保留"
    finally:
        s.close()


# ── ④ 导入：重复整批拒绝 + 首次导入可改姓名部门并同步全局 ──────
def test_import_duplicate_phone_blocks_whole_batch():
    s = _store()
    try:
        rid = _role(s, "组")
        s.add_role_member(rid, "13700000005", "张三", "市场部", actor="t")
        res = s.import_role_members(rid, [{"row": 1, "phone": "13700000005", "name": "李四", "dept": "渠道部"}],
                                    dry_run=False, actor="t")
        assert res.get("blocked") is True, res
        assert res.get("will_add") == 0, res
        assert any("重复" in (x.get("reason") or "") for x in res.get("invalid") or []), res
        n = s.conn.execute("SELECT COUNT(*) FROM member WHERE role_id = ?", (rid,)).fetchone()[0]
        assert n == 1, n
    finally:
        s.close()


def test_first_import_can_update_profile_and_syncs_globally():
    s = _store()
    try:
        rid = _role(s, "组")
        rid2 = _role(s, "组2")
        s.add_role_member(rid, "13700000006", "张三", "市场部", actor="t")
        res = s.import_role_members(rid2, [{"row": 1, "phone": "13700000006", "name": "张三改", "dept": "新部门"}],
                                     dry_run=False, actor="t")
        assert res.get("blocked") is False, res
        assert int(res.get("synced_global_rows") or 0) > 0, res
        rows = [dict(r) for r in s.conn.execute(
            "SELECT role_id, name, dept FROM member WHERE phone = '13700000006'")]
        assert len(rows) == 2, rows
        assert all(r["name"] == "张三改" for r in rows), rows
        assert all(r["dept"] == "新部门" for r in rows), rows
    finally:
        s.close()


# ── 激活判定按「角色所属平台」（不是当前通道）──────────────────
def test_activation_checked_against_role_platform_not_current_channel():
    s = _store()
    try:
        rid = _role(s, "企微组", platform="wecom")
        s.add_role_member(rid, "13700000007", "只在企微", "市场部", actor="t")
        s.upsert_delivery_target("13700000007", "wx_uid", platform="wecom")
        tpl = _notice_tpl(s, [rid])
        # 当前通道是 zdxlz（默认），但模板属于 wecom 角色 → 应按 wecom 判定激活
        assert s.platform == "zdxlz"
        insts = s.ensure_template_instances(tpl, "2026-11-09", "2026-11-09 23:59:00")
        assert len(insts) == 1, insts
    finally:
        s.close()
