# -*- coding: utf-8 -*-
"""2026-11-13 · 模块一真实 MySQL 冒烟（连生产 im_task 库跑核心链路，跑完清表）。

验证双引擎一致性（sqlite 全量测试的镜像冒烟）：
  1) Database(mysql) 建 18 张表（5.7 三坑规范 DDL）；
  2) 角色/成员（INSERT+lastrowid）/绑定（INSERT IGNORE+upsert）；
  3) 模板（MEDIUMTEXT / 中文正文）→ 实例预生成（INSERT IGNORE）；
  4) 提醒日志（INSERT IGNORE + UNIQUE(idem,retry)）；
  5) H5 令牌 + CAS 提交（并发幂等闸门）；
  6) data_snapshot（ON CONFLICT → ON DUPLICATE KEY UPDATE 翻译）；
  7) 审计（lastrowid）。
跑完 DROP 全部 18 表，库恢复空库（生产 init_schema 会重新建）。

用法（服务器或本机均可，读 .env 的 IMT_MYSQL_*）：
    IMT_DB_ENGINE=mysql python scripts/mysql_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_project_dotenv  # noqa: E402

ALL_TABLES = [
    "schema_version", "scope", "role", "member", "delivery_target", "task_template",
    "task_instance", "submission", "reminder_log", "upload_ref", "h5_token",
    "admin_user", "admin_session", "channel_config", "admin_audit", "data_snapshot",
    "pending_member", "dept_option",
]


def main() -> int:
    load_project_dotenv()
    os.environ["IMT_DB_ENGINE"] = "mysql"
    os.environ.setdefault("IMT_MYSQL_DATABASE", "im_task")
    from app.state import StateStore

    dummy = Path(tempfile.gettempdir()) / "imtask_mysql_smoke.db"
    store = StateStore(dummy, now_fn=lambda: datetime(2026, 11, 13, 16, 30, 0))
    try:
        db = store.db
        print("engine:", db.engine)
        with store.lock:
            n = store.conn.execute(
                "SELECT COUNT(1) c FROM information_schema.tables WHERE table_schema = DATABASE()"
            ).fetchone()["c"]
        print("TABLE_COUNT:", int(n))
        assert int(n) == 18, f"期望 18 张表，实际 {int(n)}"

        # 角色 + 成员 + 绑定（lastrowid / INSERT / UPSERT）
        rid = store.create_role("冒烟角色·德阳", actor="smoke", platform="zdxlz")["role_id"]
        member = store.add_role_member(rid, "13900000001", "冒烟成员", "云中台", actor="smoke")
        assert member and member.get("phone") == "13900000001", member
        store.upsert_delivery_target("13900000001", "uid_smoke_1", platform="zdxlz")
        store.upsert_delivery_target("13900000001", "uid_smoke_1", platform="zdxlz")  # 幂等再写
        print("MEMBER_OK:", member.get("name"))

        # 模板（中文正文 MEDIUMTEXT）+ 实例预生成（INSERT IGNORE）
        tpl = store.create_template(
            {
                "name": "冒烟日报", "kind": "form", "period": "day", "role_ids": [rid],
                "remind_at": "16:30", "deadline": "18:00", "has_deadline": True,
                "deadline_type": "same_day", "deadline_time": "18:00",
                "text_tpl": "【冒烟】{姓名} 请提交：含中文长文本校验" + "字" * 68000,
                "need_review": False, "enabled": True,
                "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            },
            actor="smoke",
        )
        p_key = "2026-11-13"
        insts = store.ensure_template_instances(tpl["template_id"], p_key, "2026-11-13 18:00:00")
        insts2 = store.ensure_template_instances(tpl["template_id"], p_key, "2026-11-13 18:00:00")
        assert len(insts) == len(insts2) == 1, (len(insts), len(insts2))
        inst = insts[0]
        print("INSTANCE_OK:", inst["status"], "| 文本65KB入库:", len(tpl["text_tpl"]))

        # 提醒日志（INSERT IGNORE + UNIQUE(idem,retry)）
        store.record_reminder_log(
            template_id=tpl["template_id"], instance_id=int(inst["id"]), phone="13900000001",
            kind="remind", planned_at="2026-11-13 16:30:00", attempted_at="2026-11-13 16:30:01",
            sent_at="2026-11-13 16:30:01", result="sent", retry=0, error=None, idem_key="idem-smoke-1")
        store.record_reminder_log(
            template_id=tpl["template_id"], instance_id=int(inst["id"]), phone="13900000001",
            kind="remind", planned_at="2026-11-13 16:30:00", attempted_at="2026-11-13 16:30:01",
            sent_at="2026-11-13 16:30:01", result="sent", retry=0, error=None, idem_key="idem-smoke-1")
        assert store.has_sent_reminder("idem-smoke-1") is True
        print("REMINDER_IDEM_OK")

        # H5 令牌 + CAS 提交
        raw = store.issue_h5_token("13900000001", tpl["template_id"], p_key, int(inst["id"]))
        out = store.submit_h5_task(raw_token=raw, fields={"summary": "冒烟提交"}, note="n")
        assert out["ok"] is True
        try:
            store.submit_h5_task(raw_token=raw, fields={"summary": "重复提交"}, note="n")
            raise AssertionError("重复提交未被 CAS 拦截")
        except Exception as exc:
            assert "ALREADY_SUBMITTED" in str(exc) or "重复" in str(exc), exc
        print("H5_CAS_OK:", out["status"])

        # data_snapshot upsert（ON CONFLICT → ON DUPLICATE KEY UPDATE）
        store.upsert_data_snapshot("smoke_src", p_key, {"a": "1"}, actor="smoke")
        store.upsert_data_snapshot("smoke_src", p_key, {"a": "2"}, actor="smoke")
        snap = store.get_data_snapshot_values("smoke_src", p_key)
        assert str(snap.get("a")) == "2", snap
        print("SNAPSHOT_UPSERT_OK:", snap.get("a"))

        # 审计 lastrowid
        with store.lock, store.conn:
            cur = store.conn.execute(
                "INSERT INTO admin_audit(username, action, target_type, target_id, detail, created_at) "
                "VALUES (?, 'smoke', 'system', '1', '冒烟', ?)", ("smoke", "2026-11-13 16:31:00"))
            audit_id = int(cur.lastrowid)
        assert audit_id >= 1
        print("AUDIT_LASTROWID_OK:", audit_id)
        print("SMOKE_ALL_OK")
        return 0
    finally:
        # 清表恢复空库（逐表 DROP；init_schema 之后会重建）
        try:
            with store.lock:
                for t in ALL_TABLES:
                    store.conn.execute(f"DROP TABLE IF EXISTS {t}")
            with store.lock:
                n2 = store.conn.execute(
                    "SELECT COUNT(1) c FROM information_schema.tables WHERE table_schema = DATABASE()"
                ).fetchone()["c"]
            print("CLEANUP_TABLES_LEFT:", int(n2))
        finally:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())