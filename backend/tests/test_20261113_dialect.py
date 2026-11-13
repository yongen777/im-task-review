# -*- coding: utf-8 -*-
"""2026-11-13 · 冲刺模块一：SQL 方言翻译层（sqlite → mysql）单测。

state.py 的全部业务 SQL 在 mysql 引擎下经过 `_translate_sql`；本用例覆盖五族规则，
不连任何外部库（翻译是纯函数）；mysql 真库冒烟单独跑（IMT_TEST_MYSQL=1 时启用）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import _translate_sql  # noqa: E402


def test_placeholder_becomes_mysql_style() -> None:
    assert _translate_sql("SELECT * FROM member WHERE phone = ? AND name = ?") == \
        "SELECT * FROM member WHERE phone = %s AND name = %s"


def test_placeholder_inside_string_literal_untouched() -> None:
    sql = "UPDATE t SET note = '要问吗？yes' WHERE id = ?"
    out = _translate_sql(sql)
    assert "%s" in out and "要问吗？yes" in out
    # 全角 ？（引号内文本）不是占位符，不被替换
    assert out.count("要问吗？yes") == 1


def test_insert_or_ignore_to_insert_ignore() -> None:
    out = _translate_sql(
        "INSERT OR IGNORE INTO task_instance(template_id, phone, period_key) VALUES (?, ?, ?)")
    assert out.startswith("INSERT IGNORE INTO task_instance")
    assert out.count("%s") == 3


def test_on_conflict_to_on_duplicate_key() -> None:
    sql = (
        "INSERT INTO data_snapshot(source_key, period_key, values_json, note, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source_key, period_key) DO UPDATE SET values_json = excluded.values_json, "
        "note = excluded.note, updated_by = excluded.updated_by, updated_at = excluded.updated_at"
    )
    out = _translate_sql(sql)
    assert "ON DUPLICATE KEY UPDATE" in out
    assert "values_json=VALUES(values_json)" in out
    assert "updated_at=VALUES(updated_at)" in out
    assert "excluded." not in out
    assert out.count("%s") == 6


def test_rowid_and_last_insert_rowid() -> None:
    out = _translate_sql(
        "AND kind != 'notice' ORDER BY created_at ASC, rowid ASC")
    assert "ORDER BY created_at ASC, id ASC" in out
    assert _translate_sql("SELECT last_insert_rowid()") == "SELECT LAST_INSERT_ID()"
    # 词边界：rowidx 之类的列名不被误替换
    assert _translate_sql("SELECT rowidx FROM t") == "SELECT rowidx FROM t"


def test_backtick_and_double_quote_literals() -> None:
    sql = "SELECT `a?b` FROM t WHERE c = ?"
    out = _translate_sql(sql)
    assert "`a?b`" in out and out.count("%s") == 1
