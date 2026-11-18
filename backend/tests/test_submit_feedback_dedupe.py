# -*- coding: utf-8 -*-
"""已取消机器人提交回执后，保留多个 H5 用例共用的任务实例夹具。"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402
from app.config import format_dt  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13600001234"


def _store() -> StateStore:
    return StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_ack_")))


def _instance(store: StateStore, platform: str):
    role = store.create_role(f"{platform}回推组", actor="t", platform=platform)
    store.add_role_member(role["role_id"], phone=PHONE, name="钱八")
    store.upsert_delivery_target(PHONE, "uid_1", platform=platform)
    tpl = store.create_template(
        {
            "name": "回推任务", "kind": "form", "role_ids": [role["role_id"]],
            "schema_json": [{"key": "n", "label": "户数", "type": "number", "required": True}],
            "text_tpl": "x", "remind_at": "08:00", "deadline": "23:59", "period": "day",
            "need_review": False, "enabled": True,
        },
        actor="t",
    )
    now = store.now().replace(tzinfo=None, microsecond=0)
    return store.ensure_template_instances(
        tpl["template_id"], format_dt(now)[:10], format_dt(now + timedelta(hours=8))
    )[0]
