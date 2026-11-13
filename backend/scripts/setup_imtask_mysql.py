#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 schj-z 宝塔 MySQL（28620）上初始化 IMTask 底座（用户 2026-11-13 上午授权使用本机）。

动作（全部只作用于 im_task 自己的对象，零触碰其它库/账号/配置）：
  1. CREATE DATABASE im_task（utf8mb4 / utf8mb4_unicode_ci）
  2. CREATE USER 'imtask'@'%' IDENTIFIED BY <随机强密码>（IMTask 专用账号，与 qsm / 知识库无关）
  3. GRANT ALL PRIVILEGES ON im_task.* TO 'imtask'@'%'
  4. 用新账号自验（127.0.0.1:28620 连接 + 建表/删表冒烟）
root 凭证来源：/root/.bash_history（服务器本机合规提取，不回显）。
新账号密码仅输出一次（用于写入 .env，不入任何仓库）。
先验实例指纹（@@port=28620 / hostname=schj-z）再动手。
"""
import re
import secrets
import subprocess
import sys

MYSQL_BIN = "/www/server/mysql/bin/mysql"
FINGERPRINT_OK = ("28620", "schj-z")

PW_PATTERNS = [
    re.compile(r"(?:mysql|mysqldump)(?:\s+\S+)*?\s+(?:-p|--password=)(?P<pw>\S{1,64})"),
    re.compile(r"-p'(?P<pw>[^']{1,64})'"),
    re.compile(r'-p"(?P<pw>[^"]{1,64})"'),
]


def find_root_pw() -> str:
    text = open("/root/.bash_history", encoding="utf-8", errors="ignore").read()
    for pat in PW_PATTERNS:
        for m in pat.finditer(text):
            pw = m.group("pw").strip("'\"")
            if 1 <= len(pw) <= 64 and pw != "your_password":
                return pw
    return ""


def run_mysql(pw, sql, timeout=20):
    proc = subprocess.run(
        [MYSQL_BIN, "-uroot", f"-p{pw}", "-e", sql],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc


def main() -> int:
    root_pw = find_root_pw()
    if not root_pw:
        print("NO_ROOT_PW")
        return 2
    # 0) 实例指纹校验（本次教训制度化：动手前必验指纹）
    fp = run_mysql(root_pw, "SELECT @@port, @@hostname;")
    if fp.returncode != 0 or not all(x in fp.stdout for x in FINGERPRINT_OK):
        print("FINGERPRINT_MISMATCH:", fp.stdout.strip())
        return 3
    print("FINGERPRINT_OK port=28620 host=schj-z")
    new_pw = secrets.token_urlsafe(18)
    sql = (
        "CREATE DATABASE IF NOT EXISTS im_task DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        f"CREATE USER IF NOT EXISTS 'imtask'@'%' IDENTIFIED BY '{new_pw}';"
        f"ALTER USER 'imtask'@'%' IDENTIFIED BY '{new_pw}';"
        "GRANT ALL PRIVILEGES ON im_task.* TO 'imtask'@'%';"
        "FLUSH PRIVILEGES;"
        "SHOW DATABASES LIKE 'im_task';"
        "SHOW GRANTS FOR 'imtask'@'%';"
    )
    proc = run_mysql(root_pw, sql)
    if proc.returncode != 0:
        print("SETUP_FAILED:", (proc.stderr or "")[:300])
        return 4
    print(proc.stdout.strip())
    # 新账号自验：本机 28620 连接 + 建删冒烟表
    verify = subprocess.run(
        [MYSQL_BIN, "-uimtask", f"-p{new_pw}", "-h127.0.0.1", "-P28620", "im_task",
         "-e", "CREATE TABLE IF NOT EXISTS imt_smoke(id INT PRIMARY KEY); DROP TABLE imt_smoke; SELECT DATABASE();"],
        capture_output=True, text=True, timeout=15,
    )
    if verify.returncode != 0:
        print("NEW_ACCOUNT_VERIFY_FAILED:", (verify.stderr or "")[:300])
        return 5
    print("NEW_ACCOUNT_VERIFY_OK:", verify.stdout.strip())
    print(f"IMTASK_MYSQL_PASSWORD={new_pw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
