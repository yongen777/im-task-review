#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-13 生产 MySQL 切换：仅同步两项基础配置（admin_user + channel_config）。

· 来源：/opt/imtask/data/im_task.db（sqlite，历史测试数据一概不迁）；
· 目标：MySQL im_task 库（刚由 init_schema 建好 18 表的全新库）；
· 通道配置 payload 为 AES-GCM 密文，原样拷贝（加密 key 在 .env 不变，解密即正常）；
· 幂等：目标表已有同名 username/channel 则跳过（INSERT IGNORE 语义）；
· 凭证从生产 .env 读取，不回显任何密码/哈希。
"""
import sqlite3
import sys
from pathlib import Path

import pymysql

SRC_DB = "/opt/imtask/data/im_task.db"
ENV_PATH = Path("/opt/imtask/.env")

ADMIN_COLS = ("id", "username", "pwd_hash", "must_change", "created_at",
              "display_name", "role_level", "allowed_platforms", "enabled", "last_login_at")
CHANNEL_COLS = ("id", "channel", "payload", "updated_at")


def load_env(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def copy_table(src, my, table: str, cols: tuple) -> tuple:
    rows = [dict(r) for r in src.execute(f"SELECT * FROM {table}").fetchall()]
    inserted = 0
    with my.cursor() as cur:
        for r in rows:
            values = [r.get(c) for c in cols]
            if table == "admin_user":
                cur.execute(
                    "INSERT IGNORE INTO admin_user"
                    "(id, username, pwd_hash, must_change, created_at, display_name, role_level, allowed_platforms, enabled, last_login_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", values)
            else:
                cur.execute(
                    "INSERT IGNORE INTO channel_config(id, channel, payload, updated_at) VALUES (%s,%s,%s,%s)", values)
            inserted += int(cur.rowcount or 0)
    my.commit()
    return len(rows), inserted


def main() -> int:
    env = load_env(ENV_PATH)
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    my = pymysql.connect(
        host=env.get("IMT_MYSQL_HOST", "127.0.0.1"), port=int(env.get("IMT_MYSQL_PORT", "28620")),
        user=env.get("IMT_MYSQL_USER", ""), password=env.get("IMT_MYSQL_PASSWORD", ""),
        database=env.get("IMT_MYSQL_DATABASE", "im_task"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )
    try:
        n1, i1 = copy_table(src, my, "admin_user", ADMIN_COLS)
        n2, i2 = copy_table(src, my, "channel_config", CHANNEL_COLS)
        with my.cursor() as cur:
            cur.execute("SELECT COUNT(1) c, SUM(role_level='super_admin') s FROM admin_user")
            a = cur.fetchone()
            cur.execute("SELECT channel, LENGTH(payload) plen FROM channel_config")
            ch = [(r["channel"], r["plen"]) for r in cur.fetchall()]
        print(f"admin_user: sqlite={n1} -> mysql inserted={i1} | now count={a['c']} super={a['s']}")
        print(f"channel_config: sqlite={n2} -> mysql inserted={i2} | now {ch}")
        print("SYNC_TWO_TABLES_OK")
        return 0
    finally:
        src.close()
        my.close()


if __name__ == "__main__":
    sys.exit(main())
