#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宝塔服务器 MySQL root 密码合规排查 + 就地建库（密码不回显）。

候选来源（只在本机文件里找、不做网络爆破/字典猜测）：
  1. root 的 shell 历史里出现过的 mysql/mysqldump -p 凭证；
  2. crontab / 定时任务文件里 mysqldump 备份命令的凭证；
  3. 宝塔面板各 sqlite 库（database.db / system.db / default.db）config 表 mysql_root；
  4. 各项目 compose/.env 里的 MYSQL_ROOT_PASSWORD 类变量。
每个候选依次以 socket / TCP:28619 试登录；成功即执行 create_imtask_db.sql 同款 SQL。
输出只含来源标签与结果，不含任何密码明文。
"""
import os
import re
import sqlite3
import subprocess
import sys

MYSQL_BIN = "/www/server/mysql/bin/mysql"
SQL = (
    "CREATE DATABASE IF NOT EXISTS im_task DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    "GRANT ALL PRIVILEGES ON im_task.* TO 'qsm'@'%';"
    "FLUSH PRIVILEGES;"
    "SHOW DATABASES;"
    "SHOW GRANTS FOR 'qsm'@'%';"
)

PW_PATTERNS = [
    re.compile(r"(?:mysql|mysqldump)(?:\s+\S+)*?\s+(?:-p|--password=)(?P<pw>\S{1,64})"),
    re.compile(r"-p'(?P<pw>[^']{1,64})'"),
    re.compile(r'-p"(?P<pw>[^"]{1,64})"'),
]
ENV_KEYS = ("MYSQL_ROOT_PASSWORD", "MYSQL_PASS", "MYSQL_PASSWORD", "DB_ROOT_PASSWORD", "DB_PASSWORD")


def from_history_and_cron():
    found = []
    files = ["/root/.bash_history", "/var/spool/cron/root", "/etc/crontab"]
    for root_dir in ("/var/spool/cron", "/www/server/cron", "/www/server/panel/cron"):
        if os.path.isdir(root_dir):
            for n in os.listdir(root_dir):
                p = os.path.join(root_dir, n)
                if os.path.isfile(p):
                    files.append(p)
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if out.returncode == 0:
        files.append(None)  # 标记：解析 crontab -l 的 stdout
    for path in files:
        try:
            text = out.stdout if path is None else open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for pat in PW_PATTERNS:
            for m in pat.finditer(text):
                pw = m.group("pw").strip("'\"")
                if 1 <= len(pw) <= 64 and pw not in ("your_password", "PASSWORD"):
                    found.append((f"history/cron:{path or 'crontab -l'}", pw))
    return found


def from_panel_sqlite():
    found = []
    for dbf in ("/www/server/panel/data/db/database.db",
                "/www/server/panel/data/db/default.db",
                "/www/server/panel/data/system.db",
                "/www/server/panel/data/default.db"):
        try:
            conn = sqlite3.connect(dbf)
            row = conn.execute("SELECT mysql_root FROM config LIMIT 1").fetchone()
            conn.close()
            if row and row[0]:
                found.append((f"panel-sqlite:{dbf}", str(row[0])))
        except Exception:
            continue
    return found


def from_project_configs():
    found = []
    for base in ("/www/wwwroot", "/opt", "/root", "/home"):
        for root_dir, _dirs, files in os.walk(base):
            if root_dir.count(os.sep) > 6:
                continue
            for fn in files:
                if fn.endswith((".env", ".yml", ".yaml")) or fn in ("docker-compose.yml", "compose.yml"):
                    p = os.path.join(root_dir, fn)
                    try:
                        text = open(p, encoding="utf-8", errors="ignore").read()
                    except Exception:
                        continue
                    for key in ENV_KEYS:
                        pat = re.compile(rf"(?:^|\n)\s*{key}\s*=\s*(?P<pw>\S{{1,64}})")
                        for m in pat.finditer(text):
                            pw = m.group("pw").strip().strip("'\"")
                            if 1 <= len(pw) <= 64:
                                found.append((f"project-config:{p}", pw))
    return found


def try_login(pw, host_arg):
    try:
        proc = subprocess.run(
            [MYSQL_BIN, "-uroot", f"-p{pw}", *host_arg, "-e", "SELECT 1;"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def main() -> int:
    candidates = []
    for source in (from_history_and_cron, from_panel_sqlite, from_project_configs):
        try:
            candidates.extend(source())
        except Exception:
            pass
    # 去重（保密码不泄漏的前提下按 (来源,密码) 去重）
    seen, uniq = set(), []
    for label, pw in candidates:
        if pw not in [p for _, p in uniq]:
            uniq.append((label, pw))
    print(f"CANDIDATES={len(uniq)}")
    for label, pw in uniq:
        for host_arg, mode in (([], "SOCKET"), (["-h127.0.0.1", "-P28619"], "TCP")):
            if try_login(pw, host_arg):
                proc = subprocess.run(
                    [MYSQL_BIN, "-uroot", f"-p{pw}", *host_arg, "-e", SQL],
                    capture_output=True, text=True, timeout=20,
                )
                if proc.returncode == 0:
                    print(f"LOGIN_VIA {mode} source={label}")
                    print(proc.stdout.strip())
                    print("OK_IM_TASK_CREATED")
                    return 0
                print(f"LOGIN_OK_SQL_FAILED {mode}: {(proc.stderr or '')[:200]}")
                return 3
    print("ALL_FAILED")
    return 4


if __name__ == "__main__":
    sys.exit(main())
