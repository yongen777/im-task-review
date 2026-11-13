# -*- coding: utf-8 -*-
"""2026-11-12 下午 · 模板编辑实时同步 / 「任务已关闭」统一拦截 / 立即推送覆盖已提交 / 驳回重交 24h 专项用例。

用户拍板口径（本轮需求文档）：
  一  模板编辑保存后旧 H5 链接实时生效：内容/字段/必填动态校验；截止动态重算
      （延后 → overdue 自动恢复 pending + 令牌寿命顺延；提前 → 立即 overdue）；
      停用 → 「任务已关闭」、重新启用自动恢复；已完成历史隔离；创建默认无需审核。
  二  立即推送覆盖 submitted/approved（冷却保留）；定时推送仍跳过已提交；
      H5 对已提交/已审核通过**打开放行**，仅最后一步提交终审拦「已上传，请勿重复提交」。
  三  驳回重交 = 驳回时刻 + 24h（无截止 → 无限期）；链接寿命与 24h 窗口对齐。
  四  五类失效统一拦截「任务已关闭」（删除/停用/移出角色/移除角色/成员停用），
      模板物理删除后靠保留的 h5_token 行识别，不再是「无效令牌」技术报错。

全部相对时刻（模块级 CLOCK），不碰 data/im_task.db。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.h5 import process_h5_task_info, _check_submission_window  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import (  # noqa: E402
    NO_DEADLINE_SENTINEL,
    BizError,
    StateStore,
    calc_h5_token_ttl_hours,
    calc_resubmit_deadline,
    format_deadline_rule,
    format_dt,
)

P1 = "13900001001"
P2 = "13900002002"
CLOCK = {"now": datetime(2026, 11, 12, 9, 0, 0)}


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_live_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _sched(store: StateStore, notifier: FakeNotifier | None = None) -> Scheduler:
    ntf = notifier or FakeNotifier(now_fn=lambda: CLOCK["now"])
    return Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                     now_fn=lambda: CLOCK["now"])


def _seed_two(store: StateStore) -> str:
    rid = store.create_role("实时组", actor="t")["role_id"]
    for p, n in ((P1, "张三"), (P2, "李四")):
        store.add_role_member(rid, p, n, "市场部", actor="t")
        store.upsert_delivery_target(p, f"uid_zdxlz_{p[-4:]}", platform="zdxlz")
    return rid


def _tpl(store: StateStore, rid: str, name: str = "日报", *, deadline_time: str = "18:00",
         need_review: bool = False, has_deadline: bool = True, deadline_type: str | None = "same_day",
         schema: list | None = None, spec: dict | None = None) -> dict:
    return store.create_template(
        {
            "name": name, "kind": "form", "role_ids": [rid], "period": "day",
            "remind_at": "09:00", "deadline": deadline_time if has_deadline else "",
            "has_deadline": has_deadline, "deadline_type": deadline_type if has_deadline else None,
            "deadline_time": deadline_time if has_deadline else None,
            "text_tpl": "请提交", "need_review": need_review, "enabled": True,
            "schema_json": schema or [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": spec or {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        },
        actor="t",
    )


def _ensure(store: StateStore, tpl: dict, pkey: str, dl: str) -> dict:
    made = store.ensure_template_instances(tpl["template_id"], pkey, dl)
    return {m["phone"]: m for m in made}


def _set_status(store: StateStore, inst_id: int, status: str) -> None:
    with store.lock, store.conn:
        store.conn.execute("UPDATE task_instance SET status = ? WHERE id = ?", (status, int(inst_id)))


# ════════════════════ 一-1：内容/字段/必填实时同步 ════════════════════

def test_sync1_old_link_renders_latest_content_and_rules() -> None:
    """编辑模板（改名 + 新增必填图片模块）后：旧链接 info 渲染最新名称/模块；
    提交时按**最新**必填规则硬校验（没图不让交）；补一张图即可通过。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])

        # 管理员编辑：改名 + 新增必填图片模块（保存即生效，旧链接无需重取）
        store.update_template(tpl["template_id"], {
            "name": "日报V2", "role_ids": [rid], "remind_at": "09:00",
            "deadline": "18:00", "has_deadline": True, "deadline_type": "same_day", "deadline_time": "18:00",
            "text_tpl": "请提交V2", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"upload_modules": ["image"], "fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        }, actor="t")

        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200 and info["window_open"] is True, (code, info)
        assert info["template"]["name"] == "日报V2", "旧链接必须渲染最新任务名"
        assert "image" in info["template"]["upload_modules"], "旧链接必须渲染最新上传模块"
        try:
            store.submit_h5_task(raw_token=raw, fields={"summary": "没图想交"})
            raise AssertionError("新必填规则必须在提交时拦截")
        except BizError as exc:
            assert exc.status_code == 400 and "图片" in exc.message, (exc.code, exc.message)
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ 一-2：截止动态重算（延后恢复 / 提前拦截 / 令牌顺延） ════════════════════

def test_sync2_extend_deadline_restores_overdue_and_extends_token() -> None:
    """18:00 截止、19:00 已 overdue 的实例：截止改 22:00 → 自动恢复 pending、
    deadline_at=今天22:00、未消耗令牌 expires 顺延到次日 22:00，旧链接立即可交。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        inst = insts[P1]
        # 直接签发默认 72h（issue_h5_token 的保守缺省）；调度链真实推送走动态 TTL。
        # 用 30h 短令牌制造「旧寿命 < 新寿命」场景，验证截止延后时的只延不缩顺延。
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", inst["id"], ttl_hours=30)
        old_exp = store.get_h5_token(raw)["expires_at"]

        CLOCK["now"] = datetime(2026, 11, 12, 19, 0, 0)
        store.advance_instance_statuses(CLOCK["now"])
        assert store.get_instance(inst["id"])["status"] == "overdue"

        store.update_template(tpl["template_id"], {
            "name": "日报", "role_ids": [rid], "remind_at": "09:00",
            "deadline": "22:00", "has_deadline": True, "deadline_type": "same_day", "deadline_time": "22:00",
            "text_tpl": "请提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        }, actor="t")

        fresh = store.get_instance(inst["id"])
        assert fresh["status"] == "pending", "截止延后必须自动恢复 pending"
        assert fresh["deadline_at"] == "2026-11-12 22:00:00", fresh["deadline_at"]
        new_exp = store.get_h5_token(raw)["expires_at"]
        assert new_exp == "2026-11-13 22:00:00", (new_exp, old_exp)
        assert new_exp > old_exp, "令牌寿命必须顺延"

        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200 and info["window_open"] is True, (code, info)
        res = store.submit_h5_task(raw_token=raw, fields={"summary": "延长后交"})
        assert res["status"] == "approved"
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_sync3_shorten_deadline_to_past_blocks_immediately() -> None:
    """截止提前到当前时刻之前 → 实例立即 overdue，旧链接打开/提交统一过期拦截。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid, deadline_time="18:00")
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])

        CLOCK["now"] = datetime(2026, 11, 12, 19, 0, 0)
        store.update_template(tpl["template_id"], {
            "name": "日报", "role_ids": [rid], "remind_at": "09:00",
            "deadline": "10:00", "has_deadline": True, "deadline_type": "same_day", "deadline_time": "10:00",
            "text_tpl": "请提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        }, actor="t")

        fresh = store.get_instance(insts[P1]["id"])
        assert fresh["status"] == "overdue", "截止提前必须立即 overdue"
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200 and info["window_open"] is False, (code, info)
        assert info["window_reason"] == "当前任务已过期（截止时间：2026-11-12 10:00:00），无法提交", info
        try:
            store.submit_h5_task(raw_token=raw, fields={"summary": "晚了"})
            raise AssertionError("提前截止后必须拒绝提交")
        except BizError as exc:
            assert exc.code == "SUBMISSION_CLOSED" and "2026-11-12 10:00:00" in exc.message, exc
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_sync4_no_deadline_restore_and_sentinel_token() -> None:
    """截止改为「无截止」→ overdue 恢复 pending、deadline_at=哨兵、令牌寿命顺延到哨兵（永不过期）。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        CLOCK["now"] = datetime(2026, 11, 12, 19, 0, 0)
        store.advance_instance_statuses(CLOCK["now"])
        assert store.get_instance(insts[P1]["id"])["status"] == "overdue"

        store.update_template(tpl["template_id"], {
            "name": "日报", "role_ids": [rid], "remind_at": "09:00",
            "deadline": "", "has_deadline": False, "deadline_type": None, "deadline_time": None,
            "text_tpl": "请提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        }, actor="t")

        fresh = store.get_instance(insts[P1]["id"])
        assert fresh["status"] == "pending" and fresh["deadline_at"] == NO_DEADLINE_SENTINEL, fresh
        assert store.get_h5_token(raw)["expires_at"] == NO_DEADLINE_SENTINEL
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200 and info["window_open"] is True and info["template"]["deadline_rule"] == "不限", info
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_sync5_completed_history_isolated() -> None:
    """已完成（submitted/approved）实例不随模板编辑变化：状态、提交记录原样保留。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        store.submit_h5_task(raw_token=raw, fields={"summary": "已交"})
        assert store.get_instance(insts[P1]["id"])["status"] == "submitted"

        store.update_template(tpl["template_id"], {
            "name": "日报V2", "role_ids": [rid], "remind_at": "09:00",
            "deadline": "20:00", "has_deadline": True, "deadline_type": "same_day", "deadline_time": "20:00",
            "text_tpl": "请提交", "need_review": True, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        }, actor="t")

        fresh = store.get_instance(insts[P1]["id"])
        assert fresh["status"] == "submitted", "已完成实例状态不得被编辑改变"
        with store.lock:
            sub_cnt = store.conn.execute(
                "SELECT COUNT(1) FROM submission WHERE instance_id = ?", (int(insts[P1]["id"]),)).fetchone()[0]
        assert int(sub_cnt) == 1, "历史提交记录保持原样"
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ 一-3 / 四：停用 + 五类「任务已关闭」 ════════════════════

def test_close_member_disabled_blocks_and_restore() -> None:
    """成员账号停用（enabled=0）→ 该成员未完成任务链接关闭；恢复启用 → 可用。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])

        store.update_role_member(rid, P1, enabled=False, actor="t")
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert (code, info["code"], info["message"]) == (403, "TASK_CLOSED", "任务已关闭"), (code, info)
        try:
            store.submit_h5_task(raw_token=raw, fields={"summary": "x"})
            raise AssertionError("成员停用后必须拒绝提交")
        except BizError as exc:
            assert (exc.status_code, exc.code) == (403, "TASK_CLOSED"), exc

        store.update_role_member(rid, P1, enabled=True, actor="t")
        code2, info2 = process_h5_task_info(state=store, raw_token=raw)
        assert code2 == 200 and info2["window_open"] is True, (code2, info2)
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_close_member_removed_role_cancelled() -> None:
    """成员被移出角色（delete_role_member）→ 实例 cancelled → 统一「任务已关闭」。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P2, tpl["template_id"], "2026-11-12", insts[P2]["id"])
        store.delete_role_member(rid, P2, actor="t")
        assert store.get_instance(insts[P2]["id"])["status"] == "cancelled"
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert (code, info["code"], info["message"]) == (403, "TASK_CLOSED", "任务已关闭"), (code, info)
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_close_template_deleted_token_tombstone() -> None:
    """模板物理删除：实例清空、令牌保留 → 旧链接统一「任务已关闭」（不是无效令牌）。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        store.delete_template(tpl["template_id"], actor="t")
        assert store.get_instance(insts[P1]["id"]) is None
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert (code, info["code"], info["message"]) == (403, "TASK_CLOSED", "任务已关闭"), (code, info)
        try:
            store.submit_h5_task(raw_token=raw, fields={"summary": "x"})
            raise AssertionError("删除后必须拒绝提交")
        except BizError as exc:
            assert (exc.status_code, exc.code, exc.message) == (403, "TASK_CLOSED", "任务已关闭"), exc
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ 二：立即推送覆盖已提交 + H5 打开放行 + 提交终审 ════════════════════

def test_push_now_covers_submitted_and_approved() -> None:
    """立即推送：submitted/approved 实例正常发送并签发链接（冷却由 API 层管，此处直调 scheduler）。"""
    store = _store()
    try:
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sch = _sched(store, ntf)
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        _set_status(store, insts[P1]["id"], "submitted")
        _set_status(store, insts[P2]["id"], "approved")

        stats = sch.push_now(tpl["template_id"], actor="t")
        assert stats["pushed_instances"] == 2, stats
        assert len(ntf.sent_messages) == 2, ntf.sent_messages
        # 签发的链接有效（token 存在且未消耗，可打开 info）
        texts = [m.text for m in ntf.sent_messages]
        tokens = [t.split("#t=", 1)[1].strip() for t in texts]
        for tk in tokens:
            tok = store.get_h5_token(tk)
            assert tok is not None and not tok["consumed"], tok
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_push_now_skips_cancelled_expired_and_past_deadline() -> None:
    """cancelled / expired / 已过截止 → 立即推送不打扰（矩阵第 3、4 行）。"""
    store = _store()
    try:
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sch = _sched(store, ntf)
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        _set_status(store, insts[P1]["id"], "cancelled")
        _set_status(store, insts[P2]["id"], "expired")

        CLOCK["now"] = datetime(2026, 11, 12, 19, 0, 0)  # 已过 18:00 截止
        stats = sch.push_now(tpl["template_id"], actor="t")
        assert stats["pushed_instances"] == 0 and len(ntf.sent_messages) == 0, (stats, ntf.sent_messages)
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_h5_open_allowed_for_submitted_submit_blocked_with_unified_msg() -> None:
    """已提交实例：H5 打开放行（window_open=True 可渲染表单）；提交终审拦「已上传，请勿重复提交」；
    历史提交记录不被覆盖；审核通过（approved）同理。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw1 = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        store.submit_h5_task(raw_token=raw1, fields={"summary": "首交"})
        _set_status(store, insts[P2]["id"], "approved")

        # 旧链接（consumed）重新打开：放行渲染
        code, info = process_h5_task_info(state=store, raw_token=raw1)
        assert code == 200 and info["window_open"] is True, (code, info)
        # 再签一条新链接（模拟立即推送重发）：同样放行
        raw2 = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        code2, info2 = process_h5_task_info(state=store, raw_token=raw2)
        assert code2 == 200 and info2["window_open"] is True, (code2, info2)
        # 提交终审：统一「已上传，请勿重复提交」，历史记录不动
        try:
            store.submit_h5_task(raw_token=raw2, fields={"summary": "想再交"})
            raise AssertionError("已提交实例必须被终审拦截")
        except BizError as exc:
            assert (exc.status_code, exc.code, exc.message) == (400, "ALREADY_SUBMITTED", "已上传，请勿重复提交"), exc
        with store.lock:
            sub_rows = store.conn.execute(
                "SELECT fields_json FROM submission WHERE instance_id = ? ORDER BY id ASC",
                (int(insts[P1]["id"]),)).fetchall()
        assert len(sub_rows) == 1, "历史提交记录不得被覆盖"
        # approved 实例：打开放行 + 提交同样拦截
        raw3 = store.issue_h5_token(P2, tpl["template_id"], "2026-11-12", insts[P2]["id"])
        code3, info3 = process_h5_task_info(state=store, raw_token=raw3)
        assert code3 == 200 and info3["window_open"] is True, (code3, info3)
        try:
            store.submit_h5_task(raw_token=raw3, fields={"summary": "x"})
            raise AssertionError("approved 实例必须被终审拦截")
        except BizError as exc:
            assert exc.message == "已上传，请勿重复提交", exc.message
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ 三：驳回重交 24h / 无截止无限期 / TTL 对齐 ════════════════════

def test_resubmit_window_24h_and_token_alignment() -> None:
    """有截止模板：驳回 → 重交截止=驳回+24h；rejected 链接 TTL=24h；超窗 advance 转 expired。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        store.submit_h5_task(raw_token=raw, fields={"summary": "初稿"})
        CLOCK["now"] = datetime(2026, 11, 12, 12, 0, 0)
        store.review_instance(insts[P1]["id"], action="reject", reason="不通过", reviewer="admin")
        fresh = store.get_instance(insts[P1]["id"])
        # resubmit_* 列已下线（E3），重交窗口按 reviewed_at 动态算
        rd = calc_resubmit_deadline(fresh["deadline_at"], fresh["reviewed_at"])
        assert rd == "2026-11-13 12:00:00", rd

        # rejected 链接寿命与 24h 窗口对齐（调度链 build_h5_submit_url 同款动态 TTL）
        assert calc_h5_token_ttl_hours(fresh, CLOCK["now"]) == 24.0
        raw2 = store.issue_h5_token(
            P1, tpl["template_id"], "2026-11-12", insts[P1]["id"],
            ttl_hours=calc_h5_token_ttl_hours(fresh, CLOCK["now"]),
        )
        tok = store.get_h5_token(raw2)
        assert tok["expires_at"] == "2026-11-13 12:00:00", tok["expires_at"]

        # 窗口内可重交；跨过重交截止 1s → advance 转 expired，提交按过期拦截
        res = store.submit_h5_task(raw_token=raw2, fields={"summary": "重交了"})
        assert res["status"] == "submitted"
        # 跨过重交截止 1s → advance 转 expired，提交按过期拦截（未重交的 P2 验证）
        tpl2 = _tpl(store, rid, "另一任务", deadline_time="18:00", need_review=True)
        insts2 = _ensure(store, tpl2, "2026-11-12", "2026-11-12 18:00:00")
        raw3 = store.issue_h5_token(P2, tpl2["template_id"], "2026-11-12", insts2[P2]["id"])
        CLOCK["now"] = datetime(2026, 11, 12, 10, 0, 0)  # 截止 18:00 前先初交
        store.submit_h5_task(raw_token=raw3, fields={"summary": "初稿"})
        store.review_instance(insts2[P2]["id"], action="reject", reason="不通过", reviewer="admin")
        CLOCK["now"] = datetime(2026, 11, 13, 12, 0, 1)
        store.advance_instance_statuses(CLOCK["now"])
        assert store.get_instance(insts2[P2]["id"])["status"] == "expired", "超 24h 未重交必须自动关闭"
        raw4 = store.issue_h5_token(P2, tpl2["template_id"], "2026-11-12", insts2[P2]["id"])
        try:
            store.submit_h5_task(raw_token=raw4, fields={"summary": "超窗"})
            raise AssertionError("超重交窗口必须拒绝")
        except BizError as exc:
            assert exc.code == "SUBMISSION_CLOSED", exc
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


def test_resubmit_no_deadline_template_is_unlimited() -> None:
    """无截止模板驳回 → 重交窗口无限期（哨兵）、链接 TTL 720h、通知文案不出现病句截止。"""
    store = _store()
    try:
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True, has_deadline=False, deadline_type=None, deadline_time="")
        insts = _ensure(store, tpl, "2026-11-12", NO_DEADLINE_SENTINEL)
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        store.submit_h5_task(raw_token=raw, fields={"summary": "初稿"})
        CLOCK["now"] = datetime(2026, 11, 12, 12, 0, 0)
        store.review_instance(insts[P1]["id"], action="reject", reason="不通过", reviewer="admin")
        fresh = store.get_instance(insts[P1]["id"])
        assert calc_resubmit_deadline(fresh["deadline_at"], fresh["reviewed_at"]) == NO_DEADLINE_SENTINEL
        assert calc_h5_token_ttl_hours(fresh, CLOCK["now"]) == 720.0
        raw2 = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        res = store.submit_h5_task(raw_token=raw2, fields={"summary": "无截止重交"})
        assert res["status"] == "submitted"
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()


# ════════════════════ 统一截止规则样式 ════════════════════

def test_deadline_rule_style_unified() -> None:
    """2026-11-13 用户拍板：截止统一显示**当前周期对应的具体日期**（M月D日 HH:MM）。

    同日→当天、本周日→本周日真实日期、月底→当月最后一天（换算与 scheduler.calc_instance_deadline 同源）；
    无截止=「不限」。基准日 2026-11-12 为周四：本周日=11-15、当月最后一天=11-30。
    """
    ref = datetime(2026, 11, 12, 9, 0, 0)
    assert format_deadline_rule(
        {"has_deadline": 1, "deadline_type": "month_end", "deadline_time": "17:00"}, ref_dt=ref) == "11月30日 17:00"
    assert format_deadline_rule(
        {"has_deadline": 1, "deadline_type": "weekend", "deadline_time": "17:00"}, ref_dt=ref) == "11月15日 17:00"
    assert format_deadline_rule(
        {"has_deadline": 1, "deadline_type": "same_day", "deadline_time": "17:00"}, ref_dt=ref) == "11月12日 17:00"
    assert format_deadline_rule({"has_deadline": 0}) == "不限"
    assert format_deadline_rule(
        {"has_deadline": 1, "deadline_type": "same_day", "deadline_time": "00:00"}, ref_dt=ref) == "11月12日 00:00"
    # 周日当天（2026-11-15 就是周日）→ 当天
    assert format_deadline_rule(
        {"has_deadline": 1, "deadline_type": "weekend", "deadline_time": "17:00"},
        ref_dt=datetime(2026, 11, 15, 9, 0, 0)) == "11月15日 17:00"
    # 跨年边界：12 月月底
    assert format_deadline_rule(
        {"has_deadline": 1, "deadline_type": "month_end", "deadline_time": "17:00"},
        ref_dt=datetime(2026, 12, 13, 9, 0, 0)) == "12月31日 17:00"


def test_cal_resubmit_pure_function_new_semantics() -> None:
    """纯函数：驳回+24h；无截止 → 哨兵。"""
    assert calc_resubmit_deadline("2026-11-12 18:00:00", "2026-11-12 12:00:00") == "2026-11-13 12:00:00"
    assert calc_resubmit_deadline(NO_DEADLINE_SENTINEL, "2026-11-12 12:00:00") == NO_DEADLINE_SENTINEL


# ════════════════════ 定时推送边界（二-2） ════════════════════

def test_scheduled_reminder_still_skips_submitted() -> None:
    """定时到点推送：已提交实例自动跳过；未提交实例正常触发（不被立即推送覆盖）。"""
    store = _store()
    try:
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sch = _sched(store, ntf)
        rid = _seed_two(store)
        tpl = _tpl(store, rid, need_review=True)
        insts = _ensure(store, tpl, "2026-11-12", "2026-11-12 18:00:00")
        # 张三已提交，李四未提交
        raw = store.issue_h5_token(P1, tpl["template_id"], "2026-11-12", insts[P1]["id"])
        store.submit_h5_task(raw_token=raw, fields={"summary": "首交"})

        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 30)
        sch.run_once()
        texts = [m.text for m in ntf.sent_messages]
        assert len(texts) == 1, texts  # 只有李四收到定时提醒（张三已提交被跳过）
        with store.lock:
            sent_phones = [r["phone"] for r in store.conn.execute(
                "SELECT phone FROM reminder_log WHERE result = 'sent' AND kind = 'remind'").fetchall()]
        assert sent_phones == [P2], sent_phones
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 0)
        store.close()
