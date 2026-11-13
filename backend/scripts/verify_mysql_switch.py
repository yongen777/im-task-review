#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产 MySQL 切换后最终验证（不回显任何密码）。"""
import os
import sys
from pathlib import Path

import pymysql

ENV = Path("/opt/imtask/.env")
for line in ENV.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line and "IMT_MYSQL" in line.split("=", 1)[0]:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

c = pymysql.connect(
    host=os.environ["IMT_MYSQL_HOST"], port=int(os.environ["IMT_MYSQL_PORT"]),
    user=os.environ["IMT_MYSQL_USER"], password=os.environ["IMT_MYSQL_PASSWORD"],
    database=os.environ["IMT_MYSQL_DATABASE"], charset="utf8mb4",
)
cur = c.cursor()
cur.execute("SELECT COUNT(1) FROM information_schema.tables WHERE table_schema = DATABASE()")
print("TABLES:", cur.fetchone()[0])
cur.execute("SELECT COUNT(1), COALESCE(SUM(role_level = 'super_admin'), 0) FROM admin_user")
row = cur.fetchone()
print(f"ADMIN_USERS: {row[0]} (super_admin {row[1]})")
cur.execute("SELECT COUNT(1) FROM channel_config")
print("CHANNEL_CONFIGS:", cur.fetchone()[0])
c.close()
print("VERIFY_OK")
sys.exit(0)
