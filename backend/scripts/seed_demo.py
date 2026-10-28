# -*- coding: utf-8 -*-
"""造一份演示数据（只建角色/模板/成员/激活，⛔ 不发消息、⛔ 不生成实例）。

用法：
    set TEMP=D:\\tmp && python scripts/seed_demo.py
    python scripts/seed_demo.py --reset      # 先清空 data/imtask.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from imtask.config import Clock, Settings  # noqa: E402
from imtask.sender import FakeSender  # noqa: E402
from imtask.service import Service, ServiceError  # noqa: E402
from imtask.store import Store  # noqa: E402

ROLES = [("值班人员", "每天反馈值班情况"), ("办公室", "综合事务")]
MEMBERS = {
    "值班人员": [("13800000001", "张伟", "办公室"),
             ("13800000002", "李静", "办公室")],
    "办公室": [("13800000003", "王强", "综合部"), ("13800000004", "赵敏", "综合部")],
}
#: 前 3 个已激活，第 4 个留成「未激活」用来看名单与导出
ACTIVATED = [("13800000001", "3000000000000000001"),
             ("13800000002", "3711264155727279249"),
             ("13800000003", "3711264155727279250")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(settings.db_path) + suffix)
            if path.exists():
                path.unlink()
    settings.ensure_dirs()

    store = Store(settings.db_path)
    clock = Clock()
    service = Service(store=store, sender=FakeSender(), clock=clock,
                      settings=settings)

    if store.list_roles():
        print("库里已有角色，跳过造数（要重来请加 --reset）")
        return

    role_ids = {}
    for name, note in ROLES:
        role_ids[name] = service.create_role(name, note, "seed")

    for role_name, rows in MEMBERS.items():
        for phone, member_name, dept in rows:
            service.add_member(role_ids[role_name], phone, member_name, dept,
                               "seed", "seed")

    for phone, external_id in ACTIVATED:
        service.bind_by_phone(external_id, phone)

    service.create_template({
        "name": "每日值班情况反馈",
        "kind": "form",
        "submit_kind": "form",
        "period": "day",
        "remind_at": "08:30",
        "deadline": "18:00",
        "need_review": False,
        "role_ids": [role_ids["值班人员"]],
        "submit_fields": [
            {"key": "summary", "label": "今日值班情况", "type": "text",
             "required": True},
            {"key": "issues", "label": "异常件数", "type": "number"},
            {"key": "level", "label": "整体评价", "type": "select",
             "options": ["正常", "有异常"], "required": True},
        ],
        "text_tpl": "【{template}】{name} 你好，请在 {deadline} 前提交：{h5}",
    }, "seed")

    service.create_template({
        "name": "每周现场照片上传",
        "kind": "form",
        "submit_kind": "image",
        "period": "week",
        "remind_at": "5 10:00",
        "deadline": "5 17:00",
        "role_ids": [role_ids["办公室"]],
        "submit_fields": [],
        "text_tpl": "【{template}】{name} 本周现场照片请上传：{h5}",
    }, "seed")

    service.create_template({
        "name": "每天 9 点提醒晨会",
        "kind": "notice",
        "remind_at": "09:00",
        "period": "day",
        "role_ids": [role_ids["办公室"]],
        "text_tpl": "【提醒】{name} 9:30 三楼会议室开晨会",
    }, "seed")

    print("已造数：角色 %d 个，成员 %d 人，模板 %d 个"
          % (len(store.list_roles()), len(store.list_members()),
             len(store.list_templates())))
    store.close()


if __name__ == "__main__":
    main()
