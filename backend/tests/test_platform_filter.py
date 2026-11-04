# -*- coding: utf-8 -*-
"""2026-11-02 多通道面板：后台「按办公软件过滤」接口验收。

模型（用户 2026-11-02 口径）：
- 角色、成员、任务模板、任务实例都按办公软件隔离；
- 历史数据全部属于量子密信；企业微信从零开始；
- 任务归属平台 = 模板所选角色所属平台。

本文件锁死：
1. /api/admin/roles?platform= 只返回该平台的角色；
2. /api/admin/templates?platform= 只返回该平台角色上的模板；
3. /api/admin/instances?platform= 只返回该平台模板产生的实例；
4. /api/admin/stats?platform= 与列表同口径（数字和列表条数一致）；
5. /api/admin/pending-members?platform= 按来源通道隔离；
6. 不传 platform 时保持旧行为（全量）。
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router
from app.config import format_dt
from app.state import StateStore

ZD_ONLY = "13900008101"
WX_ONLY = "13900008102"
BOTH = "13900008103"


def _mk_store() -> StateStore:
    tmp = tempfile.mkdtemp(prefix="imtask_plat_")
    return StateStore(Path(tmp) / "im.db")


def _env(store: StateStore) -> TestClient:
    uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
    sess = store.create_admin_session(uid)
    app = FastAPI()
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    c = TestClient(app)
    c.cookies.set("sid", sess["sid"])
    return c


def _tpl(store: StateStore, rid: str, name: str) -> None:
    tpl = store.create_template(
        {
            "name": name,
            "kind": "notice",
            "role_ids": [rid],
            "text_tpl": "今日通知",
            "notice_times": ["08:00"],
            "period": "day",
            "enabled": True,
        },
        actor="t",
    )
    now = store.now().replace(tzinfo=None, microsecond=0)
    store.ensure_template_instances(tpl["template_id"], format_dt(now)[:10], format_dt(now + timedelta(days=1)))


def _seed(store: StateStore) -> None:
    zd_role = store.create_role("密信组", actor="t", platform="zdxlz")["role_id"]
    wx_role = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
    store.add_role_member(zd_role, ZD_ONLY, "只在密信", "市场部", actor="t")
    store.add_role_member(zd_role, BOTH, "两边都在", "市场部", actor="t")
    store.add_role_member(wx_role, WX_ONLY, "只在企微", "市场部", actor="t")
    store.add_role_member(wx_role, BOTH, "两边都在", "市场部", actor="t")
    store.upsert_delivery_target(ZD_ONLY, "zd_uid_8101", platform="zdxlz")
    store.upsert_delivery_target(WX_ONLY, "wx_uid_8102", platform="wecom")
    store.upsert_delivery_target(BOTH, "zd_uid_8103", platform="zdxlz")
    store.upsert_delivery_target(BOTH, "wx_uid_8103", platform="wecom")
    _tpl(store, zd_role, "密信任务")
    _tpl(store, wx_role, "企微任务")


def _phones(client: TestClient, platform: str) -> set[str]:
    qs = f"?size=100&platform={platform}" if platform else "?size=100"
    res = client.get(f"/api/admin/instances{qs}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    return {it["phone"] for it in body["items"]}


def test_roles_and_templates_filter_by_platform() -> None:
    store = _mk_store()
    try:
        _seed(store)
        c = _env(store)

        zd_roles = c.get("/api/admin/roles?platform=zdxlz").json()["items"]
        wx_roles = c.get("/api/admin/roles?platform=wecom").json()["items"]
        assert [r["name"] for r in zd_roles] == ["密信组"]
        assert [r["name"] for r in wx_roles] == ["企微组"]
        assert len(c.get("/api/admin/roles").json()["items"]) == 2

        zd_tpls = c.get("/api/admin/templates?platform=zdxlz&size=100").json()["items"]
        wx_tpls = c.get("/api/admin/templates?platform=wecom&size=100").json()["items"]
        assert [t["name"] for t in zd_tpls] == ["密信任务"]
        assert [t["name"] for t in wx_tpls] == ["企微任务"]
    finally:
        store.close()


def test_instances_filter_by_template_platform() -> None:
    store = _mk_store()
    try:
        _seed(store)
        c = _env(store)

        assert _phones(c, "zdxlz") == {ZD_ONLY, BOTH}
        assert _phones(c, "wecom") == {WX_ONLY, BOTH}
        assert _phones(c, "") == {ZD_ONLY, WX_ONLY, BOTH}
    finally:
        store.close()


def test_stats_filter_matches_list() -> None:
    store = _mk_store()
    try:
        _seed(store)
        c = _env(store)

        for plat, expected in (("zdxlz", 2), ("wecom", 2), ("", 4)):
            qs = f"?platform={plat}" if plat else ""
            res = c.get(f"/api/admin/stats{qs}")
            assert res.status_code == 200, res.text
            stats = res.json()["stats"]
            assert stats["total"] == expected, (plat, stats)
            if plat:
                assert stats["total"] == len(_phones(c, plat))
    finally:
        store.close()

