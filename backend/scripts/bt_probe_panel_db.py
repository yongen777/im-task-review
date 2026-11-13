#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探明宝塔面板 default.db 里 MySQL root 密码的存放位置（只打印结构/候选长度，不打印明文）。"""
import sqlite3

conn = sqlite3.connect("/www/server/panel/data/default.db")
for (tbl,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")]
    print(f"TABLE {tbl}: {cols}")
    if tbl == "config":
        for row in conn.execute("SELECT * FROM config LIMIT 1"):
            for c, v in zip(cols, row):
                s = str(v)
                print(f"  {c} = <len {len(s)}> {s[:3]}***" if "root" in c.lower() or "pass" in c.lower() or "mysql" in c.lower() else f"  {c} = {s[:24]}")
    if tbl in ("users",):
        for row in conn.execute(f"SELECT * FROM {tbl} LIMIT 2"):
            for c, v in zip(cols, row):
                s = str(v)
                print(f"  {c} = <len {len(s)}>")
conn.close()
