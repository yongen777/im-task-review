# -*- coding: utf-8 -*-
"""2026-10-30 审核待办聚合接口 /api/admin/instances/pending-reviews 的验收用例。

背景：实例列表按 period_key 单日精确匹配，而补交待审的实例必然是往日的（逾期才补交），
默认「今天」筛选下永远查不到。这个接口按「提交/申请时刻」跨自然日聚合两类待办。
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router
from app.config import format_dt
from app.state import StateStore

PHONE = "13900009201"


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp()
    return StateStore(Path(tmp) / "im.db")


def _env(store: StateStore) -> TestClient:
    uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
    sess = store.create_admin_session(uid)
    app = FastAPI()
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    c = TestClient(app)
    c.cookies.set("sid", sess["sid"])
    return c


def _role(store: StateStore) -> str:
    rid = store.create_role("审核组", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    store.upsert_delivery_target(PHONE, "uid_rv")
    return rid


def _tpl(store: StateStore, rid: str, name: str, *, enabled: bool = True) -> Dict[str, Any]:
    tpl = store.create_template(
        {
            "name": name, "kind": "form", "role_ids": [rid], "text_tpl": "请上报",
            "remind_at": "09:00", "deadline": "18:00", "period": "day",
            "need_review": True,
            "schema_json": [{"key": "summary", "label": "摘要", "type": "text"}],
            "enabled": enabled,
        },
        actor="t",
    )
    return tpl


def _inst(store: StateStore, tpl: Dict[str, Any], day_offset: int) -> Dict[str, Any]:
    """建一条 day 实例，deadline 放在未来（避免被动状态推进影响断言）。"""
    anchor = store.now().replace(tzinfo=None, microsecond=0) + timedelta(days=day_offset)
    pkey = format_dt(anchor)[:10]
    deadline = format_dt(store.now().replace(tzinfo=None, microsecond=0) + timedelta(days=day_offset + 1))
    return store.ensure_template_instances(tpl["template_id"], pkey, deadline)[0]


def _mark(store: StateStore, inst_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with store.lock, store.conn:
        store.conn.execute(f"UPDATE task_instance SET {cols} WHERE id = ?", (*fields.values(), int(inst_id)))


def test_pending_reviews_aggregates_across_natural_days() -> None:
    store = _mk_store()
    try:
        client = _env(store)
        rid = _role(store)
        tpl = _tpl(store, rid, "每日上报")

        # 今天提交
        i_today = _inst(store, tpl, 0)
        _mark(store, i_today["id"], status="submitted",
              submitted_at=format_dt(store.now().replace(tzinfo=None)))
        # 昨天提交（跨日 —— 单日筛选下查不到的那一类）
        i_yday = _inst(store, tpl, -1)
        _mark(store, i_yday["id"], status="submitted",
              submitted_at=format_dt(store.now().replace(tzinfo=None) - timedelta(days=1)))
        # 昨天逾期 + 补交待审
        i_rq = _inst(store, tpl, -2)
        _mark(store, i_rq["id"], status="overdue", resubmit_status="pending",
              resubmit_req_at=format_dt(store.now().replace(tzinfo=None) - timedelta(days=1)))
        # 停用模板的待审实例：不该出现（先建实例再停用，否则实例根本不会被生成）
        tpl_off = _tpl(store, rid, "已停用模板")
        i_off = _inst(store, tpl_off, 0)
        _mark(store, i_off["id"], status="submitted",
              submitted_at=format_dt(store.now().replace(tzinfo=None)))
        store.update_template(tpl_off["template_id"], {"enabled": False}, actor="t")

        r = client.get("/api/admin/instances/pending-reviews")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["days"] == 7

        sub_ids = {x["id"] for x in body["submitted"]}
        assert i_today["id"] in sub_ids and i_yday["id"] in sub_ids
        assert i_off["id"] not in sub_ids, "停用模板的实例不应出现在待办里"

        rq_ids = {x["id"] for x in body["resubmit"]}
        assert i_rq["id"] in rq_ids
        # 审核必需字段齐全
        row = next(x for x in body["resubmit"] if x["id"] == i_rq["id"])
        for k in ("member_name_snap", "phone", "template_name", "resubmit_req_at", "deadline_at"):
            assert row.get(k), f"缺少字段 {k}"
        assert body["total"] == len(body["submitted"]) + len(body["resubmit"])
    finally:
        store.close()


def test_pending_reviews_honours_days_window() -> None:
    store = _mk_store()
    try:
        client = _env(store)
        rid = _role(store)
        tpl = _tpl(store, rid, "每日上报")

        old = _inst(store, tpl, -10)
        _mark(store, old["id"], status="submitted",
              submitted_at=format_dt(store.now().replace(tzinfo=None) - timedelta(days=10)))

        assert all(x["id"] != old["id"] for x in client.get("/api/admin/instances/pending-reviews?days=7").json()["submitted"])
        # 放大窗口后能看到
        assert any(x["id"] == old["id"] for x in client.get("/api/admin/instances/pending-reviews?days=30").json()["submitted"])
    finally:
        store.close()


def test_pending_reviews_empty_when_nothing_to_do() -> None:
    store = _mk_store()
    try:
        client = _env(store)
        body = client.get("/api/admin/instances/pending-reviews").json()
        assert body["total"] == 0
        assert body["submitted"] == [] and body["resubmit"] == []
    finally:
        store.close()


def test_instance_list_orders_by_time_of_day() -> None:
    """列表同一天内按执行时刻从早到晚排（08:00 在 09:00 上面）。"""
    store = _mk_store()
    try:
        client = _env(store)
        rid = _role(store)
        pkey = format_dt(store.now().replace(tzinfo=None))[:10]
        deadline = format_dt(store.now().replace(tzinfo=None) + timedelta(days=1))

        late = store.create_template(
            {"name": "早会安排", "kind": "form", "role_ids": [rid], "text_tpl": "x",
             "remind_at": "09:30", "deadline": "18:00", "period": "day",
             "schema_json": [{"key": "s", "label": "s", "type": "text"}]}, actor="t")
        early = store.create_template(
            {"name": "晨间巡检", "kind": "form", "role_ids": [rid], "text_tpl": "y",
             "remind_at": "08:00", "deadline": "18:00", "period": "day",
             "schema_json": [{"key": "s", "label": "s", "type": "text"}]}, actor="t")
        store.ensure_template_instances(late["template_id"], pkey, deadline)
        store.ensure_template_instances(early["template_id"], pkey, deadline)

        items = client.get(f"/api/admin/instances?period_key={pkey}").json()["items"]
        names = [x["template_name"] for x in items]
        assert names.index("晨间巡检") < names.index("早会安排"), names
    finally:
        store.close()
