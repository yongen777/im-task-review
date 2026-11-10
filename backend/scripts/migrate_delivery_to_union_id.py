# -*- coding: utf-8 -*-
"""2026-11-10 方案A：存量 zdxlz 绑定迁移为 union_id（默认 dry-run）。

背景：external_type 列新增后，历史绑定的 external_id 是 IM 会话 userId（external_type='user'）。
user 绑定的发送前提是「该用户跟机器人说过话」（无会话 500）。官方通讯录解密打通后，
union_id 绑定可以不依赖会话直投（receive_type="union_id"，2026-11-10 实测 HTTP 200）。

本脚本把生产库里 zdxlz 平台的存量绑定逐个用官方通讯录按手机号重绑为 union_id：
  · 默认 dry-run：只报告「可迁移 / 查不到」，不改任何数据；
  · --apply 才真正写库（upsert 保留首次激活时刻 activated_at）；
  · usable=1 的行也会迁移（消息发送口径统一）；查不到通讯录的行保持原样并逐个列出；
  · 不碰 wecom 平台的绑定（union_id 机制专属量子密信）。

用法（在 D:\im-task 下）：
  .venv\\Scripts\\python.exe scripts/migrate_delivery_to_union_id.py            # 试运行
  .venv\\Scripts\\python.exe scripts/migrate_delivery_to_union_id.py --apply    # 真迁移
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.channels.zdxlz import ZdxlzChannel  # noqa: E402
from app.channels.zdxlz_contacts import ZdxlzContacts  # noqa: E402
from app.config import load_project_dotenv  # noqa: E402

load_project_dotenv(ROOT / ".env")

import os  # noqa: E402

PHONE_RE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
mask = lambda s: PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], str(s or ""))  # noqa: E731


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只试运行）")
    args = ap.parse_args()

    cfg_mod = __import__("app.config", fromlist=["Config"])
    cfg = cfg_mod.Config.from_env()
    if not cfg.channel_configured:
        print("✗ 通道凭证缺失（IMT_APP_ID / IMT_APP_SECRET），中止")
        return 1

    from app.state import StateStore  # noqa: PLC0415

    store = StateStore(cfg.db_path)
    chan = ZdxlzChannel(cfg)
    contacts = ZdxlzContacts(cfg, chan)
    try:
        with store.lock:
            rows = store.conn.execute(
                "SELECT platform, phone, external_id, external_type, usable, activated_at "
                "FROM delivery_target WHERE platform = 'zdxlz' ORDER BY activated_at ASC"
            ).fetchall()
        print(f"zdxlz 存量绑定 {len(rows)} 行")
        migrate = []
        keep: list[str] = []
        for r in rows:
            phone = str(r["phone"] or "").strip()
            if str(r["external_type"] or "user") == "union_id":
                keep.append(f"{mask(phone)} 已是 union_id（external_id={str(r['external_id'])[:6]}…）跳过")
                continue
            user = contacts.search_by_phone(phone)
            uid = str((user or {}).get("union_id") or "").strip()
            if not uid:
                keep.append(f"{mask(phone)} 通讯录查不到（保持原绑定 {str(r['external_id'])[:6]}… type=user）")
                continue
            migrate.append((phone, uid, str(r["activated_at"])))

        print(f"\n可迁移: {len(migrate)} 行；不可迁移: {len(keep)} 行")
        for line in keep:
            print("  · " + line)
        if not migrate:
            print("\n没有需要迁移的行，结束。")
            return 0

        if not args.apply:
            print("\n[dry-run] 以下行将被迁移（重跑加 --apply 生效）：")
            for phone, uid, act in migrate:
                print(f"  · {mask(phone)} -> union_id={uid[:6]}…（保留激活时刻 {act}）")
            return 0

        ok = fail = 0
        for phone, uid, act in migrate:
            try:
                store.upsert_delivery_target(phone, uid, platform="zdxlz",
                                              activated_at=act, external_type="union_id")
                ok += 1
                print(f"  ✓ {mask(phone)} -> union_id={uid[:6]}…")
            except Exception as exc:  # noqa: BLE001
                fail += 1
                print(f"  ✗ {mask(phone)} 失败: {exc}")
        print(f"\n迁移完成：成功 {ok}，失败 {fail}")
        return 0 if fail == 0 else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
