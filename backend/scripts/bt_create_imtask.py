#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在宝塔服务器上：用面板存储的 MySQL root 密码（不打印）以多种连接方式尝试登录，
成功即创建 im_task 独立库并给 qsm 赋权（scripts/create_imtask_db.sql 同款 SQL）。
只回显登录方式与执行结果，密码全程不出服务器。
"""
import sqlite3
import subprocess
import sys

MYSQL_BIN = "/www/server/mysql/bin/mysql"
PANEL_DEFAULT_DB = "/www/server/panel/data/default.db"
SQL = (
    "CREATE DATABASE IF NOT EXISTS im_task DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    "GRANT ALL PRIVILEGES ON im_task.* TO 'qsm'@'%';"
    "FLUSH PRIVILEGES;"
    "SHOW DATABASES;"
    "SHOW GRANTS FOR 'qsm'@'%';"
)


def candidate_passwords():
    pw = ""
    try:
        conn = sqlite3.connect(PANEL_DEFAULT_DB)
        row = conn.execute("SELECT mysql_root FROM config LIMIT 1").fetchone()
        conn.close()
        if row and row[0]:
            pw = str(row[0])
    except Exception:
        pw = ""
    out = []
    if pw:
        out.append(("default.db:mysql_root", pw))
    return out


def try_login(pw: str, host_arg):
    proc = subprocess.run(
        [MYSQL_BIN, "-uroot", f"-p{pw}", *host_arg, "-e", "SELECT 1;"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0


def main() -> int:
    candidates = candidate_passwords()
    if not candidates:
        print("NO_CANDIDATE")
        return 2
    for label, pw in candidates:
        for host_arg, mode in ((["-h127.0.0.1", "-P28619"], "TCP-127.0.0.1:28619"), ([], "SOCKET")):
            if try_login(pw, host_arg):
                proc = subprocess.run(
                    [MYSQL_BIN, "-uroot", f"-p{pw}", *host_arg, "-e", SQL],
                    capture_output=True, text=True, timeout=20,
                )
                if proc.returncode == 0:
                    print(f"LOGIN_VIA {mode} (source: {label})")
                    print(proc.stdout.strip())
                    print("OK_IM_TASK_CREATED")
                    return 0
                print(f"LOGIN_OK_BUT_SQL_FAILED {mode}: {(proc.stderr or '')[:200]}")
                return 3
            print(f"LOGIN_DENIED {mode} (source: {label})")
    print("ALL_CANDIDATES_FAILED")
    return 4


if __name__ == "__main__":
    sys.exit(main())
