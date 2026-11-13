# -*- coding: utf-8 -*-
"""2026-11-13 · 冲刺模块二：H5 纯净访问日志（app/h5log.py）。

验收点（用户规范）：
  1. 每请求一行，含毫秒时间戳 | 客户端 IP | 方法 路径 | 状态码 | 耗时ms；
  2. 耗时 ≥500ms 标 SLOW；
  3. token/手机号脱敏（前 4 位 + ***，手机号中段打码），不出现完整原文；
  4. 按天切割（logs/h5_access-YYYYMMDD.log）；
  5. 只记录业务端口请求；中间件异常绝不打断业务响应。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.h5log import H5AccessLogMiddleware, _mask_qs  # noqa: E402


def _app_with(log_dir: Path):
    app = FastAPI()

    @app.get("/h5/task")
    def task_page(token: str = "", phone: str = ""):
        return {"ok": True}

    @app.get("/h5/slow")
    def slow():
        import time
        time.sleep(0.55)
        return {"ok": True}

    @app.get("/h5/boom")
    def boom():
        raise RuntimeError("boom")

    @app.get("/h5/notfound-but-tracked")
    def tracked():
        return {"ok": 1}

    mw = H5AccessLogMiddleware(app=app, log_dir=log_dir)
    return mw


def test_single_line_format_and_masking() -> None:
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td)
        mw = _app_with(log_dir)
        client = TestClient(mw)
        try:
            client.get("/h5/task", params={"token": "abcdef123456", "phone": "13912345678", "x": "1"})
            client.get("/h5/slow")
            try:
                client.get("/h5/boom")
            except RuntimeError:
                pass
            files = sorted(log_dir.glob("h5_access-*.log"))
            assert len(files) == 1, files
            lines = files[0].read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 3, lines
            l1 = lines[0]
            assert "| GET /h5/task?" in l1, l1
            assert "token=abcd***" in l1, l1
            assert "139****78" in l1 and "13912345678" not in l1, l1
            assert "| 200 | " in l1, l1
            assert "abcdef123456" not in l1, l1
            assert lines[1].endswith("SLOW"), lines[1]
            assert "| 500 |" in lines[2], lines[2]  # 异常经框架转 500，日志照常落行
        finally:
            mw.close()


def test_middleware_failure_never_breaks_traffic() -> None:
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td) / "sub"
        # 预占一个与日志文件同名的**目录**使 open() 创建失败 → 验证日志故障不打断业务
        import datetime as _dt
        day = _dt.datetime.now().strftime("%Y%m%d")
        (log_dir / f"h5_access-{day}.log").mkdir(parents=True, exist_ok=True)
        mw = _app_with(log_dir)
        client = TestClient(mw)
        try:
            r = client.get("/h5/notfound-but-tracked", params={"token": "tok123456"})
            assert r.status_code == 200, r.status_code
            assert r.json() == {"ok": 1}
            assert not any(p for p in log_dir.rglob("h5_access-*.log") if p.is_file())
        finally:
            mw.close()


def test_mask_qs_unit() -> None:
    assert _mask_qs("token=abcdefgh&x=9") == "token=abcd***&x=9"
    assert _mask_qs("phone=13912345678") == "phone=139****78"
    assert _mask_qs("") == ""
    long_v = "v" * 100
    out = _mask_qs(f"k={long_v}")
    assert len(out) < 80 and "…" in out, out
