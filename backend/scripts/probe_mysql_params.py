# -*- coding: utf-8 -*-
"""MySQL 5.7.43（kfcshj 实例 / qsm@219.151.177.33:28619）兼容性只读参数采集——
模块一（双引擎数据层）建表前的工程适配依据。全程只读。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    import pymysql

    conn = pymysql.connect(
        host=os.environ.get("IMT_MYSQL_HOST", ""), port=int(os.environ.get("IMT_MYSQL_PORT", "3306")),
        user=os.environ.get("IMT_MYSQL_USER", ""), password=os.environ.get("IMT_MYSQL_PASSWORD", ""),
        connect_timeout=6, read_timeout=10, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    out = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT @@version AS ver, @@version_comment AS comment, @@hostname AS host, @@port AS port, "
            "@@sql_mode AS sql_mode, @@character_set_server AS charset_srv, @@collation_server AS coll_srv, "
            "@@innodb_default_row_format AS row_fmt, @@max_connections AS max_conn, "
            "@@innodb_lock_wait_timeout AS lock_wait, @@wait_timeout AS wait_to, "
            "@@max_allowed_packet AS max_packet, @@transaction_isolation AS isolation, "
            "@@log_bin AS binlog, @@innodb_flush_log_at_trx_commit AS flush_commit, "
            "@@explicit_defaults_for_timestamp AS ts_defaults")
        row = cur.fetchone()
        out.update({k: (str(v) if v is not None else None) for k, v in row.items()})
        # 索引长度关键参数（767/3072 陷阱判定）
        cur.execute("SHOW GLOBAL VARIABLES LIKE 'innodb_large_prefix'")
        _lp = cur.fetchone()
        if _lp:
            out["innodb_large_prefix"] = list(_lp.values())[1]
        cur.execute("SHOW GLOBAL VARIABLES LIKE 'innodb_file_format'")
        _ff = cur.fetchone()
        if _ff:
            out["innodb_file_format"] = list(_ff.values())[1]
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='im_task'")
        out["im_task_exists"] = int(cur.fetchone()["n"]) > 0
    conn.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
