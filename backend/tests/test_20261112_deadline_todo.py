# -*- coding: utf-8 -*-
"""2026-11-12 · E 批专项用例（E1 截止与Token / E2 门禁 / E3 下线补交 / E4【待办】/ 外加停用删除口径）。

E1 —— db + scheduler：has_deadline / deadline_type(same_day|weekend|month_end) / deadline_time；
    无截止存哨兵 9999-12-31 23:59:59（展示层「不限」）；Token 有效期 = min(截止+24h, 720h)，
    无截止 = 720h；提交后即刻 consumed=1。
E2 —— H5 三道门禁：token 已消耗只读、截止硬拦截统一提示
    「当前任务已过期（截止时间：{deadline_at}），无法提交」（不区分链接过期与任务截止）。
E3 —— 「申请补交」全链路彻底下线（rejected 驳回重交保留）。
E4 —— 私聊【待办】：剥【】[] 匹配「待办/我的任务/未完成」→ 查 pending|rejected 未截止
    （封顶 5 条）逐项签发动态 TTL 链接；常规 ack 末尾追加【待办】指引。
外加 —— 停用模板已下发链接仍可提交；删除模板后链接一律关（含 rejected 挂起中的实例）。

全部相对时刻（模块级 CLOCK），不碰 data/im_task.db。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.events import handle_inbound_text  # noqa: E402
from app.api.h5 import process_h5_task_info, process_h5_task_submit  # noqa: E402
from app.config import format_dt  # noqa: E402
from app.scheduler import Scheduler, FakeNotifier, calc_instance_deadline  # noqa: E402
from app.state import (  # noqa: E402
    NO_DEADLINE_SENTINEL,
    BizError,
    StateStore,
    calc_h5_token_ttl_hours,
    effective_instance_deadline,
)

PHONE = "13900001001"
CLOCK = {"now": datetime(2026, 11, 12, 9, 0, 0)}
UNIFIED_PREFIX = "当前任务已过期（截止时间："


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_e_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _sched(store: StateStore, notifier: FakeNotifier | None = None) -> Scheduler:
    ntf = notifier or FakeNotifier(now_fn=lambda: CLOCK["now"])
    return Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                     now_fn=lambda: CLOCK["now"])


def _seed_member(store: StateStore, platform: str = "zdxlz", phone: str = PHONE) -> str:
    """建「角色 + 名册 + 平台绑定」，返回 role_id（实例生成与【待办】都依赖激活态）。"""
    plat_name = {"zdxlz": "测试组", "wecom": "企微组"}[platform]
    rid = store.create_role(plat_name, actor="t", platform=platform)["role_id"]
    store.add_role_member(rid, phone, "张三", "市场部", actor="t")
    store.upsert_delivery_target(phone, f"uid_{platform}_{phone[-4:]}", platform=platform)
    return rid


def _form_tpl(store: StateStore, rid: str, name: str = "日报", *, has_deadline: bool = True,
              deadline_type: str | None = "same_day", deadline_time: str | None = "18:00",
              legacy_deadline: str = "18:00", need_review: bool = False) -> dict:
    return store.create_template(
        {
            "name": name, "kind": "upload", "role_ids": [rid], "period": "day",
            "remind_at": "09:00", "deadline": legacy_deadline if deadline_time is None or deadline_time == "" else deadline_time,
            "has_deadline": has_deadline, "deadline_type": deadline_type if has_deadline else None,
            "deadline_time": deadline_time if has_deadline else None,
            "text_tpl": "请提交", "need_review": need_review, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"upload_modules": ["fields"], "fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        },
        actor="t",
    )


def _ensure_today(store: StateStore, tpl: dict, deadline: str, pkey: str = "2026-11-12") -> dict:
    return store.ensure_template_instances(tpl["template_id"], pkey, deadline)[0]


# ════════════════════ E1：calc_instance_deadline 纯函数 ════════════════════

def test_e1_calc_instance_deadline_matrix() -> None:
    """2026-11-12 为周四：当天/本周日(11-15)/本月月底(11-30)/无截止哨兵/旧模板回落。"""
    anchor = datetime(2026, 11, 12, 9, 0, 0)
    assert calc_instance_deadline({"has_deadline": 0}, anchor) == NO_DEADLINE_SENTINEL
    assert calc_instance_deadline({"has_deadline": False}, anchor) == NO_DEADLINE_SENTINEL
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "same_day", "deadline_time": "18:00"}, anchor
    ) == "2026-11-12 18:00:00"
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "weekend", "deadline_time": "20:30"}, anchor
    ) == "2026-11-15 20:30:00"  # 周四所在 ISO 周的周日 = 11-15
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "month_end", "deadline_time": "23:59"}, anchor
    ) == "2026-11-30 23:59:00"
    # deadline_time 缺省回落旧列 deadline（HH:MM）
    assert calc_instance_deadline(
        {"has_deadline": 1, "deadline_type": "same_day", "deadline": "17:45"}, anchor
    ) == "2026-11-12 17:45:00"
    # 旧模板（无 deadline_type）沿用 legacy 算法
    assert calc_instance_deadline(
        {"has_deadline": None, "deadline_type": None, "deadline": "18:00",
         "deadline_offset": 0, "period": "day"}, anchor
    ) == "2026-11-12 18:00:00"


def test_e1_invalid_deadline_params_rejected() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        # 非法 deadline_type
        try:
            store.create_template(
                {"name": "坏类型", "kind": "upload", "role_ids": [rid], "remind_at": "09:00",
                 "deadline": "18:00", "has_deadline": True, "deadline_type": "friday", "deadline_time": "18:00",
                 "text_tpl": "x", "period": "day"}, actor="t",
            )
            raise AssertionError("非法 deadline_type 必须被 400 拦下")
        except BizError as exc:
            assert exc.status_code == 400 and "deadline_type" in exc.message
        # 有截止但缺少任何时刻来源
        try:
            store.create_template(
                {"name": "缺时刻", "kind": "upload", "role_ids": [rid], "remind_at": "09:00",
                 "has_deadline": True, "text_tpl": "x", "period": "day"}, actor="t",
            )
            raise AssertionError("缺少截止时刻必须被 400 拦下")
        except BizError as exc:
            assert exc.status_code == 400 and "HH:MM" in exc.message, exc.message
        # 无截止时不再要求 remind_at < deadline
        ok_tpl = store.create_template(
            {"name": "顺序自由", "kind": "upload", "role_ids": [rid], "remind_at": "20:00",
             "deadline": "08:00", "has_deadline": False, "text_tpl": "x", "period": "day"}, actor="t",
        )
        assert ok_tpl["has_deadline"] is False
    finally:
        store.close()


# ════════════════════ E1：模板三件套落库与部分更新 ════════════════════

def test_e1_template_deadline_fields_roundtrip() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        t1 = _form_tpl(store, rid, "无截止", has_deadline=False)
        assert t1["has_deadline"] is False and t1["deadline_type"] is None and t1["deadline_time"] is None
        assert t1["deadline"] is None  # 旧列也置空，老链路一致

        t2 = _form_tpl(store, rid, "周任务", deadline_type="weekend", deadline_time="20:30")
        assert t2["has_deadline"] is True and t2["deadline_type"] == "weekend"
        assert t2["deadline_time"] == "20:30" and t2["deadline"] == "20:30"  # 旧列同步

        # 部分更新：不带 has_deadline 键 → 保留原值；显式切换 → 三件套整体转变
        t2b = store.update_template(t2["template_id"], {"name": "周任务改"}, actor="t")
        assert t2b["has_deadline"] is True and t2b["deadline_type"] == "weekend" and t2b["deadline_time"] == "20:30"
        t2c = store.update_template(t2["template_id"], {"has_deadline": False}, actor="t")
        assert t2c["has_deadline"] is False and t2c["deadline_type"] is None and t2c["deadline_time"] is None
        assert t2c["deadline"] is None
    finally:
        store.close()


# ════════════════════ E1：无截止任务全链路（哨兵 + 720h + 永不逾期） ════════════════════

def test_e1_no_deadline_workflow_sentinel_and_720h() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "无截止任务", has_deadline=False)
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sched = _sched(store, ntf)
        stats = sched.run_once()
        assert stats["sent"] == 1
        # 提醒文案：截止时间渲染「不限」
        assert "截止时间：不限" in ntf.sent_messages[-1].text, ntf.sent_messages[-1].text

        inst = store.get_instance_by_key(tpl["template_id"], PHONE, "2026-11-12")
        assert inst["deadline_at"] == NO_DEADLINE_SENTINEL
        assert effective_instance_deadline(inst) == NO_DEADLINE_SENTINEL

        # 哨兵永不过期：推到极远 advance 也不流转
        store.advance_instance_statuses(datetime(2027, 12, 31, 23, 59, 0))
        inst = store.get_instance(inst["id"])
        assert inst["status"] == "pending", inst["status"]

        # 调度链签发的链接 TTL = 720h（30 天）
        raw_token = ntf.sent_messages[-1].text.split("#t=", 1)[1].strip()
        tok = store.get_h5_token(raw_token)
        assert tok["expires_at"] == format_dt(CLOCK["now"] + timedelta(hours=720)), tok["expires_at"]
        assert calc_h5_token_ttl_hours(inst, CLOCK["now"]) == 720.0

        # 到「未来」仍可提交，late_submit=False，提交后 token consumed=1
        CLOCK["now"] = datetime(2026, 12, 1, 10, 0, 0)
        code, info = process_h5_task_info(state=store, raw_token=raw_token)
        assert code == 200 and info["window_open"] is True, info.get("window_reason")
        res = store.submit_h5_task(raw_token=raw_token, fields={"summary": "补了"})
        assert res["late_submit"] is False
        assert store.get_h5_token(raw_token)["consumed"] == 1
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_e1_token_ttl_dynamic_strategy() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        # 截止在 2h 后 → TTL = 26h
        inst = _ensure_today(store, _form_tpl(store, rid, "短窗", deadline_time="11:00"), "2026-11-12 11:00:00")
        assert calc_h5_token_ttl_hours(inst, CLOCK["now"]) == 26.0
        # 截止在 30 天后 → 24h+30d=744h 封顶 720h
        inst2 = _ensure_today(
            store, _form_tpl(store, rid, "长窗", deadline_type="month_end", deadline_time="18:00"),
            "2026-12-12 18:00:00",
        )
        assert calc_h5_token_ttl_hours(inst2, CLOCK["now"]) == 720.0
        # 已过截止但仍在 +24h 宽限内 → TTL = 剩余宽限（口径：min(截止+24h, 720h) 从签发时刻起算）
        expired = {"deadline_at": "2026-11-12 10:00:00", "status": "overdue"}
        assert calc_h5_token_ttl_hours(expired, datetime(2026, 11, 12, 11, 0, 0)) == 23.0
        # 超出 +24h 宽限 → 保底 1h（页面还能打开看到过期提示）
        assert calc_h5_token_ttl_hours(expired, datetime(2026, 11, 13, 12, 0, 0)) == 1.0
        # None → 兜底 72h（函数签名兼容旧调用点）
        assert calc_h5_token_ttl_hours(None) == 72.0
    finally:
        store.close()


# ════════════════════ E2：到点统一过期提示（info + submit + 过期 token） ════════════════════

def test_e2_overdue_unified_message_and_submit_closed() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "当天截止", deadline_time="10:00")
        inst = _ensure_today(store, tpl, "2026-11-12 10:00:00")
        raw_token = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", inst["id"])

        # 10:05：过截止 → overdue
        CLOCK["now"] = datetime(2026, 11, 12, 10, 5, 0)
        adv = store.advance_instance_statuses(CLOCK["now"])
        assert adv["to_overdue"] == 1
        try:
            store.submit_h5_task(raw_token=raw_token, fields={"summary": "晚了"})
            raise AssertionError("到点后必须拒绝提交")
        except BizError as exc:
            assert exc.status_code == 400 and exc.code == "SUBMISSION_CLOSED", (exc.code, exc.message)
            assert exc.message == UNIFIED_PREFIX + "2026-11-12 10:00:00），无法提交", exc.message

        # info 端同样提示（H5 黄条）
        code, info = process_h5_task_info(state=store, raw_token=raw_token)
        assert code == 200
        assert info["window_open"] is False
        assert info["window_reason"] == UNIFIED_PREFIX + "2026-11-12 10:00:00），无法提交", info["window_reason"]

        # REST 层走 process_h5_task_submit → 同一文案
        code2, body2 = process_h5_task_submit(state=store, raw_token=raw_token, data={"payload": {"fields": {"summary": "x"}}})
        assert (code2, body2["code"]) == (400, "SUBMISSION_CLOSED")
        assert body2["message"].startswith(UNIFIED_PREFIX)

        # 链接本身过期（ttl 极短）→ TOKEN_EXPIRED 且文案同口径（不区分链接过期与任务截止）
        short = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", inst["id"], ttl_hours=0.01)
        CLOCK["now"] = datetime(2026, 11, 12, 10, 30, 0)
        code3, body3 = process_h5_task_info(state=store, raw_token=short)
        assert (code3, body3["code"]) == (401, "TOKEN_EXPIRED")
        assert body3["message"] == UNIFIED_PREFIX + "2026-11-12 10:00:00），无法提交", body3["message"]
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ E3：申请补交下线 + 逾期通知文案 ════════════════════

def test_e3_resubmit_channel_decommissioned() -> None:
    store = _store()
    try:
        # state 层函数已删除
        for name in ("request_resubmit", "approve_resubmit", "reject_resubmit", "list_overdue_in_window"):
            assert not hasattr(store, name), f"{name} 应随补交链路删除"
        # 「申请补交」文本不再有专门分支 → 落到带【待办】指引的常规 ack，且零写库
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "补交遗留", deadline_time="18:00")
        inst = _ensure_today(store, tpl, "2026-11-12 18:00:00")
        sent: list[tuple[str, str]] = []
        code, body = handle_inbound_text(
            state=store, user_id="uid_zdxlz_1001", msg_text="申请补交",
            send=lambda text, tag: sent.append((text, tag)), platform="zdxlz", base_url="http://e.com",
        )
        assert code == 200 and body["bound"] is True, body
        assert any(tag == "ack_nonphone" for _, tag in sent), sent
        assert any("如需重新获取未完成任务的提交链接，请回复【待办】。" in t for t, _ in sent), sent[0][0]
        # 「申请补交」不再出现在任何回复里（该指令已下线）
        assert all("申请补交" not in t for t, _ in sent), sent
        # resubmit_status 保持 NULL（零写库）
        assert store.get_instance(inst["id"])["resubmit_status"] is None
    finally:
        store.close()


def test_e3_scheduler_overdue_notice_pure_deadline_wording() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "逾期通知", deadline_time="10:00")
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sched = _sched(store, ntf)
        sched.run_once()
        assert ntf.sent_messages, "应有提醒"

        # 10:10（截止后 10 分钟）→ R14 逾期通知：纯截止告知，无任何补交字样
        CLOCK["now"] = datetime(2026, 11, 12, 10, 10, 0)
        sched.run_once()
        overdue_msgs = [m.text for m in ntf.sent_messages if "已逾期" in m.text]
        assert overdue_msgs, [m.text for m in ntf.sent_messages]
        assert "当前任务已过期（截止时间：2026-11-12 10:00:00），无法提交" in overdue_msgs[-1], overdue_msgs[-1]
        assert "申请补交" not in overdue_msgs[-1], overdue_msgs[-1]

        # 30s 内重复 tick：kind='overdue' 幂等（只发 1 条，不刷屏）
        CLOCK["now"] = datetime(2026, 11, 12, 10, 10, 30)
        sched.run_once()
        again = [m.text for m in ntf.sent_messages if "已逾期" in m.text]
        assert again == overdue_msgs, f"逾期通知重复发送: {again}"
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ E4：【待办】指令 ════════════════════

def _collect(sent: list[tuple[str, str]]):
    return lambda text, tag: sent.append((text, tag))


def test_e4_todo_command_basic_and_variants() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        t1 = _form_tpl(store, rid, "晨报", deadline_time="18:00")
        t2 = _form_tpl(store, rid, "晚报", deadline_time="19:30")
        _ensure_today(store, t1, "2026-11-12 18:00:00")
        _ensure_today(store, t2, "2026-11-12 19:30:00")

        for text in ("【待办】", "[待办]", " 待办 ", "我的任务", "未完成"):
            sent: list[tuple[str, str]] = []
            code, body = handle_inbound_text(
                state=store, user_id="uid_zdxlz_1001", msg_text=text,
                send=_collect(sent), platform="zdxlz", base_url="http://e.com",
            )
            assert code == 200 and body["path"] == "todo" and body["count"] == 2, body
            reply = sent[-1][0]
            assert "您当前有 2 项未完成的任务" in reply, reply
            for kw in ("晨报", "晚报", "提交链接：http://e.com/h5/task#t=", "今天 18:00", "今天 19:30"):
                assert kw in reply, reply

        # 签发的 token 有效期为动态值：晨报截止 18:00 → now 09:00 + min(9h+24h, 720h) = 33h
        rows = store.conn.execute(
            "SELECT expires_at FROM h5_token WHERE phone = ? AND template_id = ?",
            (PHONE, t1["template_id"]),
        ).fetchall()
        assert rows, "待办回复应签发新 token"
        assert rows[-1]["expires_at"] == format_dt(CLOCK["now"] + timedelta(hours=33)), dict(rows[-1])
    finally:
        store.close()


def test_e4_todo_scope_filters_and_cap() -> None:
    store = _store()
    try:
        # 平台隔离：同一手机号在两平台各有一条名册（AGENTS 跨平台用例口径）
        rid_z = _seed_member(store, "zdxlz")
        rid_w = _seed_member(store, "wecom")
        tz = _form_tpl(store, rid_z, "密信任务", deadline_time="18:00")
        tw = _form_tpl(store, rid_w, "企微任务", deadline_time="18:00")
        _ensure_today(store, tz, "2026-11-12 18:00:00")
        _ensure_today(store, tw, "2026-11-12 18:00:00")
        # 通知类不含；已截止的逾期不含；停用模板不含
        tn = store.create_template(
            {"name": "通知", "kind": "notice", "role_ids": [rid_z], "notice_times": ["12:00"],
             "remind_at": "12:00", "text_tpl": "通知正文", "period": "day", "enabled": True}, actor="t")
        _ensure_today(store, tn, "2026-11-12 23:59:00")
        tp = _form_tpl(store, rid_z, "已过截止", deadline_time="09:30")
        _ensure_today(store, tp, "2026-11-12 09:30:00")
        store.advance_instance_statuses(CLOCK["now"])  # 09:30 < 09:00? no-op（未过）
        CLOCK["now"] = datetime(2026, 11, 12, 9, 45, 0)
        store.advance_instance_statuses(CLOCK["now"])  # tp → overdue
        td = _form_tpl(store, rid_z, "已停用", deadline_time="18:00")
        inst_td = _ensure_today(store, td, "2026-11-12 18:00:00")
        store.update_template(td["template_id"], {"enabled": False}, actor="t")

        # 密信通道查：只有 tz（tw 归企微、tn 是 notice、tp 已逾期、td 已停用）
        items = store.list_pending_todo(PHONE, platform="zdxlz", now_dt=CLOCK["now"])
        assert [i["template_name"] for i in items] == ["密信任务"], [i["template_name"] for i in items]
        # 企微通道查：只有 tw；实例 id 不串
        items_w = store.list_pending_todo(PHONE, platform="wecom", now_dt=CLOCK["now"])
        assert [i["template_name"] for i in items_w] == ["企微任务"]
        assert items_w[0]["id"] != items[0]["id"]
        assert inst_td["id"] not in [i["id"] for i in items] + [i["id"] for i in items_w]

        # 封顶 5：再造成 8 条 pending（合计 9 条），limit=5 → 只回 5 条最早的
        # （⚠️ 反向验证教训：只造 5 条时用例是摆设——拆掉 LIMIT 守卫也是 5 条，测不出拦截）
        for i in range(8):
            _ensure_today(store, _form_tpl(store, rid_z, f"批量{i}", deadline_time=f"{12 + i}:00"),
                          f"2026-11-12 {12 + i}:00:00")
        got = store.list_pending_todo(PHONE, platform="zdxlz", now_dt=CLOCK["now"], limit=5)
        assert len(got) == 5, [(g["template_name"], g["deadline_at"]) for g in got]
        assert all(g["template_name"].startswith("批量") for g in got), [g["template_name"] for g in got]
        deadlines = [g["deadline_at"] for g in got]
        assert deadlines == sorted(deadlines), deadlines
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_e4_todo_rejected_counts_and_link_ttl_covers_rewindow() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "驳回重交", deadline_time="18:00", need_review=True)
        inst = _ensure_today(store, tpl, "2026-11-12 18:00:00")
        raw = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", inst["id"])
        store.submit_h5_task(raw_token=raw, fields={"summary": "初稿"})
        # 12:00 驳回 → 重交截止 = max(11-15 18:00, 11-13 12:00) = 11-15 18:00
        CLOCK["now"] = datetime(2026, 11, 12, 12, 0, 0)
        rej = store.review_instance(inst["id"], action="reject", reason="太糊", reviewer="admin")
        assert rej["status"] == "rejected"
        inst = store.get_instance(inst["id"])  # 重新取：本地 dict 还是 review 前的快照

        items = store.list_pending_todo(PHONE, platform="zdxlz", now_dt=CLOCK["now"])
        assert [i["template_name"] for i in items] == ["驳回重交"], items
        # 待办签发的链接 TTL 以重交截止为基准再 +24h：
        # 重交截止 = max(11-15 18:00, 11-13 12:00) = 11-15 18:00，
        # (11-16 18:00) - (11-12 12:00) = 102h → 链接寿命覆盖整个 78h 重交窗口。
        ttl = calc_h5_token_ttl_hours(inst, CLOCK["now"])
        assert ttl == 102.0, ttl
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_e4_todo_empty_reply_and_unbound_zero_writes() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        _ = rid
        sent: list[tuple[str, str]] = []
        code, body = handle_inbound_text(
            state=store, user_id="uid_zdxlz_1001", msg_text="待办",
            send=_collect(sent), platform="zdxlz", base_url="http://e.com",
        )
        assert code == 200 and body["path"] == "todo" and body["count"] == 0, body
        assert "您当前暂无待提交的任务" in sent[-1][0] and sent[-1][1].startswith("todo_"), sent

        # 未绑定用户：零落库红线（不建绑定、不落待归纳、不签 token）
        sent.clear()
        code2, body2 = handle_inbound_text(
            state=store, user_id="stranger_999", msg_text="【待办】",
            send=_collect(sent), platform="zdxlz", base_url="http://e.com",
        )
        assert code2 == 200 and body2["bound"] is False, body2
        assert any("尚未录入您的业务权限" in t for t, _ in sent), sent
        with store.lock:
            token_cnt = store.conn.execute("SELECT COUNT(1) FROM h5_token").fetchone()[0]
            pending_cnt = store.conn.execute("SELECT COUNT(1) FROM pending_member").fetchone()[0]
        assert token_cnt == 0 and pending_cnt == 0, (token_cnt, pending_cnt)
    finally:
        store.close()


# ════════════════════ 外加：停用可提交 / 删除关死（含 rejected 挂起） ════════════════════

def test_extra_disabled_template_issued_link_still_submits() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "待停用", deadline_time="18:00")
        inst = _ensure_today(store, tpl, "2026-11-12 18:00:00")
        raw_token = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", inst["id"])

        # 停用模板（enabled=0）：已下发链接仍可正常查看与提交
        store.update_template(tpl["template_id"], {"enabled": False}, actor="t")
        CLOCK["now"] = datetime(2026, 11, 12, 10, 0, 0)
        code, info = process_h5_task_info(state=store, raw_token=raw_token)
        assert code == 200 and info["window_open"] is True, (code, info.get("window_reason"))
        res = store.submit_h5_task(raw_token=raw_token, fields={"summary": "停用后仍交"})
        assert res["status"] == "approved"  # need_review=False
        assert store.get_instance(inst["id"])["status"] == "approved"
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_extra_deleted_template_link_closed_including_rejected() -> None:
    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "待删除", deadline_time="18:00", need_review=True)
        inst = _ensure_today(store, tpl, "2026-11-12 18:00:00")
        raw = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", inst["id"])
        store.submit_h5_task(raw_token=raw, fields={"summary": "初稿"})
        CLOCK["now"] = datetime(2026, 11, 12, 12, 0, 0)
        store.review_instance(inst["id"], action="reject", reason="重来", reviewer="admin")
        assert store.get_instance(inst["id"])["status"] == "rejected"

        # 删除模板 → rejected 挂起实例一并关死（重交窗口内也交不了）
        store.delete_template(tpl["template_id"], actor="t")
        # 2026-11-12 下午口径修正：删除模板 = **物理清除**（实例/提交/令牌随模板一起删）。
        # 旧链接的令牌已不存在 → info 返回 TOKEN_INVALID、submit 以「链接无效」拒绝 —— 仍然交不了。
        assert store.get_instance(inst["id"]) is None, "删除模板后实例行必须物理清除"
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert (code, info["code"]) == (401, "TOKEN_INVALID"), (code, info)
        assert "H5 访问令牌" in info["message"], info["message"]
        try:
            store.submit_h5_task(raw_token=raw, fields={"summary": "还想交"})
            raise AssertionError("删除后必须拒绝提交")
        except BizError as exc:
            assert exc.status_code == 404 and exc.code == "NOT_FOUND", (exc.status_code, exc.code, exc.message)
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_extra_h5_upload_for_deleted_template_rejected() -> None:
    """上传端点（附件）对已删除模板同样关死：令牌随物理清除一并删除 → TOKEN_INVALID，不收文件。"""
    from app.api.h5 import _validate_upload_target_and_limits

    store = _store()
    try:
        rid = _seed_member(store)
        tpl = _form_tpl(store, rid, "删除后上传", deadline_time="18:00", need_review=False)
        inst = _ensure_today(store, tpl, "2026-11-12 18:00:00")
        raw = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-12", inst["id"])
        store.delete_template(tpl["template_id"], actor="t")
        result = _validate_upload_target_and_limits(store, raw, "photo.png")
        err = result[-1]
        assert err is not None and err[0] == 401, result
        assert err[1]["code"] == "TOKEN_INVALID", err
    finally:
        store.close()
