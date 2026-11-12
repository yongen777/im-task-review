# -*- coding: utf-8 -*-
"""2026-11-12 下午：清空全部业务数据（保留管理员与通道配置），为 MySQL 迁移减负（默认 dry-run）。

范围（一次物理清空，自备 .db 全量备份）：
  task_instance / submission / upload_ref / h5_token / reminder_log
  task_template / member / delivery_target / data_snapshot / pending_member
  scope / dept_option / role
  磁盘：uploads/ 全清 + public_img/ 全清 + dashboards/ 全清（都在 data 目录内，
        与服务运行目录对齐；.db/-wal/-shm 一律不碰）

保留下列数据（管理员体系与通道配置不动）：
  admin_user / admin_session / admin_audit / channel_config / schema_version

用法（在 D:\\im-task 下；生产机上为 /opt/imtask）：
  .venv\\Scripts\\python.exe scripts/purge_business_data.py             # 试运行（只统计）
  .venv\\Scripts\\python.exe scripts/purge_business_data.py --apply     # 真清库（先自动备份）

注意：运行时请先停服务（systemctl stop imtask），避免写入与清库互相覆盖；
      --apply 会先把 im_task.db 复制成 im_task.db.bak-purge-<时间戳> 再动手。
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_project_dotenv, resolve_data_dir  # noqa: E402

load_project_dotenv(ROOT / ".env")

# 保留表（管理员/通道/审计）；business 清空表按依赖顺序（子表在前）
KEEP_TABLES = ("admin_user", "admin_session", "admin_audit", "channel_config", "schema_version")
PURGE_TABLES = (
    "submission", "upload_ref", "h5_token", "reminder_log", "task_instance",
    "data_snapshot", "task_template", "pending_member", "member",
    "delivery_target", "scope", "dept_option", "role",
)
PURGE_DIRS = ("uploads", "public_img", "dashboards")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正清库（默认只统计 dry-run）")
    args = ap.parse_args()

    from app.state import StateStore  # noqa: PLC0415

    data_dir = resolve_data_dir(ROOT / ".env").resolve()
    db_path = data_dir / "im_task.db"
    if not db_path.is_file():
        print(f"✗ 找不到数据库文件：{db_path}")
        return 1

    store = StateStore(db_path)
    try:
        with store.lock:
            missing = [t for t in PURGE_TABLES + KEEP_TABLES
                       if not store.conn.execute(
                           "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
            if missing:
                print(f"✗ 库里缺表（先跑服务完成初始化再清）：{missing}")
                return 1
            counts = {t: int(store.conn.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0] or 0)
                      for t in PURGE_TABLES}
            keep_admins = int(store.conn.execute("SELECT COUNT(1) FROM admin_user").fetchone()[0] or 0)

        total = sum(counts.values())
        print("== 业务数据统计（将清空）==")
        for t in PURGE_TABLES:
            print(f"  {t:<16} {counts[t]:>8}")
        print(f"  {'合计':<16} {total:>8}")
        print(f"== 保留 == admin_user {keep_admins} 行（admin_session/admin_audit/channel_config 一并保留）")
        disk_note = [f"{d}/" for d in PURGE_DIRS if (data_dir / d).exists()]
        print(f"== 磁盘目录将清空：{', '.join(disk_note) if disk_note else '（无）'}（均位于 {data_dir} 内）")

        if not args.apply:
            print("\n[ dry-run ] 未改动任何数据；确认无误后加 --apply 执行清库。")
            return 0

        # 自动备份
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = db_path.with_name(f"im_task.db.bak-purge-{stamp}")
        shutil.copyfile(db_path, backup_path)
        print(f"✓ 已备份：{backup_path}")

        with store.lock, store.conn:
            for t in PURGE_TABLES:
                cur = store.conn.execute(f"DELETE FROM {t}")
                print(f"  清空 {t:<16} {cur.rowcount or 0} 行")
            store.conn.execute("INSERT INTO admin_audit(username, action, target_type, target_id, detail, created_at) "
                               "VALUES ('system', 'purge.business_data', '*', '*', ?, ?)",
                               (f"{{\"tables\": {PURGE_TABLES!r}, \"kept\": {KEEP_TABLES!r}}}",
                                datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        # 磁盘目录重建式清空（只动 data 目录内白名单目录）
        for d in PURGE_DIRS:
            target = (data_dir / d).resolve()
            if not target.exists():
                continue
            # 防御：必须仍在 data_dir 内（不跟随符号链接逃逸）
            if not target.is_relative_to(data_dir.resolve()):
                print(f"✗ 路径异常，跳过：{target}")
                continue
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            print(f"  清空目录 {d}/")
            (target / ".gitkeep").touch()
        print("✓ 清库完成（管理员账号 / 通道配置 / 审计日志保留）。")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
