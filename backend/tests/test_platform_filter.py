# -*- coding: utf-8 -*-
"""2026-11-02 多通道面板：后台「按办公软件过滤」接口验收。

背景：业务数据（角色/成员/模板/实例）全局共用，但面板要能只看某个通道的人。
用户口径 B：同一成员在两个平台都有绑定时，两个平台都要看到（双绑定双发）。

本文件锁死：
1. /api/admin/instances?platform= 只返回「该平台有可用绑定」的人员实例；
2. /api/admin/stats?platform= 与列表同口径（数字和列表条数一致）；
3. /api/admin/pending-members?platform= 按来源通道隔离；
4. 不传 platform 时保持旧行为（全量）。
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

ONLY_ZD = "13900008101"
ONLY_WX = "13900008102"
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


def _seed(store: StateStore) -> None:
    rid = store.create_role("多平台组", actor="t")["role_id"]
    store.add_role_member(rid, ONLY_ZD, "只在密信", "市场部", actor="t")
    store.add_role_member(rid, ONLY_WX, "只在企微", "市场部", actor="t")
    store.add_role_member(rid, BOTH, "两边都在", "市场部", actor="t")
    store.upsert_delivery_target(ONLY_ZD, "zd_uid_8101", platform="zdxlz")
    store.upsert_delivery_target(ONLY_WX, "wx_uid_8102", platform="wecom")
    store.upsert_delivery_target(BOTH, "zd_uid_8103", platform="zdxlz")
    store.upsert_delivery_target(BOTH, "wx_uid_8103", platform="wecom")

    tpl = store.create_template(
        {
            "name": "分平台面板测试",
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
    pkey = format_dt(now)[:10]
    deadline = format_dt(now + timedelta(days=1))
    store.ensure_template_instances(tpl["template_id"], pkey, deadline)


def _phones(client: TestClient, platform: str) -> set[str]:
    qs = f"?size=100&platform={platform}" if platform else "?size=100"
    res = client.get(f"/api/admin/instances{qs}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    return {it["phone"] for it in body["items"]}


def test_instances_filter_by_platform() -> None:
    store = _mk_store()
    try:
        _seed(store)
        c = _env(store)

        assert _phones(c, "zdxlz") == {ONLY_ZD, BOTH}
        assert _phones(c, "wecom") == {ONLY_WX, BOTH}
        # 不传 platform：保持旧行为，全量
        assert _phones(c, "") == {ONLY_ZD, ONLY_WX, BOTH}
    finally:
        store.close()


def test_stats_filter_matches_list() -> None:
    store = _mk_store()
    try:
        _seed(store)
        c = _env(store)

        for plat, expected in (("zdxlz", 2), ("wecom", 2), ("", 3)):
            qs = f"?platform={plat}" if plat else ""
            res = c.get(f"/api/admin/stats{qs}")
            assert res.status_code == 200, res.text
            stats = res.json()["stats"]
            assert stats["total"] == expected, (plat, stats)
            assert stats["total"] == len(_phones(c, plat))
    finally:
        store.close()


def test_pending_members_filter_by_platform() -> None:
    store = _mk_store()
    try:
        store.upsert_pending_member("zd_new_1", "你好", phone=ONLY_ZD, name="密信新人", platform="zdxlz")
        store.upsert_pending_member("wx_new_1", "你好", phone=ONLY_WX, name="企微新人", platform="wecom")
        c = _env(store)

        zd = c.get("/api/admin/pending-members?platform=zdxlz").json()
        assert [it["platform"] for it in zd["items"]] == ["zdxlz"]
        assert zd["total"] == 1

        wx = c.get("/api/admin/pending-members?platform=wecom").json()
        assert [it["platform"] for it in wx["items"]] == ["wecom"]

        allp = c.get("/api/admin/pending-members").json()
        assert allp["total"] == 2
    finally:
        store.close()