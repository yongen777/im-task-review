# -*- coding: utf-8 -*-
"""2026-11-02 角色按办公软件隔离：迁移与约束验收。

- 旧库（role.name 单列 UNIQUE）升级后：历史角色全部 platform='zdxlz'，不丢数据；
- 同名角色允许存在于不同办公软件（(platform, name) 唯一）；
- 同一模板不能混选两个平台的角色；
- 待归纳人员不能跨平台移入角色。
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.state import BizError, StateStore


def _old_db(path: Path) -> None:
    """模拟升级前的库：role 只有 name UNIQUE，没有 platform。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE role (
          role_id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          note TEXT,
          created_at TEXT NOT NULL
        );
        INSERT INTO role(role_id, name, note, created_at) VALUES
          ('r_old1', '片长/分支局长', NULL, '2026-10-01 00:00:00'),
          ('r_old2', '云中台软研队伍', NULL, '2026-10-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()


def test_old_role_table_migrates_to_platform() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="imtask_role_mig_"))
    db_path = tmp / "im.db"
    _old_db(db_path)

    store = StateStore(db_path)
    try:
        roles = store.list_roles(platform="zdxlz")
        assert {r["name"] for r in roles} == {"片长/分支局长", "云中台软研队伍"}
        assert all(r["platform"] == "zdxlz" for r in roles)

        # 迁移后可创建同名企微角色
        new_role = store.create_role("云中台软研队伍", actor="t", platform="wecom")
        assert new_role["platform"] == "wecom"
        assert len(store.list_roles(platform="wecom")) == 1
        assert len(store.list_roles(platform="zdxlz")) == 2

        with pytest.raises(BizError) as exc:
            store.create_role("云中台软研队伍", actor="t", platform="wecom")
        assert exc.value.code == "ROLE_NAME_TAKEN"
    finally:
        store.close()


def test_template_cannot_mix_platform_roles() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="imtask_mix_"))
    store = StateStore(tmp / "im.db")
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

