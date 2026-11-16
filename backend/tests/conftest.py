# -*- coding: utf-8 -*-
"""方案B 测试底座（2026-11-16）：纯 MySQL `im_task_test` + 防误连生产熔断锁。

铁律（用户拍板）：
  · 测试目标库必须 `_test` 结尾、Host 必须本机（127.0.0.1 / localhost）；
  · ⛔ 严禁测试代码在任何情况下连接生产库 `im_task` 或知识库 `qsm**` ——
    不满足直接 pytest.exit() 中止（不是 warning，是硬熔断）。
用例隔离：每个用例开始前 TRUNCATE 全部表；建表 + 种子行（schema_version / root scope）
由 `Database.init_schema` 自愈重建。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mysql_testbank import TEST_CFG_BASE, close_shared_pool, truncate_all_tables  # noqa: E402

# ── 防误连生产熔断锁（测试套件初始化连接前必须过；不满足直接中止） ──────────
_TEST_HOST = TEST_CFG_BASE["host"]
_TEST_DB = TEST_CFG_BASE["database"]
_problems: list[str] = []
if not _TEST_DB.endswith("_test"):
    _problems.append(f"目标库名必须以 _test 结尾（当前 {_TEST_DB!r}）")
if _TEST_HOST not in ("127.0.0.1", "localhost"):
    _problems.append(f"目标 Host 必须为本机（当前 {_TEST_HOST!r}）")
if _TEST_DB in ("im_task", "qsm", "qsm_bot") or _TEST_DB.startswith("qsm"):
    _problems.append(f"目标库 {_TEST_DB!r} 是生产/知识库，严禁作为测试库")
if _problems:
    pytest.exit("⛔ 防误连生产熔断锁已触发：" + "；".join(_problems), returncode=1)

# 强制覆盖连接环境变量（防外部 .env / shell 残留把测试指到生产），并清除旧的引擎开关
os.environ["IMT_MYSQL_HOST"] = _TEST_HOST
os.environ["IMT_MYSQL_PORT"] = str(TEST_CFG_BASE["port"])
os.environ["IMT_MYSQL_USER"] = TEST_CFG_BASE["user"]
os.environ["IMT_MYSQL_PASSWORD"] = TEST_CFG_BASE["password"]
os.environ["IMT_MYSQL_DATABASE"] = _TEST_DB
os.environ.pop("IMT_DB_ENGINE", None)  # 方案B：双引擎开关已移除，任何残留值一并丢弃


def _ensure_test_database() -> None:
    """首次引导：连本机 MySQL（不带库名）建出 im_task_test（utf8mb4）。"""
    import pymysql

    conn = pymysql.connect(
        host=_TEST_HOST, port=TEST_CFG_BASE["port"], user=TEST_CFG_BASE["user"],
        password=TEST_CFG_BASE["password"], charset="utf8mb4", autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{_TEST_DB}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


_ensure_test_database()


@pytest.fixture(autouse=True)
def _fresh_test_db():
    """每个用例开始前：重注入连接环境 + 清空全部表 —— 用例间完全隔离，绝不串状态。

    重注入原因（2026-11-16 实锤）：test_stage_2a / test_stage_2b / test_h5_img_page 等
    旧用例会 `os.environ.clear()` 隔离 Config 测试 → 把本 conftest 注入的 IMT_MYSQL_*
    一并抹掉；SQLite 临时库时代无害，纯 MySQL 后同进程后续用例直接 RuntimeError。
    """
    os.environ["IMT_MYSQL_HOST"] = _TEST_HOST
    os.environ["IMT_MYSQL_PORT"] = str(TEST_CFG_BASE["port"])
    os.environ["IMT_MYSQL_USER"] = TEST_CFG_BASE["user"]
    os.environ["IMT_MYSQL_PASSWORD"] = TEST_CFG_BASE["password"]
    os.environ["IMT_MYSQL_DATABASE"] = _TEST_DB
    os.environ.pop("IMT_DB_ENGINE", None)
    truncate_all_tables()
    yield


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """测试会话结束关闭共享连接池，释放本机 MySQL 连接。"""
    close_shared_pool()