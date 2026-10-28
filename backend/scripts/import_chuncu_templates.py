# -*- coding: utf-8 -*-
"""春促按角色提醒 · 片长/分支局长 7 时点模板导入（2026-10-27）。

依据《每个角色的每日重点工作1018.xlsx》「片长、分支局长」sheet 的 7 个时点，
创建 notice 模板：文字提醒 = 固定文案；数据提醒 = attach_image 看板图（人工填数本期主通道）。

用法（幂等，可重复执行）：
    .\\.venv\\Scripts\\python.exe scripts\\import_chuncu_templates.py            # 导入/更新 7 个模板
    .\\.venv\\Scripts\\python.exe scripts\\import_chuncu_templates.py --list     # 仅查看
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import resolve_data_dir  # noqa: E402
from app.state import StateStore  # noqa: E402

ROLE_NAME = "片长/分支局长"
NAME_PREFIX = "春促·"

# 5 时点配置（文案 = 《春促角色清单_数据.csv》原文；数据值待市场部定义，本期留空，填报后自动附看板）
TEMPLATES = [
    {
        "name": NAME_PREFIX + "晨会安排 08:15",
        "notice_times": ["08:15"],
        "text_tpl": (
            "一、文字提醒：\\n"
            "1、开晨会前，昨天的账先过一遍：外呼打了几户、效果怎么样；昨天通报里的1+1155完成率和沉淀商机也顺手理一理\\n"
            "2、今天各小组要出多少，会上定清楚；各组今天的触客商机（三管一配）挨个看一眼够不够\\n"
            "3、昨天卖得差的队伍，今天帮扶跟到人\\n"
            "二、数据提醒：\\n"
            "1、昨日通报中该支局1+1155核心指标完成率、沉淀商机数\\n"
            "2、商机外呼及意向单到人数量，是否达成进度\\n"
            "（数据填报后自动附数据看板）"
        ),
        "attach_image": True,
        "data_keys": [
            {"key": "completion_rate", "label": "1+1155完成率", "unit": "%"},
            {"key": "potential_count", "label": "沉淀商机数", "unit": "户"},
            {"key": "outbound_count", "label": "商机外呼到人", "unit": "户"},
            {"key": "intent_count", "label": "意向单到人", "unit": "单"},
        ],
    },
    {
        "name": NAME_PREFIX + "上午业务点检、午间复盘 12:00",
        "notice_times": ["12:00"],
        "text_tpl": (
            "一、文字提醒：\\n"
            "1、上门情况：上午战况看一眼：你管的支局累计上门XX户，外出队伍晒图挨个点检\\n"
            "2、午间复盘：还没破零的小组拉出来，跟优秀队伍比一比差在哪，下午重点盯他们\\n"
            "3、上午收的本网、异网商机抽查几条，重点看质量，别只看条数\\n"
            "二、数据提醒：\\n"
            "1、根据农村、城市2号群晒图，统计该分支局上午上门次数\\n"
            "2、商机意向单及回数量，是否达成进度\\n"
            "3、该支局上午1+1155重点指标实时受理数据\\n"
            "（数据填报后自动附数据看板）"
        ),
        "attach_image": True,
        "data_keys": [
            {"key": "morning_visits", "label": "上午上门次数", "unit": "次"},
            {"key": "intent_count", "label": "意向单及回量", "unit": "单"},
            {"key": "am_accept_1_1155", "label": "上午1+1155受理", "unit": ""},
        ],
    },
    {
        "name": NAME_PREFIX + "上门协同 15:00",
        "notice_times": ["15:00"],
        "text_tpl": (
            "一、文字提醒：\\n"
            "1、下午是上门的黄金时段：存量维系上门和存量经理、厅店、营维CP搭伙跑\\n"
            "2、没破零的队伍，跟着一起上门，边跑边纠偏\\n"
            "二、数据提醒：\\n"
            "商机意向单及回数量，是否达成进度\\n"
            "（数据填报后自动附数据看板）"
        ),
        "attach_image": True,
        "data_keys": [
            {"key": "intent_count", "label": "意向单及回量", "unit": "单"},
            {"key": "progress_status", "label": "进度达成", "unit": ""},
        ],
    },
    {
        "name": NAME_PREFIX + "夜间补差 18:00",
        "notice_times": ["18:00"],
        "text_tpl": (
            "一、文字提醒：请关注当日业务发展，有差距的再夜间补齐\\n"
            "二、数据提醒：\\n"
            "该分支局上午1+1155重点指标实时受理数据\\n"
            "（数据填报后自动附数据看板）"
        ),
        "attach_image": True,
        "data_keys": [
            {"key": "am_accept_1_1155", "label": "1+1155实时受理", "unit": ""},
        ],
    },
    {
        "name": NAME_PREFIX + "下午业务点检、晚间复盘 20:00",
        "notice_times": ["20:00"],
        "text_tpl": (
            "一、文字提醒：\\n"
            "1、盯次日预约数量、质量\\n"
            "2、总结今日得失、差距\\n"
            "3、梳理明日提升板块\\n"
            "4、明天要打哪里、谁需要你协同上门，今晚把时间约好\\n"
            "二、数据提醒：\\n"
            "1、该支局当日1+1155重点指标受理数据\\n"
            "2、根据农村、城市2号群晒图，统计该分支局全天上门次数\\n"
            "3、商机意向单及回数量，是否达成进度\\n"
            "（数据填报后自动附数据看板）"
        ),
        "attach_image": True,
        "data_keys": [
            {"key": "daily_accept_1_1155", "label": "当日1+1155受理", "unit": ""},
            {"key": "day_visits", "label": "全天上门次数", "unit": "次"},
            {"key": "intent_count", "label": "意向单及回量", "unit": "单"},
        ],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="导入春促片长/分支局长 7 时点 notice 模板")
    parser.add_argument("--list", action="store_true", help="仅查看现有春促模板，不做变更")
    args = parser.parse_args()

    data_dir = resolve_data_dir()
    store = StateStore(data_dir / "im_task.db")

    existing = store.list_templates(q=NAME_PREFIX, kind="notice", page=1, size=50)
    existing_by_name = {t["name"]: t for t in existing["items"]}
    if args.list:
        print(f"现有春促模板 {len(existing_by_name)} 个：")
        for name, t in existing_by_name.items():
            keys = ",".join(dk["key"] for dk in t.get("data_keys") or [])
            print(f"  [{t['template_id']}] {name} times={t.get('notice_times')} attach_image={t.get('attach_image')} keys=[{keys}]")
        return 0

    # 角色复用或创建
    role = None
    for r in store.list_roles():
        if r["name"] == ROLE_NAME:
            role = r
            break
    if role is None:
        role = store.create_role(name=ROLE_NAME, note="春促按角色提醒 · 片长/分支局长", actor="import_chuncu")
        print(f"✔ 创建角色: {ROLE_NAME} -> {role['role_id']}")
    else:
        print(f"· 复用角色: {ROLE_NAME} ({role['role_id']})")

    for spec in TEMPLATES:
        payload = {
            "name": spec["name"],
            "kind": "notice",
            "role_ids": [role["role_id"]],
            "text_tpl": spec["text_tpl"],
            "notice_times": spec["notice_times"],
            "period": "day",
            "attach_image": spec["attach_image"],
            "data_keys": spec["data_keys"],
            "enabled": True,
        }
        cur = existing_by_name.get(spec["name"])
        if cur is None:
            t = store.create_template(payload, actor="import_chuncu")
            print(f"✔ 创建模板: {t['name']} -> {t['template_id']} (attach_image={t['attach_image']}, {len(t['data_keys'])} 个数据卡片)")
        else:
            t = store.update_template(cur["template_id"], payload, actor="import_chuncu")
            print(f"· 更新模板: {t['name']} ({t['template_id']})")

    # 清理不在当前配置里的旧春促模板（7时点->5时点 幂等替换）
    valid_names = {spec["name"] for spec in TEMPLATES}
    for t in store.list_templates(q=NAME_PREFIX, kind="notice", page=1, size=100)["items"]:
        if t["name"] not in valid_names:
            store.delete_template(t["template_id"], actor="import_chuncu")
            print(f"✂ 清理旧模板: {t['name']} ({t['template_id']})")

    print("\n完成。后台人工填数入口：POST /api/admin/data-snapshots")
    print('示例 body: {"source_key": "<template_id>", "period_key": "2026-10-28", "values": {"completion_rate": "86.5", "potential_count": "23"}}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
