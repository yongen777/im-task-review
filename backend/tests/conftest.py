# -*- coding: utf-8 -*-
"""app/ 布局（阶段 2A~2C）的测试夹具。

⚠️ 2026-10-26 换轨说明：原来这里 import 的是 `imtask`（旧 P0 实现），
那套代码与它的 4 个测试文件已整体归档到 `_legacy/`。
现在被测代码是项目根的 `app/` 包，靠 `pytest.ini` 的 `pythonpath = .` 找到，
因此本文件只需要保证"从任何目录跑 pytest 都能 import app"。

各测试文件自己用 `tempfile.mkdtemp()` 建临时库（⛔ 不共享状态、⛔ 不碰 data/im_task.db）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
