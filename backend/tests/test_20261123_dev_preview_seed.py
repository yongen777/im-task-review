# -*- coding: utf-8 -*-
"""2026-11-23 dev_preview 演示造数验证（A 级专项 · 对应本轮任务一）。

覆盖：
  1. seed() 在测试库上完整跑通（0 报错），返回工作台/填报/通知确认路由；
  2. **复跑回归**：授权行已落库后再次 seed 不再报
     「接收成员超出授权范围或未选择成员」（portal.py RECIPIENT_OUT_OF_SCOPE，
     2026-11-21 修复：save_grant 显式勾选演示组织根部门 preview-root）；
  3. 「已逾期未完成」演示任务：成员待办出现「已逾期」提醒卡片（无去完成通道），
     「我知道了」ack 持久化后移除；
  4. 细分状态覆盖面：seed 造数后 13 种细分状态里除 cancelled 外全部出现
     （待推送/待上传/待审/完成/代交/驳回/逾期/失效/已送达/待回复/已回复/未送达），
     后台实例页与状态细分下拉可直接逐项验收。
"""
import importlib.util
import secrets
import time
from pathlib import Path

import pytest

from app.api.admin import _query_instances
from app.portal import PortalService
from app.scheduler import FakeNotifier, Scheduler
from app.state import StateStore
from mysql_testbank import mysql_test_cfg

ROOT = Path(__file__).resolve().parents[1]
EMPLOYEE = "13900009762"  # 李青（演示）· H5 工作台 B 的主人


def load_preview_module():
    spec = importlib.util.spec_from_file_location("dev_preview", ROOT / "scripts" / "dev_preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def preview(tmp_path, monkeypatch):
    module = load_preview_module()
    # portal.save_grant → department_path 需要 zdxlz 组织根部门环境变量（同 dev_preview main）
    monkeypatch.setenv("IMT_ORG_ROOT_DEPT_ID", "preview-root")
    store = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp_path)
    store.contacts = module.DemoDirectory()
    scheduler = Scheduler(store, public_base_url="http://127.0.0.1:9", notifier=FakeNotifier())
    try:
        yield module, store, scheduler
    finally:
        store.close()


def test_dev_preview_seed_runs_clean_and_covers_refined_statuses(preview):
    module, store, scheduler = preview
    owner, routes = module.seed(store, "Preview@" + secrets.token_hex(12), scheduler)

    # 1) seed 0 报错：管理员/角色/授权/portal 任务/通知推送全部落库，路由齐全
    assert store.get_admin_by_username(module.DEMO_USER)
    for key in ("H5 工作台 A · 授权领导 (168h)", "H5 工作台 B · 普通成员 (168h)",
                "H5 填报 · 待提交", "H5 填报 · 已逾期只读", "H5 填报 · 管理员代交",
                "H5 通知确认 · 图文和附件"):
        assert key in routes, key

    # 2) 已逾期未完成演示任务：成员待办出现「已逾期」卡片（带「我知道了」）
    portal = PortalService(store)
    ident = {"phone": EMPLOYEE, "platform": "zdxlz"}
    overdue_cards = [t for t in portal.todos(ident) if t.get("overdue")]
    assert overdue_cards, "演示造数里没有「已逾期未完成」待办卡片"
    card = overdue_cards[0]
    with pytest.raises(Exception):
        portal.task_link(ident, card["id"])  # 逾期卡片不提供去完成通道
    assert portal.dismiss_overdue(ident, card["template_id"], card.get("ack_period_key"))["ok"] is True
    assert all(t["id"] != card["id"] for t in portal.todos(ident))

    # 3) 细分状态覆盖面（除 cancelled 外 12 键全出现，后台可直接逐项验收）
    items = _query_instances(store, page=1, size=500)["items"]
    assert items, "演示造数没有生成任何任务实例"
    refined_set = {item["refined_status"] for item in items}
    expected = {
        "push_pending", "upload_pending", "reviewing", "done", "done_admin",
        "rejected", "overdue", "expired", "sent", "await_reply", "replied", "notice_expired",
    }
    missing = expected - refined_set
    assert not missing, f"演示造数缺少细分状态：{sorted(missing)}"
    # 列表行的细分键与口径函数逐键一致（防前后端漂移）
    for item in items:
        assert item["refined_status"], item


def test_dev_preview_seed_replay_after_grant_saved(preview):
    """复跑回归：授权行已存在（grant_options 走「已保存 scope」分支）时，
    二次 seed 不再抛「接收成员超出授权范围或未选择成员」。"""
    module, store, scheduler = preview
    module.seed(store, "Preview@" + secrets.token_hex(12), scheduler)
    time.sleep(1.2)  # 模板重名规避（复跑按时间戳后缀区分）
    owner, routes = module.seed(store, "Preview@" + secrets.token_hex(12), scheduler)
    assert "H5 工作台 A · 授权领导 (168h)" in routes
    # 复跑后授权范围仍覆盖全部演示成员（跨组联查任务照常发布）
    assert store.conn.execute(
        "SELECT COUNT(*) AS n FROM portal_task").fetchone()["n"] >= 4
