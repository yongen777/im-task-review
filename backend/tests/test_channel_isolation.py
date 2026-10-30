# -*- coding: utf-8 -*-
"""2026-10-30 通道隔离：业务数据共用，但「投递绑定」必须按通道分开。

用户疑问：现在能同时配量子密信和企业微信，选不同凭证，角色成员/任务实例会不同吗？
答案：
  · 角色 / 成员 / 模板 / 任务实例 / 提交记录 —— **同一套，不分通道**（一个库一份业务数据）
  · 投递绑定（谁用什么 ID 能收到消息）—— **按通道分开**，表主键就是 (platform, phone)

本文件锁死第二点：切换通道后不能把 A 通道的 external_id 当成 B 通道的用。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.state import StateStore

PHONE = "13900007001"


def _mk(platform: str = "zdxlz") -> StateStore:
    return StateStore(Path(tempfile.mkdtemp()) / "im.db", platform=platform)


def _role(store: StateStore) -> str:
    rid = store.create_role("云中台软研队伍", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    return rid


# ── 绑定按通道隔离 ────────────────────────────────────────────────
def test_binding_in_one_channel_invisible_in_other() -> None:
    store = _mk("zdxlz")
    try:
        store.upsert_delivery_target(PHONE, "zdxlz_uid_001")
        assert store.get_delivery_target(PHONE)["external_id"] == "zdxlz_uid_001"

        # 切到企业微信：同一个手机号在企微下还没绑定
        store.platform = "wecom"
        assert store.get_delivery_target(PHONE) is None
        assert store.get_delivery_target_by_external_id("zdxlz_uid_001") is None

        # 企微下单独绑定，两条互不覆盖
        store.upsert_delivery_target(PHONE, "wecom_userid_001")
        assert store.get_delivery_target(PHONE)["external_id"] == "wecom_userid_001"

        store.platform = "zdxlz"
        assert store.get_delivery_target(PHONE)["external_id"] == "zdxlz_uid_001"
    finally:
        store.close()


def test_touch_by_external_id_respects_channel() -> None:
    """回调侧按 external_id 找绑定：用错通道的 ID 必须找不到（否则会串号发送）。"""
    store = _mk("wecom")
    try:
        _role(store)  # touch 要求手机号仍在有效名册里
        store.upsert_delivery_target(PHONE, "wecom_userid_002")
        assert store.touch_delivery_target_by_external_id("wecom_userid_002") is not None

        store.platform = "zdxlz"
        assert store.touch_delivery_target_by_external_id("wecom_userid_002") is None
        assert store.touch_delivery_target_by_external_id("zdxlz_uid_999") is None
    finally:
        store.close()


def test_mark_unusable_only_affects_current_channel() -> None:
    store = _mk("zdxlz")
    try:
        store.upsert_delivery_target(PHONE, "zdxlz_uid_003")
        store.platform = "wecom"
        store.upsert_delivery_target(PHONE, "wecom_userid_003")

        store.mark_delivery_target_unusable(PHONE)  # 只影响 wecom
        assert store.get_delivery_target(PHONE) is None

        store.platform = "zdxlz"
        assert store.get_delivery_target(PHONE) is not None
    finally:
        store.close()


# ── 成员名册的「已激活」标记跟当前通道一致 ────────────────────────
def test_member_list_activation_follows_channel() -> None:
    store = _mk("zdxlz")
    try:
        rid = _role(store)
        store.upsert_delivery_target(PHONE, "zdxlz_uid_004")

        mine = store.list_role_members(rid)
        row = mine["items"][0]
        # _fmt_member 用 activated 表达「该通道下已有可用绑定」
        assert row["activated"] is True

        # 切到企微：同一个人在这里还没激活，不能显示成「已激活」
        store.platform = "wecom"
        row2 = store.list_role_members(rid)["items"][0]
        assert row2["activated"] is False
    finally:
        store.close()


def test_instance_targets_follow_channel() -> None:
    """生成实例时取「谁能接收」，也必须按当前通道取。"""
    store = _mk("zdxlz")
    try:
        rid = _role(store)
        store.upsert_delivery_target(PHONE, "zdxlz_uid_005")

        ok_members = store.get_dedup_enabled_members_for_roles([rid])
        assert [(m["phone"], m["activated"]) for m in ok_members] == [(PHONE, True)]

        # 换通道后成员还在（业务数据共用），但这边的绑定没了 → activated=False
        store.platform = "wecom"
        after = store.get_dedup_enabled_members_for_roles([rid])
        assert [(m["phone"], m["activated"]) for m in after] == [(PHONE, False)]
    finally:
        store.close()


# ── 业务数据本身不分通道 ──────────────────────────────────────────
def test_business_data_is_shared_across_channels() -> None:
    """角色/成员/模板/实例在同一份库里：换通道不会多出一套数据。"""
    store = _mk("zdxlz")
    try:
        rid = _role(store)
        tpl = store.create_template(
            {
                "name": "通用任务", "kind": "form", "role_ids": [rid], "text_tpl": "x",
                "remind_at": "09:00", "deadline": "18:00", "period": "day",
                "schema_json": [{"key": "a", "label": "A", "type": "text"}],
            },
            actor="t",
        )
        store.platform = "wecom"
        # 同一个 store 换通道后，角色、模板、成员都还是那一份
        assert store.get_template(tpl["template_id"])["name"] == "通用任务"
        assert len(store.list_role_members(rid)["items"]) == 1
        assert [r["name"] for r in store.list_roles()] == ["云中台软研队伍"]
    finally:
        store.close()


def test_default_platform_is_zdxlz() -> None:
    """默认参数必须还是 zdxlz —— 现网零行为变化。"""
    store = StateStore(Path(tempfile.mkdtemp()) / "im.db")
    try:
        assert store.platform == "zdxlz"
        store.upsert_delivery_target(PHONE, "uid_default")
        assert store.get_delivery_target(PHONE, include_unusable=True)["platform"] == "zdxlz"
    finally:
        store.close()


def test_create_apps_sets_store_platform_from_config() -> None:
    """装配时 store.platform 必须跟着通道走，否则切了通道还在读旧绑定。"""
    from app.config import Config
    from app.main import create_apps

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(
        public_port=21011, admin_port=21012, public_base_url="https://task.example.com",
        data_dir=tmp, channel="wecom",
        wecom_corpid="corp", wecom_agentid="1000002", wecom_secret="sec",
    )
    pub, adm, store, chan = create_apps(config=cfg)
    try:
        assert store.platform == "wecom"
    finally:
        store.close()
        chan.close()


def test_member_create_and_update_read_current_channel() -> None:
    """add_role_member / update_role_member 返回的「已激活」也要跟当前通道走。

    管理端新增/编辑成员时，接口会把该成员在当前通道下是否有可用绑定一起返回，
    前端据此显示「已激活 / 未激活」。这两处原来写死 platform='zdxlz'，
    切到企业微信后会把量子密信的绑定当成企微已激活（或反之）。
    """
    store = _mk("wecom")
    try:
        rid = store.create_role("云中台软研队伍", actor="t")["role_id"]
        store.upsert_delivery_target(PHONE, "wecom_userid_006")

        # 企微下已有绑定 → 新增成员返回已激活
        created = store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
        assert created["activated"] is True

        # 切回量子密信：这边还没绑定 → 编辑成员不能显示成已激活
        store.platform = "zdxlz"
        updated = store.update_role_member(rid, PHONE, name="张三")
        assert updated["activated"] is False

        # 密信侧也绑上 → 已激活
        store.upsert_delivery_target(PHONE, "zdxlz_uid_006")
        updated2 = store.update_role_member(rid, PHONE, name="张三")
        assert updated2["activated"] is True

        # 再切回企微：仍然看企微的绑定，不受密信影响
        store.platform = "wecom"
        assert store.update_role_member(rid, PHONE, name="张三")["activated"] is True
    finally:
        store.close()
