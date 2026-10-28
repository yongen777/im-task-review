# -*- coding: utf-8 -*-
"""数据看板图片渲染（2026-10-27 春促按角色提醒 · 服务端渲染方案）。

职责：把当日数据快照渲染成手机友好的竖版 PNG，供机器人以 image 消息推送。
设计要点：
- 纯 matplotlib 渲染，无浏览器/无 AI 生图依赖，服务器 7×24 无人值守可用；
- 中文字体自动探测（Windows YaHei / Linux Noto CJK / 文泉驿），缺字体时降级仍可出图；
- 输出尺寸固定 9:16 竖版（540×960 @100dpi），适配手机端单屏浏览。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import matplotlib

    matplotlib.use("Agg")  # 无头渲染，服务器无显示器环境必需
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    HAS_MPL = True
except Exception:  # pragma: no cover
    HAS_MPL = False

_FONT_CANDIDATES = [
    "Microsoft YaHei", "Microsoft YaHei UI", "SimHei",
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "WenQuanYi Zen Hei",
]


class DashboardError(Exception):
    """看板渲染失败。"""


def _resolve_cjk_font() -> None:
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _FONT_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["axes.unicode_minus"] = False  # 无中文字体也尽量出图（中文会变方块，日志可查）


def render_dashboard(
    *,
    title: str,
    subtitle: str,
    cards: List[Dict[str, Any]],
    note: str = "",
    out_path: str,
    accent: str = "#1B5FBC",
    deep: str = "#0F2447",
) -> Path:
    """渲染一张竖版数据看板 PNG。

    参数：
    - title: 看板主标题（事项名，如「晨会安排 · 数据提醒」）
    - subtitle: 副标题（日期 + 角色，如「2026-10-27 · 片长/分支局长」）
    - cards: 数据卡片列表 [{"label": "1+1155完成率", "value": "86.5", "unit": "%"}, ...]
    - note: 底部备注（数据截至时间 / 数据来源）
    - out_path: 输出 PNG 路径（自动建目录）
    返回：Path(out_path)
    """
    if not HAS_MPL:
        raise DashboardError("matplotlib 未安装，无法渲染看板（pip install matplotlib）")
    if not title or not cards:
        raise DashboardError("render_dashboard 需要 title 与至少 1 张数据卡片")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _resolve_cjk_font()

    fig_w, fig_h, dpi = 5.4, 9.6, 100  # 540×960 竖版
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#F2F5FA")

    # ── 顶部标题区（深蓝渐变感：双层色块）──
    ax.add_patch(FancyBboxPatch((0.04, 0.855), 0.92, 0.115, boxstyle="round,pad=0.008,rounding_size=0.018", facecolor=deep, edgecolor="none"))
    ax.text(0.07, 0.938, str(title)[:28], fontsize=15, fontweight="bold", color="white", va="center")
    ax.text(0.07, 0.885, str(subtitle)[:40], fontsize=9.5, color="#B9C8E6", va="center")

    # ── 数据卡片区（每行 2 列，区块整体垂直居中，消除头重脚轻）──
    per_row = 2
    rows = (len(cards) + per_row - 1) // per_row
    area_top, area_bottom = 0.815, 0.125
    gap = 0.024
    card_h = min(0.24, (area_top - area_bottom - gap * (rows - 1)) / max(rows, 1))
    card_w = (0.92 - gap) / per_row
    total_h = rows * card_h + (rows - 1) * gap
    start_y = area_top - ((area_top - area_bottom) - total_h) / 2  # 区块垂直居中

    for idx, card in enumerate(cards):
        r, c = divmod(idx, per_row)
        x = 0.04 + c * (card_w + gap)
        y = start_y - card_h - r * (card_h + gap)
        ax.add_patch(FancyBboxPatch((x, y), card_w, card_h, boxstyle="round,pad=0.006,rounding_size=0.016", facecolor="white", edgecolor="#D8E1EF", linewidth=0.8))
        label = str(card.get("label") or "")[:14]
        value = str(card.get("value") if card.get("value") not in (None, "") else "—")
        unit = str(card.get("unit") or "").strip()
        if len(value) > 9:
            value = value[:9] + "…"
        ax.text(x + card_w / 2, y + card_h * 0.68, label, fontsize=10.5, color="#5A6B85", ha="center", va="center")
        # 数值与单位合并为单一文本居中，彻底消除间距不一致
        display = (value + unit) if unit else value
        ax.text(x + card_w / 2, y + card_h * 0.34, display, fontsize=16.5 if len(display) <= 7 else 14, fontweight="bold", color=accent, ha="center", va="center")

    # ── 底部备注区 ──
    ax.text(0.5, 0.072, str(note or "数据来源：人工填报")[:44], fontsize=10, color="#66768D", ha="center", va="center")
    ax.text(0.5, 0.038, "由 OPS 任务调度助手自动生成", fontsize=9, color="#8A97AD", ha="center", va="center")

    fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


def render_snapshot_dashboard(
    *,
    title: str,
    subtitle: str,
    data_keys: List[Dict[str, Any]],
    values: Dict[str, Any],
    note: str = "",
    out_path: str,
) -> Path:
    """按模板 data_keys + 快照 values 渲染看板（调度链路的便捷入口）。

    data_keys: [{"key": "completion_rate", "label": "1+1155完成率", "unit": "%"}, ...]
    values: {"completion_rate": "86.5", ...} —— 缺失的 key 渲染为「—」。
    """
    cards = []
    for dk in data_keys:
        key = str(dk.get("key") or "")
        raw_val = values.get(key)
        cards.append({
            "label": str(dk.get("label") or key),
            "value": "" if raw_val in (None, "") else str(raw_val),
            "unit": str(dk.get("unit") or ""),
        })
    return render_dashboard(title=title, subtitle=subtitle, cards=cards, note=note, out_path=out_path)
