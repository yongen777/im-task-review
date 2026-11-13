# -*- coding: utf-8 -*-
"""数据层连接工厂：双引擎（sqlite 默认 / mysql 8.0 兼容 5.7.43）。

sqlite：单文件 + WAL + DEFERRED 事务（历史行为不变，全量单测跑这一层）。
mysql（2026-11-13 冲刺模块一）：
  · `Database` 按环境变量 IMT_DB_ENGINE 分支，生产换引擎零改码；
  · 连接走 dbutils.PooledDB 连接池（每事务借还，InnoDB 行级锁）；
  · SQL 方言翻译器 `_translate_sql`（?→%s、INSERT OR IGNORE→INSERT IGNORE、
    ON CONFLICT→ON DUPLICATE KEY UPDATE、rowid→id、last_insert_rowid→LAST_INSERT_ID）；
  · 5.7 三坑规范（用户拍板）：索引列显式 VARCHAR(64/128)；TEXT 列禁 DEFAULT；
    索引行长在 innodb_large_prefix=ON + DYNAMIC 行格式下 3072 字节无忧。
"""
from __future__ import annotations

import os
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

-- 2026-11-10 方案A：external_id 的 id 体系 —— 'user'=IM 会话 userId（历史值，存量绑定不动）
-- / 'union_id'=通讯录 union_id（官方口径：可不建会话直接投递，receive_type="union_id"）。
CREATE TABLE IF NOT EXISTS delivery_target (
  platform      TEXT NOT NULL DEFAULT 'zdxlz',
  phone         TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  external_type TEXT NOT NULL DEFAULT 'user',
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
  updated_at      TEXT NOT NULL,
  /* 2026-11-12 任务E1：截止配置三件套。
     has_deadline=0（无截止）时实例截止时间统一存哨兵 9999-12-31 23:59:59
     （deadline_at 列 NOT NULL 不动，字符串比较天然永不过期，展示层特判「不限」）。
     deadline_type ∈ {same_day, weekend, month_end}；NULL = 旧模板回落
     旧列 deadline(HH:MM) + deadline_offset 的算法，行为不变。 */
  has_deadline    INTEGER NOT NULL DEFAULT 1,
  deadline_type   TEXT,
  deadline_time   TEXT
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


def _ensure_member_columns(conn: sqlite3.Connection) -> None:
    """2026-11-11 用户拍板：member.dept_path —— 官方通讯录完整组织路径（层级统计归集）。

    · dept 仍存**末级部门名**（激活列表展示口径不变）；
    · dept_path 存官方全路径（段分隔 "/"，如 四川/德阳/市公司本部/云中台（…）/产数支撑中心）——
      统计「云中台」时按路径段归一化匹配，直属（周艳）与下级部门（郑佳）都能卷进来。
    """
    _ensure_column(conn, "member", "dept_path", "TEXT")


def _migrate_multi_admin(conn: sqlite3.Connection) -> None:
    """2026-11-04 多管理员分级：超级管理员（只读+账号管理）+ 分级管理员（数据按 owner_id 隔离）。

    幂等：全部是 ADD COLUMN / UPDATE，重复执行安全；历史数据 owner_id 默认 1（admin），
    由运维脚本决定是否改挂到某个具体分级管理员名下。

    ⚠️ 业务表名以本仓库为准：role / member / task_template / task_instance
    （外部方案里写的 `roster` 表在本仓库不存在，成员表叫 member）。
    """
    _ensure_column(conn, "admin_user", "display_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "admin_user", "role_level", "TEXT NOT NULL DEFAULT 'sub_admin'")
    # 2026-11-05 代登录（assume/unassume）：代管会话记录原超管会话 sid，供一键切回
    _ensure_column(conn, "admin_session", "parent_sid", "TEXT")
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
    """连接管理（sqlite 单文件 WAL / mysql 连接池，双引擎按 IMT_DB_ENGINE 切换）。"""

    def __init__(self, db_path: Path, now_str_fn) -> None:
        self.db_path = Path(db_path)
        self.engine = (os.environ.get("IMT_DB_ENGINE", "").strip().lower() or "sqlite")
        self.lock = threading.RLock()
        self._now_str_fn = now_str_fn
        if self.engine == "mysql":
            self._init_mysql()
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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

    def _init_mysql(self) -> None:
        """mysql 引擎装配：连接池 + schema 初始化（表结构见 MYSQL_DDL）。"""
        cfg = mysql_config_from_env()
        self.conn = MySQLConnProxy(cfg)
        self.init_schema()

    def init_schema(self) -> None:
        with self.lock:
            if self.engine == "mysql":
                _init_mysql_schema(self.conn, self._now_str_fn, self.lock)
                return
            self.conn.executescript(DDL_SCRIPT)
            _ensure_column(self.conn, "task_template", "allow_note", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(self.conn, "task_template", "anchor_weekday", "INTEGER")
            _ensure_column(self.conn, "task_template", "anchor_dom", "INTEGER")
            _ensure_column(self.conn, "task_template", "deadline_offset", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(self.conn, "delivery_target", "usable", "INTEGER NOT NULL DEFAULT 1")
            # 2026-11-10 方案A：绑定补 external_type（'user' | 'union_id'）；存量行默认 'user'，行为不变
            _ensure_column(self.conn, "delivery_target", "external_type", "TEXT NOT NULL DEFAULT 'user'")
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
            # 2026-11-10 月度执行日：day 周期模板可指定「每月几号执行」（JSON 数组，1~31 多选）；
            # NULL/空 = 不启用月度模式（回落每周 exec_weekdays 判定）。选中 31 = 当月最后一天
            # 也执行（调度层自动适配 2 月 28/29、4/6/9/11 月 30、大月 31）。
            _ensure_column(self.conn, "task_template", "exec_dom_days", "TEXT")
            # 2026-11-02 多通道并行：待归纳池记录来源通道（zdxlz/wecom），
            # 提示管理员「这个人从哪个办公软件来」。唯一键暂不变（后续单独重建表）。
            _ensure_column(self.conn, "pending_member", "platform", "TEXT")
            # 2026-11-03 H5 登记表单：待归纳池落库部门（部门选项由后台配置）
            _ensure_column(self.conn, "pending_member", "dept", "TEXT")
            # 2026-10-28 前端完整配置回显：存储 AI Studio 前端的完整模板 spec（upload_modules/text_label 等）
            _ensure_column(self.conn, "task_template", "spec_json", "TEXT")
            # 2026-10-29 逾期补交审核制的 resubmit_* 四列：链路已于 2026-11-12 任务E3 彻底下线，
            # 列保留仅为存量历史数据兼容（不再读写）；列定义动不了（SQLite 不能 DROP COLUMN 前的时代习惯）。
            _ensure_column(self.conn, "task_instance", "resubmit_req_at", "TEXT")
            _ensure_column(self.conn, "task_instance", "resubmit_status", "TEXT")
            _ensure_column(self.conn, "task_instance", "resubmit_decided_by", "TEXT")
            _ensure_column(self.conn, "task_instance", "resubmit_decided_at", "TEXT")
            # 2026-11-12 任务E1：模板截止配置三件套（旧库补列，历史模板 has_deadline 默认 1、
            # deadline_type 留空 = 回落旧 deadline+deadline_offset 算法）。
            _ensure_column(self.conn, "task_template", "has_deadline", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(self.conn, "task_template", "deadline_type", "TEXT")
            _ensure_column(self.conn, "task_template", "deadline_time", "TEXT")
            # 2026-11-12 晚 · 不定期任务（菜单自选）：无推送时刻、无截止、调度链物理隔离，
            # 用户私聊回复【菜单】/ 数字按需签发一次性填报链接（Pull 模式）。
            _ensure_column(self.conn, "task_template", "is_adhoc", "INTEGER NOT NULL DEFAULT 0")
            # 2026-11-02 角色按办公软件隔离：历史角色回填 zdxlz，唯一键改为 (platform, name)
            _migrate_role_platform(self.conn)
            # 2026-11-11：member.dept_path（官方通讯录完整组织路径，层级统计归集）
            _ensure_member_columns(self.conn)
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
            if self.engine == "mysql":
                self.conn.close()  # 关闭连接池
                return
            self.conn.close()


# ---------------------------------------------------------------------------
# MySQL 引擎（2026-11-13 冲刺模块一）：方言翻译 + 连接池代理 + 5.7 兼容 DDL
# ---------------------------------------------------------------------------

MYSQL_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
  version     INT NOT NULL,
  updated_at  VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scope (
  scope_id   VARCHAR(64)  NOT NULL,
  name       VARCHAR(128) NOT NULL,
  type       VARCHAR(16)  NOT NULL DEFAULT 'dept',
  parent_id  VARCHAR(64)  DEFAULT NULL,
  created_at VARCHAR(32)  NOT NULL,
  PRIMARY KEY (scope_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS role (
  role_id    VARCHAR(64)  NOT NULL,
  name       VARCHAR(128) NOT NULL,
  note       VARCHAR(512) DEFAULT NULL,
  platform   VARCHAR(16)  NOT NULL DEFAULT 'zdxlz',
  owner_id   INT          NOT NULL DEFAULT 1,
  created_at VARCHAR(32)  NOT NULL,
  PRIMARY KEY (role_id),
  UNIQUE KEY uk_role_platform_name (platform, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS member (
  id         INT AUTO_INCREMENT,
  role_id    VARCHAR(64)  NOT NULL,
  phone      VARCHAR(32)  NOT NULL,
  name       VARCHAR(128) NOT NULL,
  dept       VARCHAR(128) DEFAULT NULL,
  dept_path  VARCHAR(512) DEFAULT NULL,
  enabled    TINYINT      NOT NULL DEFAULT 1,
  source     VARCHAR(32)  NOT NULL DEFAULT 'manual',
  owner_id   INT          NOT NULL DEFAULT 1,
  created_at VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_member_role_phone (role_id, phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS delivery_target (
  platform      VARCHAR(16)  NOT NULL DEFAULT 'zdxlz',
  phone         VARCHAR(32)  NOT NULL,
  external_id   VARCHAR(128) NOT NULL,
  external_type VARCHAR(16)  NOT NULL DEFAULT 'user',
  usable        TINYINT      NOT NULL DEFAULT 1,
  activated_at  VARCHAR(32)  NOT NULL,
  last_event_at VARCHAR(32)  NOT NULL,
  PRIMARY KEY (platform, phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS task_template (
  template_id     VARCHAR(64)  NOT NULL,
  name            VARCHAR(128) NOT NULL,
  kind            VARCHAR(16)  NOT NULL,
  role_ids        VARCHAR(512) NOT NULL,
  owner_id        INT          NOT NULL DEFAULT 1,
  schema_json     MEDIUMTEXT,
  allow_note      TINYINT      NOT NULL DEFAULT 0,
  text_tpl        MEDIUMTEXT   NOT NULL,
  remind_at       VARCHAR(8)   DEFAULT NULL,
  notice_times    VARCHAR(255) DEFAULT NULL,
  has_deadline    TINYINT      NOT NULL DEFAULT 1,
  deadline_type   VARCHAR(16)  DEFAULT NULL,
  deadline_time   VARCHAR(8)   DEFAULT NULL,
  deadline        VARCHAR(8)   DEFAULT NULL,
  period          VARCHAR(16)  NOT NULL,
  anchor_weekday  TINYINT      DEFAULT NULL,
  anchor_dom      TINYINT      DEFAULT NULL,
  deadline_offset INT          NOT NULL DEFAULT 0,
  need_review     TINYINT      NOT NULL DEFAULT 0,
  max_file_mb     INT          DEFAULT NULL,
  enabled         TINYINT      NOT NULL DEFAULT 1,
  deleted         TINYINT      NOT NULL DEFAULT 0,
  is_adhoc        TINYINT      NOT NULL DEFAULT 0,
  attach_image         TINYINT      NOT NULL DEFAULT 0,
  data_keys            VARCHAR(1024) DEFAULT NULL,
  attach_image_path    VARCHAR(255)  DEFAULT NULL,
  attach_image_name    VARCHAR(128)  DEFAULT NULL,
  exec_weekdays        VARCHAR(64)   DEFAULT NULL,
  exec_dom_days        VARCHAR(64)   DEFAULT NULL,
  spec_json            MEDIUMTEXT,
  created_by     VARCHAR(64)  NOT NULL,
  created_at     VARCHAR(32)  NOT NULL,
  updated_at     VARCHAR(32)  NOT NULL,
  PRIMARY KEY (template_id),
  KEY ix_tpl_owner (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS task_instance (
  id            INT AUTO_INCREMENT,
  template_id   VARCHAR(64)  NOT NULL,
  phone         VARCHAR(32)  NOT NULL,
  name_snap     VARCHAR(128) NOT NULL,
  dept_snap     VARCHAR(128),
  period_key    VARCHAR(16)  NOT NULL,
  status        VARCHAR(16)  NOT NULL,
  deadline_at   VARCHAR(32)  NOT NULL,
  owner_id      INT          NOT NULL DEFAULT 1,
  created_at    VARCHAR(32)  NOT NULL,
  submitted_at  VARCHAR(32)  DEFAULT NULL,
  reviewed_at   VARCHAR(32)  DEFAULT NULL,
  reviewer      VARCHAR(64)  DEFAULT NULL,
  reject_reason VARCHAR(512) DEFAULT NULL,
  reject_count  INT          NOT NULL DEFAULT 0,
  late_submit   TINYINT      NOT NULL DEFAULT 0,
  resubmit_req_at       VARCHAR(32) DEFAULT NULL,
  resubmit_status       VARCHAR(16) DEFAULT NULL,
  resubmit_decided_by   VARCHAR(64) DEFAULT NULL,
  resubmit_decided_at   VARCHAR(32) DEFAULT NULL,
  resubmit_deadline     VARCHAR(32) DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_inst_tpl_phone_period (template_id(64), phone, period_key),
  KEY ix_inst_status (status, deadline_at),
  KEY ix_inst_period (period_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS submission (
  id          INT AUTO_INCREMENT,
  instance_id INT         NOT NULL,
  fields_json MEDIUMTEXT,
  file_ref    VARCHAR(64)  DEFAULT NULL,
  file_name   VARCHAR(255) DEFAULT NULL,
  mime        VARCHAR(128) DEFAULT NULL,
  size        INT          DEFAULT NULL,
  note        TEXT,
  refs_json   MEDIUMTEXT,
  source      VARCHAR(16)  NOT NULL,
  status      VARCHAR(16)  NOT NULL,
  created_at  VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  KEY ix_sub_inst (instance_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS reminder_log (
  id           INT AUTO_INCREMENT,
  template_id  VARCHAR(64) NOT NULL,
  instance_id  INT         DEFAULT NULL,
  phone        VARCHAR(32) NOT NULL,
  kind         VARCHAR(16) NOT NULL,
  planned_at   VARCHAR(32) NOT NULL,
  attempted_at VARCHAR(32) DEFAULT NULL,
  sent_at      VARCHAR(32) DEFAULT NULL,
  result       VARCHAR(16) NOT NULL,
  error        VARCHAR(255) DEFAULT NULL,
  retry        INT         NOT NULL DEFAULT 0,
  idem_key     VARCHAR(64) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_rem_idem_retry (idem_key, retry),
  KEY ix_rem_idem (idem_key, result)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS upload_ref (
  ref         VARCHAR(64)  NOT NULL,
  phone       VARCHAR(32)  NOT NULL,
  template_id VARCHAR(64)  DEFAULT NULL,
  file_name   VARCHAR(255) NOT NULL,
  mime        VARCHAR(128) NOT NULL,
  size        INT          NOT NULL,
  rel_path    VARCHAR(255) NOT NULL,
  consumed    TINYINT      NOT NULL DEFAULT 0,
  created_at  VARCHAR(32)  NOT NULL,
  PRIMARY KEY (ref),
  KEY ix_upload_phone (phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS h5_token (
  token_id    VARCHAR(64) NOT NULL,
  phone       VARCHAR(32) NOT NULL,
  template_id VARCHAR(64) NOT NULL,
  period_key  VARCHAR(16) NOT NULL,
  instance_id INT         DEFAULT NULL,
  purpose     VARCHAR(16) NOT NULL,
  created_at  VARCHAR(32) NOT NULL,
  expires_at  VARCHAR(32) NOT NULL,
  consumed    TINYINT     NOT NULL DEFAULT 0,
  PRIMARY KEY (token_id),
  KEY ix_h5tok_phone_tpl (phone, template_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_user (
  id               INT AUTO_INCREMENT,
  username         VARCHAR(64) NOT NULL,
  pwd_hash         VARCHAR(255) NOT NULL,
  must_change      TINYINT     NOT NULL DEFAULT 0,
  display_name     VARCHAR(128) NOT NULL DEFAULT '',
  role_level       VARCHAR(16)  NOT NULL DEFAULT 'sub_admin',
  allowed_platforms VARCHAR(255) NOT NULL DEFAULT '["zdxlz","wecom"]',
  enabled          TINYINT      NOT NULL DEFAULT 1,
  last_login_at    VARCHAR(32)  DEFAULT NULL,
  created_at       VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_admin_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_session (
  sid         VARCHAR(64) NOT NULL,
  user_id     INT         NOT NULL,
  csrf        VARCHAR(64) NOT NULL,
  parent_sid  VARCHAR(64) DEFAULT NULL,
  created_at  VARCHAR(32) NOT NULL,
  last_seen   VARCHAR(32) NOT NULL,
  expires_at  VARCHAR(32) NOT NULL,
  PRIMARY KEY (sid),
  KEY ix_adm_sess_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS channel_config (
  id         INT AUTO_INCREMENT,
  channel    VARCHAR(16)  NOT NULL,
  payload    MEDIUMTEXT   NOT NULL,
  updated_at VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_channel (channel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_audit (
  id          INT AUTO_INCREMENT,
  username    VARCHAR(64) NOT NULL,
  action      VARCHAR(64) NOT NULL,
  target_type VARCHAR(64) DEFAULT NULL,
  target_id   VARCHAR(64) DEFAULT NULL,
  detail      TEXT,
  created_at  VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  KEY ix_audit_user_time (username, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS data_snapshot (
  id          INT AUTO_INCREMENT,
  source_key  VARCHAR(128) NOT NULL,
  period_key  VARCHAR(16)  NOT NULL,
  values_json MEDIUMTEXT  NOT NULL,
  note        VARCHAR(512) DEFAULT NULL,
  updated_by  VARCHAR(64) DEFAULT NULL,
  updated_at  VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_data_snapshot (source_key(64), period_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pending_member (
  id           INT AUTO_INCREMENT,
  external_id  VARCHAR(128) NOT NULL,
  platform     VARCHAR(16)  DEFAULT NULL,
  phone        VARCHAR(32)  DEFAULT NULL,
  name         VARCHAR(128) DEFAULT NULL,
  dept         VARCHAR(128) DEFAULT NULL,
  last_message TEXT,
  activated_at VARCHAR(32)  NOT NULL,
  updated_at   VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_pending_ext (external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dept_option (
  id         INT AUTO_INCREMENT,
  name       VARCHAR(128) NOT NULL,
  created_at VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_dept_option (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
""".strip()


def mysql_config_from_env() -> dict:
    """MySQL 连接参数（.env 由 config 加载；此处只读 os.environ）。"""
    host = os.environ.get("IMT_MYSQL_HOST", "").strip() or "127.0.0.1"
    port = int(os.environ.get("IMT_MYSQL_PORT", "3306").strip() or "3306")
    user = os.environ.get("IMT_MYSQL_USER", "").strip()
    password = os.environ.get("IMT_MYSQL_PASSWORD", "")
    database = os.environ.get("IMT_MYSQL_DATABASE", "").strip() or "im_task"
    if not user:
        raise RuntimeError("IMT_DB_ENGINE=mysql 但未配置 IMT_MYSQL_USER（检查 .env）")
    return {
        "host": host, "port": port, "user": user, "password": password,
        "database": database, "charset": "utf8mb4",
    }


def _translate_sql(sql: str) -> str:
    """sqlite 方言 → mysql 方言（state.py 全部 SQL 的在线翻译层）。

    五族规则（覆盖 state.py 的既有用法，grep 实证过）：
      1. ? 占位符 → %s（跳过引号内字符——文本里可能带 ?，如 JSON）；
      2. INSERT OR IGNORE INTO t → INSERT IGNORE INTO t；
      3. ON CONFLICT(cols) DO UPDATE SET a=excluded.a,... →
         ON DUPLICATE KEY UPDATE a=VALUES(a),...（全库唯一键已在表上，冲突集等价）；
      4. rowid → id（仅 task_instance 等自增主键表用 ORDER BY rowid）；
      5. last_insert_rowid() → LAST_INSERT_ID()。
    """
    out: list[str] = []
    i, n = 0, len(sql)
    in_str: str | None = None
    while i < n:
        ch = sql[i]
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = None
            elif ch == "\\" and in_str == "'" and i + 1 < n:  # sqlite 转义 ''
                out.append(sql[i + 1])
                i += 1
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        # 规则 2
        if sql.startswith("INSERT OR IGNORE INTO", i):
            out.append("INSERT IGNORE INTO")
            i += len("INSERT OR IGNORE INTO")
            continue
        # 规则 6（2026-11-13 member-pool 实锤补丁）：sqlite 命名占位符 :name → pymysql 的 %(name)s；
        # 参数 dict 两边原生兼容（sqlite 认 :name，pymysql 认 %(name)s），无需转换参数结构。
        if ch == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append("%(" + sql[i + 1:j] + ")s")
            i = j
            continue
        # 规则 3
        if sql.startswith("ON CONFLICT", i):
            j = sql.find("DO UPDATE SET", i)
            if j == -1:
                raise ValueError(f"ON CONFLICT 无 DO UPDATE 分支（暂不支持）: {sql[:80]}")
            cols = sql[i + len("ON CONFLICT"): j].strip()
            set_part = sql[j + len("DO UPDATE SET"):]
            sets = [s.strip() for s in set_part.split(",") if s.strip()]
            mysql_sets = []
            for s in sets:
                if "=" not in s:
                    raise ValueError(f"ON CONFLICT 不认识的 SET 片段: {s}")
                col, expr = s.split("=", 1)
                col = col.strip()
                mysql_sets.append(f"{col}=VALUES({col})" if "excluded." in expr else f"{col}={expr.strip()}")
            out.append("ON DUPLICATE KEY UPDATE " + ", ".join(mysql_sets))
            i = j + len("DO UPDATE SET") + len(set_part)
            continue
        # 规则 4/5（词边界）
        if sql.startswith("rowid", i) and (i + 5 >= n or not (sql[i + 5].isalnum() or sql[i + 5] == "_")):
            out.append("id")
            i += 5
            continue
        if sql.startswith("last_insert_rowid()", i):
            out.append("LAST_INSERT_ID()")
            i += len("last_insert_rowid()")
            continue
        if ch == "?":
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class RowLike(dict):
    """pymysql 行 → sqlite3.Row 的鸭子形态（支持 ['k']、[0]、dict()、keys()）。"""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class _CursorWrap:
    """pymysql cursor 包装：行转 RowLike；暴露 lastrowid/rowcount/description。"""

    def __init__(self, cur) -> None:
        self._cur = cur
        self.lastrowid = cur.lastrowid
        self.rowcount = cur.rowcount
        self.description = cur.description

    @staticmethod
    def _row(r):
        return RowLike(r) if r is not None else None  # DictCursor 行已是 dict 子类

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for r in self._cur:
            yield self._row(r)

    def close(self):
        try:
            self._cur.close()
        except Exception:  # noqa: BLE001
            pass


class MySQLConnProxy:
    """state.py 的 `self.conn` 鸭子替身（mysql 引擎）。

    语义对齐 sqlite3.Connection：
      · `with proxy:` —— 事务段（可重入计数；首个进入借连接，最后退出 commit/归还）；
      · execute/executemany —— 无事务段时：借连接 → 执行 → 立即 commit → 归还；
      · 翻译在每条语句边界内完成（参数无需处理——qmark 顺序两方一致）。
    线程模型：sticky 连接按线程本地存储（FastAPI 线程池 + 调度线程各自独立事务），
    池由 dbutils.PooledDB 管理（mincached=2 / maxconnections=16，够 8 投递线程+Web 并发）。
    """

    def __init__(self, cfg: dict) -> None:
        import pymysql
        from dbutils.pooled_db import PooledDB

        self._cfg = cfg
        self._tls = threading.local()
        self._pool = PooledDB(
            creator=pymysql,
            mincached=2, maxconnections=16, blocking=True,
            ping=1,  # 每次借出 ping 检活（8 小时断连自愈）
            host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"],
            database=cfg["database"], charset=cfg["charset"],
            cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        )

    # ---- 连接借还 ----
    def _borrow(self):
        return self._pool.connection()

    def _release(self, conn, *, rollback: bool) -> None:
        try:
            if rollback:
                conn.rollback()
            else:
                conn.commit()
        finally:
            conn.close()  # PooledDB: 归还池

    # ---- 事务段（sqlite `with conn:` 对齐）----
    def __enter__(self):
        st = getattr(self._tls, "stack", None)
        if st:
            self._tls.stack = st + [st[-1]]
            return self
        conn = self._borrow()
        self._tls.stack = [conn]
        return self

    def __exit__(self, exc_type, exc, tb):
        st = getattr(self._tls, "stack", None)
        if not st:
            return False
        conn = st.pop()
        if st:
            return False  # 嵌套：最外层负责提交
        try:
            self._release(conn, rollback=exc_type is not None)
        except Exception:  # noqa: BLE001 - 事务提交失败在异常路径下不让连接泄漏
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return False

    # ---- 执行 ----
    def execute(self, sql: str, params=()):
        msql = _translate_sql(sql)
        st = getattr(self._tls, "stack", None)
        if st:
            cur = st[-1].cursor()
            cur.execute(msql, params)
            return _CursorWrap(cur)
        conn = self._borrow()
        try:
            cur = conn.cursor()
            cur.execute(msql, params)
            wrapped = _CursorWrap(cur)
            conn.commit()
            return wrapped
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def executemany(self, sql: str, seq_of_params):
        msql = _translate_sql(sql)
        st = getattr(self._tls, "stack", None)
        if st:
            cur = st[-1].cursor()
            cur.executemany(msql, seq_of_params)
            return _CursorWrap(cur)
        conn = self._borrow()
        try:
            cur = conn.cursor()
            cur.executemany(msql, seq_of_params)
            wrapped = _CursorWrap(cur)
            conn.commit()
            return wrapped
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # sqlite 兼容杂项
    def commit(self):
        st = getattr(self._tls, "stack", None)
        if st:
            st[-1].commit()

    def rollback(self):
        st = getattr(self._tls, "stack", None)
        if st:
            st[-1].rollback()

    def close(self):
        self._pool.close()  # 关池（进程退出/测试清理）


def _init_mysql_schema(proxy: "MySQLConnProxy", now_str_fn, lock: threading.RLock) -> None:
    """mysql 建库初始化：DDL（幂等）+ 种子行（与 sqlite init_schema 尾段对齐）。"""
    stmts = [s.strip() for s in MYSQL_DDL.split(";") if s.strip()]
    # CREATE TABLE ... 独立事务执行（DDL 隐式提交，逐条执行即可）
    with lock:
        for stmt in stmts:
            proxy.execute(stmt)
        with proxy as conn_tx:
            now_str = now_str_fn()
            row = conn_tx.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn_tx.execute(
                    "INSERT INTO schema_version(version, updated_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now_str))
            elif int(row["version"]) < SCHEMA_VERSION:
                conn_tx.execute(
                    "UPDATE schema_version SET version = ?, updated_at = ?",
                    (SCHEMA_VERSION, now_str))
            if conn_tx.execute("SELECT scope_id FROM scope WHERE scope_id = 'root'").fetchone() is None:
                conn_tx.execute(
                    "INSERT INTO scope(scope_id, name, type, parent_id, created_at) "
                    "VALUES ('root', ?, 'dept', NULL, ?)", ("默认组织", now_str))
