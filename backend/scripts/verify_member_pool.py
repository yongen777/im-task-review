#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""member-pool MySQL 修复验证：直接调 StateStore.list_member_pool（只读，不动数据）。"""
import os
import sys
from pathlib import Path

ENV = Path("/opt/imtask/.env")
for line in ENV.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "/opt/imtask")
from app.state import StateStore  # noqa: E402

store = StateStore(Path("/opt/imtask/data/im_task.db"))
try:
    for plat in ("zdxlz", "wecom", ""):
        items = store.list_member_pool(platform=plat or None)
        print(f"pool(platform={plat!r}): {len(items)} items")
    items = store.list_member_pool(q="测试", platform="zdxlz")
    print(f"pool(q=测试): {len(items)} items")
    print("MEMBER_POOL_OK")
finally:
    store.db.close()
