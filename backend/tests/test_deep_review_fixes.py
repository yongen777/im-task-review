# -*- coding: utf-8 -*-
"""2026-11-05 深度复核问题修复的回归用例。

覆盖三处：
1. parse_admin_account 在 @staticmethod 里误用 self（role_level 为空时 NameError）
2. get_admin_session 未绑定账号 enabled 状态（直接改库停用账号，会话应立即失效）
3. 登录失败计数字典无容量上限（伪造用户名/IP 可无限膨胀）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import admin as admin_module  # noqa: E402
from app.state import StateStore  # noqa: E402


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_deep_")) / "im.db")


def test_parse_admin_account_blank_role_level() -> None:
    """role_level 为空时 parse_admin_account 不许炸，回落 sub_admin。"""
    store = _store()
    try:
        store.create_admin_account(username="legacy_row", display_name="旧数据")
        # 模拟历史/手工数据：把 role_level 清空（DDL 有默认值，但绕过默认写入就可能为空）
        with store.lock, store.conn:
            store.conn.execute("UPDATE admin_user SET role_level = '' WHERE username = 'legacy_row'")
            row = store.conn.execute("SELECT * FROM admin_user WHERE username = 'legacy_row'").fetchone()
        d = StateStore.parse_admin_account(row)
        assert d["role_level"] == StateStore.SUB_ADMIN, d
    finally:
        store.close()


def test_session_invalid_when_account_disabled_directly() -> None:
    """绕过 API 直接改库停用账号（enabled=0），既有会话必须立即失效。"""
    store = _store()
    try:
        acct = store.create_admin_account(username="s1", display_name="s1")
        sess = store.create_admin_session(int(acct["id"]))
        assert store.get_admin_session(sess["sid"]) is not None
        with store.lock, store.conn:
            store.conn.execute("UPDATE admin_user SET enabled = 0 WHERE id = ?", (int(acct["id"]),))
        assert store.get_admin_session(sess["sid"]) is None
    finally:
        store.close()


def test_login_failure_dict_bounded() -> None:
    """失败计数字典超过上限时淘汰 15 分钟未活跃的条目，不随攻击膨胀。"""
    lock = admin_module._LOGIN_FAIL_LOCK
    with lock:
        saved = dict(admin_module._LOGIN_FAILURES)
        admin_module._LOGIN_FAILURES.clear()
    try:
        import time as _time

        with lock:
            for i in range(admin_module._LOGIN_FAIL_MAX_KEYS):
                # 半小时前的陈旧失败：应当被淘汰
                admin_module._LOGIN_FAILURES[f"login:fake{i}:10.0.0.1"] = {
                    "count": 1.0, "locked_until": 0.0, "last_fail": _time.time() - 1800.0,
                }
        assert len(admin_module._LOGIN_FAILURES) >= admin_module._LOGIN_FAIL_MAX_KEYS
        admin_module._login_note_failure("login:newuser:10.0.0.2")
        assert len(admin_module._LOGIN_FAILURES) <= admin_module._LOGIN_FAIL_MAX_KEYS
        assert "login:newuser:10.0.0.2" in admin_module._LOGIN_FAILURES
    finally:
        with lock:
            admin_module._LOGIN_FAILURES.clear()
            admin_module._LOGIN_FAILURES.update(saved)
