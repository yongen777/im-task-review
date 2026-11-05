# -*- coding: utf-8 -*-
"""SQLite 数据库连接、WAL 模式、全库 14 张表 = 10 业务表 + 3 支撑表 + 1 版本门表 schema_version（§4 + v1.2 §16 P6）。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

# §16 P6 台账约定：全库 14 张 = 10 业务表 + 3 支撑表(admin_user/admin_session/admin_audit) + 1 版本门表(schema_version)
# BUSINESS_TABLES 保留 13 项（10 业务表 + 3 支撑表）
BUSINESS_TABLES = (
    "scope", "role", "member", "delivery_target", "task_template",
    "task_instance", "submission", "reminder_log", "upload_ref", "h5_token",
    "admin_user", "admin_session", "admin_audit",
)
VERSION_TABLE = "schema_version"

DDL_SCRIPT = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS schema_version (
  version     INTEGER NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scope (
  scope_id   TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  type       TEXT NOT NULL DEFAULT 'dept',
  parent_id  TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role (
  role_id    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  note       TEXT,
  platform   TEXT NOT NULL DEFAULT 'zdxlz',
  created_at TEXT NOT NULL,
  UNIQUE(platform, name)
);

CREATE TABLE IF NOT EXISTS member (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  role_id    TEXT NOT NULL,
  phone      TEXT NOT NULL,
  name       TEXT NOT NULL,
  dept       TEXT,
  enabled    INTEGER NOT NULL DEFAULT 1,
  source     TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL,
  UNIQUE(role_id, phone)
);

CREATE TABLE IF NOT EXISTS delivery_target (
  platform      TEXT NOT NULL DEFAULT 'zdxlz',
  phone         TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  usable        INTEGER NOT NULL DEFAULT 1,
  activated_at  TEXT NOT NULL,
  last_event_at TEXT NOT NULL,
  PRIMARY KEY (platform, phone)
);

CREATE TABLE IF NOT EXISTS task_template (
  template_id     TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  kind            TEXT NOT NULL,
  role_ids        TEXT NOT NULL,
  schema_json     TEXT,
  allow_note      INTEGER NOT NULL DEFAULT 0,
  text_tpl        TEXT NOT NULL,
  remind_at       TEXT,
  notice_times    TEXT,
  deadline        TEXT,
  period          TEXT NOT NULL,
  anchor_weekday  INTEGER,
  anchor_dom      INTEGER,
  deadline_offset INTEGER NOT NULL DEFAULT 0,
  need_review     INTEGER NOT NULL DEFAULT 0,
  max_file_mb     INTEGER,
  enabled         INTEGER NOT NULL DEFAULT 1,
  deleted         INTEGER NOT NULL DEFAULT 0,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_instance (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id   TEXT NOT NULL,
  phone         TEXT NOT NULL,
  name_snap     TEXT NOT NULL,
  dept_snap     TEXT,
  period_key    TEXT NOT NULL,
  status        TEXT NOT NULL,
  deadline_at   TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  submitted_at  TEXT,
  reviewed_at   TEXT,
  reviewer      TEXT,
  reject_reason TEXT,
  reject_count  INTEGER NOT NULL DEFAULT 0,
  late_submit   INTEGER NOT NULL DEFAULT 0,
  UNIQUE(template_id, phone, period_key)
);
CREATE INDEX IF NOT EXISTS ix_inst_status ON task_instance(status, deadline_at);
CREATE INDEX IF NOT EXISTS ix_inst_period ON task_instance(period_key);

CREATE TABLE IF NOT EXISTS submission (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id INTEGER NOT NULL,
  fields_json TEXT,
  file_ref    TEXT,
  file_name   TEXT,
  mime        TEXT,
  size        INTEGER,
  note        TEXT,
  refs_json   TEXT,
  source      TEXT NOT NULL,
  status      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sub_inst ON submission(instance_id, id);

CREATE TABLE IF NOT EXISTS reminder_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id  TEXT NOT NULL,
  instance_id  INTEGER,
  phone        TEXT NOT NULL,
  kind         TEXT NOT NULL,
  planned_at   TEXT NOT NULL,
  attempted_at TEXT,
  sent_at      TEXT,
  result       TEXT NOT NULL,
  error        TEXT,
  retry        INTEGER NOT NULL DEFAULT 0,
  idem_key     TEXT NOT NULL,
  UNIQUE(idem_key, retry)
);
CREATE INDEX IF NOT EXISTS ix_rem_idem ON reminder_log(idem_key, result);

CREATE TABLE IF NOT EXISTS upload_ref (
  ref         TEXT PRIMARY KEY,
  phone       TEXT NOT NULL,
  template_id TEXT,
  file_name   TEXT NOT NULL,
  mime        TEXT NOT NULL,
  size        INTEGER NOT NULL,
  rel_path    TEXT NOT NULL,
  consumed    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS h5_token (
  token_id    TEXT PRIMARY KEY,
  phone       TEXT NOT NULL,
  template_id TEXT NOT NULL,
  period_key  TEXT NOT NULL,
  instance_id INTEGER,
  purpose     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  consumed    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin_user (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  username    TEXT NOT NULL UNIQUE,
  pwd_hash    TEXT NOT NULL,
  must_change INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_session (
  sid        TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  csrf       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen  TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

-- 2026-10-30 通道配置向导：凭证加密存库（AES-GCM），换一家公司部署只填表单、不改代码。
-- payload 是密文（v1:base64(nonce+ct)），⛔ 任何情况下都不落明文；channel 只有 zdxlz / wecom。
CREATE TABLE IF NOT EXISTS channel_config (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  channel    TEXT NOT NULL UNIQUE,
  payload    TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  username    TEXT NOT NULL,
  action      TEXT NOT NULL,
  target_type TEXT,
  target_id   TEXT,
  detail      TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_snapshot (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key  TEXT NOT NULL,
  period_key  TEXT NOT NULL,
  values_json TEXT NOT NULL DEFAULT '{}',
  note        TEXT,
  updated_by  TEXT,
  updated_at  TEXT NOT NULL,
  UNIQUE(source_key, period_key)
);

CREATE INDEX IF NOT EXISTS ix_data_snapshot_lookup ON data_snapshot(source_key, period_key);

CREATE TABLE IF NOT EXISTS pending_member (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  external_id  TEXT NOT NULL UNIQUE,
  phone        TEXT,
  name         TEXT,
  dept         TEXT,
  last_message TEXT,
  activated_at TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

-- 2026-11-03 部门选项：后台管理员配置，供成员表单与 H5 登记页下拉选择
CREATE TABLE IF NOT EXISTS dept_option (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def _migrate_multi_admin(conn: sqlite3.Connection) -> None:
    """2026-11-04 多管理员分级：超级管理员（只读+账号管理）+ 分级管理员（数据按 owner_id 隔离）。

    幂等：全部是 ADD COLUMN / UPDATE，重复执行安全；历史数据 owner_id 默认 1（admin），
    由运维脚本决定是否改挂到某个具体分级管理员名下。

    ⚠️ 业务表名以本仓库为准：role / member / task_template / task_instance
    （外部方案里写的 `roster` 表在本仓库不存在，成员表叫 member）。
    """
    _ensure_column(conn, "admin_user", "display_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "admin_user", "role_level", "TEXT NOT NULL DEFAULT 'sub_admin'")
    _ensure_column(conn, "admin_user", "allowed_platforms", "TEXT NOT NULL DEFAULT '[\"zdxlz\",\"wecom\"]'")
    _ensure_column(conn, "admin_user", "enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "admin_user", "last_login_at", "TEXT")
    for _t in ("role", "member", "task_template", "task_instance"):
        _ensure_column(conn, _t, "owner_id", "INTEGER NOT NULL DEFAULT 1")
    # 主账号 admin 固定为超级管理员 + 双平台
    conn.execute(
        "UPDATE admin_user SET role_level = 'super_admin', "
        "display_name = CASE WHEN display_name = '' THEN '超级管理员' ELSE display_name END, "
        "allowed_platforms = '[\"zdxlz\",\"wecom\"]' "
        "WHERE username = 'admin'"
    )
    conn.commit()


def _migrate_reminder_log_unique(conn: sqlite3.Connection) -> None:
    """若旧库 reminder_log 为单列 idem_key UNIQUE，使用显式事务 BEGIN IMMEDIATE 原子迁移为 UNIQUE(idem_key, retry)。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reminder_log'").fetchone()
    if not row or not row[0] or "UNIQUE(idem_key, retry)" in str(row[0]):
        return

    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(reminder_log)").fetchall()}
    attempted_expr = "COALESCE(attempted_at, sent_at, planned_at)" if "attempted_at" in old_cols else "COALESCE(sent_at, planned_at)"

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TABLE IF EXISTS reminder_log_new")
        conn.execute(
            "CREATE TABLE reminder_log_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, template_id TEXT NOT NULL, instance_id INTEGER, "
            "phone TEXT NOT NULL, kind TEXT NOT NULL, planned_at TEXT NOT NULL, attempted_at TEXT, sent_at TEXT, "
            "result TEXT NOT NULL, error TEXT, retry INTEGER NOT NULL DEFAULT 0, idem_key TEXT NOT NULL, "
            "UNIQUE(idem_key, retry))"
        )
        conn.execute(
            f"INSERT OR IGNORE INTO reminder_log_new("
            f"id, template_id, instance_id, phone, kind, planned_at, attempted_at, sent_at, result, error, retry, idem_key"
            f") SELECT id, template_id, instance_id, phone, kind, planned_at, {attempted_expr}, sent_at, result, error, retry, idem_key "
            f"FROM reminder_log"
        )
        conn.execute("DROP TABLE reminder_log")
        conn.execute("ALTER TABLE reminder_log_new RENAME TO reminder_log")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rem_idem ON reminder_log(idem_key, result)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_role_platform(conn: sqlite3.Connection) -> None:
    """2026-11-02 角色按办公软件隔离：role 表补 platform 列，唯一键由 name 改为 (platform, name)。

    旧库是 name 单列 UNIQUE，SQLite 不能直接改约束，只能重建表；
    历史角色全部属于量子密信（当时只有这一条通道），因此统一回填 'zdxlz'。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(role)").fetchall()}
    if "platform" in cols:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TABLE IF EXISTS role_new")
        conn.execute(
            "CREATE TABLE role_new ("
            "role_id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT, "
            "platform TEXT NOT NULL DEFAULT 'zdxlz', created_at TEXT NOT NULL, "
            "UNIQUE(platform, name))"
        )
        conn.execute(
            "INSERT INTO role_new(role_id, name, note, platform, created_at) "
            "SELECT role_id, name, note, 'zdxlz', created_at FROM role"
        )
        conn.execute("DROP TABLE role")
        conn.execute("ALTER TABLE role_new RENAME TO role")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


class Database:
    """单文件 SQLite 连接管理（开启 WAL，配合线程级可重入锁串行写入）。"""

    def __init__(self, db_path: Path, now_str_fn) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._now_str_fn = now_str_fn
        self.lock = threading.RLock()
        # 2026-10-29 P1-1：改为 DEFERRED 隔离级别，使 `with self.conn:` 真正开事务、
        # 中途异常能整体回滚；原来 isolation_level=None 是自动提交，多条写操作没有原子性。
        # 2026-11-05：补写锁等待。调度线程与请求线程共用一个连接，WAL 下写写仍互斥；
        # 不设 timeout 时遇到瞬时竞争会立刻抛 database is locked，这里统一等 15s。
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level="DEFERRED",
            timeout=15.0,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.init_schema()

    def init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(DDL_SCRIPT)
            _ensure_column(self.conn, "task_template", "allow_note", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(self.conn, "task_template", "anchor_weekday", "INTEGER")
            _ensure_column(self.conn, "task_template", "anchor_dom", "INTEGER")
            _ensure_column(self.conn, "task_template", "deadline_offset", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(self.conn, "delivery_target", "usable", "INTEGER NOT NULL DEFAULT 1")
            _migrate_reminder_log_unique(self.conn)
            _ensure_column(self.conn, "reminder_log", "attempted_at", "TEXT")
            # 2026-10-27 春促数据看板：模板附带数据图开关 + 依赖的数据变量清单
            _ensure_column(self.conn, "task_template", "attach_image", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(self.conn, "task_template", "data_keys", "TEXT")
            # 2026-10-28 上报任务多模块：文字说明 + 图片/文件可同时提交，附件从单个扩展为多个
            _ensure_column(self.conn, "submission", "refs_json", "TEXT")
            # 2026-10-28 通知类「数据图片提醒」：管理员上传图片，到点直接随消息推送
            _ensure_column(self.conn, "task_template", "attach_image_path", "TEXT")
            # 2026-10-29 H5 图文页：管理员填写的图片名称（描述），用于直链文案与 H5 页面标题
            _ensure_column(self.conn, "task_template", "attach_image_name", "TEXT")
            # 2026-10-29 执行日：day 周期模板可指定「只在周几执行」（JSON 数组，1=周一…7=周日）；
            # 空/NULL = 全周执行（调度层 fail-open）。
            _ensure_column(self.conn, "task_template", "exec_weekdays", "TEXT")
            # 2026-11-02 多通道并行：待归纳池记录来源通道（zdxlz/wecom），
            # 提示管理员「这个人从哪个办公软件来」。唯一键暂不变（后续单独重建表）。
            _ensure_column(self.conn, "pending_member", "platform", "TEXT")
            # 2026-11-03 H5 登记表单：待归纳池落库部门（部门选项由后台配置）
            _ensure_column(self.conn, "pending_member", "dept", "TEXT")
            # 2026-10-28 前端完整配置回显：存储 AI Studio 前端的完整模板 spec（upload_modules/text_label 等）
            _ensure_column(self.conn, "task_template", "spec_json", "TEXT")
            # 2026-10-29 逾期补交审核制：逾期后必须「申请补交 → 管理员审核 → 30 分钟新链接」，
            # 每实例只有一次申请机会（resubmit_status 一旦非空即视为已用过）。
            _ensure_column(self.conn, "task_instance", "resubmit_req_at", "TEXT")
            _ensure_column(self.conn, "task_instance", "resubmit_status", "TEXT")
            _ensure_column(self.conn, "task_instance", "resubmit_decided_by", "TEXT")
            _ensure_column(self.conn, "task_instance", "resubmit_decided_at", "TEXT")
            # 2026-11-02 角色按办公软件隔离：历史角色回填 zdxlz，唯一键改为 (platform, name)
            _migrate_role_platform(self.conn)
            # 2026-11-04 多管理员分级（超级管理员 + 分级管理员，数据按 owner_id 隔离）
            _migrate_multi_admin(self.conn)

            now_str = self._now_str_fn()
            row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO schema_version(version, updated_at) VALUES (?, ?)", (SCHEMA_VERSION, now_str))
            elif int(row["version"]) < SCHEMA_VERSION:
                self.conn.execute("UPDATE schema_version SET version = ?, updated_at = ?", (SCHEMA_VERSION, now_str))

            if self.conn.execute("SELECT scope_id FROM scope WHERE scope_id = 'root'").fetchone() is None:
                self.conn.execute("INSERT INTO scope(scope_id, name, type, parent_id, created_at) VALUES ('root', '默认组织', 'dept', NULL, ?)", (now_str,))

            # 2026-10-29 P1-1：DEFERRED 模式下迁移语句会挂在未提交事务里，这里显式落盘
            self.conn.commit()

    def get_schema_version(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            return int(row["version"]) if row else 0

    def close(self) -> None:
        with self.lock:
            self.conn.close()
