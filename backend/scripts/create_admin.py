# -*- coding: utf-8 -*-
"""管理员建号 / 重置密码 CLI 工具（规格书 §14 阶段一交付物）。

用法：
  1. 交互式输入密码（推荐，使用 getpass 不回显）：
     python -m scripts.create_admin --username admin
  2. 非交互式（自动化测试或初始化脚本）：
     python -m scripts.create_admin --username admin --password Password123 --must-change
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import resolve_data_dir
from app.state import StateStore, validate_new_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IMTask 后台管理员建号 / 重置密码工具")
    parser.add_argument(
        "-u",
        "--username",
        required=True,
        help="管理员登录账号名（例如 admin）",
    )
    parser.add_argument(
        "-p",
        "--password",
        default=None,
        help="管理员密码（不传则通过 getpass 交互式安全输入；需 ≥8 位且含字母与数字）",
    )
    parser.add_argument(
        "--must-change",
        action="store_true",
        help="标记首次登录必须修改密码 (must_change=1)",
    )
    args = parser.parse_args(argv)

    username = args.username.strip()
    if not username:
        print("错误：用户名不能为空", file=sys.stderr)
        return 1

    password = args.password
    if password is None:
        pwd1 = getpass.getpass(f"请输入管理员 [{username}] 的密码（≥8位，含字母与数字）: ")
        pwd2 = getpass.getpass("请再次确认密码: ")
        if pwd1 != pwd2:
            print("错误：两次输入的密码不一致", file=sys.stderr)
            return 1
        password = pwd1

    pwd_err = validate_new_password(password)
    if pwd_err:
        print(f"错误：{pwd_err}", file=sys.stderr)
        return 1

    db_path = resolve_data_dir() / "im_task.db"
    store = StateStore(db_path)
    try:
        user_id = store.upsert_admin_user(
            username=username,
            password=password,
            must_change=bool(args.must_change),
            actor="cli",
        )
        print(
            f"管理员账号已就绪: id={user_id}, username={username}, "
            f"must_change={bool(args.must_change)}, db={db_path}"
        )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
