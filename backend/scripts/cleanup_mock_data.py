# -*- coding: utf-8 -*-
"""清除测试/演示数据（2026-10-27 验收后清场，正式接入真实数据前使用）。

保留（正式配置，不动）：
  - task_template  春促模板
  - role           角色定义（片长/分支局长）
  - admin_user     后台管理员账号
  - schema_version/scope

清除（测试/过程数据）：
  - member            角色成员（测试录入的名单，正式名单另行导入）
  - delivery_target   绑定关系（用户需重新发手机号绑定）
  - data_snapshot     人工填数快照（含验收测试数据）
  - reminder_log      提醒投递日志
  - admin_audit       后台审计日志
  - task_instance / submission  任务实例与提交
  - h5_token          H5 令牌
  - admin_session     后台会话（清理后需重新登录）

用法（服务器上）：
  .venv/bin/python scripts/cleanup_mock_data.py --dry-run   # 只看各表行数
  .venv/bin/python scripts/cleanup_mock_data.py --yes       # 实际清除
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import resolve_data_dir  # noqa: E402

CLEAN_TABLES = [
    "member",
    "delivery_target",
    "data_snapshot",
    "reminder_log",
    "admin_audit",
    "task_instance",
    "submission",
    "h5_token",
    "admin_session",
]
KEEP_TABLES = ["task_template", "role", "admin_user", "scope", "schema_version"]


def main() -> int:
    ap = argparse.ArgumentParser(description="清除 IMTask 测试数据（保留模板/角色/管理员）")
    ap.add_argument("--yes", action="store_true", help="实际执行清除（默认只 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="兼容写法：仅展示行数")
    args = ap.parse_args()

    db_path = resolve_data_dir() / "im_task.db"
    if not db_path.is_file():
        raise SystemExit(f"✗ 数据库不存在: {db_path}")
    print(f"数据库: {db_path}")

    conn = sqlite3.connect(str(db_path))
    print("\n—— 保留（不动） ——")
    for t in KEEP_TABLES:
        try:
            n = conn.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0]
            print(f"  {t:<18} {n} 行")
        except sqlite3.Error:
            print(f"  {t:<18} (表不存在，跳过)")

    print("\n—— 待清除 ——")
    total = 0
    for t in CLEAN_TABLES:
        try:
            n = conn.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            n = 0
            print(f"  {t:<18} (表不存在)")
            continue
        total += n
        print(f"  {t:<18} {n} 行")

    if not args.yes:
        print(f"\n[dry-run] 共 {total} 行待清除。加 --yes 实际执行。")
        conn.close()
        return 0

    print("\n—— 执行清除 ——")
    for t in CLEAN_TABLES:
        try:
            cur = conn.execute(f"DELETE FROM {t}")
            print(f"  {t:<18} 清除 {cur.rowcount} 行")
        except sqlite3.Error as exc:
            print(f"  {t:<18} 跳过: {exc}")
    conn.commit()
    conn.close()
    print(f"\n✅ 清除完成（共 {total} 行）。模板/角色/管理员已保留；"
          "用户需重新发手机号绑定，后台需重新登录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
