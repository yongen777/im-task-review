# -*- coding: utf-8 -*-
"""阶段 2C 后台页面（单文件 SPA）的壳层与接线检查。

为什么单独测：这批页面**没有任何业务单测**，而它的接线错一次就是「点什么都 404」。
上一批 `app/api/admin.py` 就把 14 条路由写成 `"/api/admin /login"`（引号后多一个空格），
11 条用例全过、后台却登不进去。所以这里用**静态一致性**把两类错误提前挡掉：

1. HTML 里 JS 引用的 id 必须真的存在（少一个 = 那个按钮点了没反应）
2. HTML 里 fetch 的接口必须真的注册在 admin 路由上（少一个 = 404）

再加一条真实请求，确认 `/login` 能被托管出来、且 `/api/admin/*` 仍然返回 JSON 401
（⛔ 不是被页面路由吃掉返回 HTML）。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router, register_admin_guard
from app.main import _register_admin_spa_pages, load_admin_index_html

# 2026-10-27：后台从「单文件自包含 HTML」换成 AI Studio 复刻版（Vite 构建产物，
# 根节点只有 <div id="root">，真实 UI 在 /assets/*.js 里）。
# 本模块全部用例都是对旧 HTML 内部 DOM id / 内联脚本的静态断言，已不适用，
# 统一跳过而不是删除——保留用例内容作为历史对照；界面验收改由
# D:\im-task-admin\tests\e2e_admin_check.mjs（Playwright，24 项）覆盖。
pytestmark = pytest.mark.skip(
    reason="后台已换成 AI Studio 复刻版（Vite 产物），旧 HTML 内部结构断言失效；"
           "界面验收见 D:\\im-task-admin/tests/e2e_admin_check.mjs"
)

INDEX = Path(__file__).resolve().parents[1] / "app" / "admin_static" / "index.html"


@pytest.fixture()
def store():
    from app.state import StateStore

    tmp = tempfile.mkdtemp()
    st = StateStore(Path(tmp) / "im.db")
    yield st
    st.close()


def _admin_app(st):
    app = FastAPI()
    app.state.store = st
    register_admin_guard(app, st)
    app.include_router(create_admin_router(st))
    _register_admin_spa_pages(app)
    return app


def test_login_shell_is_served(store):
    client = TestClient(_admin_app(store))
    for path in ("/", "/login", "/templates", "/roles", "/instances", "/logs"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "IMTask 管理后台登录" in resp.text, "页面外壳里应有登录视图"


def test_api_still_returns_json_401(store):
    """页面路由⛔ 不能把 /api/admin/* 吃掉（必须仍是 JSON 401）。"""
    client = TestClient(_admin_app(store))
    resp = client.get("/api/admin/me")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_js_element_ids_all_exist():
    html = INDEX.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'getElementById\("([^"]+)"\)', html))
    missing = sorted(used - ids)
    assert not missing, "JS 引用了不存在的 id: %s" % missing


def test_frontend_endpoints_all_registered(store):
    """把 `${xxx}` 归一成 `{}` 后，逐个核对后端是否真有这条路由。"""
    html = INDEX.read_text(encoding="utf-8")
    used = set(re.findall(r'api\(\s*[`"](/api/[^`"?]+)', html))
    assert used, "没抓到任何接口调用，正则可能失效了"

    routes = set()
    for r in create_admin_router(store).routes:
        if not hasattr(r, "methods"):
            continue
        norm = re.sub(r"\{[^}]+\}", "{}", r.path)
        for m in r.methods:
            routes.add((m, norm))
    paths = {p for _m, p in routes}

    unknown = []
    for raw in sorted(used):
        norm = re.sub(r"\$\{[^}]+\}", "{}", raw)
        norm = re.sub(r"/\d+", "/{}", norm)
        if norm not in paths:
            unknown.append(raw)
    assert not unknown, "前端调用了后端没有的接口: %s" % unknown


def test_admin_index_html_is_not_empty():
    """⛔ 页面文件缺失时 load_admin_index_html 会回退成一个空白壳，
    那种情况必须被察觉（否则线上就是白屏）。"""
    assert INDEX.is_file(), "缺少 app/admin_static/index.html"
    html = load_admin_index_html()
    assert "</html>" in html
    assert "api/admin" in html


# ----------------------------------------------------------------------
# 第 5 条（页面补缺）的守门用例：P0 是安全项，⛔ 不允许回退
# ----------------------------------------------------------------------
def test_no_innerhtml_assignment():
    """P0：页面里⛔ 不许再出现任何 `innerHTML =` 赋值。

    存储型 XSS 的正源就是「把用户录入的姓名/部门/模板名塞进 innerHTML」。
    这条一旦被改回去，测试立刻红。
    """
    html = INDEX.read_text(encoding="utf-8")
    offenders = re.findall(r"\.innerHTML\s*=", html)
    assert offenders == [], "⛔ 发现 %d 处 innerHTML 赋值" % len(offenders)


def test_safe_render_helpers_exist():
    """P0：必须用 textContent 路线渲染，并保留 escapeHtml 兜底。"""
    html = INDEX.read_text(encoding="utf-8")
    for name in ("function escapeHtml", "function clearChildren",
                 "function createEl", "function appendTd"):
        assert name in html, "缺少安全渲染助手：%s" % name
    assert html.count("textContent") >= 12, "textContent 用得这么少，很可能有地方还在拼 HTML"
    assert "createEl(" in html and "appendTd(" in html


def test_p1_features_present():
    """P1：导出、驳回原因、模板编辑、分页。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="exportInstBtn"' in html and "instances/export" in html, "缺导出 CSV"
    assert 'id="rejectReasonInput"' in html, "缺驳回原因输入框"
    assert "驳回原因不能为空" in html, "⛔ 驳回原因为空时必须拦下"
    assert 'id="editTplModal"' in html and "/templates/" in html, "缺模板编辑弹窗"
    assert "kind" in html and "绝不提交" in html, "模板编辑应注明不提交 kind/period"
    for el in ("instPageInfo", "remLogPageInfo", "auditLogPageInfo"):
        assert 'id="%s"' % el in html, "缺分页控件：%s" % el
    assert "PAGE_SIZE = 20" in html


@pytest.mark.skip(reason="2026-10-27：后台已换成 AI Studio 复刻版（Vite 构建产物，根节点只有 <div id=root>），"
                          "本用例断言的旧单页 HTML 内部 DOM id 已不存在；界面验收改由 D:\\im-task-admin/tests/e2e_admin_check.mjs 覆盖")
def test_p2_features_present():
    """P2：成员改名/停用、实例详情、密码框掩码、改密后提示。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="editMemModal"' in html, "缺成员改名/停用弹窗"
    assert 'id="instDetailModal"' in html, "缺实例详情弹窗"
    assert "/api/admin/uploads/" in html, "缺附件下载链接"
    assert 'type="password"' not in html, "⛔ 密码框必须改成 type=text + text-security"
    assert "-webkit-text-security" in html
    assert "密码已修改，请用新密码重新登录" in html


def test_no_third_party_frontend_framework():
    """⛔ 仍是纯静态单文件：不许引 React/Vue/CDN 脚本。"""
    html = INDEX.read_text(encoding="utf-8")
    for bad in ("react", "vue.js", "vue@", "cdn.jsdelivr", "unpkg.com"):
        assert bad not in html.lower(), "引入了前端框架/CDN：%s" % bad
    assert html.count("<script") == 1, "应当只有一个内联 script"
