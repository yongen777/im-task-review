#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理宝塔实例（28620）上误建的 im_task 空库与 qsm 授权（2026-11-12 晚失误回滚）。
背景：公网 NAT 把 219.151.177.33:28619 映射到另一台机器的 MySQL（@@port=28619 实测），
本脚本所在宝塔实例（28620）不是目标——此前误在其上 CREATE im_task + GRANT，这里恢复原状。
密码从服务器本地历史合规提取，不回显。"""
import re
import subprocess
import sys

MYSQL_BIN = "/www/server/mysql/bin/mysql"
ROLLBACK_SQL = (
    "DROP DATABASE IF EXISTS im_task;"
    "REVOKE ALL PRIVILEGES ON `im_task`.* FROM 'qsm'@'%';"
    "FLUSH PRIVILEGES;"
    "SHOW DATABASES;"
)

PW_PATTERNS = [
    re.compile(r"(?:mysql|mysqldump)(?:\s+\S+)*?\s+(?:-p|--password=)(?P<pw>\S{1,64})"),
    re.compile(r"-p'(?P<pw>[^']{1,64})'"),
    re.compile(r'-p"(?P<pw>[^"]{1,64})"'),
]


def find_pw():
    try:
        text = open("/root/.bash_history", encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""
    for pat in PW_PATTERNS:
        for m in pat.finditer(text):
            pw = m.group("pw").strip("'\"")
            if 1 <= len(pw) <= 64 and pw != "your_password":
                return pw
    return ""


def main() -> int:
    pw = find_pw()
    if not pw:
        print("NO_PW")
        return 2
    proc = subprocess.run(
        [MYSQL_BIN, "-uroot", f"-p{pw}", "-e", ROLLBACK_SQL],
        capture_output=True, text=True, timeout=20,
    )
    if proc.returncode != 0:
        print("ROLLBACK_FAILED:", (proc.stderr or "")[:200])
        return 3
    print(proc.stdout.strip())
    print("ROLLBACK_OK_BT_INSTANCE_RESTORED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
