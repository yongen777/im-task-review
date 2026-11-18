# -*- coding: utf-8 -*-
"""2026-11-12 晚 · 不定期任务（菜单自选 · Pull 模式）+ 角色删除引用加固 专项用例。

需求映射：
  一  无推送时刻（调度物理隔离）/ 无截止（永不逾期、H5 不显示截止）/【菜单】唯一触发词 /
     数字回复即刻签发 / 提交即焚、支持多次；
  二  三个交互场景的精确文案；
  三  未在册零写入红线 / 平台天然隔离 / 角色权限约束 / 后台配置互斥（推送/截止强制置空）；
  四  角色删除引用校验加固（JSON 数组与逗号分隔统一提取，100% 拦截误删）；
  五  提交后链接作废（刷新只显示「您已完成本次提交」）、再回复数字拿全新链接。

全部相对时刻（模块级 CLOCK），不碰 data/im_task.db。
"""
from __future__ import annotations

import re
import sys
import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402

from app.api.events import handle_inbound_text  # noqa: E402
from app.api.h5 import process_h5_task_info  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import (  # noqa: E402
    NO_DEADLINE_SENTINEL,
    BizError,
    StateStore,
    parse_role_ids,
)

P = "13900007001"
P2 = "13900007002"
UID = "uid_zdxlz_7001"
UID2 = "uid_zdxlz_7002"
CLOCK = {"now": datetime(2026, 11, 12, 19, 30, 0)}


def _store() -> StateStore:
    return StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_adhoc_")), now_fn=lambda: CLOCK["now"])


def _seed_zdxlx(store: StateStore, phone: str = P, uid: str = UID, name: str = "王五") -> str:
    rid = store.create_role("密信巡查组", actor="t", platform="zdxlz")["role_id"]
    store.add_role_member(rid, phone, name, "安全部", actor="t")
    store.upsert_delivery_target(phone, uid, platform="zdxlz")
    return rid


def _adhoc_tpl(store: StateStore, rid: str, name: str = "现场安全隐患排查上报", *,
               enabled: bool = True, kind: str = "form") -> dict:
    return store.create_template(
        {
            "name": name, "kind": kind, "period": "day", "role_ids": [rid], "is_adhoc": True,
            "remind_at": "09:00", "deadline": "18:00", "has_deadline": True,
            "deadline_type": "same_day", "deadline_time": "18:00",
            "exec_weekdays": [1, 2, 3, 4, 5], "exec_dom_days": [],
            "text_tpl": "请完成隐患排查上报",
            "schema_json": [{"key": "summary", "label": "隐患说明", "type": "text", "required": True}],
            "spec": {"upload_modules": ["fields", "text"],
                     "fields": [{"key": "summary", "label": "隐患说明", "type": "text", "required": True}]},
            "enabled": enabled,
        },
        actor="t",
    )


def _sent() -> "list[tuple[str, str]]":
    out: "list[tuple[str, str]]" = []
    return out


def _run(store: StateStore, user_id: str, msg: str, base_url: str = "http://t.example.com"):
    box = _sent()

    def send(text: str, tag: str) -> None:
        box.append((text, tag))

    code, body = handle_inbound_text(
        state=store, user_id=user_id, msg_text=msg, send=send, platform="zdxlz", base_url=base_url,
    )
    return code, body, box


def _row_count(store: StateStore, table: str, where: str = "1=1", params: tuple = ()) -> int:
    with store.lock:
        return int(store.conn.execute(f"SELECT COUNT(1) FROM {table} WHERE {where}", params).fetchone()[0])


# ════════════════════ 一：调度物理隔离 + push_now 拒绝 ════════════════════

def test_adhoc_scheduler_physical_isolation_and_push_now_refused() -> None:
    """adhoc 模板：定时调度器连跑 3 轮零消息零实例（物理隔离）；push_now 明确拒绝。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid)
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sch = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                        now_fn=lambda: CLOCK["now"])
        for _ in range(3):  # 跨执行日/提醒时点多轮
            CLOCK["now"] = CLOCK["now"] + timedelta(seconds=30)
            stats = sch.run_once()
            assert stats["generated_instances"] == 0 and stats["sent"] == 0, stats
        assert len(ntf.sent_messages) == 0, ntf.sent_messages
        assert _row_count(store, "task_instance") == 0, "adhoc 模板绝不允许调度层生成实例"
        # 调度取数口径：list_active_templates 默认不含 adhoc
        assert all(not t.get("is_adhoc") for t in store.list_active_templates(include_notice=True))
        # push_now（一键推送）对 adhoc 明确拒绝
        tpl = [t for t in store.list_templates()["items"] if t.get("is_adhoc")][0]
        try:
            sch.push_now(tpl["template_id"], actor="t")
            raise AssertionError("adhoc 模板必须拒绝手动推送")
        except BizError as exc:
            assert (exc.status_code, exc.code) == (400, "VALIDATION_FAILED"), exc
            assert "个人工作台" in exc.message, exc.message
    finally:
        store.close()


def test_adhoc_never_overdue_and_h5_hides_deadline() -> None:
    """adhoc 实例 deadline=哨兵：advance 到 2099 年也不逾期；H5 徽章恒「截止 不限」。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        tpl = _adhoc_tpl(store, rid)
        # 2026-11-18：工作台点击事项调用同一签发服务，文本数字已下线。
        raw = store.issue_adhoc_report(tpl["template_id"], P, platform="zdxlz")
        assert _row_count(store, "task_instance") == 0
        # 100 年后也不流转 overdue/expired
        stats = store.advance_instance_statuses(datetime(2099, 1, 1))
        assert stats["to_overdue"] == 0, stats
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200 and info["window_open"] is True, (code, info)
        assert info["instance"]["deadline_at"] == NO_DEADLINE_SENTINEL
        assert info["template"]["is_adhoc"] is True
        assert info["published_at"] is None
        assert info["template"]["deadline_rule"] == "不限", info["template"]["deadline_rule"]
        assert info["template"]["has_deadline"] is False
    finally:
        store.close()


# ════════════════════ 二：三个交互场景的精确文案 ════════════════════

def test_menu_scenario_texts_exact():
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid, "隐患上报")
        for text in ("你好", "菜单", "1", "99"):
            _, body, box = _run(store, UID, text)
            assert body['path'] == 'portal'
            assert box[0][0].startswith('【德阳量密助手】您好，请点击下方链接进入您的个人任务工作台：')
            assert '/h5/portal#t=' in box[0][0]
            assert '回复' not in box[0][0]
        assert _row_count(store, 'task_instance') == 0
    finally:
        store.close()


def test_menu_scenario2_empty_list_text():
    store = _store()
    try:
        _seed_zdxlx(store)
        _, body, box = _run(store, UID, "菜单")
        assert body['path'] == 'portal'
        assert '/h5/portal#t=' in box[0][0]
        assert store.list_adhoc_menu_items(P, platform='zdxlz') == []
    finally:
        store.close()


# ════════════════════ 三：平台隔离 / 角色权限 / 未在册零写入 / 互斥校验 ════════════════════

def test_menu_platform_isolation_and_role_scope():
    store = _store()
    try:
        rz = _seed_zdxlx(store)
        rw = store.create_role('企微报备组', platform='wecom')['role_id']
        store.add_role_member(rw, P2, '赵六')
        store.upsert_delivery_target(P2, 'wecom_user', platform='wecom')
        z = _adhoc_tpl(store, rz, '密信事项')
        w = _adhoc_tpl(store, rw, '企微事项')
        assert [t['template_id'] for t in store.list_adhoc_menu_items(P, platform='zdxlz')] == [z['template_id']]
        assert [t['template_id'] for t in store.list_adhoc_menu_items(P2, platform='wecom')] == [w['template_id']]
        with pytest.raises(BizError):
            store.issue_adhoc_report(w['template_id'], P, platform='zdxlz')
    finally:
        store.close()


def test_menu_unregistered_zero_writes() -> None:
    """未在册用户发【菜单】/数字 → 统一权限文案，零写入（member/实例/令牌/绑定全不变）。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid)
        before = (_row_count(store, "task_instance"), _row_count(store, "h5_token"),
                  _row_count(store, "member"), _row_count(store, "delivery_target"))
        for msg in ("菜单", "1", "你好"):
            _, body, box = _run(store, "uid_stranger_x", msg)
            assert body["bound"] is False, body
            assert "尚未录入您的业务权限" in box[0][0], box[0][0]
        after = (_row_count(store, "task_instance"), _row_count(store, "h5_token"),
                 _row_count(store, "member"), _row_count(store, "delivery_target"))
        assert before == after, (before, after)
    finally:
        store.close()


def test_adhoc_template_interlock_forces_blank_schedule_and_deadline() -> None:
    """后台互斥：is_adhoc=1 → 推送时刻/截止/执行日强制置空；notice+adhoc 直接 400。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        tpl = _adhoc_tpl(store, rid, "带截止的不定期")  # payload 故意带 remind_at/deadline/执行日
        assert tpl["is_adhoc"] is True
        assert tpl["text_tpl"] == ""
        assert tpl["remind_at"] is None, tpl["remind_at"]
        assert tpl["has_deadline"] is False
        assert tpl["deadline_time"] is None and tpl["deadline_type"] is None
        assert not tpl["deadline"], tpl["deadline"]
        assert not tpl["exec_weekdays"] and not tpl["exec_dom_days"], (tpl["exec_weekdays"], tpl["exec_dom_days"])
        # 编辑切换回普通模式：恢复执行时间/截止的显式配置
        upd = store.update_template(tpl["template_id"], {
            "is_adhoc": False, "remind_at": "09:00", "has_deadline": True, "text_tpl": "请按时填报",
            "deadline_type": "same_day", "deadline_time": "18:00", "exec_weekdays": [1],
        }, actor="t")
        assert upd["is_adhoc"] is False and upd["remind_at"] == "09:00" and upd["has_deadline"] is True
        try:
            store.create_template({
                "name": "通知混菜单", "kind": "notice", "period": "day", "role_ids": [rid],
                "is_adhoc": True, "text_tpl": "x",
                "notice_times": ["08:00"], "spec": {"notice_items": []},
            }, actor="t")
            raise AssertionError("notice + adhoc 必须拒绝")
        except BizError as exc:
            assert (exc.status_code, exc.code) == (400, "VALIDATION_FAILED"), exc
            assert "自主上报" in exc.message, exc.message
    finally:
        store.close()


# ════════════════════ 五：提交即焚 + 多次上报 + 冷却防刷 ════════════════════

def test_adhoc_one_shot_link_and_multi_report() -> None:
    """链接一次性：提交成功后刷新只显示「您已完成本次提交」、再提交终审拦截；
    再次回复数字 → 全新实例 + 全新链接，可继续上报（一人多次上报）。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid, "隐患上报")

        def _issue(n: int = 1) -> str:
            item = store.list_adhoc_menu_items(P, platform="zdxlz")[n - 1]
            return store.issue_adhoc_report(item["template_id"], P, platform="zdxlz")

        raw1 = _issue(1)
        res = store.submit_h5_task(raw_token=raw1, fields={"summary": "3 号楼消防通道堵塞"}, note="现场情况说明")
        assert res["status"] == "approved"  # 默认无需审核
        # 刷新同一链接：只显示完成提示（window_open=False），页面不再渲染可编辑表单
        code, info = process_h5_task_info(state=store, raw_token=raw1)
        assert code == 200 and info["window_open"] is False, (code, info)
        assert info["window_reason"] == "您已完成本次提交", info["window_reason"]
        # 同链接二次提交 → 终审统一拦截（不覆盖历史提交）
        try:
            store.submit_h5_task(raw_token=raw1, fields={"summary": "想再交"})
            raise AssertionError("一次性链接禁止二次提交")
        except BizError as exc:
            assert (exc.status_code, exc.code, exc.message) == (400, "ALREADY_SUBMITTED", "已上传，请勿重复提交"), exc
        assert _row_count(store, "submission") == 1

        # 上一次已提交后可以立即再次打开；新表单成功提交前没有新实例。
        raw2 = _issue(1)
        assert raw2 != raw1
        assert _row_count(store, "task_instance") == 1
        res2 = store.submit_h5_task(raw_token=raw2, fields={"summary": "地库渗水"}, note="现场情况说明")
        assert res2["status"] == "approved"
        assert _row_count(store, "submission") == 2
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 19, 30, 0)
        store.close()


def test_adhoc_two_devices_can_open_without_creating_instances():
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        tpl = _adhoc_tpl(store, rid)
        pc = store.issue_adhoc_report(tpl['template_id'], P, platform='zdxlz')
        mobile = store.issue_adhoc_report(tpl['template_id'], P, platform='zdxlz')
        assert pc != mobile
        for token in (pc, mobile):
            assert process_h5_task_info(state=store, raw_token=token)[1]['window_open'] is True
        with pytest.raises(BizError):
            store.submit_h5_task(raw_token=mobile, fields={})
        assert _row_count(store, 'task_instance') == 0
        assert _row_count(store, 'submission') == 0
        assert store.list_pending_todo(P, platform='zdxlz') == []
    finally:
        store.close()


# ════════════════════ 四：角色删除引用校验加固 ════════════════════

def test_role_delete_reference_hardening() -> None:
    """删除角色时规范化历史引用，并保留模板和其他完整角色 ID。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid, "正常引用")
        with store.lock, store.conn:
            store.conn.execute(
                "INSERT INTO task_template(template_id, name, kind, role_ids, text_tpl, period, created_by, created_at, updated_at, owner_id) "
                "VALUES ('t_dirty', '脏数据模板', 'form', ?, 'x', 'day', 't', '2026-11-12 19:00:00', '2026-11-12 19:00:00', 1)",
                (f"{rid},r_abc123,{rid}ff,",),
            )
        res = store.delete_role(rid, actor="t")
        assert res["ok"] is True and res["deleted_members"] == 1, res
        templates = {t["name"]: t["role_ids"] for t in store.list_templates()["items"]}
        assert templates["脏数据模板"] == ["r_abc123", f"{rid}ff"]
        assert templates["正常引用"] == []
    finally:
        store.close()


def test_parse_role_ids_variants() -> None:
    """规范解析纯函数：JSON 数组 / 逗号 / 分号 / 混杂脏数据统一提取。"""
    assert parse_role_ids('["r_abc123","r_def456"]') == ["r_abc123", "r_def456"]
    assert parse_role_ids("r_abc123,r_def456") == ["r_abc123", "r_def456"]
    assert parse_role_ids("r_abc123, r_def456;") == ["r_abc123", "r_def456"]
    assert parse_role_ids("【r_abc123】r_def456") == ["r_abc123", "r_def456"]
    assert parse_role_ids("") == [] and parse_role_ids(None) == []
    assert parse_role_ids(["r_a", "r_b"]) == ["r_a", "r_b"]  # 已是列表
