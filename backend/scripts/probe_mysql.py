# -*- coding: utf-8 -*-
"""MySQL 8.0 底座切换 · **只读探针**（2026-11-12 冲刺模块一第一步）。

用户指令铁律：
  · 在执行任何写操作前，必须先进行只读探测（本脚本全程零写入：不建库、不建表、不 INSERT）；
  · 严禁对实例中任何非本系统的既有库表执行删除、修改或清空；
  · 探测结论二选一：
      ① 账号具备 CREATE 权限 → 优先方案：独立库 `im_task`（utf8mb4）隔离；
      ② 无建库权限 → 备选方案：所有表名统一 `imt_` 前缀，建在账号被授权的既有库内。

用法：python scripts/probe_mysql.py [--env .env]
凭证从环境变量 / .env 读取（IMT_MYSQL_HOST/PORT/USER/PASSWORD/DATABASE），不硬编码。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_env(env_path: Path) -> None:
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=str(ROOT / ".env"))
    args = parser.parse_args()
    _load_env(Path(args.env))

    try:
        import pymysql
    except ImportError:
        print("MISSING_DRIVER: pip install pymysql")
        return 2

    host = os.environ.get("IMT_MYSQL_HOST", "")
    port = int(os.environ.get("IMT_MYSQL_PORT", "3306"))
    user = os.environ.get("IMT_MYSQL_USER", "")
    password = os.environ.get("IMT_MYSQL_PASSWORD", "")
    target_db = os.environ.get("IMT_MYSQL_DATABASE", "im_task")

    if not (host and user and password):
        print("MISSING_CONFIG: IMT_MYSQL_HOST/USER/PASSWORD")
        return 2

    report: dict = {"host": host, "port": port, "user": user, "target_database": target_db}

    conn = pymysql.connect(
        host=host, port=port, user=user, password=password,
        connect_timeout=6, read_timeout=10, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            # 0) 服务端自报 TCP 端口/主机名/版本注释（实例指纹：判定公网 28619 与本机实例的关系）
            cur.execute("SELECT @@port AS p, @@version_comment AS c, @@hostname AS h")
            _row0 = cur.fetchone()
            report["server_port"] = _row0["p"]
            report["server_version_comment"] = _row0["c"]
            report["server_hostname"] = _row0["h"]
            # 1) 服务器版本
            cur.execute("SELECT VERSION() AS v")
            report["server_version"] = cur.fetchone()["v"]

            # 2) 当前用户与授权（SHOW GRANTS 是纯读）
            cur.execute("SELECT CURRENT_USER() AS u")
            report["current_user"] = cur.fetchone()["u"]
            cur.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = [list(r.values())[0] for r in cur.fetchall()]
            report["grants"] = grants

            # 3) 实例中现存全部数据库（只读清点，用于隔离避让）
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME NOT IN ('information_schema','mysql','performance_schema','sys') "
                "ORDER BY SCHEMA_NAME")
            report["existing_databases"] = [r["SCHEMA_NAME"] for r in cur.fetchall()]

            # 4) 各库表名清单（只读，明确「知识库机器人」等既有数据所在，供前缀避让核对）
            cur.execute(
                "SELECT TABLE_SCHEMA AS db, TABLE_NAME AS tbl FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA NOT IN ('information_schema','mysql','performance_schema','sys') "
                "ORDER BY TABLE_SCHEMA, TABLE_NAME")
            per_db: dict = {}
            for r in cur.fetchall():
                per_db.setdefault(r["db"], []).append(r["tbl"])
            report["tables_per_database"] = per_db
            # 前缀冲突核对：imt_ 前缀是否与既有任何表撞名
            all_tables = [t for tbls in per_db.values() for t in tbls]
            report["imt_prefix_conflicts"] = [t for t in all_tables if t.lower().startswith("imt_")]

            # 5) 目标库 im_task 是否已存在；存在则清点其表（可能是本系统旧表）
            report["target_database_exists"] = target_db in report["existing_databases"]
            if report["target_database_exists"]:
                cur.execute(
                    "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                    (target_db,))
                report["target_database_tables"] = [r["TABLE_NAME"] for r in cur.fetchall()]

            # 6) 建库权限判定（纯靠 GRANT 文本，不做任何真实 CREATE 试写）
            grant_text = " ".join(grants).upper()
            has_global_create = (
                "CREATE ON *.*" in grant_text.replace("`", "")
                or "ALL PRIVILEGES ON *.*" in grant_text
                or "ALL ON *.*" in grant_text
            )
            has_target_create = f"CREATE ON `{target_db.upper()}`".upper().replace("`", "") in grant_text.replace("`", "").upper() and False
            # 库级 CREATE 授权在 SHOW GRANTS 里形如 `GRANT ... ON `im_task`.*`，且要求库已存在；
            # 建新库必须全局 CREATE，因此以全局判定为准
            report["can_create_database"] = bool(has_global_create)
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
