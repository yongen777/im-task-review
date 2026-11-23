# -*- coding: utf-8 -*-
"""2026-11-23 第二波（B 级专项）：MySQL 5.7 高选择性索引 + 细分状态物化冗余列。

覆盖：
  1. SCHEMA_VERSION=3：6 条新索引建表即在；重复执行 _upgrade_mysql_schema 幂等补建
     （information_schema.statistics 探测，MySQL 5.7.43 兼容，不用 8.0 的
     CREATE INDEX IF NOT EXISTS）；
  2. 3 条高频查询 EXPLAIN 命中索引（FORCE INDEX 断言 key 与 type != ALL）；
  3. 物化列迁移回填：模拟生产旧库（DROP 两列）→ 升级 → first_sent_at / 
     latest_submit_source 按 reminder_log / submission 一次性回填，二次启动零操作；
  4. 实时维护：record_reminder_log 直记、complete_claimed_reminder 两段式投递、
     用户提交 / 管理员代交 / 驳回重算；首次送达语义（后续 sent 不覆盖）；
     非投递类 kind（回执提示）不写 first_sent_at；
  5. 口径一致：admin 细分 SQL 零相关子查询（机械断言无 SELECT），且物化列与
     旧口径子查询（MIN(sent_at) / 最新非驳回来源）逐行相等。
"""
from pathlib import Path

import pytest

from app.api.admin import _REFINED_STATUS_SQL, _query_instances, refined_status_case_sql
from app.db import _upgrade_mysql_schema
from app.state import StateStore
from mysql_testbank import mysql_test_cfg

NEW_INDEXES = (
    ("task_instance", "ix_inst_phone_status_dl"),
    ("member", "ix_member_phone"),
    ("delivery_target", "ix_dt_plat_ext"),
    ("h5_token", "ix_h5tok_instance"),
    ("h5_token", "ix_h5tok_expires"),
    ("submission", "ix_sub_inst_status_id"),
)

# 旧口径的 kind 清单（迁移回填 / instance_published_at 兜底同源，见 state._FIRST_SENT_KINDS）
OLD_PUBLISHED_KINDS = (
    "'remind','manual_push','notice','notice_img','notice_img_link','notice_file',"
    "'notice_file_link','notice_video','notice_vid_link','portal'"
)


@pytest.fixture
def store(tmp_path):
    s = StateStore(mysql_cfg=mysql_test_cfg(), data_dir=tmp_path)
    yield s
    s.close()


def _index_exists(conn, table, name):
    row = conn.execute(
        "SELECT COUNT(1) AS cnt FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?",
        (table, name),
    ).fetchone()
    return int(row["cnt"] or 0) > 0


def _make_task(store, owner_id=1, *, review=True, phone="13800009601", name="物化甲"):
    rid = store.create_role("物化列验证组", owner_id=owner_id)["role_id"]
    store.add_role_member(rid, phone, name, owner_id=owner_id)
    tid = store.create_template({
        "name": "物化列日报", "kind": "form", "period": "day", "remind_at": "10:00",
        "deadline": "18:00", "role_ids": [rid], "need_review": review,
        "text_tpl": "请提交物化列日报", "allow_note": True, "schema_json": [],
        "spec": {"upload_modules": ["text"]},
    }, owner_id=owner_id)["template_id"]
    return tid, phone


def _instance(store, tid, phone, period="2026-11-23", deadline="2026-12-31 23:59:59"):
    return store.ensure_instance(tid, phone, "物化甲", None, period, deadline)


def _submit_user(store, phone, tid, inst, note="成员提交"):
    raw = store.issue_h5_token(phone, tid, inst["period_key"], inst["id"])
    store.submit_h5_task(raw_token=raw, note=note, refs=[])
    return store.get_instance(inst["id"])


# ── 1. 索引：建表即在 + 升级幂等 ────────────────────────────────────────
def test_indexes_created_and_upgrade_idempotent(store):
    for table, name in NEW_INDEXES:
        assert _index_exists(store.conn, table, name), f"{table}.{name} 建表后应存在"
    # 模拟老库缺索引：DROP 后重复升级必须幂等补建，不抛错
    store.conn.execute("ALTER TABLE member DROP INDEX ix_member_phone")
    assert not _index_exists(store.conn, "member", "ix_member_phone")
    _upgrade_mysql_schema(store.conn)
    for table, name in NEW_INDEXES:
        assert _index_exists(store.conn, table, name), f"{table}.{name} 升级后应补建"
    # 再跑一次（全部已存在）：零操作、不抛错、不重复建
    _upgrade_mysql_schema(store.conn)
    for table, name in NEW_INDEXES:
        assert _index_exists(store.conn, table, name)


# ── 2. EXPLAIN：3 条高频查询命中索引 ───────────────────────────────────
def test_hot_queries_hit_indexes(store):
    tid, phone = _make_task(store)
    _instance(store, tid, phone)
    store.upsert_delivery_target(phone, "union-x1", external_type="union_id")

    cases = (
        ("SELECT id FROM task_instance FORCE INDEX (ix_inst_phone_status_dl) "
         "WHERE phone = '13800009601' AND status = 'pending' AND deadline_at >= '2026-01-01 00:00:00'",
         "ix_inst_phone_status_dl"),
        ("SELECT id FROM member FORCE INDEX (ix_member_phone) WHERE phone = '13800009601'",
         "ix_member_phone"),
        ("SELECT phone FROM delivery_target FORCE INDEX (ix_dt_plat_ext) "
         "WHERE platform = 'zdxlz' AND external_id = 'union-x1'",
         "ix_dt_plat_ext"),
    )
    for sql, want_key in cases:
        row = store.conn.execute(f"EXPLAIN {sql}").fetchone()
        assert row is not None, sql
        assert str(row["type"]) != "ALL", f"{sql} 全表扫描：{dict(row)}"
        assert str(row["key"]) == want_key, f"{sql} 未命中 {want_key}：{dict(row)}"


# ── 3. 物化列：旧库升级一次性回填 ───────────────────────────────────────
def test_materialized_columns_backfill_on_upgrade(store, tmp_path):
    tid, phone = _make_task(store)
    inst = _instance(store, tid, phone)
    store.record_reminder_log(template_id=tid, instance_id=inst["id"], phone=phone,
                              kind="remind", planned_at="2026-11-23 10:00:00",
                              sent_at="2026-11-23 10:00:02", result="sent",
                              error=None, idem_key="mt|1")
    store.record_reminder_log(template_id=tid, instance_id=inst["id"], phone=phone,
                              kind="remind", planned_at="2026-11-23 10:00:00",
                              sent_at="2026-11-23 11:00:00", result="sent",
                              error=None, idem_key="mt|2")
    _submit_user(store, phone, tid, inst)

    # 模拟生产旧库（SCHEMA_VERSION<3）：物化列不存在 → 升级时补列并回填历史行
    store.conn.execute("ALTER TABLE task_instance DROP COLUMN first_sent_at")
    store.conn.execute("ALTER TABLE task_instance DROP COLUMN latest_submit_source")
    _upgrade_mysql_schema(store.conn)

    row = store.conn.execute(
        "SELECT first_sent_at, latest_submit_source FROM task_instance WHERE id = ?",
        (inst["id"],)).fetchone()
    assert row["first_sent_at"] == "2026-11-23 10:00:02"  # 首次（MIN）送达语义
    assert row["latest_submit_source"] == "user"
    # 二次启动：列已在 → 零操作不抛错、值不漂移
    _upgrade_mysql_schema(store.conn)
    row2 = store.conn.execute(
        "SELECT first_sent_at, latest_submit_source FROM task_instance WHERE id = ?",
        (inst["id"],)).fetchone()
    assert dict(row2) == dict(row)


# ── 4. 实时维护：投递 / 提交 / 代交 / 驳回 ─────────────────────────────
def test_first_sent_at_and_latest_source_realtime_maintenance(store, tmp_path):
    tid, phone = _make_task(store)
    a = _instance(store, tid, phone)
    b = _instance(store, tid, "13800009602", period="2026-11-24")
    store.add_role_member(store.conn.execute(
        "SELECT role_id FROM member WHERE phone=?", (phone,)).fetchone()["role_id"],
        "13800009602", "物化乙", owner_id=1)
    c = _instance(store, tid, phone, period="2026-11-25")

    def col(instance_id):
        return dict(store.conn.execute(
            "SELECT first_sent_at, latest_submit_source FROM task_instance WHERE id=?",
            (instance_id,)).fetchone())

    # ① 直记 sent：写首次送达；再记一次更晚的 sent 不覆盖（首次语义）
    store.record_reminder_log(template_id=tid, instance_id=a["id"], phone=phone,
                              kind="remind", planned_at="2026-11-23 10:00:00",
                              sent_at="2026-11-23 10:00:02", result="sent",
                              error=None, idem_key="rt|a1")
    assert col(a["id"])["first_sent_at"] == "2026-11-23 10:00:02"
    store.record_reminder_log(template_id=tid, instance_id=a["id"], phone=phone,
                              kind="remind", planned_at="2026-11-23 10:00:00",
                              sent_at="2026-11-23 12:00:00", result="sent",
                              error=None, idem_key="rt|a2")
    assert col(a["id"])["first_sent_at"] == "2026-11-23 10:00:02"
    # ② 失败投递不写；非投递类 kind（回执提示）也不写
    store.record_reminder_log(template_id=tid, instance_id=b["id"], phone=phone,
                              kind="remind", planned_at="2026-11-23 10:00:00",
                              sent_at=None, result="failed", error="no route",
                              idem_key="rt|b-fail")
    store.record_reminder_log(template_id=tid, instance_id=b["id"], phone=phone,
                              kind="notice_receipt", planned_at="2026-11-23 10:00:00",
                              sent_at="2026-11-23 10:05:00", result="sent",
                              error=None, idem_key="rt|b-receipt")
    assert col(b["id"])["first_sent_at"] is None
    # ③ 调度器两段式（claim pending → complete sent）同样落列
    log_id = store.record_reminder_log(template_id=tid, instance_id=c["id"], phone=phone,
                                       kind="remind", planned_at="2026-11-23 10:00:00",
                                       sent_at=None, result="pending", error=None,
                                       idem_key="rt|c-claim")
    assert col(c["id"])["first_sent_at"] is None
    with store.conn:
        store.complete_claimed_reminder(int(log_id), sent_at="2026-11-23 10:00:07",
                                        result="sent", error=None)
    assert col(c["id"])["first_sent_at"] == "2026-11-23 10:00:07"

    # ④ 用户提交 → 'user'；驳回后重交再驳回 → 无非驳回记录置 NULL；管理员代交 → 'admin'
    inst_a = _submit_user(store, phone, tid, a)
    assert col(a["id"])["latest_submit_source"] == "user"
    store.review_instance(a["id"], "reject", "admin1", reason="内容不符规范")
    assert col(a["id"])["latest_submit_source"] is None  # 唯一提交被驳回
    _submit_user(store, phone, tid, store.get_instance(a["id"]))
    assert col(a["id"])["latest_submit_source"] == "user"
    store.review_instance(a["id"], "reject", "admin1", reason="第二次驳回")
    assert col(a["id"])["latest_submit_source"] is None
    store.manual_mark_submitted(a["id"], 1, "admin1", uploads_dir=tmp_path, note="管理员代交")
    assert col(a["id"])["latest_submit_source"] == "admin"

    # ⑤ 细分筛选在新列上直接可用（零子查询）：待推送/待上传口径不漂移
    ids_push = {it["id"] for it in _query_instances(store, status="push_pending")["items"]}
    ids_upload = {it["id"] for it in _query_instances(store, status="upload_pending")["items"]}
    assert b["id"] in ids_push and b["id"] not in ids_upload
    assert c["id"] in ids_upload and c["id"] not in ids_push


# ── 5. 零子查询 + 新旧口径逐行一致 ─────────────────────────────────────
def test_refined_sql_has_no_subqueries_and_matches_legacy(store, tmp_path):
    # 机械断言：细分 CASE 与全部键条件里没有任何相关子查询
    for key, cond in _REFINED_STATUS_SQL.items():
        assert "SELECT" not in cond.upper(), f"{key} 仍含子查询：{cond}"
    assert "SELECT" not in refined_status_case_sql().upper()

    tid, phone = _make_task(store, review=False)
    x = _instance(store, tid, phone)
    y = _instance(store, tid, "13800009603", period="2026-11-26")
    store.record_reminder_log(template_id=tid, instance_id=y["id"], phone="13800009603",
                              kind="remind", planned_at="2026-11-26 10:00:00",
                              sent_at="2026-11-26 10:00:01", result="sent",
                              error=None, idem_key="pl|y1")
    _submit_user(store, phone, tid, x)

    for inst_id in (x["id"], y["id"]):
        row = dict(store.conn.execute(
            "SELECT first_sent_at, latest_submit_source FROM task_instance WHERE id=?",
            (inst_id,)).fetchone())
        legacy_published = store.conn.execute(
            f"SELECT MIN(sent_at) AS v FROM reminder_log WHERE instance_id=? "
            f"AND result='sent' AND kind IN ({OLD_PUBLISHED_KINDS})",
            (inst_id,)).fetchone()["v"]
        legacy_source = store.conn.execute(
            "SELECT source AS v FROM submission WHERE instance_id=? AND status!='rejected' "
            "ORDER BY id DESC LIMIT 1", (inst_id,)).fetchone()
        assert row["first_sent_at"] == legacy_published, inst_id
        assert row["latest_submit_source"] == (legacy_source["v"] if legacy_source else None)
        # 列表行下发的 published_at（前端「送达时间」）与旧口径一致
        item = next(it for it in _query_instances(store)["items"] if it["id"] == inst_id)
        assert item["published_at"] == legacy_published
    # 免审 user 提交 → done；x 无送达记录 → push_pending；y 已送达 → upload_pending
    refined = {it["id"]: it["refined_status"] for it in _query_instances(store)["items"]}
    assert refined[x["id"]] == "done"
    assert refined[y["id"]] == "upload_pending"
