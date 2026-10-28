# -*- coding: utf-8 -*-
"""远程导入：把本地定义的春促片长 7 时点模板推到服务器后台（走 admin API，无需 SSH）。

用法：
    python scripts/remote_import_chuncu.py            # 默认推到 203.0.113.10:9013
    环境变量 IMT_REMOTE_ADMIN_URL / IMT_REMOTE_ADMIN_USER / IMT_REMOTE_ADMIN_PASS 可覆盖
"""
from __future__ import annotations

import http.client
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.import_chuncu_templates import ROLE_NAME, TEMPLATES  # noqa: E402

BASE = os.environ.get("IMT_REMOTE_ADMIN_URL", "http://203.0.113.10:9013")
USER = os.environ.get("IMT_REMOTE_ADMIN_USER", "admin")
PASS = os.environ.get("IMT_REMOTE_ADMIN_PASS", "")


class AdminClient:
    def __init__(self, base: str) -> None:
        p = urlparse(base)
        self.host, self.port, self.conn = p.hostname, p.port or 80, None
        self.sid, self.csrf = "", ""

    def _call(self, method: str, path: str, payload=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=20)
        headers = {"Content-Type": "application/json"}
        if self.sid:
            headers["Cookie"] = self.sid
        if payload is not None:
            headers["X-CSRF-Token"] = self.csrf
        body = json.dumps(payload) if payload is not None else None
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        conn.close()
        return resp.status, data

    def login(self, user: str, password: str) -> None:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=20)
        body = json.dumps({"username": user, "password": password})
        conn.request("POST", "/api/admin/login", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        if resp.status != 200 or not data.get("ok"):
            raise SystemExit(f"登录失败: HTTP {resp.status} {data}")
        raw_cookie = resp.getheader("Set-Cookie") or ""
        self.sid = [p for p in raw_cookie.split(";") if p.strip().startswith("sid=")][0].strip()
        self.csrf = data["csrf_token"]
        conn.close()
        print(f"· 已登录 {self.host}:{self.port} (user={user})")

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, payload):
        return self._call("POST", path, payload)


def main() -> int:
    if not PASS:
        raise SystemExit("缺少管理员密码：请设置 IMT_REMOTE_ADMIN_PASS 环境变量")
    cli = AdminClient(BASE)
    cli.login(USER, PASS)

    # 1) 角色：存在则复用
    st, roles = cli.get("/api/admin/roles")
    role = next((r for r in roles.get("items", []) if r["name"] == ROLE_NAME), None)
    if role is None:
        st, data = cli.post("/api/admin/roles", {"name": ROLE_NAME, "note": "春促按角色提醒 · 片长/分支局长"})
        role = data.get("role") or data
        print(f"✔ 创建角色: {ROLE_NAME} -> {role['role_id']}")
    else:
        print(f"· 复用角色: {ROLE_NAME} ({role['role_id']})")

    # 2) 模板：按 name 幂等（存在则更新）
    st, existing = cli.get("/api/admin/templates?size=50")
    by_name = {t["name"]: t for t in existing.get("items", [])}

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
        cur = by_name.get(spec["name"])
        if cur is None:
            st, data = cli.post("/api/admin/templates", payload)
            if st != 200 or not data.get("ok"):
                print(f"  ✗ 创建失败 [{spec['name']}]: HTTP {st} {data}")
                continue
            tid = data.get("template", {}).get("template_id") or data.get("template_id")
            print(f"✔ 创建模板: {spec['name']} -> {tid}")
        else:
            tid = cur["template_id"]
            # PUT 更新
            st, data = cli._call("PUT", f"/api/admin/templates/{tid}", payload)
            if st != 200 or not data.get("ok"):
                print(f"  ✗ 更新失败 [{spec['name']}]: HTTP {st} {data}")
                continue
            print(f"· 更新模板: {spec['name']} ({tid})")

    # 3) 复核
    st, final = cli.get("/api/admin/templates?size=50")
    items = final.get("items", [])
    print(f"\n服务器当前模板数: {final.get('total')}")
    for t in items:
        print(f"  [{t['template_id']}] {t['name']} times={t.get('notice_times')} attach={t.get('attach_image')} keys={len(t.get('data_keys') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
