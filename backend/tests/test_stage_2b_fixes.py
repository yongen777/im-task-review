# -*- coding: utf-8 -*-
"""阶段 2B 修正包（AI Studio 第四次交付）的验收用例。

这批改了 5 处，但它自己那 12 条单测**没有随文件交付**，所以这里按可观测行为重新锁一遍。
每一例都对应一条线上会出事的具体后果，⛔ 不是照着实现抄断言。

1. 重试退避：失败后必须按 30 / 120 / 300 秒退避，⛔ 不能 30 秒一跳烧完 3 次
2. attempted_at：没有"本次尝试发生在什么时候"就根本算不出退避
3. 500 无会话：⛔ 不能删绑定，只能标 usable=0，external_id 要留着排障
4. 迁移：旧库（单列 UNIQUE）必须是显式事务迁移，数据不丢、attempted_at 回填
5. 上传：分块读到上限即中止，超限⛔ 不留磁盘残留
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from app.api import h5 as h5_api
from app.config import SHANGHAI_TZ
from app.scheduler import FakeNotifier, Scheduler
from app.state import StateStore

PHONE = "13800138099"
EXT_ID = "3000000000000000001"
BASE = datetime(2026, 10, 26, 18, 0, 5, tzinfo=SHANGHAI_TZ)


def _store(name="im.db") -> StateStore:
    tmp = Path(tempfile.mkdtemp())
    return StateStore(tmp / name)


def _setup_task(state: StateStore, kind="form", deadline="19:00"):
    role = state.create_role("开发", None)
    state.add_role_member(role["role_id"], PHONE, "张三", "云中台")
    state.bind_member_with_guardrails(phone=PHONE, external_id=EXT_ID, path="C")
    payload = {
        "name": "每日日报", "kind": kind, "role_ids": [role["role_id"]],
        "text_tpl": "{姓名} 请提交{任务名}", "period": "day",
        "remind_at": "18:00", "deadline": deadline,
    }
    if kind == "form":
        payload["schema_json"] = [
            {"key": "done", "label": "完成", "type": "text", "required": True}
        ]
    return state.create_template(payload)


def _retries(state: StateStore):
    return [r["retry"] for r in state.conn.execute(
        "SELECT retry FROM reminder_log ORDER BY retry, id").fetchall()]


def test_retry_respects_backoff_delays():
    """失败后 0/30/60 秒三跳都⛔ 不该重试；到 35s 才 retry=1，155s 才 2，455s 才 3。"""
    state = _store()
    _setup_task(state)
    notifier = FakeNotifier(now_fn=lambda: BASE)
    notifier.fail_receive_ids.add(EXT_ID)
    sched = Scheduler(state=state, public_base_url="http://127.0.0.1:18090",
                      notifier=notifier, now_fn=lambda: BASE)

    offsets = [0, 29, 30, 149, 150, 449, 450, 460]
    seen = {}
    for off in offsets:
        now = BASE + timedelta(seconds=off)
        sched._now_fn = (lambda c: (lambda: c))(now)
        sched.run_once(now)
        seen[off] = _retries(state)

    assert seen[0] == [0], "第一轮应只有 retry=0"
    assert seen[29] == [0], "不足 30s ⛔ 不能重试"
    assert seen[30] == [0, 1], "满 30s 才允许 retry=1"
    assert seen[149] == [0, 1], "距 retry=1 才 119s，⛔ 不能进 retry=2（要求 120s）"
    assert seen[150] == [0, 1, 2], "满 120s 才允许 retry=2"
    assert seen[449] == [0, 1, 2], "距 retry=2 才 299s，⛔ 不能进 retry=3（要求 300s）"
    assert seen[450] == [0, 1, 2, 3], "满 300s 才允许 retry=3"
    assert seen[460] == [0, 1, 2, 3], "retry=3 是上限，⛔ 不再继续"
    state.close()


def test_attempted_at_is_recorded():
    """attempted_at 必须记"这次尝试发生在什么时候"，否则退避无从计算。"""
    state = _store()
    _setup_task(state)
    notifier = FakeNotifier(now_fn=lambda: BASE)
    notifier.fail_receive_ids.add(EXT_ID)
    sched = Scheduler(state=state, public_base_url="http://127.0.0.1:18090",
                      notifier=notifier, now_fn=lambda: BASE)
    sched.run_once(BASE)

    row = state.conn.execute(
        "SELECT attempted_at, sent_at, result FROM reminder_log").fetchone()
    assert row["result"] == "failed"
    assert row["attempted_at"] == "2026-10-26 18:00:05"
    assert row["sent_at"] is None
    state.close()


def test_500_keeps_binding_but_marks_unusable():
    """平台 500（无会话）⛔ 不能抹掉绑定：标 usable=0，external_id 保留。"""
    state = _store()
    _setup_task(state)
    notifier = FakeNotifier(now_fn=lambda: BASE)
    notifier.no_session_receive_ids.add(EXT_ID)
    sched = Scheduler(state=state, public_base_url="http://127.0.0.1:18090",
                      notifier=notifier, now_fn=lambda: BASE)
    sched.run_once(BASE)

    assert state.get_delivery_target(PHONE) is None, "不可用后不该再参与发送"
    kept = state.get_delivery_target(PHONE, include_unusable=True)
    assert kept is not None, "⛔ 记录不能删掉"
    assert kept["external_id"] == EXT_ID, "⛔ external_id 必须留着排障"
    assert int(kept["usable"]) == 0
    assert notifier.sent_messages == []
    state.close()


def test_migration_from_single_column_unique():
    """旧库（idem_key 单列 UNIQUE）-> 显式事务迁移为 UNIQUE(idem_key, retry)，数据不丢。"""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE reminder_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          template_id TEXT NOT NULL, instance_id INTEGER, phone TEXT NOT NULL,
          kind TEXT NOT NULL, planned_at TEXT NOT NULL, sent_at TEXT,
          result TEXT NOT NULL, error TEXT, retry INTEGER NOT NULL DEFAULT 0,
          idem_key TEXT NOT NULL UNIQUE
        );
        INSERT INTO reminder_log(template_id, phone, kind, planned_at, sent_at,
                                 result, retry, idem_key)
        VALUES('t1', '13800138099', 'remind', '2026-10-26 18:00:00',
               '2026-10-26 18:00:03', 'sent', 0, 'idem-old-1');
        """
    )
    conn.commit()
    conn.close()

    state = StateStore(db)          # 打开即迁移
    sql = state.conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='reminder_log'").fetchone()[0]
    assert "UNIQUE(idem_key, retry)" in sql
    assert "reminder_log_new" not in sql

    row = state.conn.execute("SELECT * FROM reminder_log").fetchone()
    assert row["idem_key"] == "idem-old-1", "迁移⛔ 不能丢数据"
    assert row["attempted_at"] == "2026-10-26 18:00:03", "attempted_at 应从 sent_at 回填"
    state.close()


class _FakeUpload:
    """按块返回数据的上传替身（记录被调用次数，验证"超限即停"）。"""

    def __init__(self, total_bytes: int, chunk: int = 64 * 1024):
        self.left = total_bytes
        self.chunk = chunk
        self.calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.left <= 0:
            return b""
        n = min(self.chunk, self.left)
        self.left -= n
        return b"a" * n


def test_bounded_read_stops_at_limit():
    """超过上限立刻中止，⛔ 不把整个文件读进内存。"""
    fake = _FakeUpload(20 * 1024 * 1024)      # 20MB
    data, too_large = asyncio.run(
        h5_api.read_upload_file_bounded(fake, 1 * 1024 * 1024))
    assert too_large is True and data is None
    assert fake.calls <= 18, "1MB 上限只需读 ~17 块，实际读了 %d 块" % fake.calls


def test_upload_oversize_leaves_no_file():
    """超限上传：400 FILE_TOO_LARGE，且 uploads 目录零残留。"""
    state = _store()
    _setup_task(state, kind="file")
    tpl = state.list_active_templates()[0]
    inst = state.ensure_template_instances(
        tpl["template_id"], "2026-10-26", "2026-10-26 19:00:00")[0]
    raw = state.issue_h5_token(phone=PHONE, template_id=tpl["template_id"],
                               period_key="2026-10-26", instance_id=int(inst["id"]))

    tmp = Path(tempfile.mkdtemp())
    uploads = tmp / "uploads"
    big = b"PK\x03\x04" + b"a" * (21 * 1024 * 1024)      # 21MB，超过硬上限 20MB
    code, body = h5_api.process_h5_task_upload(
        state=state, uploads_dir=uploads, raw_token=raw,
        filename="报表.zip", content_bytes=big)
    assert (code, body["code"]) == (400, "FILE_TOO_LARGE")
    leftover = [p for p in uploads.rglob("*") if p.is_file()] if uploads.exists() else []
    assert leftover == [], "超限上传⛔ 不许留磁盘残留: %s" % leftover
    state.close()
