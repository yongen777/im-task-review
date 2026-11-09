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
  4. 不删除 /opt/imtask 下任何目录 —— tar 解压只覆盖；data/ 永不打包、永不上传（例外：⑨ 清理未被引用的历史构建产物）
  5. 打包强制断言 admin_static/index.html + assets/*.js 存在（缺则中止，防后台白屏）

部署流程：版本库一致性检查 → 打包 → 预检 → 备份(app+.env) → 上传覆盖 → 装依赖 → 重启 → 验收
（healthz 四项 + echostr 回显 + 后台 200 + 远程 pytest【tests 临时上传，跑完即删】）→ 清理历史构建产物
→ 失败自动回滚。

生产目录瘦身（2026-11-05）：scripts/tests 不再常驻 /opt/imtask —— 远程验收时临时上传 tests 跑完删除。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── 连接参数：优先读环境变量，避免把某一套环境的地址/账号写死（2026-11-05）──
DEFAULT_HOST = os.environ.get("IMT_DEPLOY_HOST", "203.0.113.10")
DEFAULT_PORT = int(os.environ.get("IMT_DEPLOY_PORT", "20401"))
DEFAULT_USER = os.environ.get("IMT_DEPLOY_USER", "root")
DEFAULT_KEY = os.environ.get("IMT_DEPLOY_KEY", str(Path.home() / ".ssh" / "imtask_deploy_key"))
REMOTE_DIR = os.environ.get("IMT_DEPLOY_DIR", "/opt/imtask")

# 生产目录只放运行所需的 app/（含前端产物）+ 依赖清单 + 文档；
# scripts（含部署脚本自身与含真实信息的工作区脚本）与 tests 不常驻生产（2026-11-05）。
INCLUDE = ["app", "requirements.txt", "README.md"]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "data", "docs", "_legacy"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".db-shm", ".db-wal"}
BACKUP_KEEP = 3
# DB 备份保留份数（2026-11-06 workbuddy 分支 165a3bc 合入：此前部署脚本完全不备份也不轮转 DB）
DB_BACKUP_KEEP = 3


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


REPO_ROOT = Path(os.environ.get("IMT_REPO", r"D:\im-task-review"))
REPO_PAIRS = [
    ("app", "backend/app"),
    ("tests", "backend/tests"),
    ("scripts", "backend/scripts"),
]


def _tree_map(root: Path, sub: str) -> dict[str, Path]:
    base = root / sub
    if not base.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(base)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if f.suffix in EXCLUDE_SUFFIX:
            continue
        # 前端构建产物带哈希、且版本库会累积历史版本（工作区只留当前版），
        # 逐文件比对会产生大量假差异 → 只比 index.html，assets 交给打包断言管。
        if "admin_static/assets" in str(rel).replace("\\", "/"):
            continue
        out[str(rel).replace("\\", "/")] = f
    return out


def _same_content(fa: Path, fb: Path) -> bool:
    """内容是否等价。

    两道归一化，缺一个闸门就没用：
      1. 忽略 CRLF/LF 差异（Windows 检出与工作区行尾常不一致）；
      2. 两边都先跑一遍脱敏替换 —— 版本库里存的是**脱敏副本**（真实姓名/手机号/IP
         换成占位值），直接比字节会把每个含真实姓名的文件都判成"不同"。
    规则见 scripts/scrub_rules.py（与提交前脱敏用的是同一份）。
    """
    a, b = fa.read_bytes(), fb.read_bytes()
    if a == b:
        return True
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from scrub_rules import scrub  # type: ignore[import-not-found]

        na = scrub(a.decode("utf-8").replace("\r\n", "\n"))
        nb = scrub(b.decode("utf-8").replace("\r\n", "\n"))
    except UnicodeDecodeError:
        return False
    except Exception:  # noqa: BLE001 - 规则文件缺失时退回纯行尾归一化
        try:
            na = a.decode("utf-8").replace("\r\n", "\n")
            nb = b.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            return False
    return na == nb


def check_repo_sync(allow_drift: bool) -> None:
    """部署前闸门：工作区代码必须与版本库一致，否则中止。

    为什么需要：2026-11-02~03 连续四次出现「线上已跑、代码没入库」——
    改动只落在工作区，git 里还是上一版。事后靠人工补提交，一旦漏掉，
    线上代码就和版本库脱钩，回滚/追责都失去依据。
    这里做三件事：
      1. 版本库自身必须 clean（有未提交改动 → 中止）；git 命令本身失败（如对象库损坏）也中止 ——
         stdout 为空不能当作 clean（2026-11-06 实测事故：坏 .git 时闸门静默失效）；
      2. 工作区（app/tests/scripts）与版本库逐文件比对；
      3. 前端源码（D:\\im-task-admin\\src ↔ admin-ui/src）同样逐文件比对
         （2026-11-05 实测事故：前端源码镜像漏同步 24 小时无人发现）。
    ⛔ 只读检查，不改任何文件；确实要强推时用 --allow-drift。
    """
    if not (REPO_ROOT / ".git").exists():
        print(f"    ⚠ 未找到版本库 {REPO_ROOT}，跳过一致性检查（建议设置 IMT_REPO）")
        return

    dirty_proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if dirty_proc.returncode != 0:
        print("✗ 版本库状态不可读（git status 失败，仓库可能损坏），中止部署")
        tip = (dirty_proc.stderr or dirty_proc.stdout or "").strip()
        if tip:
            print(f"  {tip[:200]}")
        raise SystemExit(1)
    dirty = dirty_proc.stdout.strip()
    diffs: list[str] = []
    for ws_sub, repo_sub in REPO_PAIRS:
        a = _tree_map(ROOT, ws_sub)
        b = _tree_map(REPO_ROOT, repo_sub)
        for rel in sorted(set(a) | set(b)):
            fa, fb = a.get(rel), b.get(rel)
            if fa is None:
                diffs.append(f"{repo_sub}/{rel}（版本库有、工作区没有）")
            elif fb is None:
                diffs.append(f"{ws_sub}/{rel}（工作区有、版本库没有）")
            elif not _same_content(fa, fb):
                diffs.append(f"{ws_sub}/{rel}（内容不同）")
    # 前端源码镜像比对（admin-ui 的 src 目录对应前端工程工作区）
    ui_src = Path(os.environ.get("IMT_ADMIN_UI_SRC", r"D:\im-task-admin\src"))
    if ui_src.is_dir():
        a = _tree_map(ui_src.parent, "src")
        b = _tree_map(REPO_ROOT, "admin-ui/src")
        for rel in sorted(set(a) | set(b)):
            fa, fb = a.get(rel), b.get(rel)
            if fa is None:
                diffs.append(f"admin-ui/src/{rel}（版本库有、前端工作区没有）")
            elif fb is None:
                diffs.append(f"admin-ui/src/{rel}（前端有、版本库没有）")
            elif not _same_content(fa, fb):
                diffs.append(f"admin-ui/src/{rel}（内容不同）")
    for extra in ("README.md", "requirements.txt"):
        fa, fb = ROOT / extra, REPO_ROOT / "backend" / extra
        if fa.exists() and fb.exists() and not _same_content(fa, fb):
            diffs.append(f"{extra}（内容不同）")

    if not dirty and not diffs:
        print(f"    ✓ 代码与版本库一致（{REPO_ROOT.name}）")
        return

    if allow_drift:
        print(f"    ⚠ 代码与版本库不一致，但已指定 --allow-drift，继续部署")
        if dirty:
            print(f"      版本库有未提交改动：{len(dirty.splitlines())} 项")
        for d in diffs[:10]:
            print(f"      · {d}")
        return

    print("✗ 代码与版本库不一致，中止部署（防止线上代码与 git 脱钩）")
    if dirty:
        print(f"  版本库有未提交改动：{len(dirty.splitlines())} 项")
        for line in dirty.splitlines()[:10]:
            print(f"    {line}")
    for d in diffs[:10]:
        print(f"  差异：{d}")
    if len(diffs) > 10:
        print(f"  …另有 {len(diffs) - 10} 个文件不同")
    print("  处理方式：先把改动同步进版本库并提交；确需照现状上线时加 --allow-drift")
    raise SystemExit(1)


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


def build_tests_tarball() -> Path:
    """把本地 tests 打成小包供远程验收用（跑完即删，不常驻生产目录）。"""
    tar_path = Path(os.environ.get("TEMP", "/tmp")) / "imtask_tests.tar.gz"
    if tar_path.exists():
        tar_path.unlink()
    with tarfile.open(tar_path, "w:gz") as tar:
        for p in sorted((ROOT / "tests").rglob("*")):
            if not p.is_file() or p.suffix in EXCLUDE_SUFFIX:
                continue
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            tar.add(p, arcname=str(p.relative_to(ROOT)).replace("\\", "/"))
    return tar_path


def assert_admin_assets(names: list[str]) -> None:
    """后台是 Vite 构建产物：index.html 缺 → 白屏；CSS 缺 → 无样式裸页面（两次事故都出在这）。

    2026-10-29 起不只查「有没有」，而是**按 index.html 里真实引用的 /assets/* 逐个对包**，
    哈希对不上（只传了 JS 没传新 CSS）会直接中止，不用等到线上看到裸页面。
    """
    name_set = set(names)
    if "app/admin_static/index.html" not in name_set:
        raise SystemExit("✗ 打包缺 app/admin_static/index.html（后台白屏风险），中止")
    js = [n for n in names if n.startswith("app/admin_static/assets/") and n.endswith(".js")]
    css = [n for n in names if n.startswith("app/admin_static/assets/") and n.endswith(".css")]
    if not js:
        raise SystemExit("✗ 打包缺 app/admin_static/assets/*.js（Vite 产物），中止")
    if not css:
        raise SystemExit("✗ 打包缺 app/admin_static/assets/*.css（无样式裸页面风险），中止")

    index_path = ROOT / "app" / "admin_static" / "index.html"
    refs = sorted(set(re.findall(r"/assets/[A-Za-z0-9._-]+", index_path.read_text(encoding="utf-8"))))
    if not refs:
        raise SystemExit("✗ index.html 未引用任何 /assets/*，构建产物异常，中止")
    missing = [r for r in refs if f"app/admin_static{r}" not in name_set]
    if missing:
        raise SystemExit(f"✗ index.html 引用的资源不在包内：{missing}（会白屏 / 裸页面），中止")


def main() -> int:
    ap = argparse.ArgumentParser(description="IMTask 远程一键部署（安全版：不碰 .env/不重置密码）")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--dry-run", action="store_true", help="只打包+连通+预检，不改服务器任何状态")
    ap.add_argument("--skip-deps", action="store_true")
    ap.add_argument("--skip-tests", action="store_true", help="跳过临时上传 tests 的远程 pytest")
    ap.add_argument("--allow-drift", action="store_true",
                    help="允许工作区与版本库不一致（默认会中止，防止线上代码与 git 脱钩）")
    args = ap.parse_args()
    H, P, U, K = args.host, args.port, args.user, args.key

    if not Path(K).is_file():
        raise SystemExit(f"✗ 私钥不存在: {K}")

    print("⓪ 版本库一致性检查（工作区 vs git）")
    check_repo_sync(args.allow_drift)

    print("① 打包（app/requirements.txt/README.md，排除 data 与缓存；scripts/tests 不进生产）")
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
    print(f"③ 服务器备份（app → app.bak-{ts}；DB → im_task.db.bak-{ts}；.env 只备不动）")
    code, _ = ssh_run(H, P, U, K,
                      f"cd {REMOTE_DIR} && rm -rf app.bak-{ts} && cp -a app app.bak-{ts} && cp -a .env .env.bak-{ts} && echo BACKUP_OK",
                      timeout=120, show=2)
    if "BACKUP_OK" not in _:
        print("✗ 备份失败，中止部署")
        return 1

    # DB 一致性快照：用 sqlite3 .backup 而不是 cp（workbuddy 分支 165a3bc 合入）。
    # 线上库是 WAL 模式，直接 cp 拿到的是「主库 + 未 checkpoint 的 -wal」半一致状态；
    # .backup 走 SQLite 在线备份 API，产出单文件且已 checkpoint。
    # 备份失败不阻断部署（DB 内容未变），但必须显式告警。
    print("   DB 一致性快照（sqlite3 .backup，不用 cp）")
    db_bak_cmd = (
        f"cd {REMOTE_DIR}/data && "
        f"sqlite3 im_task.db \".backup 'im_task.db.bak-{ts}'\" 2>/dev/null && echo DBBAK_OK"
    )
    code, _ = ssh_run(H, P, U, K, db_bak_cmd, timeout=180, show=2)
    if "DBBAK_OK" not in _:
        print("   ⚠ DB 快照失败（不阻断部署，但本次部署没有新的 DB 回滚点）")

    print("④ 上传并覆盖（tar 只覆盖不删除，data/ 不在包内）")
    if scp_upload(H, P, U, K, tar_path, "/tmp/imtask_app.tar.gz") != 0:
        return 1
    code, _ = ssh_run(H, P, U, K,
                      f"cd {REMOTE_DIR} && find app -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null; "
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

    # 2026-10-29：按 index.html 真实引用逐个 curl，挡住「页面能开但 JS/CSS 是 404」
    code, refs_out = ssh_run(
        H, P, U, K,
        f"grep -o '/assets/[A-Za-z0-9._-]*' {REMOTE_DIR}/app/admin_static/index.html | sort -u",
        timeout=30, show=2,
    )
    asset_refs = [x.strip() for x in refs_out.splitlines() if x.strip().startswith("/assets/")]
    if not asset_refs:
        print("    ✗ index.html 里没有 /assets/* 引用"); ok = False
    for ref in asset_refs:
        code, out = ssh_run(
            H, P, U, K,
            f'curl -s -m 8 -o /dev/null -w "%{{http_code}}" http://127.0.0.1:9013{ref}',
            timeout=30, show=1,
        )
        if "200" not in out:
            print(f"    ✗ 静态资源不可用：{ref} → {out.strip()}"); ok = False
    if ok and asset_refs:
        print(f"    静态资源 {len(asset_refs)} 个全部 200")
    if not args.skip_tests:
        print("⑧ 远程 pytest（tests 临时上传，跑完即删，不常驻生产目录）")
        tests_tar = build_tests_tarball()
        if scp_upload(H, P, U, K, tests_tar, "/tmp/imtask_tests.tar.gz") != 0:
            print("    ✗ tests 上传失败"); ok = False
        else:
            # 2026-11-09 修复验收误判（11-09 wb 部署事故实测）：旧写法 grep "failed" 有两个洞——
            # ① 管道里 tail 恒返 0，ssh 退出码失去意义；② collection error 的文本是 "error(s)"
            #   不是 "failed"，41 条收集失败照样打印"验收通过"。
            # 现改用退出码哨兵：pytest 直跑取真实退出码，非 0 一律判失败（断言失败/收集错误/环境崩）。
            code, _ = ssh_run(H, P, U, K,
                              f"cd {REMOTE_DIR} && tar -xzf /tmp/imtask_tests.tar.gz && "
                              ".venv/bin/python -m pytest -o addopts=\"\" -q; rc=$?; "
                              "echo PYTEST_EXIT_CODE=$rc; exit 0",
                              timeout=600, show=6)
            ssh_run(H, P, U, K, f"rm -rf {REMOTE_DIR}/tests /tmp/imtask_tests.tar.gz", timeout=60, show=1)
            m = re.search(r"PYTEST_EXIT_CODE=(\d+)", _)
            pytest_rc = int(m.group(1)) if m else 1
            if pytest_rc != 0:
                print(f"    ✗ 远程 pytest 失败（退出码 {pytest_rc}，含断言失败/收集错误）"); ok = False
            else:
                passed = re.search(r"(\d+) passed", _)
                print(f"    ✓ 远程 pytest 全绿（{passed.group(1) if passed else '?'} passed）")

    print("⑨ 清理生产目录：未被引用的历史构建产物 + 遗留 scripts")
    keep_cmd = (
        f"cd {REMOTE_DIR} && "
        "KEEP=$( (cat app/admin_static/index.html; cat app.bak-*/admin_static/index.html 2>/dev/null) "
        "| grep -o '/assets/[A-Za-z0-9._-]*' | sort -u | sed 's#^/assets/##' ); "
        "if [ -n \"$KEEP\" ]; then removed=0; for f in app/admin_static/assets/*; do "
        "[ -f \"$f\" ] || continue; b=$(basename \"$f\"); "
        "printf '%s\\n' \"$KEEP\" | grep -qx \"$b\" || { rm -f -- \"$f\"; removed=$((removed+1)); }; done; "
        "echo ASSETS_REMOVED=$removed; else echo ASSETS_KEEP_EMPTY_SKIP; fi; "
        f"rm -rf {REMOTE_DIR}/scripts"
    )
    ssh_run(H, P, U, K, keep_cmd, timeout=120, show=3)

    # 清理旧备份（保留最近 BACKUP_KEEP 份）
    # 备份轮转：删除前先把「将被删除的清单」打印出来留痕（workbuddy 分支 165a3bc 合入）。
    # 2026-11-06 排序修正（teleagent 实测铁证）：轮转一律用「文件名 sort」，不用 mtime 倒序列法
    #   原因：.env 自 10-29 未改过，cp -a 保留了源 mtime → 全部 .env.bak mtime 相同 →
    #   mtime 排序退化，历次部署一直在「删新留旧」（实测：mtime 排序删的是
    #   20261106-121628 最新备份；sort 删的是 20261029 最旧）。备份名含
    #   时间戳天然单调，业界（Capistrano keep last N）也一律按目录名排序。
    print("⑩ 备份轮转（删除前先列清单留痕）")
    for label, pattern, keep in (("app", "app.bak-*", BACKUP_KEEP),
                                  (".env", ".env.bak-*", BACKUP_KEEP),
                                  ("DB", "data/im_task.db.bak-*", DB_BACKUP_KEEP)):
        # 只认「备份本体」：排除 SQLite WAL 模式派生的 -wal/-shm 伴生文件
        _, out = ssh_run(H, P, U, K,
                         f"cd {REMOTE_DIR} && ls -1d {pattern} 2>/dev/null "
                         f"| grep -vE '\\-(wal|shm)$' | sort | "
                         f"head -n -{keep} | sed 's#^#将删除: #'",
                         timeout=60, show=40)
        lines = [x.strip() for x in out.strip().splitlines() if x.strip()]
        if lines:
            for line in lines:
                print(f"   [轮转/{label}] {line}")
        else:
            print(f"   [轮转/{label}] 无需删除（保留最近 {keep} 份；按文件名时间戳取最旧）")
    # 删除命令与清单同口径（sort 排序）
    ssh_run(H, P, U, K,
            f"cd {REMOTE_DIR} && ls -1d app.bak-* 2>/dev/null | grep -vE '\\-(wal|shm)$' "
            f"| sort | head -n -{BACKUP_KEEP} | xargs -r rm -rf; "
            f"ls -1d .env.bak-* 2>/dev/null | grep -vE '\\-(wal|shm)$' "
            f"| sort | head -n -{BACKUP_KEEP} | xargs -r rm -f; "
            f"ls -1d data/im_task.db.bak-* 2>/dev/null | grep -vE '\\-(wal|shm)$' "
            f"| sort | head -n -{DB_BACKUP_KEEP} | xargs -r rm -f",
            timeout=60, show=1)

    print("\n" + ("✅ 部署完成且验收通过" if ok else "⚠️ 部署完成但有验收项异常，请人工复核"))
    print(f"   备份：{REMOTE_DIR}/app.bak-{ts}（回滚：rm -rf app && mv app.bak-{ts} app && systemctl restart imtask）")
    print(f"   DB 快照：{REMOTE_DIR}/data/im_task.db.bak-{ts}"
          f"（回滚：先 systemctl stop imtask，再 cp data/im_task.db.bak-{ts} data/im_task.db，然后 start）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
