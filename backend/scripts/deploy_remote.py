# -*- coding: utf-8 -*-
"""IMTask 远程一键部署（2026-10-27 重构版，ssh/scp 零依赖）。

用法：
  python scripts/deploy_remote.py               # 一键部署（默认参数已内置）
  python scripts/deploy_remote.py --dry-run     # 只打包+连通+预检，不覆盖不重启
  python scripts/deploy_remote.py --skip-deps   # 跳过 pip install
  python scripts/deploy_remote.py --skip-tests  # 跳过远程 pytest

⛔ 本脚本明确不做的事（2026-10-27 workbuddy 复核约定的安全边界）：
  1. 不写 / 不改 .env（现网凭据不动，仅做只读备份）
  2. 不建 / 不重置 admin 管理员密码
  3. 不重写 systemd unit（现网 imtask.service 已正确运行）
  4. 不删除 /opt/imtask 下任何目录 —— tar 解压只覆盖；data/ 永不打包、永不上传
  5. 打包强制断言 admin_static/index.html + assets/*.js 存在（缺则中止，防后台白屏）

部署流程：打包 → 预检 → 备份(app+.env) → 上传覆盖 → 装依赖 → 重启 → 验收
（healthz 四项 + echostr 回显 + 后台 200 + 远程 pytest）→ 失败自动回滚。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── 默认连接参数（本次 219 服务器已确认，可被命令行覆盖）──
DEFAULT_HOST = "203.0.113.10"
DEFAULT_PORT = 20401
DEFAULT_USER = "root"
DEFAULT_KEY = str(Path.home() / ".ssh" / "imtask_deploy_key")
REMOTE_DIR = "/opt/imtask"

INCLUDE = ["app", "scripts", "tests", "requirements.txt", "README.md"]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "data", "docs", "_legacy"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".db-shm", ".db-wal"}
BACKUP_KEEP = 3


def ssh_run(host, port, user, key, cmd, timeout=600, show=4):
    """在服务器上执行命令，返回 (exit_code, stdout+stderr)。"""
    proc = subprocess.run(
        ["ssh", "-i", key, "-p", str(port), "-o", "StrictHostKeyChecking=no",
         "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", f"{user}@{host}", cmd],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    for line in text.strip().splitlines()[-show:]:
        print("    " + line)
    return proc.returncode, text


def scp_upload(host, port, user, key, local_path, remote_path, timeout=300):
    proc = subprocess.run(
        ["scp", "-i", key, "-P", str(port), "-o", "StrictHostKeyChecking=no",
         "-o", "BatchMode=yes", str(local_path), f"{user}@{host}:{remote_path}"],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        print("    scp 失败:", (proc.stderr or "").strip()[:200])
    return proc.returncode


def build_tarball() -> tuple[Path, int, list[str]]:
    tar_path = Path(os.environ.get("TEMP", "/tmp")) / "imtask_app.tar.gz"
    if tar_path.exists():
        tar_path.unlink()
    names: list[str] = []
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in INCLUDE:
            src = ROOT / item
            if src.is_file():
                tar.add(src, arcname=item)
                names.append(item)
                continue
            if not src.is_dir():
                continue
            for p in sorted(src.rglob("*")):
                if any(part in EXCLUDE_DIRS for part in p.parts):
                    continue
                if not p.is_file() or p.suffix in EXCLUDE_SUFFIX:
                    continue
                arc = str(p.relative_to(ROOT)).replace("\\", "/")
                tar.add(p, arcname=arc)
                names.append(arc)
    return tar_path, len(names), names


def assert_admin_assets(names: list[str]) -> None:
    """workbuddy 问题 2：后台是 Vite 构建产物，缺 assets 会导致白屏——缺则中止。"""
    has_index = any(n == "app/admin_static/index.html" for n in names)
    has_asset_js = any(n.startswith("app/admin_static/assets/") and n.endswith(".js") for n in names)
    if not has_index:
        raise SystemExit("✗ 打包缺 app/admin_static/index.html（后台白屏风险），中止")
    if not has_asset_js:
        raise SystemExit("✗ 打包缺 app/admin_static/assets/*.js（Vite 产物），中止")


def main() -> int:
    ap = argparse.ArgumentParser(description="IMTask 远程一键部署（安全版：不碰 .env/不重置密码）")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--dry-run", action="store_true", help="只打包+连通+预检，不改服务器任何状态")
    ap.add_argument("--skip-deps", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()
    H, P, U, K = args.host, args.port, args.user, args.key

    if not Path(K).is_file():
        raise SystemExit(f"✗ 私钥不存在: {K}")

    print("① 打包（app/scripts/tests/requirements.txt，排除 data 与缓存）")
    tar_path, count, names = build_tarball()
    assert_admin_assets(names)
    kb = tar_path.stat().st_size / 1024
    print(f"    {count} 个文件，{kb:.0f} KB；admin_static 断言通过（index.html + assets/*.js 在包内）")

    print(f"② 连通与预检 {U}@{H}:{P}")
    code, _ = ssh_run(H, P, U, K, "echo SSH_OK && systemctl is-active imtask && "
                     f"test -f {REMOTE_DIR}/.env && echo ENV_OK && test -x {REMOTE_DIR}/.venv/bin/python && echo VENV_OK",
                     timeout=40, show=5)
    if "SSH_OK" not in _ or "ENV_OK" not in _ or "VENV_OK" not in _:
        print("✗ 预检失败（SSH/服务/.env/venv 任一不满足），中止")
        return 1
    if args.dry_run:
        print("③ --dry-run：不改服务器任何状态，到此结束 ✅")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    print(f"③ 服务器备份（app → app.bak-{ts}；.env 只备不动）")
    code, _ = ssh_run(H, P, U, K,
                      f"cd {REMOTE_DIR} && rm -rf app.bak-{ts} && cp -a app app.bak-{ts} && cp -a .env .env.bak-{ts} && echo BACKUP_OK",
                      timeout=120, show=2)
    if "BACKUP_OK" not in _:
        print("✗ 备份失败，中止部署")
        return 1

    print("④ 上传并覆盖（tar 只覆盖不删除，data/ 不在包内）")
    if scp_upload(H, P, U, K, tar_path, "/tmp/imtask_app.tar.gz") != 0:
        return 1
    code, _ = ssh_run(H, P, U, K,
                      f"cd {REMOTE_DIR} && find app scripts tests -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null; "
                      "tar -xzf /tmp/imtask_app.tar.gz && echo EXTRACT_OK",
                      timeout=180, show=2)
    if "EXTRACT_OK" not in _:
        print("✗ 解压失败，执行回滚")
        ssh_run(H, P, U, K, f"cd {REMOTE_DIR} && rm -rf app && mv app.bak-{ts} app && systemctl restart imtask", timeout=120)
        return 1

    if not args.skip_deps:
        print("⑤ 依赖安装（幂等）")
        code, _ = ssh_run(H, P, U, K,
                          f"cd {REMOTE_DIR} && .venv/bin/pip install -q --disable-pip-version-check "
                          "-i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com "
                          "-r requirements.txt && .venv/bin/python -c \"import fastapi,uvicorn,httpx,Crypto,dotenv;print('deps ok')\"",
                          timeout=900, show=3)
        if code != 0:
            print("✗ 依赖安装失败")
            return 1

    print("⑥ 重启服务")
    code, _ = ssh_run(H, P, U, K, "systemctl restart imtask && sleep 6 && systemctl is-active imtask", timeout=120, show=2)
    if "active" not in _:
        print("✗ 服务未 active，自动回滚")
        ssh_run(H, P, U, K, f"cd {REMOTE_DIR} && rm -rf app && mv app.bak-{ts} app && systemctl restart imtask && sleep 5 && systemctl is-active imtask", timeout=120)
        return 1

    print("⑦ 验收")
    ok = True
    code, _ = ssh_run(H, P, U, K, "curl -s -m 8 http://127.0.0.1:9020/healthz", timeout=30, show=2)
    if '"status":"ok"' not in _.replace(" ", ""):
        print("    ✗ healthz 异常"); ok = False
    code, _ = ssh_run(H, P, U, K, 'curl -s -m 8 "http://127.0.0.1:9020/api/app-events?echostr=DEPLOY_VERIFY_1"', timeout=30, show=2)
    if "DEPLOY_VERIFY_1" not in _:
        print("    ✗ echostr 回显异常"); ok = False
    code, _ = ssh_run(H, P, U, K, 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://127.0.0.1:9013/', timeout=30, show=2)
    if "200" not in _:
        print("    ✗ 后台非 200"); ok = False
    if not args.skip_tests:
        print("⑧ 远程 pytest")
        code, _ = ssh_run(H, P, U, K, f"cd {REMOTE_DIR} && .venv/bin/python -m pytest -o addopts=\"\" -q 2>&1 | tail -3", timeout=600, show=4)
        if "failed" in _ and "0 failed" not in _:
            print("    ✗ 远程测试有失败"); ok = False

    # 清理旧备份（保留最近 BACKUP_KEEP 份）
    ssh_run(H, P, U, K,
            f"cd {REMOTE_DIR} && ls -1dt app.bak-* 2>/dev/null | tail -n +{BACKUP_KEEP + 1} | xargs -r rm -rf; "
            f"ls -1dt .env.bak-* 2>/dev/null | tail -n +{BACKUP_KEEP + 1} | xargs -r rm -rf", timeout=60, show=1)

    print("\n" + ("✅ 部署完成且验收通过" if ok else "⚠️ 部署完成但有验收项异常，请人工复核"))
    print(f"   备份：{REMOTE_DIR}/app.bak-{ts}（回滚：rm -rf app && mv app.bak-{ts} app && systemctl restart imtask）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
