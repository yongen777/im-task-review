# -*- coding: utf-8 -*-
"""2026-11-13 · 冲刺模块三：多维统计看板（stats_dashboard）。

验收点（用户规范）：
  1. 四 KPI：应交总数 / 已交数 / 完成率 / 按时率（+ 平均提交耗时分钟）；
  2. 三视图：报数（fields 数值）/ 文本（note 与文本字段）/ 附件（refs）分流正确；
  3. 督办红榜：form 过期未交按滞留时长倒序；无截止（哨兵 9999）不进红榜；
  4. 分级管理员 owner_id 隔离（互不可见）；超管 owner=None 全局口径。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.state import StateStore  # noqa: E402

CLOCK = {"now": datetime(2026, 11, 13, 19, 0, 0)}


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_dash_")) / "im.db",
                      now_fn=lambda: CLOCK["now"])


def _seed(store: StateStore, owner_id: int, tag: str) -> str:
    rid = store.create_role(f"看板组{tag}", actor="t", platform="zdxlz")["role_id"]
    with store.lock, store.conn:
        store.conn.execute("UPDATE role SET owner_id = ? WHERE role_id = ?", (owner_id, rid))
    for i, (ph, nm) in enumerate([("13900000001", "张三"), ("13900000002", "李四"), ("13900000003", "王五")]):
        store.add_role_member(rid, ph, nm, f"部门{i}", actor="t")
    store.upsert_delivery_target("13900000001", f"u{tag}1", platform="zdxlz")
    store.upsert_delivery_target("13900000002", f"u{tag}2", platform="zdxlz")
    store.upsert_delivery_target("13900000003", f"u{tag}3", platform="zdxlz")
    tpl = store.create_template(
        {
            "name": f"看板日报{tag}", "kind": "form", "period": "day", "role_ids": [rid],
            "remind_at": "16:30", "deadline": "18:00", "has_deadline": True,
            "deadline_type": "same_day", "deadline_time": "18:00",
            "text_tpl": "请提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "qty", "label": "数量", "type": "text", "required": True}],
            "spec": {"upload_modules": ["fields", "text"]},
        },
        actor="t",
    )
    with store.lock, store.conn:
        store.conn.execute("UPDATE task_template SET owner_id = ? WHERE template_id = ?", (owner_id, tpl["template_id"]))
    return tpl["template_id"]


def _mk_instances(store: StateStore, tpl_id: str) -> dict:
    """3 个实例（直接铺库模拟历史数据，看板只负责读聚合）：
    张三按时交（数值+文本）、李四迟交、王五过期未交。"""
    p_key = "2026-11-13"
    store.ensure_template_instances(tpl_id, p_key, "2026-11-13 18:00:00")
    insts = {i["phone"]: i for i in store.list_instances_for_period(tpl_id, p_key)}
    zs, ls, ww = (insts["13900000001"], insts["13900000002"], insts["13900000003"])
    with store.lock, store.conn:
        # 张三：16:00 建，17:00 按时交（approved，数值 42 + note 文本）
        store.conn.execute(
            "UPDATE task_instance SET created_at='2026-11-13 16:00:00', status='approved', "
            "submitted_at='2026-11-13 17:00:00' WHERE id = ?", (int(zs["id"]),))
        store.conn.execute(
            "INSERT INTO submission(instance_id, fields_json, note, refs_json, source, status, created_at) "
            "VALUES (?, ?, ?, NULL, 'user', 'approved', '2026-11-13 17:00:00')",
            (int(zs["id"]), '{"qty": 42}', "当日完成"))
        # 李四：16:00 建，19:30 迟交（late_submit=1，文本字段）
        store.conn.execute(
            "UPDATE task_instance SET created_at='2026-11-13 16:00:00', status='approved', "
            "submitted_at='2026-11-13 19:30:00', late_submit=1 WHERE id = ?", (int(ls["id"]),))
        store.conn.execute(
            "INSERT INTO submission(instance_id, fields_json, note, refs_json, source, status, created_at) "
            "VALUES (?, ?, ?, NULL, 'user', 'approved', '2026-11-13 19:30:00')",
            (int(ls["id"]), '{"memo": "补交说明"}', "迟交说明"))
        # 王五：不交 → 红榜（deadline 18:00，now 19:00 → 滞留 1 小时）
    return insts


def test_dashboard_kpis_views_redlist_and_owner_isolation() -> None:
    store = _store()
    try:
        t1 = _seed(store, owner_id=1, tag="A")
        t2 = _seed(store, owner_id=2, tag="B")
        _mk_instances(store, t1)
        # B 组也生成当期实例（3 人全 pending，无提交）——隔离测试才有对照面
        store.ensure_template_instances(t2, "2026-11-13", "2026-11-13 18:00:00")

        # 分级管理员 A（owner_id=1）：只看自己的模板
        da = store.stats_dashboard(owner_id=1)
        k = da["kpis"]
        assert k["should_submit"] == 3, k
        assert k["submitted"] == 2, k           # 张三 approved + 李四 approved（need_review=False）
        assert k["pending"] == 1 and k["overdue"] == 0, k  # 王五未交（pending）
        assert k["completion_rate"] == 66.7, k
        assert k["late_submit"] == 1, k          # 李四 19:00 > 18:00
        assert k["on_time_rate"] == 66.7, k
        assert k["avg_submit_minutes"] is not None and k["avg_submit_minutes"] > 0, k

        # 三视图：张三数值 42 / 李四文本 / 附件王五无
        numbers = da["views"]["numbers"]
        assert any(n["value"] == 42 and n["field"] == "qty" and n["name"] == "张三" for n in numbers), numbers
        texts = da["views"]["texts"]
        assert any(t.get("text") == "当日完成" and t["name"] == "张三" for t in texts), texts
        assert any("补交说明" in str(t) or "迟交说明" in str(t) for t in texts), texts
        assert da["views"]["files"] == [] or all(f["name"] != "王五" for f in da["views"]["files"])

        # 红榜：王五（过期未交），滞留 1 小时
        assert len(da["red_list"]) == 1, da["red_list"]
        red = da["red_list"][0]
        assert red["name"] == "王五" and red["phone"] == "13900000003", red
        assert red["overdue_hours"] == 1, red

        # owner B 视角：自己的 3 个实例全 pending（无提交）
        db2 = store.stats_dashboard(owner_id=2)
        assert db2["kpis"]["should_submit"] == 3 and db2["kpis"]["submitted"] == 0, db2["kpis"]
        assert db2["views"]["numbers"] == [] and db2["views"]["texts"] == []
        # B 的红榜：B 模板截止 18:00 未交 → 3 人全在榜
        assert len(db2["red_list"]) == 3, db2["red_list"]

        # 超管全局（owner=None）：A+B 合计 6 人
        dall = store.stats_dashboard(owner_id=None)
        assert dall["kpis"]["should_submit"] == 6, dall["kpis"]
        assert dall["kpis"]["submitted"] == 2, dall["kpis"]
        assert len(dall["red_list"]) == 4, [r["name"] for r in dall["red_list"]]
    finally:
        store.close()


def test_dashboard_no_deadline_sentinel_not_in_redlist() -> None:
    store = _store()
    try:
        rid = store.create_role("无截止组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, "13911110001", "赵六", "部门X", actor="t")
        store.upsert_delivery_target("13911110001", "uz6", platform="zdxlz")
        tpl = store.create_template(
            {
                "name": "无截止任务", "kind": "form", "period": "day", "role_ids": [rid],
                "remind_at": "16:30", "has_deadline": False,
                "text_tpl": "请提交", "need_review": False, "enabled": True,
                "schema_json": [], "spec": {"upload_modules": ["fields"]},
            },
            actor="t",
        )
        p_key = "2026-11-13"
        store.ensure_template_instances(tpl["template_id"], p_key, "9999-12-31 23:59:59")
        d = store.stats_dashboard(owner_id=None)
        # 哨兵截止（不限）不进红榜
        assert all(r["phone"] != "13911110001" for r in d["red_list"]), d["red_list"]
    finally:
        store.close()
