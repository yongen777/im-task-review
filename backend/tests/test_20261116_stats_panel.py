# -*- coding: utf-8 -*-
"""2026-11-16 · 上传任务数据统计分析面板（仅分级管理员 / 单任务独占 / 定期不定期自适应 / Excel 实时导出）。

验收矩阵（用户工程指令）：
  ① 权限红线：超管五端点一律 403；跨属主 403；任务清单只含自己名下的上传任务（notice 排除）；
  ② 智能周期：日 / ISO 周（含按日推送的 weekend 任务聚整周）/ 月（含按日推送的 month_end 任务聚整月），
     周/月视图同人多实例按「周期内最优状态」去重成一人一行；
  ③ 模式A：四 KPI（need_review=true 时已完成带「待审/已通过」括号口径）+ 任务专属动态部门树
     （只含实际覆盖成员链路；无路径归「未分配部门」）+ 部门子树过滤 + 明细筛选/搜索/分页/预览；
  ④ 模式B（不定期）：当天人次 / 当天人数去重 / 历史累计 + 近 7 天趋势 + 当天流水明细；
  ⑤ Excel：.xlsx 原生 openpyxl、无合并单元格、冻结首行、全列 AutoFilter、完成状态统一收敛 4 种、
     动态内容列 + 补充说明、图片附件不导出、=+-@ 公式注入转义、文件名=任务名+导出时间（不带秒）；
  ⑥ 前端静态契约：单任务独占（无「全部任务」）、超管导航不渲染、模板行「统计」按钮联动。
"""
from __future__ import annotations

import io
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.state import StateStore  # noqa: E402

CLOCK = {"now": datetime(2026, 11, 16, 12, 0, 0)}
PK_DAY = "2026-11-16"

# 成员布局（成员 A 有完整路径、赵六无路径 → 未分配部门；钱七更深一级）
P_ZS, P_LS, P_WW, P_ZL, P_QQ = ("13800116001", "13800116002", "13800116003", "13800116004", "13800116005")
PATH_CCS = "中国电信德阳分公司"
PATH_BEN = f"{PATH_CCS}/市公司本部"
PATH_YZT = f"{PATH_BEN}/云中台"
PATH_CS = f"{PATH_YZT}/产数支撑中心"
PATH_WY = f"{PATH_YZT}/网运中心"
PATH_ZQ = f"{PATH_CCS}/政企部"
PATH_BZ = f"{PATH_CS}/班组一"


def _store() -> StateStore:
    return StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_stats_")),
                      now_fn=lambda: CLOCK["now"])


def _app(store: StateStore) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t"))
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _sub_ready(app: FastAPI, admin: TestClient, username: str) -> tuple:
    aid = int(admin.post("/api/admin/accounts", json={"username": username, "display_name": username})
              .json()["account"]["id"])
    c = _login(app, username, StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
    c.post("/api/admin/change-password",
           json={"old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    return aid, _login(app, username, "NewPass@2026")


def _mk_members(store: StateStore, rid: str, owner_id: int, tag: str) -> None:
    rows = [
        (P_ZS, "张三", "产数支撑中心", PATH_CS),
        (P_LS, "李四", "网运中心", PATH_WY),
        (P_WW, "王五", "政企部", PATH_ZQ),
        (P_ZL, "赵六", "", None),
        (P_QQ, "钱七", "班组一", PATH_BZ),
    ]
    with store.lock, store.conn:
        for ph, nm, dept, dp in rows:
            store.conn.execute(
                "INSERT INTO member(role_id, phone, name, dept, dept_path, enabled, source, owner_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, 'manual', ?, '2026-11-01 08:00:00')",
                (rid, ph, nm, dept, dp, owner_id))
        store.conn.execute(
            "INSERT INTO member(role_id, phone, name, dept, dept_path, enabled, source, owner_id, created_at) "
            "VALUES (?, ?, '孙八', '', NULL, 1, 'manual', ?, '2026-11-01 08:00:00')",
            (rid, f"13800116009{tag[-1]}", owner_id))


def _mk_form_tpl(sa: TestClient, rid: str, name: str, **extra) -> str:
    body = {
        "name": name, "kind": "form", "period": "day", "role_ids": [rid],
        "remind_at": "08:15", "deadline": "18:00", "has_deadline": True,
        "deadline_type": "same_day", "deadline_time": "18:00",
        "text_tpl": "请提交", "need_review": True, "allow_note": True,
        "enabled": True,
        "schema_json": [
            {"key": "qty", "label": "数量", "type": "number", "required": True},
            {"key": "memo2", "label": "备注说明", "type": "text"},
        ],
        "spec": {"upload_modules": ["fields", "text"]},
    }
    body.update(extra)
    r = sa.post("/api/admin/templates", json=body)
    assert r.status_code == 200, r.text
    return r.json()["template_id"]


def _mk_periodic_instances(store: StateStore, tpl_id: str, p_key: str) -> dict:
    """五人实例：张三待审、李四通过、王五待提交、赵六逾期、钱七驳回。"""
    people = [(P_ZS, "张三", "产数支撑中心"), (P_LS, "李四", "网运中心"), (P_WW, "王五", "政企部"),
              (P_ZL, "赵六", ""), (P_QQ, "钱七", "班组一")]
    insts = {}
    for ph, nm, dept in people:
        insts[ph] = store.ensure_instance(tpl_id, ph, nm, dept, p_key, "2026-11-16 18:00:00")
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE task_instance SET status='submitted', submitted_at='2026-11-16 09:00:00' WHERE id=?",
            (int(insts[P_ZS]["id"]),))
        store.conn.execute(
            "UPDATE task_instance SET status='approved', submitted_at='2026-11-16 09:30:00' WHERE id=?",
            (int(insts[P_LS]["id"]),))
        store.conn.execute(
            "UPDATE task_instance SET status='overdue' WHERE id=?", (int(insts[P_ZL]["id"]),))
        store.conn.execute(
            "UPDATE task_instance SET status='rejected', submitted_at='2026-11-16 10:00:00' WHERE id=?",
            (int(insts[P_QQ]["id"]),))
        store.conn.execute(
            "INSERT INTO submission(instance_id, fields_json, note, refs_json, source, status, created_at) "
            "VALUES (?, ?, ?, NULL, 'user', 'submitted', '2026-11-16 09:00:00')",
            (int(insts[P_ZS]["id"]), '{"qty": 42}', "第一条说明"))
        store.conn.execute(
            "INSERT INTO submission(instance_id, fields_json, note, refs_json, source, status, created_at) "
            "VALUES (?, ?, ?, NULL, 'user', 'approved', '2026-11-16 09:30:00')",
            (int(insts[P_LS]["id"]), '{"qty": 7, "memo2": "-3"}', "=SUM(A1:A2)"))
        store.conn.execute(
            "INSERT INTO submission(instance_id, fields_json, note, refs_json, source, status, created_at) "
            "VALUES (?, ?, ?, NULL, 'user', 'rejected', '2026-11-16 10:00:00')",
            (int(insts[P_QQ]["id"]), '{"qty": 5}', "被打回的那次"))


def _mk_cancelled(store: StateStore, tpl_id: str, p_key: str) -> None:
    inst = store.ensure_instance(tpl_id, "13800116009", "孙八", "", p_key, "2026-11-16 18:00:00")
    with store.lock, store.conn:
        store.conn.execute("UPDATE task_instance SET status='cancelled' WHERE id=?", (int(inst["id"]),))


def _seed_main(tag: str) -> dict:
    """主验收数据底座：超管 + 两个分级管理员 + A 的日报模板 + 五人五状态实例。"""
    store = _store()
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    app = _app(store)
    admin = _login(app, "admin", "Admin@2026")
    aid1, sa1 = _sub_ready(app, admin, f"sp1{tag}")
    aid2, sa2 = _sub_ready(app, admin, f"sp2{tag}")
    rid = sa1.post("/api/admin/roles", json={"name": f"统计组{tag}"}).json()["role_id"]
    _mk_members(store, rid, aid1, tag)
    tpl_id = _mk_form_tpl(sa1, rid, f"统计日报{tag}")
    _mk_periodic_instances(store, tpl_id, PK_DAY)
    _mk_cancelled(store, tpl_id, PK_DAY)
    # notice 模板（不应出现在任务清单）
    r = sa1.post("/api/admin/templates", json={
        "name": f"通知{tag}", "kind": "notice", "role_ids": [rid],
        "text_tpl": "x", "notice_times": ["09:00"], "period": "day", "enabled": True})
    assert r.status_code == 200, r.text
    return {"store": store, "app": app, "admin": admin, "sa1": sa1, "sa2": sa2,
            "aid1": aid1, "aid2": aid2, "rid": rid, "tpl_id": tpl_id}


def _q(sa: TestClient, path: str, **params) -> object:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v != "" and v is not None)
    return sa.get(f"/api/admin/{path}" + (f"?{qs}" if qs else ""))


# ── ① 权限红线 ──────────────────────────────────────────────────────

def test_super_admin_forbidden_on_all_five_endpoints() -> None:
    ctx = _seed_main("a")
    try:
        a, tpl = ctx["admin"], ctx["tpl_id"]
        paths = (
            "stats-panel/tasks",
            f"stats-panel/overview?template_id={tpl}&period_key={PK_DAY}",
            f"stats-panel/instances?template_id={tpl}&period_key={PK_DAY}",
            f"stats-panel/submissions-day?template_id={tpl}&date={PK_DAY}",
            f"stats-panel/export?template_id={tpl}&period_key={PK_DAY}",
        )
        for p in paths:
            r = a.get(f"/api/admin/{p}")
            assert r.status_code == 403, (p, r.status_code, r.text)
            assert r.json()["error"]["code"] == "SUPER_ADMIN_FORBIDDEN", (p, r.text)
    finally:
        ctx["store"].close()


def test_cross_owner_forbidden_and_tasks_scoping() -> None:
    ctx = _seed_main("b")
    try:
        sa2, tpl = ctx["sa2"], ctx["tpl_id"]
        # 跨属主：overview / instances / export 全部 403 FORBIDDEN_OWNER
        for path in (f"stats-panel/overview?template_id={tpl}&period_key={PK_DAY}",
                     f"stats-panel/instances?template_id={tpl}&period_key={PK_DAY}",
                     f"stats-panel/export?template_id={tpl}&period_key={PK_DAY}",
                     f"stats-panel/submissions-day?template_id={tpl}&date={PK_DAY}"):
            r = _q(sa2, path)
            assert r.status_code == 403, (path, r.text)
            assert r.json()["error"]["code"] == "FORBIDDEN_OWNER", r.text
        # 任务清单：A 只见自己的上传任务（notice 排除、无「全部任务」伪项、字段齐全供前端选周期）
        items = ctx["sa1"].get("/api/admin/stats-panel/tasks").json()["items"]
        assert [it["template_id"] for it in items] == [tpl], items
        it = items[0]
        assert it["name"].startswith("统计日报") and it["kind"] == "form" and it["need_review"] is True
        assert it["deadline_type"] == "same_day" and it["is_adhoc"] is False and it["has_deadline"] is True
        # B 名下没有任何上传任务 → 空清单
        assert ctx["sa2"].get("/api/admin/stats-panel/tasks").json()["items"] == []
        # 不存在/已删模板 → 404
        r = _q(ctx["sa1"], f"stats-panel/overview?template_id=t_nope&period_key={PK_DAY}")
        assert r.status_code == 404
    finally:
        ctx["store"].close()


# ── ②③ 模式A：KPI / 动态部门树 / 部门过滤 / 明细 ───────────────────

def test_periodic_kpis_need_review_bracket_and_dynamic_tree() -> None:
    ctx = _seed_main("c")
    try:
        ov = _q(ctx["sa1"], f"stats-panel/overview?template_id={ctx['tpl_id']}&period_key={PK_DAY}").json()
        assert ov["mode"] == "periodic"
        k = ov["kpis"]
        # 应参与=5（cancelled 排除）；已完成=submitted+approved=2；未完成=3；完成率 40%
        assert k["total"] == 5, k
        assert k["done"] == 2, k
        assert k["not_done"] == 3, k
        assert k["rate"] == 40.0, k
        # need_review=true：已完成括号口径数据（待审1/已通过1）
        assert k["pending_review"] == 1 and k["approved"] == 1, k
        assert k["pending"] == 1 and k["overdue"] == 1 and k["rejected"] == 1, k
        # 动态部门树：只含实际覆盖链路（无「市场部」这类未覆盖节点；无路径 → 未分配部门）
        paths = {n["path"]: n for n in ov["dept_tree"]}
        assert PATH_CS in paths and paths[PATH_CS]["count"] == 2, paths
        assert PATH_WY in paths and PATH_BZ in paths and paths[PATH_BZ]["count"] == 1
        assert PATH_ZQ in paths and "未分配部门" in paths
        assert not any("市场部" in p for p in paths), paths
        # 末级判定依据：班组一没有任何子节点（前端据此隐藏对比图）
        assert not any(p.startswith(PATH_BZ + "/") for p in paths), paths
        # 部门节点统计（对比图口径）：根（有真实路径的 4 人，未分配部门为平行组）；云中台 3 覆盖 2 完成
        ds = ov["dept_stats"]
        assert ds[PATH_CCS] == {"covered": 4, "done": 2}, ds[PATH_CCS]
        assert ds[PATH_YZT] == {"covered": 3, "done": 2}, ds[PATH_YZT]
        assert ds["未分配部门"] == {"covered": 1, "done": 0}, ds.get("未分配部门")
    finally:
        ctx["store"].close()


def test_dept_filter_scopes_kpis() -> None:
    ctx = _seed_main("d")
    try:
        ov = _q(ctx["sa1"], f"stats-panel/overview?template_id={ctx['tpl_id']}&period_key={PK_DAY}&dept_path={PATH_BEN}").json()
        k = ov["kpis"]
        # 市公司本部子树：张三(待审)+李四(通过)+钱七(驳回) → 3 人，完成 2，未完成 1
        assert k["total"] == 3 and k["done"] == 2 and k["not_done"] == 1, k
        assert k["rate"] == 66.7, k
    finally:
        ctx["store"].close()


def test_instances_filter_search_pagination_and_preview() -> None:
    ctx = _seed_main("e")
    try:
        tpl = ctx["tpl_id"]
        base = f"stats-panel/instances?template_id={tpl}&period_key={PK_DAY}"
        r = _q(ctx["sa1"], base).json()
        assert r["total"] == 5 and len(r["items"]) == 5, r
        st = {i["phone"]: i["status"] for i in r["items"]}
        assert st == {P_ZS: "submitted", P_LS: "approved", P_WW: "pending", P_ZL: "overdue", P_QQ: "rejected"}, st
        # cancelled 不出现
        assert all(i["name"] != "孙八" for i in r["items"])
        # 完整部门路径装饰 + 未分配部门兜底
        by_phone = {i["phone"]: i for i in r["items"]}
        assert by_phone[P_ZS]["dept_path"] == PATH_CS
        assert by_phone[P_ZL]["dept_path"] == "未分配部门"
        # 内容预览：按模板字段顺序取首个非空值
        assert by_phone[P_ZS]["preview"] == "42", by_phone[P_ZS]
        assert by_phone[P_QQ]["preview"] == "5", by_phone[P_QQ]
        assert by_phone[P_WW]["preview"] == ""
        # 状态组筛选
        for group, want in (("done", 2), ("todo", 3), ("pending", 1), ("overdue", 1), ("rejected", 1)):
            rr = _q(ctx["sa1"], base + "&status_group=" + group).json()
            assert rr["total"] == want, (group, rr["total"])
        # 姓名搜索 + 部门过滤 + 分页
        assert _q(ctx["sa1"], base + "&q=张三").json()["total"] == 1
        assert _q(ctx["sa1"], base + "&q=13800116005").json()["total"] == 1
        assert _q(ctx["sa1"], base + f"&dept_path={PATH_BEN}").json()["total"] == 3
        p2 = _q(ctx["sa1"], base + "&size=2&page=2").json()
        assert p2["total"] == 5 and len(p2["items"]) == 2 and p2["page"] == 2, p2
    finally:
        ctx["store"].close()


def test_week_and_month_period_ranges_dedupe_persons() -> None:
    """周/月视图：按日推送的 weekend/month_end 任务聚整周期，同人去重取「周期内最优状态」。"""
    ctx = _seed_main("f")
    try:
        store, sa1 = ctx["store"], ctx["sa1"]
        wk_tpl = _mk_form_tpl(sa1, ctx["rid"], "周末报", deadline_type="weekend", deadline="23:59")
        mo_tpl = _mk_form_tpl(sa1, ctx["rid"], "月报", deadline_type="month_end", deadline="23:59")
        # 周视图：张三周一交了、周二~周五 pending（日实例）→ 周视图 1 人=已提交
        for d in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"):
            inst = store.ensure_instance(wk_tpl, P_ZS, "张三", "产数支撑中心", d, "2026-11-15 23:59:59")
            if d == "2026-11-09":
                with store.lock, store.conn:
                    store.conn.execute(
                        "UPDATE task_instance SET status='approved', submitted_at='2026-11-09 10:00:00' WHERE id=?",
                        (int(inst["id"]),))
            inst2 = store.ensure_instance(wk_tpl, P_WW, "王五", "政企部", d, "2026-11-15 23:59:59")
        ov = _q(sa1, f"stats-panel/overview?template_id={wk_tpl}&period_key=2026-W46").json()
        assert ov["kpis"]["total"] == 2 and ov["kpis"]["done"] == 1 and ov["kpis"]["rate"] == 50.0, ov["kpis"]
        rows = _q(sa1, f"stats-panel/instances?template_id={wk_tpl}&period_key=2026-W46").json()
        assert rows["total"] == 2, rows  # 10 条日实例 → 去重 2 人
        st = {i["phone"]: i["status"] for i in rows["items"]}
        assert st[P_ZS] == "approved" and st[P_WW] == "pending", st
        # 月视图：李四 1 号逾期、15 号补交通过 → 月视图 1 人=已提交（最优状态）
        for d, st2 in (("2026-11-01", "overdue"), ("2026-11-15", "approved")):
            inst = store.ensure_instance(mo_tpl, P_LS, "李四", "网运中心", d, "2026-11-30 23:59:59")
            with store.lock, store.conn:
                store.conn.execute("UPDATE task_instance SET status=? WHERE id=?", (st2, int(inst["id"])))
        ov2 = _q(sa1, f"stats-panel/overview?template_id={mo_tpl}&period_key=2026-11").json()
        assert ov2["kpis"]["total"] == 1 and ov2["kpis"]["done"] == 1 and ov2["kpis"]["rate"] == 100.0, ov2["kpis"]
    finally:
        ctx["store"].close()


# ── ④ 模式B：不定期任务 ────────────────────────────────────────────

def test_adhoc_overview_trend_and_day_list() -> None:
    ctx = _seed_main("g")
    try:
        store, sa1 = ctx["store"], ctx["sa1"]
        tpl = _mk_form_tpl(sa1, ctx["rid"], "随手报", is_adhoc=True)
        with store.lock, store.conn:
            # adhoc 无推送时刻/截止（互斥校验要求）
            store.conn.execute(
                "UPDATE task_template SET remind_at='', has_deadline=0, deadline_type=NULL, deadline_time=NULL "
                "WHERE template_id=?", (tpl,))
        inst_x1 = store.ensure_instance(tpl, P_ZS, "张三", "产数支撑中心", "adhoc-20261116-090000-a1b2c3", "9999-12-31 23:59:59")
        inst_x2 = store.ensure_instance(tpl, P_ZS, "张三", "产数支撑中心", "adhoc-20261116-103000-d4e5f6", "9999-12-31 23:59:59")
        inst_y = store.ensure_instance(tpl, P_WW, "王五", "政企部", "adhoc-20261115-180000-112233", "9999-12-31 23:59:59")
        with store.lock, store.conn:
            for iid, at in ((inst_x1["id"], "2026-11-16 09:00:00"), (inst_x2["id"], "2026-11-16 10:30:00"),
                            (inst_y["id"], "2026-11-15 18:00:00")):
                store.conn.execute(
                    "INSERT INTO submission(instance_id, fields_json, note, refs_json, source, status, created_at) "
                    "VALUES (?, '{\"qty\": 1}', 'x', NULL, 'user', 'approved', ?)",
                    (int(iid), at))
        ov = _q(sa1, f"stats-panel/overview?template_id={tpl}&period_key={PK_DAY}").json()
        assert ov["mode"] == "adhoc" and ov["template"]["is_adhoc"] is True
        k = ov["kpis"]
        assert k["day_count"] == 2 and k["day_unique"] == 1, k          # 当天张三 2 次
        assert k["total_count"] == 3 and k["total_unique"] == 2, k      # 累计含王五昨天 1 次
        # 趋势 7 天（以所选日期收尾），当天 2、昨天 1
        assert [t["date"] for t in ov["trend"]] == [f"2026-11-{10 + i:02d}" for i in range(7)]
        td = {t["date"]: t for t in ov["trend"]}
        assert td[PK_DAY]["count"] == 2 and td["2026-11-15"]["count"] == 1, ov["trend"]
        # 当天流水明细（每条提交一行）+ 部门过滤
        lst = _q(sa1, f"stats-panel/submissions-day?template_id={tpl}&date={PK_DAY}").json()
        assert lst["total"] == 2 and [i["name"] for i in lst["items"]] == ["张三", "张三"], lst
        assert lst["items"][0]["preview"] == "1"
        ovf = _q(sa1, f"stats-panel/overview?template_id={tpl}&period_key={PK_DAY}&dept_path={PATH_BEN}").json()
        # 市公司本部子树只含张三（王五在政企部平行支路）→ 累计只剩张三 2 次
        assert ovf["kpis"]["day_count"] == 2 and ovf["kpis"]["total_count"] == 2, ovf["kpis"]
        assert ovf["kpis"]["day_unique"] == 1 and ovf["kpis"]["total_unique"] == 1, ovf["kpis"]
        lstf = _q(sa1, f"stats-panel/submissions-day?template_id={tpl}&date={PK_DAY}&dept_path={PATH_BEN}").json()
        assert lstf["total"] == 2
        export = _q(sa1, f"stats-panel/export?template_id={tpl}&period_key={PK_DAY}&dept_path={PATH_BEN}")
        assert export.status_code == 200
        ws = load_workbook(io.BytesIO(export.content)).active
        assert [r[6] for r in ws.iter_rows(min_row=2, values_only=True)] == ["2026-11-16 10:30", "2026-11-16 09:00"]
        older = lst["items"][-1]
        with store.conn:
            store.conn.execute(
                "INSERT INTO submission(instance_id, note, source, status, created_at) "
                "VALUES (?, '新一次上报', 'user', 'submitted', '2026-11-17 09:00:00')",
                (older["instance_id"],),
            )
        detail = _q(sa1, f"instances/{older['instance_id']}?submission_id={older['submission_id']}")
        assert detail.status_code == 200
        assert detail.json()["submission"]["note"] == "x"
        assert detail.json()["instance"]["template_name"] == "随手报"
        assert _q(sa1, f"instances/{older['instance_id']}?submission_id={lst['items'][0]['submission_id']}").status_code == 404
        # 智能周期元数据：is_adhoc 任务在清单里可被判别
        items = sa1.get("/api/admin/stats-panel/tasks").json()["items"]
        assert any(it["template_id"] == tpl and it["is_adhoc"] for it in items)
    finally:
        ctx["store"].close()


# ── ⑤ Excel 导出 ──────────────────────────────────────────────────

def test_export_xlsx_full_contract() -> None:
    ctx = _seed_main("h")
    try:
        r = _q(ctx["sa1"], f"stats-panel/export?template_id={ctx['tpl_id']}&period_key={PK_DAY}")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        # 文件名 = 任务名 + 导出时间（年月日几点几分，不带秒）
        cd = unquote(r.headers.get("content-disposition", ""))
        m = re.search(r"全部覆盖部门_统计日报h_2026／11／16 12：00\.xlsx", cd)
        assert m, cd
        wb = load_workbook(io.BytesIO(r.content))
        ws = wb.active
        # 表头：固定 7 列 + 数量 + 备注说明（动态字段）+ 补充说明（allow_note）= 10 列
        headers = [c.value for c in ws[1]]
        assert headers == ["序号", "姓名", "手机号", "所属部门（末级）", "完整部门路径", "完成状态", "提交时间",
                           "数量", "备注说明", "补充说明"], headers
        # 刚性：无合并单元格 + 冻结首行 + 全列 AutoFilter
        assert len(ws.merged_cells.ranges) == 0, list(ws.merged_cells.ranges)
        assert ws.freeze_panes == "A2", ws.freeze_panes
        assert ws.auto_filter.ref == "A1:J6", ws.auto_filter.ref  # 5 行数据（cancelled 排除）+ 表头
        rows = {ws.cell(row=i, column=2).value: i for i in range(2, ws.max_row + 1)}
        assert set(rows) == {"王五", "赵六", "钱七", "李四", "张三"}, set(rows)
        g = lambda i, c: ws.cell(row=i, column=c).value  # noqa: E731
        # 完成状态统一收敛 4 种
        assert g(rows["张三"], 6) == "已提交（待审核）" and g(rows["李四"], 6) == "已完成（审核通过）"
        assert g(rows["王五"], 6) == "待上传" and g(rows["赵六"], 6) == "已逾期未交" and g(rows["钱七"], 6) == "已驳回待重交"
        # 未提交：提交时间留空；已提交带时间（不带秒）
        assert (g(rows["王五"], 7) or "") == ""
        assert str(g(rows["张三"], 7)) == "2026-11-16 09:00", g(rows["张三"], 7)
        # 完整部门路径（斜杠全路径）与未维护兜底
        assert g(rows["张三"], 5) == PATH_CS
        assert g(rows["赵六"], 5) == "未分配部门" and (g(rows["赵六"], 4) or "未分配部门") == "未分配部门"
        # 动态内容列 + 补充说明；公式注入转义（- / = 前置单引号）
        assert g(rows["张三"], 8) == 42 and g(rows["张三"], 10) == "第一条说明"
        assert g(rows["李四"], 9) == "'-3" and g(rows["李四"], 10) == "'=SUM(A1:A2)", (g(rows["李四"], 9), g(rows["李四"], 10))
        assert (g(rows["赵六"], 8) or "") == ""
        assert g(rows["钱七"], 8) == 5
        # 部门范围导出：只含市公司本部子树 3 人
        r2 = _q(ctx["sa1"], f"stats-panel/export?template_id={ctx['tpl_id']}&period_key={PK_DAY}&dept_path={PATH_BEN}")
        assert "市公司本部_统计日报h_2026／11／16 12：00.xlsx" in unquote(r2.headers["content-disposition"])
        wb2 = load_workbook(io.BytesIO(r2.content))
        ws2 = wb2.active
        names2 = {ws2.cell(row=i, column=2).value for i in range(2, ws2.max_row + 1)}
        assert names2 == {"张三", "李四", "钱七"}, names2
        # 越权导出 → 403
        assert _q(ctx["sa2"], f"stats-panel/export?template_id={ctx['tpl_id']}&period_key={PK_DAY}").status_code == 403
    finally:
        ctx["store"].close()


# ── ⑥ 前端静态契约（无浏览器环境下守住关键交互红线）───────────────

def test_task_picker_uses_execution_cycle_before_deadline() -> None:
    ctx = _seed_main("cycle")
    try:
        sa, rid = ctx["sa1"], ctx["rid"]
        monthly = _mk_form_tpl(sa, rid, "每月上报", exec_dom_days=[16], has_deadline=False)
        weekly = _mk_form_tpl(sa, rid, "每周上报", exec_weekdays=[1])
        legacy = _mk_form_tpl(sa, rid, "原生月任务", period="month")
        deadline = _mk_form_tpl(sa, rid, "按月收集", deadline_type="month_end")
        adhoc = _mk_form_tpl(sa, rid, "随时上报", is_adhoc=True)
        items = {r["template_id"]: r for r in _q(sa, "stats-panel/tasks").json()["items"]}
        for tid, mode, key in (
            (monthly, "month", "2026-11"), (weekly, "week", "2026-W47"),
            (legacy, "month", "2026-11"), (deadline, "month", "2026-11"),
            (adhoc, "day", PK_DAY), (ctx["tpl_id"], "day", PK_DAY),
        ):
            assert (items[tid]["statistics_period"], items[tid]["current_period_key"]) == (mode, key)
    finally:
        ctx["store"].close()


def test_submission_content_and_unassigned_department_export_match() -> None:
    ctx = _seed_main("content")
    try:
        store, sa, tid = ctx["store"], ctx["sa1"], ctx["tpl_id"]
        inst = store.get_instance_by_key(tid, P_ZS, PK_DAY)
        for ref, name, mime in (("image-ref", "凭证.png", "image/png"), ("file-ref", "清单.pdf", "application/pdf")):
            store.create_upload_ref(ref=ref, phone=P_ZS, template_id=tid, file_name=name, mime=mime, size=10, rel_path=name)
        with store.conn:
            store.conn.execute(
                "UPDATE submission SET fields_json = ?, refs_json = ? WHERE instance_id = ?",
                ('{"qty": 0}', '["image-ref", "file-ref"]', inst["id"]),
            )
        base = f"template_id={tid}&period_key={PK_DAY}"
        row = _q(sa, f"stats-panel/instances?{base}&q={P_ZS}").json()["items"][0]
        assert row["content"]["fields"] == [{"key": "qty", "label": "数量", "value": "0"}]
        assert row["content"]["note"] == "第一条说明"
        assert [(a["file_name"], a["is_image"]) for a in row["content"]["attachments"]] == [
            ("凭证.png", True), ("清单.pdf", False),
        ]
        scoped = f"{base}&dept_path=未分配部门"
        rows = _q(sa, f"stats-panel/instances?{scoped}").json()["items"]
        result = _q(sa, f"stats-panel/export?{scoped}")
        assert result.status_code == 200
        ws = load_workbook(io.BytesIO(result.content)).active
        assert [r[1] for r in ws.iter_rows(min_row=2, values_only=True)] == [r["name"] for r in rows] == ["赵六"]
    finally:
        ctx["store"].close()


def test_frontend_static_contract() -> None:
    root = Path(__file__).resolve().parents[1].parent / "im-task-admin"
    stat = root / "src" / "components" / "imtask" / "StatisticsView.tsx"
    app_t = root / "src" / "App.tsx"
    tpls = root / "src" / "components" / "imtask" / "TemplatesView.tsx"
    if not (stat.exists() and app_t.exists() and tpls.exists()):
        pytest.skip("前端源码目录不在本机，跳过静态契约检查")
    s = stat.read_text(encoding="utf-8")
    # 剥掉块注释/行注释——静态契约只盯真实 UI 代码，注释里的红线描述本身不算违规
    s_code = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s_code = re.sub(r"^\s*//.*$", "", s_code, flags=re.M)
    assert "任务数据统计" in s_code
    assert "全部任务" not in s_code, "⛔ 单任务独占红线：面板严禁出现「全部任务」"
    assert "{deptChildren.length > 0" in s_code, "末级部门（无子级）必须隐藏对比图"
    assert "一键复制未交名单" in s_code and "全部覆盖部门" in s_code
    assert "年第" in s_code and "周 (" in s_code and "fmtDate(mon)" in s_code, \
        "周选择器 label 必须形如 2026年第46周 (11/09 - 11/15)"
    a = app_t.read_text(encoding="utf-8")
    assert "activeTab === 'stats' && !isSuperAdmin" in a, "超管直访统计页也必须拦"
    assert "任务数据统计" in a
    t = tpls.read_text(encoding="utf-8")
    assert "查看该任务的数据统计" in t and "setStatsFocus(String(tpl.id))" in t, "模板行「统计」按钮必须联动跳转"
