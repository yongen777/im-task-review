# -*- coding: utf-8 -*-
"""数据层：纯 MySQL（5.7.43 兼容；2026-11-16 方案B 彻底移除 SQLite 双引擎）。

架构（方案B，用户拍板 2026-11-16）：
  · 唯一引擎 = pymysql + dbutils.PooledDB 连接池（每事务借还，InnoDB 行级锁并发）；
  · `MySQLConnProxy` 是 state.py 的 `self.conn` 鸭子替身：
      - `with proxy:` 事务段（可重入计数；首个进入借连接，最后退出 commit/归还）；
      - execute / executemany 无事务段时：借连接 → 执行 → 立即 commit → 归还；
      - 连接按 threading.local 线程本地存储（FastAPI 线程池 + 调度线程各自独立事务）；
  · `_translate_sql`：SQL 适配层（? 占位符 → %s、INSERT OR IGNORE → INSERT IGNORE、
    ON CONFLICT(...) DO UPDATE → ON DUPLICATE KEY UPDATE、last_insert_rowid() → LAST_INSERT_ID()、
    :name → %(name)s；
  · 5.7 方言三坑规范（用户拍板）：索引列显式 VARCHAR(64/128)；TEXT 列禁 DEFAULT；
    索引行长在 innodb_large_prefix=ON + DYNAMIC 行格式下 3072 字节无忧。
  · ⛔ SQLite 双引擎分支（IMT_DB_ENGINE / DDL_SCRIPT / _ensure_column / _migrate_*
    / executescript / WAL PRAGMA）已于 2026-11-16 方案B 整体移除——
    彻底消灭「测试（sqlite）绿、上线（mysql）红」的方言差异隐患。
  · ⛔ 进程级全局写锁（threading.RLock）同步退役：并发安全改由
    **每线程独立连接事务 + InnoDB 行级锁 + 唯一键幂等（INSERT IGNORE）**兜底；
    `_NoLock` 仅保留 `with store.lock:` 语法兼容（空上下文，零排队）。

连接参数（.env）：IMT_MYSQL_HOST / IMT_MYSQL_PORT / IMT_MYSQL_USER /
IMT_MYSQL_PASSWORD / IMT_MYSQL_DATABASE（默认 im_task）。
池参数（可选）：IMT_MYSQL_MINCACHED（默认 2）/ IMT_MYSQL_MAXCONNECTIONS（默认 16）；
测试底座用小池（见 tests/conftest.py）。
"""
from __future__ import annotations

import os
import re
import threading

# state 层唯一键竞态兜底用的稳定异常名（pymysql.err.IntegrityError）。
# 2026-11-16 方案B：原 sqlite3.IntegrityError 捕获点统一改捕本名。
from pymysql.err import IntegrityError  # noqa: F401  (re-export)

# 只读语句判定（方案B 性能修复 2026-11-16）：autocommit=False 的连接上，
# 只读查询若也 commit 会白付一次 InnoDB fsync（本机实测 ~6ms/次，2665 条批量
# 投递的 prepare 段因此多耗 23s）——只读一律 rollback 收隐式事务（零 fsync）。
_READ_ONLY_RE = re.compile(r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|WITH)\b", re.IGNORECASE)


def _is_read_only(sql: str) -> bool:
    return bool(_READ_ONLY_RE.match(sql or ""))

SCHEMA_VERSION = 1

# §16 P6 台账约定：10 业务表 + 3 支撑表(admin_user/admin_session/admin_audit)
# + 1 版本门表(schema_version) + 4 功能表(channel_config/data_snapshot/pending_member/dept_option)
BUSINESS_TABLES = (
    "scope", "role", "member", "delivery_target", "task_template",
    "task_instance", "submission", "reminder_log", "upload_ref", "h5_token",
    "admin_user", "admin_session", "admin_audit",
)
VERSION_TABLE = "schema_version"

MYSQL_DDL = """
CREATE TABLE IF NOT EXISTS portal_event (
 event_key VARCHAR(64) NOT NULL, created_at VARCHAR(32) NOT NULL, PRIMARY KEY(event_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS portal_grant (
 owner_id INT NOT NULL, platform VARCHAR(16) NOT NULL, phone VARCHAR(32) NOT NULL,
 enabled TINYINT NOT NULL DEFAULT 0, scope_json MEDIUMTEXT NOT NULL, updated_at VARCHAR(32) NOT NULL,
 PRIMARY KEY(owner_id,platform,phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS portal_task (
 task_id VARCHAR(64) NOT NULL, platform VARCHAR(16) NOT NULL, creator_phone VARCHAR(32) NOT NULL,
 owner_id INT NOT NULL, template_json MEDIUMTEXT NOT NULL, recipients_json MEDIUMTEXT NOT NULL,
 created_at VARCHAR(32) NOT NULL, PRIMARY KEY(task_id), KEY ix_portal_creator(platform,creator_phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
  dept       VARCHAR(200) DEFAULT NULL,
  dept_path  VARCHAR(1024) DEFAULT NULL,
  is_left_org TINYINT NOT NULL DEFAULT 0,
  directory_checked_at VARCHAR(32) DEFAULT NULL,
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
  dept_snap     VARCHAR(200),
  period_key    VARCHAR(64)  NOT NULL,
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
  KEY ix_rem_idem (idem_key, result),
  KEY ix_rem_instance_sent (instance_id, result, kind, sent_at)
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
  period_key  VARCHAR(64) NOT NULL,
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
  KEY ix_audit_user_time (username, id),
  KEY ix_audit_target_action (target_type, target_id, action, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS data_snapshot (
  id          INT AUTO_INCREMENT,
  source_key  VARCHAR(128) NOT NULL,
  period_key  VARCHAR(64)  NOT NULL,
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
  dept         VARCHAR(200) DEFAULT NULL,
  last_message TEXT,
  activated_at VARCHAR(32)  NOT NULL,
  updated_at   VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_pending_ext (external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dept_option (
  id         INT AUTO_INCREMENT,
  name       VARCHAR(200) NOT NULL,
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
        raise RuntimeError("未配置 IMT_MYSQL_USER（检查 .env）")
    return {
        "host": host, "port": port, "user": user, "password": password,
        "database": database, "charset": "utf8mb4",
    }


def _translate_sql(sql: str, *, has_params: bool = False) -> str:
    """SQL 适配层（业务层 SQL → MySQL 方言，语句边界内在线翻译）。

    六族规则（覆盖 state.py 的既有用法，grep 实证过）：
      1. ? 占位符 → %s（跳过引号内字符——文本里可能带 ?，如 JSON）；
      2. INSERT OR IGNORE INTO t → INSERT IGNORE INTO t；
      3. ON CONFLICT(cols) DO UPDATE SET a=excluded.a,... →
         ON DUPLICATE KEY UPDATE a=VALUES(a),...（全库唯一键已在表上，冲突集等价）；
      4. last_insert_rowid() → LAST_INSERT_ID()（业务 SQL 已全部改用 MySQL 合法列，
       ⛔ 不再翻译 rowid：2026-11-16 方案B 实测 task_template 等无自增主键表用 rowid 排序会 1054）；
      6. :name 命名占位符 → %(name)s（参数 dict 两边原生兼容）。
    """
    out: list[str] = []
    i, n = 0, len(sql)
    in_str: str | None = None
    while i < n:
        ch = sql[i]
        if in_str:
            # 方案B 补丁（2026-11-16）：pymysql 对带参 SQL 做 % 格式化，
            # 字符串字面量里的 %（LIKE 模式等）必须转义为 %%，否则
            # "not enough arguments for format string"。占位符都在引号外，不受影响。
            out.append("%%" if has_params and ch == "%" else ch)
            if ch == in_str:
                in_str = None
            elif ch == "\\" and in_str == "'" and i + 1 < n:
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
        # 规则 6（2026-11-13 member-pool 实锤补丁）：命名占位符 :name → %(name)s；
        # 参数 dict 两边原生兼容（业务层认 :name，pymysql 认 %(name)s），无需转换参数结构。
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
        # 规则 5（2026-11-16 方案B：rowid 翻译已移除——业务 SQL 全部改用 MySQL 合法列；
        # 仅保留 last_insert_rowid() → LAST_INSERT_ID() 的历史兼容）
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
    """pymysql 行 → sqlite3.Row 的鸭子形态（支持 ['k']、[0]、dict()、keys()、tuple(row)）。

    2026-11-16 方案B 补丁：`tuple(row)`/`list(row)` 在 sqlite3.Row 下得到**值**序列，
    而 dict 子类默认迭代键名 —— 这里 override __iter__ 返回值迭代以对齐旧语义
    （dict(row) 的 fast-path / **row 解包 / json.dumps 都不受影响）。
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)

    def __iter__(self):  # noqa: D401 - 对齐 sqlite3.Row 的迭代语义
        return iter(self.values())


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
    """state.py 的 `self.conn` 鸭子替身（纯 MySQL，方案B 后的唯一连接层）。

    事务模型：
      · `with proxy:` —— 事务段（可重入计数；首个进入借连接，最后退出 commit/归还，
        异常路径整体回滚再归还）；
      · execute / executemany —— 无事务段时：借连接 → 执行 → 立即 commit → 归还；
      · 翻译在每条语句边界内完成（参数无需处理——占位符顺序两边一致）。
    线程模型：连接栈按 threading.local（FastAPI 线程池 + 调度线程各自独立事务），
    池由 dbutils.PooledDB 管理（默认 mincached=2 / maxconnections=16，
    可用 IMT_MYSQL_MINCACHED / IMT_MYSQL_MAXCONNECTIONS 调整——测试底座用小池）。
    """

    def __init__(self, cfg: dict) -> None:
        import pymysql
        from dbutils.pooled_db import PooledDB

        self._cfg = cfg
        self._tls = threading.local()
        mincached = int(cfg.get("mincached") or os.environ.get("IMT_MYSQL_MINCACHED", "2"))
        maxconnections = int(cfg.get("maxconnections") or os.environ.get("IMT_MYSQL_MAXCONNECTIONS", "16"))
        shared_pool = cfg.get("pool")
        if shared_pool is not None:
            # 2026-11-16 方案B：测试底座注入共享池（500+ 用例复用同一池，防连接爆量）
            self._pool = shared_pool
            self._owns_pool = False
            return
        self._owns_pool = True
        self._pool = PooledDB(
            creator=pymysql,
            mincached=mincached, maxconnections=maxconnections, blocking=True,
            ping=1,  # 每次借出 ping 检活（8 小时断连自愈）
            host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"],
            database=cfg["database"], charset=cfg["charset"],
            cursorclass=pymysql.cursors.DictCursor, autocommit=False,
            # 方案B 语义对齐（2026-11-16）：默认 rowcount=实际改变行；SQLite 时代
            # 业务/用例按「匹配行数」口径（如全局停用返回 rows=3）。
            # FOUND_ROWS 让 UPDATE 的 rowcount 等于命中行数，行为与旧引擎一致。
            client_flag=pymysql.constants.CLIENT.FOUND_ROWS,
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

    # ---- 事务段 ----
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
        msql = _translate_sql(sql, has_params=bool(params))
        st = getattr(self._tls, "stack", None)
        if st:
            cur = st[-1].cursor()
            if params:
                cur.execute(msql, params)
            else:
                cur.execute(msql)  # 空参保留字面百分号
            return _CursorWrap(cur)
        conn = self._borrow()
        try:
            cur = conn.cursor()
            if params:
                cur.execute(msql, params)
            else:
                cur.execute(msql)
            wrapped = _CursorWrap(cur)
            if _is_read_only(msql):
                conn.rollback()  # 只读：关隐式事务，零 fsync（commit 白付一次磁盘同步）
            else:
                conn.commit()
            return wrapped
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def executemany(self, sql: str, seq_of_params):
        seq = list(seq_of_params or [])
        msql = _translate_sql(sql, has_params=bool(seq and seq[0]))
        st = getattr(self._tls, "stack", None)
        if st:
            cur = st[-1].cursor()
            if seq:
                cur.executemany(msql, seq)
            else:
                return _CursorWrap(cur)
            return _CursorWrap(cur)
        conn = self._borrow()
        try:
            cur = conn.cursor()
            if seq:
                cur.executemany(msql, seq)
            wrapped = _CursorWrap(cur)
            conn.commit()
            return wrapped
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # 事务段之外的手动 commit/rollback（对齐 sqlite3.Connection 旧语法）
    def commit(self):
        st = getattr(self._tls, "stack", None)
        if st:
            st[-1].commit()

    def rollback(self):
        st = getattr(self._tls, "stack", None)
        if st:
            st[-1].rollback()

    def close(self):
        # 共享池（测试底座注入）绝不由单个 store 关闭
        if getattr(self, "_owns_pool", True):
            self._pool.close()  # 关池（进程退出/测试清理）


class _NoLock:
    """方案B（2026-11-16）：进程级全局锁退役——并发安全改由
    每线程独立连接事务 + InnoDB 行级锁 + 唯一键幂等（INSERT IGNORE，主发送路径
    task_instance / reminder_log / dept_option 全走唯一键兜底）承担。

    保留 `with store.lock:` 魔法上下文（state / admin / tests 约 220 处调用面）：
    空实现、零排队，语义 = 「这里曾经是个临界区，现在由数据库自己保证」。
    """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _upgrade_mysql_schema(proxy: "MySQLConnProxy") -> None:
    """列宽升级（幂等）：业务演进中 VARCHAR 上限不够的列自动扩宽。

    2026-11-16 方案B 首批（SQLite TEXT 时代无感、MySQL 严格模式 1406 拒绝写入）：
      · period_key 16 → 64——adhoc 一次性任务 period_key 为
        `adhoc-<YYYYmmdd-HHMMSS>-<hex6>`（28 字符），16 装不下。
    按当前库实际列宽对比后才 ALTER，已是目标宽度则零操作（生产热库无感）。
    """
    upgrades = (
        # (table, column, 目标宽度, 空值语义) —— ⚠️ MODIFY 不带空值说明会重置为 NOT NULL（实测踩坑）
        ("task_instance", "period_key", 64, "NOT NULL"),
        ("h5_token", "period_key", 64, "NOT NULL"),
        ("data_snapshot", "period_key", 64, "NOT NULL"),
        # 2026-11-16 方案B 第二批：部门名业务上限 200 字（MAX_DEPT_NAME_LEN），
        # SQLite TEXT 时代无感；首批 DDL 误设 VARCHAR(128) → 1406 拒绝 200 字部门名。
        ("member", "dept", 200, "DEFAULT NULL"),
        ("member", "dept_path", 1024, "DEFAULT NULL"),
        ("task_instance", "dept_snap", 200, "DEFAULT NULL"),
        ("pending_member", "dept", 200, "DEFAULT NULL"),
        ("dept_option", "name", 200, "NOT NULL"),
    )
    for table, col, want, null_spec in upgrades:
        row = proxy.execute(
            "SELECT CHARACTER_MAXIMUM_LENGTH AS maxlen FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?",
            (table, col)).fetchone()
        if row is None:
            continue  # 表不存在（DDL 阶段失败会先行抛出）
        cur_len = int(row["maxlen"] or 0)
        if cur_len < want:
            proxy.execute(
                f"ALTER TABLE `{table}` MODIFY COLUMN `{col}` VARCHAR({want}) {null_spec}")


    member_columns = {
        row[0] for row in proxy.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'member'"
        ).fetchall()
    }
    if "is_left_org" not in member_columns:
        proxy.execute("ALTER TABLE member ADD COLUMN is_left_org TINYINT NOT NULL DEFAULT 0")
    if "directory_checked_at" not in member_columns:
        proxy.execute("ALTER TABLE member ADD COLUMN directory_checked_at VARCHAR(32) DEFAULT NULL")

    if not proxy.execute("SHOW INDEX FROM reminder_log WHERE Key_name='ix_rem_instance_sent'").fetchone():
        proxy.execute("ALTER TABLE reminder_log ADD INDEX ix_rem_instance_sent (instance_id, result, kind, sent_at)")
    if not proxy.execute("SHOW INDEX FROM admin_audit WHERE Key_name='ix_audit_target_action'").fetchone():
        proxy.execute("ALTER TABLE admin_audit ADD INDEX ix_audit_target_action (target_type, target_id, action, id)")


def _init_mysql_schema(proxy: "MySQLConnProxy", now_str_fn) -> None:
    """建库初始化：DDL（幂等）+ 列宽升级 + 种子行。"""
    stmts = [s.strip() for s in MYSQL_DDL.split(";") if s.strip()]
    # CREATE TABLE ... 独立事务执行（DDL 隐式提交，逐条执行即可）
    for stmt in stmts:
        proxy.execute(stmt)
    _upgrade_mysql_schema(proxy)
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


class Database:
    """纯 MySQL 连接管理（PooledDB 连接池 + 每线程独立事务）。"""

    def __init__(self, now_str_fn, *, mysql_cfg: dict | None = None,
                 db_path=None) -> None:
        # db_path 为历史签名兼容位（纯 MySQL 无文件库概念，传入即忽略）
        self._now_str_fn = now_str_fn
        cfg = dict(mysql_cfg) if mysql_cfg else mysql_config_from_env()
        self.conn = MySQLConnProxy(cfg)
        # 方案B：全局写锁退役（并发由行级锁 + 唯一键兜底；_NoLock 仅保语法兼容）
        self.lock = _NoLock()
        self.init_schema()

    def init_schema(self) -> None:
        _init_mysql_schema(self.conn, self._now_str_fn)

    def get_schema_version(self) -> int:
        row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(row["version"]) if row else 0

    def close(self) -> None:
        self.conn.close()  # 关闭连接池
