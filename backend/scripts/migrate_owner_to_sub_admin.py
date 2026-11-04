# -*- coding: utf-8 -*-
"""2026-11-04 多管理员分级：生产历史数据归属迁移。

用户口径：「现在这些本地创建是全部信息可以给我纳入分级管理员，就是以我的名义创建的一个
分级管理员。以后我创建任务和管理成员就在你创建的这个我的分级管理员里。」

做法：
  1. 建（或复用）一个分级管理员账号：username=zhengjia, display_name=张三, 双平台授权；
     初始密码走系统默认（Hds22999!）+ must_change=1（首次登录强制改密）。
  2. 把历史业务数据（role / member / task_template / task_instance）owner_id 从 1 改挂到它名下。
  3. admin 保持 super_admin，历史数据即以「张三」这个分级管理员的名义存在。

幂等：重复执行不会重复建号、不会重复迁移（transfer_owner 只改 != 目标的行）。
回滚：把四张表 owner_id 改回 1 即可（脚本 --rollback）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.state import StateStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite 路径")
    ap.add_argument("--username", default="zhengjia")
    ap.add_argument("--display-name", default="张三")
    ap.add_argument("--rollback", action="store_true", help="把历史数据改回 owner_id=1（admin）")
    args = ap.parse_args()

    store = StateStore(Path(args.db))
    try:
        if args.rollback:
            moved = {}
            with store.lock, store.conn:
                for tbl in ("role", "member", "task_template", "task_instance"):
                    cur = store.conn.execute(f"UPDATE {tbl} SET owner_id = 1 WHERE owner_id != 1")
                    moved[tbl] = int(cur.rowcount or 0)
            print("[rollback] 已改回 owner_id=1:", moved)
            return 0

        # 1) 建号或复用
        existing = store.conn.execute(
            "SELECT id, username, role_level FROM admin_user WHERE username = ?", (args.username,)
        ).fetchone()
        if existing:
            admin_id = int(existing["id"])
            print(f"[1/2] 复用已有账号 {args.username}（id={admin_id}, role_level={existing['role_level']}）")
        else:
            acct = store.create_admin_account(
                username=args.username,
                display_name=args.display_name,
                allowed_platforms=["zdxlz", "wecom"],
                actor="admin",
            )
            admin_id = int(acct["id"])
            print(f"[1/2] 已创建分级管理员 {args.username}（id={admin_id}），"
                  f"初始密码 {StateStore.DEFAULT_SUB_ADMIN_PASSWORD}，首次登录须改密")

        # 2) 历史数据改挂
        res = store.transfer_owner(admin_id, actor="admin")
        print(f"[2/2] 历史数据归属迁移: {res['moved']}")

        # 3) 核对
        for tbl in ("role", "member", "task_template", "task_instance"):
            rows = store.conn.execute(
                f"SELECT owner_id, COUNT(1) AS c FROM {tbl} GROUP BY owner_id"
            ).fetchall()
            print(f"    {tbl}: {[(int(r['owner_id']), int(r['c'])) for r in rows]}")
        admins = store.conn.execute(
            "SELECT id, username, display_name, role_level, allowed_platforms, enabled, must_change FROM admin_user"
        ).fetchall()
        print("    账号:", [dict(r) for r in admins])
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
