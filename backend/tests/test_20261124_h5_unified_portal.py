# -*- coding: utf-8 -*-
"""2026-11-24 H5 视觉与交互统一重构专项。

覆盖：
  ① 全部 H5 页 <title> 统一为 IMTask，且 JS 中不再动态改 document.title；
  ② portal-create / notice-receipt 顶栏对齐 task.html 契约
     （3px 品牌蓝边条 + 44×44 线性返回 + 居中品牌 + 右侧浅灰页面名）；
  ③ portal.html 四种待办状态卡片统一骨架
     （标题+胶囊+按钮同行 / 截止时间单行 / 统一提示条；删除开发者内部文案）；
  ④ 「我的待办」二级切换 Tab（当前待办 N ｜ 提交记录 M）＋ 左右手势滑动切换的
     方向/阈值判断代码存在；info 接口新增只读 history_count 与提交记录口径一致。
"""
import re
from pathlib import Path

from app.portal import PortalService  # noqa: F401  ( fixture 复用链需要 )
from test_20261117_manual_mark_submitted import ctx  # noqa: F401  (fixture 链)
from test_20261118_portal import portal  # noqa: F401  (fixture)

H5_DIR = Path(__file__).resolve().parents[1] / "app" / "h5"
PAGES = ["bind.html", "image.html", "notice-receipt.html", "portal-create.html",
         "portal.html", "task.html"]


def _read(name):
    return (H5_DIR / name).read_text(encoding="utf-8")


# ── ① 全部页面标题统一 IMTask ───────────────────────────────────────────
def test_all_h5_titles_are_imtask():
    for name in PAGES:
        text = _read(name)
        title = re.search(r"<title>(.*?)</title>", text, re.S).group(1)
        assert title.strip() == "IMTask", f"{name}: {title!r}"
        # JS 不得动态修改网页标题
        for script in re.findall(r"<script[^>]*>(.*?)</script>", text, re.S):
            assert "document.title" not in script, name


# ── ② 二级页面顶栏完全对齐 task.html ───────────────────────────────────
def test_secondary_pages_share_task_topbar_contract():
    task = _read("task.html")
    for name, sub in (("portal-create.html", "创建任务"), ("notice-receipt.html", "通知确认")):
        text = _read(name)
        # 3px 品牌蓝边条
        assert re.search(r"border-top:\s*3px\s+solid\s+(var\(--blue\)|#15558a)", text), name
        # 居中品牌 + 右侧浅灰页面名 + 44×44 线性返回（线性左箭头与 task.html 同一 SVG 路径）
        assert '<span class="topbar-brand">德阳量密助手</span>' in text, name
        assert f'<span class="topbar-sub">{sub}</span>' in text, name
        assert 'id="page-back"' in text and "M15 5l-7 7 7 7" in text, name
        assert ".page-back" in text and "44px" in text, name
        # 不再各画一套大标题顶栏
        assert "header-title" not in text and "header-title-main" not in text, name
        # 红线：零外部 CDN、无 crypto.randomUUID
        assert "https://" not in text and "crypto.randomUUID" not in text, name
    # task.html 自身顶栏字段保持既有标准
    assert '<span class="topbar-brand">德阳量密助手</span>' in task


# ── ③ 四种待办状态卡片共用同一骨架 ─────────────────────────────────────
def test_portal_todo_cards_share_one_skeleton():
    portal = _read("portal.html")
    # 骨架三要素：标题行（含胶囊位）/ 截止时间行 / 统一提示条
    assert "task-title-row" in portal and "task-deadline" in portal, "标题+截止行骨架缺失"
    assert "todoCardShell" in portal, "四种状态未共用同一卡片骨架函数"
    for tag in ("tag-overdue", "tag-resubmit", "tag-reply", "tag-warn"):
        assert tag in portal, f"状态胶囊 {tag} 缺失"
    for note in ("task-note-warn", "task-note-info", "task-note-overdue"):
        assert note in portal, f"统一提示条 {note} 缺失"
    # 删除面向开发者的内部文案与旧分散样式
    assert "不影响后台统计" not in portal
    assert "reject-note" not in portal and "receipt-note" not in portal
    # 按钮：去完成 / 去查看确认 / 我知道了 仍齐备
    for label in ("去完成", "去查看确认", "我知道了"):
        assert label in portal, label


# ── ④ 我的待办二级 Tab + 手势滑动 + info.history_count ──────────────────
def test_portal_second_level_switch_and_swipe():
    portal = _read("portal.html")
    assert "todo-switch" in portal and 'data-view="todo"' in portal and 'data-view="history"' in portal
    assert "当前待办 (" in portal and "提交记录 (" in portal
    # 手势：水平阈值 64px + 方向比 1.6 + 时长 700ms 判断（防上下滚动误触）
    assert "Math.abs(dx) < 64" in portal
    assert "Math.abs(dy) * 1.6" in portal
    assert "dt > 700" in portal
    # 提交记录视图保留日期筛选与卡片直出按钮
    assert "history-filter" in portal and "全部日期" in portal and "加载更多" in portal


def test_info_returns_history_count_matching_records(portal):
    """info.history_count（二级 Tab 徽标）与 submission_history 全量口径一致。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.h5 import create_h5_router

    p, ident, (s, scheduler, *_rest) = portal
    app = FastAPI()
    app.include_router(create_h5_router(s, s.data_dir / "uploads", scheduler=scheduler))
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + p.issue(ident["phone"], ident["platform"])
    data = client.post("/api/h5/portal/info", json={}).json()
    assert data["history_count"] == len(p.submission_history(ident))
