# -*- coding: utf-8 -*-
"""2026-10-29 H5 图文页回归：/h5/img/ 标题渲染、?raw=1 原图、旧链接回退。"""
from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.scheduler import Scheduler
from app.state import StateStore

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class H5ImgPageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="imtask_h5img_")
        root = Path(self._tmp.name)
        self.cfg = Config(
            public_port=9020,
            admin_port=9013,
            public_base_url="http://203.0.113.10:9020",
            data_dir=root / "data",
        )
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.uploads_dir.mkdir(parents=True, exist_ok=True)

        from app.channels.zdxlz import ZdxlzChannel
        from app.main import create_apps

        self.store = StateStore(self.cfg.db_path, now_fn=lambda: __import__("datetime").datetime(2026, 10, 29, 12, 0, 0))
        self.notifier = type("N", (), {"send_text": lambda *a, **k: None})()
        chan = ZdxlzChannel(self.cfg)
        self.scheduler = Scheduler(
            state=self.store, public_base_url=self.cfg.public_base_url, notifier=self.notifier,
            now_fn=lambda: __import__("datetime").datetime(2026, 10, 29, 12, 0, 0),
        )
        try:
            from fastapi.testclient import TestClient

            pub_app, _, _, _ = create_apps(
                config=self.cfg, state=self.store, channel=chan, notifier=self.notifier,
                now_fn=lambda: __import__("datetime").datetime(2026, 10, 29, 12, 0, 0),
            )
            self.client = TestClient(pub_app)
        except ImportError:  # pragma: no cover
            self.client = None

    def tearDown(self) -> None:
        try:
            self.store.close()
            self._tmp.cleanup()
        finally:
            os.environ.clear()

    def _make_img(self) -> Path:
        p = self.cfg.data_dir / "notice_attach"
        p.mkdir(parents=True, exist_ok=True)
        f = p / "test_dash.png"
        f.write_bytes(_PNG_1PX)
        return f

    def test_publish_link_writes_title_and_page_renders(self) -> None:
        if self.client is None:  # pragma: no cover
            self.skipTest("fastapi 未安装")
        src = self._make_img()
        url = self.scheduler._publish_image_link(str(src), "上午上门情况统计图")
        assert url, "直链发布失败"
        assert url.startswith("http://203.0.113.10:9020/h5/img/")
        name = url.rsplit("/", 1)[-1]

        # 1) 默认：HTML 图文页，标题 = 图片名称
        r = self.client.get(f"/h5/img/{name}")
        assert r.status_code == 200, r.text[:200]
        assert "text/html" in r.headers.get("content-type", ""), r.headers
        assert "上午上门情况统计图" in r.text
        assert f"{name}?raw=1" in r.text

        # 2) ?raw=1：原始图片字节
        r2 = self.client.get(f"/h5/img/{name}?raw=1")
        assert r2.status_code == 200
        assert r2.content == _PNG_1PX

        # 3) 非法文件名 404
        assert self.client.get("/h5/img/../etc/passwd").status_code in (404, 400)
        assert self.client.get("/h5/img/zzzz.png").status_code == 404

    def test_old_link_without_title_falls_back(self) -> None:
        if self.client is None:  # pragma: no cover
            self.skipTest("fastapi 未安装")
        src = self._make_img()
        url = self.scheduler._publish_image_link(str(src), None)  # 旧路径：不带名称
        name = url.rsplit("/", 1)[-1]
        r = self.client.get(f"/h5/img/{name}")
        assert r.status_code == 200
        assert "数据图片" in r.text  # 无 .title 文件 → 回退默认标题

    def test_template_roundtrip_attach_image_name(self) -> None:
        """模板保存/回读 attach_image_name（H5.5 核心数据流）。"""
        rid = self.store.create_role("测试角色", actor="admin")["role_id"]
        tpl = self.store.create_template(
            {
                "name": "带名图模板", "kind": "notice", "period": "day",
                "role_ids": [rid], "remind_at": "08:15", "notice_times": ["08:15"], "text_tpl": "今日通知",
                "attach_image": True, "attach_image_path": "img_attach/a.png",
                "attach_image_name": "上午上门情况统计图",
                "enabled": True,
            },
            actor="admin",
        )
        assert tpl["attach_image_name"] == "上午上门情况统计图"
        got = self.store.get_template(tpl["template_id"])
        assert got["attach_image_name"] == "上午上门情况统计图"

        # 更新不传名称 → 保留旧值（兼容启停等部分更新路径）
        self.store.update_template(
            tpl["template_id"], {"name": "带名图模板2", "enabled": False}, actor="admin"
        )
        got2 = self.store.get_template(tpl["template_id"])
        assert got2["attach_image_name"] == "上午上门情况统计图"


if __name__ == "__main__":
    unittest.main()