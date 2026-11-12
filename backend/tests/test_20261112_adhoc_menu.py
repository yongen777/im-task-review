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

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_adhoc_")) / "im.db", now_fn=lambda: CLOCK["now"])


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
            assert "菜单" in exc.message, exc.message
    finally:
        store.close()


def test_adhoc_never_overdue_and_h5_hides_deadline() -> None:
    """adhoc 实例 deadline=哨兵：advance 到 2099 年也不逾期；H5 徽章恒「截止 不限」。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        tpl = _adhoc_tpl(store, rid)
        # 成员私聊数字 → 签发
        _, _, box = _run(store, UID, "菜单")
        assert box and "自主上报" in box[0][0], box
        _, body, box2 = _run(store, UID, "1")
        assert body["found"] is True, body
        link = [ln for ln in box2[0][0].splitlines() if "#t=" in ln][0]
        raw = link.split("#t=", 1)[1].strip()
        inst = store.get_instance(int(body["instance_id"]))
        assert inst["deadline_at"] == NO_DEADLINE_SENTINEL, inst["deadline_at"]
        assert inst["period_key"].startswith("adhoc-"), inst["period_key"]
        # 100 年后也不流转 overdue/expired
        stats = store.advance_instance_statuses(datetime(2099, 1, 1))
        assert stats["to_overdue"] == 0, stats
        code, info = process_h5_task_info(state=store, raw_token=raw)
        assert code == 200 and info["window_open"] is True, (code, info)
        assert info["template"]["deadline_rule"] == "不限", info["template"]["deadline_rule"]
        assert info["template"]["has_deadline"] is False
    finally:
        store.close()


# ════════════════════ 二：三个交互场景的精确文案 ════════════════════

def test_menu_scenario_texts_exact() -> None:
    """场景1（兜底含双入口）/ 场景2（清单文案）/ 场景3（签发与未找到文案）逐句断言。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid, "现场安全隐患排查上报")
        _adhoc_tpl(store, rid, "突发网络故障紧急报备")

        # 场景 1：非指令文字 → 兜底（含【待办】与【菜单】双指引）
        _, _, box = _run(store, UID, "你好")
        assert "回复【待办】" in box[0][0] and "回复【菜单】" in box[0][0], box[0][0]

        # 场景 2：【菜单】→ 清单（编号升序 + 两条事项 + 结尾指引）
        _, _, box = _run(store, UID, "菜单")
        menu = box[0][0]
        assert menu.startswith("【自主上报事项清单】"), menu
        assert "请直接回复对应【数字编号】获取专属提交链接：" in menu
        assert "1. 【现场安全隐患排查上报】" in menu and "2. 【突发网络故障紧急报备】" in menu, menu
        assert "（直接回复数字如\"1\"即可进入填报）" in menu

        # 场景 3a：数字越界 → 未找到
        _, _, box = _run(store, UID, "99")
        assert box[0][0] == "【OPS任务调度助手】未找到编号为 99 的事项，请回复【菜单】查看最新列表。", box[0][0]

        # 场景 3b：数字命中 → 签发一次性链接（模板文案）
        _, body, box = _run(store, UID, "1")
        msg = box[0][0]
        assert msg.startswith("请点击链接完成【现场安全隐患排查上报】填报："), msg
        assert "👉 提交入口：http://t.example.com/h5/task#t=" in msg, msg
        assert "（本链接提交后自动作废；如需再次上报请重新回复\"1\"）" in msg, msg
    finally:
        store.close()


def test_menu_scenario2_empty_list_text() -> None:
    """无任何可报事项 → 场景 2 空清单固定文案。"""
    store = _store()
    try:
        _seed_zdxlx(store)
        _, _, box = _run(store, UID, "菜单")
        assert box[0][0] == "【OPS任务调度助手】当前暂无可自主上报的任务事项。", box[0][0]
    finally:
        store.close()


# ════════════════════ 三：平台隔离 / 角色权限 / 未在册零写入 / 互斥校验 ════════════════════

def test_menu_platform_isolation_and_role_scope() -> None:
    """密信用户只看密信平台事项；企微专属事项与无角色用户的菜单都拿不到；数字越权按未找到拦截。"""
    store = _store()
    try:
        rid_z = _seed_zdxlx(store)  # 密信角色 + P
        rid_w = store.create_role("企微报备组", actor="t", platform="wecom")["role_id"]
        store.add_role_member(rid_w, P2, "赵六", "网运部", actor="t")
        store.upsert_delivery_target(P2, "uid_wecom_7002", platform="wecom")
        _adhoc_tpl(store, rid_z, "密信隐患上报")
        _adhoc_tpl(store, rid_w, "企微专属报备")  # 企微平台事项
        _adhoc_tpl(store, rid_z, "密信第二事项")

        # 密信用户【菜单】：只看到密信平台的 2 条（企微的不串门）
        _, _, box = _run(store, UID, "菜单")
        menu = box[0][0]
        assert "密信隐患上报" in menu and "密信第二事项" in menu, menu
        assert "企微专属报备" not in menu, menu

        # 企微用户【菜单】：只看到企微平台事项（数字 1 = 企微专属报备）
        box_w = _sent()
        handle_inbound_text(
            state=store, user_id="uid_wecom_7002", msg_text="菜单",
            send=lambda t, g: box_w.append((t, g)), platform="wecom", base_url="http://t.example.com",
        )
        assert "企微专属报备" in box_w[0][0] and "密信隐患上报" not in box_w[0][0], box_w[0][0]

        # 角色权限：P 属于密信巡查组 → 企微事项对 P 而言「不在菜单」→ 数字按未找到拦截
        _, _, box = _run(store, UID, "菜单")
        nos = [ln for ln in box[0][0].splitlines() if ln.startswith(("1.", "2."))]
        assert len(nos) == 2, box[0][0]
        _, body, box = _run(store, UID, "3")
        assert body["found"] is False, body
        assert "未找到编号为 3" in box[0][0], box[0][0]
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
        assert tpl["remind_at"] is None, tpl["remind_at"]
        assert tpl["has_deadline"] is False
        assert tpl["deadline_time"] is None and tpl["deadline_type"] is None
        assert not tpl["deadline"], tpl["deadline"]
        assert not tpl["exec_weekdays"] and not tpl["exec_dom_days"], (tpl["exec_weekdays"], tpl["exec_dom_days"])
        # 编辑切换回普通模式：恢复执行时间/截止的显式配置
        upd = store.update_template(tpl["template_id"], {
            "is_adhoc": False, "remind_at": "09:00", "has_deadline": True,
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
            assert "不定期" in exc.message, exc.message
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
            _, body, box = _run(store, UID, str(n))
            assert body.get("found") is True, body
            link_line = [ln for ln in box[0][0].splitlines() if "#t=" in ln][0]
            return link_line.split("#t=", 1)[1].strip()

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

        # 冷却窗口外再次回复数字 → 全新实例 + 全新链接，可继续上报
        CLOCK["now"] = CLOCK["now"] + timedelta(seconds=StateStore.ADHOC_MENU_COOLDOWN_SECONDS + 5)
        raw2 = _issue(1)
        assert raw2 != raw1
        assert _row_count(store, "task_instance") == 2, "第二次上报必须创建全新实例"
        res2 = store.submit_h5_task(raw_token=raw2, fields={"summary": "地库渗水"}, note="现场情况说明")
        assert res2["status"] == "approved"
        assert _row_count(store, "submission") == 2
    finally:
        CLOCK["now"] = datetime(2026, 11, 12, 19, 30, 0)
        store.close()


def test_adhoc_number_cooldown_blocks_repeat_issuance() -> None:
    """防手抖：冷却窗口内重复回复同一数字 → 不建新实例、不签新链接，只提示已获取。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        _adhoc_tpl(store, rid)
        _, body, _ = _run(store, UID, "1")
        assert body["found"] is True, body
        n_inst, n_tok = _row_count(store, "task_instance"), _row_count(store, "h5_token")
        _, body2, box2 = _run(store, UID, "1")  # 同秒再点
        assert body2.get("cooldown") is True, body2
        assert "已获取过" in box2[0][0] and "请先完成本次填报" in box2[0][0], box2[0][0]
        assert _row_count(store, "task_instance") == n_inst and _row_count(store, "h5_token") == n_tok
    finally:
        store.close()


# ════════════════════ 四：角色删除引用校验加固 ════════════════════

def test_role_delete_reference_hardening() -> None:
    """role_ids 脏数据（逗号/混杂格式）一律被识别为引用 → 409 拦截，杜绝漏判误删。"""
    store = _store()
    try:
        rid = _seed_zdxlx(store)
        # 正常 JSON 数组引用 → 409
        _adhoc_tpl(store, rid, "正常引用")
        try:
            store.delete_role(rid, actor="t")
            raise AssertionError("被 JSON 数组引用的角色必须 409")
        except BizError as exc:
            assert (exc.status_code, exc.code) == (409, "ROLE_IN_USE"), exc
            assert "请先删除相关任务模板" in exc.message, exc.message
        store.delete_template([t for t in store.list_templates()["items"] if t["is_adhoc"]][0]["template_id"], actor="t")

        # 脏数据：逗号分隔（非 JSON）→ 旧实现 json.loads 失败置空导致漏删，新实现必须 409
        with store.lock, store.conn:
            store.conn.execute(
                "INSERT INTO task_template(template_id, name, kind, role_ids, text_tpl, period, created_by, created_at, updated_at, owner_id) "
                "VALUES ('t_dirty', '脏数据模板', 'form', ?, 'x', 'day', 't', '2026-11-12 19:00:00', '2026-11-12 19:00:00', 1)",
                (f"{rid},r_other,",),
            )
        try:
            store.delete_role(rid, actor="t")
            raise AssertionError("逗号分隔脏引用必须 409 拦截")
        except BizError as exc:
            assert (exc.status_code, exc.code) == (409, "ROLE_IN_USE"), exc
        with store.lock, store.conn:
            store.conn.execute("DELETE FROM task_template WHERE template_id = 't_dirty'")

        # 无引用 → 删除成功（放行路径不回归）
        res = store.delete_role(rid, actor="t")
        assert res["ok"] is True and res["deleted_members"] == 1, res
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
