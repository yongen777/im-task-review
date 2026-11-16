# -*- coding: utf-8 -*-
"""2026-11-02 角色按办公软件隔离：迁移与约束验收。

- 旧库（role.name 单列 UNIQUE）升级后：历史角色全部 platform='zdxlz'，不丢数据；
- 同名角色允许存在于不同办公软件（(platform, name) 唯一）；
- 同一模板不能混选两个平台的角色；
- 待归纳人员不能跨平台移入角色。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402
from app.state import BizError, StateStore


def test_old_role_table_migrates_to_platform() -> None:
    """方案B（2026-11-16）：原「旧 SQLite 库升级迁移」用例随双引擎退役——
    MySQL DDL 出生即带 platform 列（DEFAULT 'zdxlz'），历史行为由默认值保证。
    这里保留同类约束验收：同名角色分平台共存 + (platform, name) 唯一。
    """
    store = StateStore(mysql_cfg=mysql_test_cfg())
    try:
        z1 = store.create_role("片长/分支局长", actor="t", platform="zdxlz")
        assert z1["platform"] == "zdxlz"

        # 迁移后可创建同名企微角色
        new_role = store.create_role("云中台软研队伍", actor="t", platform="wecom")
        assert new_role["platform"] == "wecom"
        assert len(store.list_roles(platform="wecom")) == 1
        assert len(store.list_roles(platform="zdxlz")) == 1

        with pytest.raises(BizError) as exc:
            store.create_role("云中台软研队伍", actor="t", platform="wecom")
        assert exc.value.code == "ROLE_NAME_TAKEN"
    finally:
        store.close()


def test_template_cannot_mix_platform_roles() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="imtask_mix_"))
    store = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp)
    try:
        z = store.create_role("密信组", actor="t", platform="zdxlz")["role_id"]
        w = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        with pytest.raises(BizError) as exc:
            store.create_template(
                {
                    "name": "混选模板", "kind": "notice", "role_ids": [z, w],
                    "text_tpl": "x", "notice_times": ["08:00"], "period": "day", "enabled": True,
                },
                actor="t",
            )
        assert exc.value.code == "VALIDATION_FAILED"
        assert "同一办公软件" in exc.value.message
    finally:
        store.close()

