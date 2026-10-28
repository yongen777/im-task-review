# -*- coding: utf-8 -*-
"""后台页面 P0（存储型 XSS）的**运行时**验证。

静态检查（`test_no_innerhtml_assignment`）只能证明"源码里没有 innerHTML 赋值"，
证明不了"渲染结果真的是纯文本"。这里用 Node 把页面脚本跑起来：

  tests/tools/xss_render_check.js 提供一套 DOM 替身，**innerHTML 一旦被赋值就抛错**，
  再灌入 `<img src=x onerror=alert(1)>` 作为成员姓名，检查渲染出的单元格文本
  等于原始字符串、且没有凭空造出 <img>/<b> 元素。

找不到可用的 Node 时 skip（⛔ 不假装通过）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tests" / "tools" / "xss_render_check.js"

CANDIDATES = [
    os.environ.get("IMTASK_NODE", ""),
    r"C:\Users\lenovo\.workbuddy\binaries\node\versions\22.22.2-2\node.exe",
    shutil.which("node") or "",
]


def _usable_node() -> str:
    """挑一个真能跑的 node（系统里可能装的是被 shim 劫持的 11.x，跑不动）。"""
    for cand in CANDIDATES:
        if not cand or not Path(cand).is_file():
            continue
        try:
            probe = subprocess.run([cand, "-e", "console.log('ok')"],
                                   capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and "ok" in probe.stdout:
            return cand
    return ""


@pytest.mark.skip(reason="2026-10-27：后台已换成 AI Studio 复刻版（Vite 构建产物），本用例依赖旧单页 HTML 的脚本形状，"
                          "已不适用；新后台的渲染安全由 React 默认转义保证，界面验收见 D:\\im-task-admin/tests/e2e_admin_check.mjs")
def test_rendered_member_fields_are_plain_text():
    node = _usable_node()
    if not node:
        pytest.skip("没有可用的 Node 运行时，跳过浏览器级渲染检查")
    if not TOOL.is_file():
        pytest.skip("缺少 tests/tools/xss_render_check.js")

    proc = subprocess.run([node, str(TOOL)], capture_output=True, text=True, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, "渲染路径存在注入风险：\n%s" % out
    assert "PASS" in out and "0" in out
    assert "凭空造出的 <img>/<b> 元素数: 0" in out, out
    assert "innerHTML 赋值尝试次数: 0" in out, out
