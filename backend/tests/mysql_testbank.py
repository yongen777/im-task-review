# -*- coding: utf-8 -*-
"""方案B（2026-11-16）测试底座：纯 MySQL `im_task_test` 共享连接池。

为什么单独一个模块：
  · conftest / 各测试文件都要建 StateStore（每用例一个），若每个 StateStore 自建
    PooledDB，500+ 用例会把本机 MySQL max_connections 打爆——这里全测试共享一个池
    （mincached=2 / maxconnections=12），池注入走 MySQLConnProxy 的 cfg["pool"]。

安全边界（防误连生产熔断锁）：见 tests/conftest.py —— 库名必须 `_test` 结尾、
Host 必须本机；本模块只 obey 环境变量 IMT_TEST_MYSQL_*，绝不读取生产 IMT_MYSQL_*。
"""
from __future__ import annotations

import os
import threading

TEST_CFG_BASE = {
    "host": (os.environ.get("IMT_TEST_MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1"),
    "port": int(os.environ.get("IMT_TEST_MYSQL_PORT", "3306")),
    "user": os.environ.get("IMT_TEST_MYSQL_USER", "root"),
    "password": os.environ.get("IMT_TEST_MYSQL_PASSWORD", "123456"),
    "database": (os.environ.get("IMT_TEST_MYSQL_DATABASE", "im_task_test").strip() or "im_task_test"),
    "charset": "utf8mb4",
}

_POOL_LOCK = threading.Lock()
_POOL = None


def mysql_test_cfg() -> dict:
    """返回带共享连接池的 StateStore 连接 cfg（所有测试用例复用同一个池）。"""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            import pymysql
            from dbutils.pooled_db import PooledDB

            _POOL = PooledDB(
                creator=pymysql, mincached=2, maxconnections=12, blocking=True, ping=1,
                host=TEST_CFG_BASE["host"], port=TEST_CFG_BASE["port"],
                user=TEST_CFG_BASE["user"], password=TEST_CFG_BASE["password"],
                database=TEST_CFG_BASE["database"], charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor, autocommit=False,
                # 与 app/db.py 对齐：rowcount=匹配行数（SQLite 时代业务口径）
                client_flag=pymysql.constants.CLIENT.FOUND_ROWS,
            )
    return {**TEST_CFG_BASE, "pool": _POOL}


def close_shared_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.close()
            except Exception:  # noqa: BLE001
                pass
            _POOL = None


def truncate_all_tables() -> None:
    """清空测试库全部表（用例间隔离；表/种子行由 Database.init_schema 自愈重建）。"""
    import pymysql

    conn = pymysql.connect(
        host=TEST_CFG_BASE["host"], port=TEST_CFG_BASE["port"],
        user=TEST_CFG_BASE["user"], password=TEST_CFG_BASE["password"],
        database=TEST_CFG_BASE["database"], charset="utf8mb4", autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [r[0] if isinstance(r, (list, tuple)) else next(iter(r.values())) for r in cur.fetchall()]
            for t in tables:
                cur.execute(f"TRUNCATE TABLE `{t}`")
    finally:
        conn.close()