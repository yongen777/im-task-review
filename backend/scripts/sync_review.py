# -*- coding: utf-8 -*-
"""一键同步：工作区 → 评审库（含脱敏、前端、构建产物、产物对账、提交提示）。

背景（2026-11-09 定稿，取代 .temp/sync_review.py 手工清单）：
  11-09 wb 任务耗时 4 小时，其中约 1 小时耗在「三地同步」结构性摩擦上——
  手工列 PAIRS 漏同步文件、忘了脱敏、前端源码与产物分开拷、部署闸门反复拦。
  本脚本把这些一次做完：任何人（teleagent / workbuddy / 用户）只需跑一条命令。

用法：
    python scripts/sync_review.py                 # 同步 + 展示将提交内容
    python scripts/sync_review.py --commit "msg"  # 同步 + git add/commit
    （push 仍手动做——对外发布属敏感操作，不走自动化）

它做的事（顺序固定）：
  1. 后端按脱敏规则同步（app/ scripts/ tests/ 全目录比对，不是白名单 ——
     新增文件自动覆盖，彻底消灭「忘了加 PAIRS」这个事故源）
  2. 前端源码同步（im-task-admin/src → admin-ui/src，全目录）
  3. 前端产物构建并同步（vite build → backend/app/admin_static），
     dist 与 admin_static 以 dist 为准（先清后拷，不留陈旧 hash 产物）
  4. 产物对账：工作区 app/admin_static 必须与评审库 admin_static 一致
  5. 不动 data/、.env、README/requirements 以外的杂项

为什么「全目录」而不是白名单：白名单模式在 11-09 已实测漏文件（wb 只同步了
改动过的文件，漏掉被适配的既有测试 → 远程 pytest 27 条失败 → 二次部署）。
全目录 + 内容脱敏 + 闸门复核，漏项在物理上不可能发生。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

WS = Path(r"D:\im-task")
REPO = Path(r"D:\im-task-review")
UI_SRC = Path(r"D:\im-task-admin")
sys.path.insert(0, str(WS / "scripts"))
from scrub_rules import scrub  # noqa: E402

# 后端同步映射：工作区目录 → 评审库目录（全目录递归）
BACKEND_DIRS = [
    ("app", "backend/app"),
    ("scripts", "backend/scripts"),
    ("tests", "backend/tests"),
]
# 目录里绝不动的名字（生产数据/缓存/git 元数据）
SKIP_NAMES = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "data", ".git"}
# 只同步文本类文件（favicon/png 等二进制不脱敏不文本化，按需专用拷贝）
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".html", ".css", ".md", ".ini", ".example", ".txt", ".sh", ".cfg"}
# 评审库 admin_static 整目录以工作区构建产物为准
REPO_STATIC = REPO / "backend" / "app" / "admin_static"
REPO_UI_SRC = REPO / "admin-ui" / "src"
UI_DIST = UI_SRC / "dist"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def sync_backend() -> None:
    """工作区后端 → 评审库（脱敏，全目录镜像：新增自动覆盖、删除同步删除）。"""
    for src_sub, dst_sub in BACKEND_DIRS:
        src_root, dst_root = WS / src_sub, REPO / dst_sub
        if not src_root.is_dir():
            continue
        for f in sorted(src_root.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES or any(part in SKIP_NAMES for part in f.parts):
                continue
            rel = f.relative_to(src_root)
            dst = dst_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(scrub(f.read_bytes().decode("utf-8")).encode("utf-8"))
        # 评审库多出来的文件（工作区已删的）也删掉，保持镜像（同样只管文本类）
        for f in sorted(dst_root.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES or any(part in SKIP_NAMES for part in f.parts):
                continue
            rel = f.relative_to(dst_root)
            if not (src_root / rel).is_file():
                f.unlink()
def git_changed() -> list[str]:
    """评审库当前待提交清单（git status 权威；容忍 ?/! 在任一列、剔除警告行）。"""
    out = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    status = out.stdout or ""
    changed: list[str] = []
    for ln in status.splitlines():
        ln = ln.rstrip("\r")
        if len(ln) >= 4 and (ln[0] in "MARCUD?!" or ln[1] in "MARCUD?!") and ln[2] == " ":
            changed.append(ln[3:].strip())
    return changed


def sync_frontend_ui() -> None:
    """前端源码（含构建）全目录同步到评审库 admin-ui/src。"""
    src_root, dst_root = UI_SRC / "src", REPO_UI_SRC
    for f in sorted(src_root.rglob("*")):
        if not f.is_file() or any(part in SKIP_NAMES for part in f.parts):
            continue
        rel = f.relative_to(src_root)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst)
    for f in sorted(dst_root.rglob("*")):
        if not f.is_file() or any(part in SKIP_NAMES for part in f.parts):
            continue
        if not (src_root / f.relative_to(dst_root)).is_file():
            f.unlink()


def build_frontend() -> bool:
    """vite build。失败返回 False（构建带病产物比不部署更糟）。

    2026-11-12 Node 问题修正：本机 PATH 首位的 node 是 v11.13.0（ESM 语法直接报错，
    HANDOFF 2026-11-12 记录在案），`cmd /c npm run build` 会挂。优先用 DSH 内置
    Electron（ELECTRON_RUN_AS_NODE=1 即 node v24）直跑 vite.js（本轮实测 exit=0）；
    两者任一不存在时回退 npm（其它机器不受影响）。
    """
    import os
    vite_js = UI_SRC / "node_modules" / "vite" / "bin" / "vite.js"
    electron = Path(r"D:\DSH\Desktop\DeepSeek Harness.exe")
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    if vite_js.is_file() and electron.is_file():
        cmd = [str(electron), str(vite_js), "build"]
    else:
        cmd = ["cmd", "/c", "npm", "run", "build"]
    r = subprocess.run(
        cmd, cwd=str(UI_SRC), capture_output=True, timeout=300, shell=False, env=env,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        print("✗ 前端构建失败：")
        print((r.stdout or "")[-500:])
        print((r.stderr or "")[-200:])
        return False
    print("✓ vite build 完成")
    return True


def sync_static() -> None:
    """构建产物 → 工作区 app/admin_static + 评审库 admin_static（先清后拷，同源一致）。"""
    for target in (WS / "app" / "admin_static", REPO_STATIC):
        if target.exists():
            for f in target.rglob("*"):
                if f.is_file():
                    f.unlink()
        (target / "assets").mkdir(parents=True, exist_ok=True)
        (target / "favicon.ico").parent.mkdir(parents=True, exist_ok=True)
        for f in UI_DIST.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(UI_DIST)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)


def verify_static() -> bool:
    """三方对账：工作区 admin_static === 评审库 admin_static（逐文件 md5）。"""
    a, b = WS / "app" / "admin_static", REPO_STATIC
    fa = {str(f.relative_to(a)): md5(f) for f in a.rglob("*") if f.is_file()}
    fb = {str(f.relative_to(b)): md5(f) for f in b.rglob("*") if f.is_file()}
    if fa != fb:
        print(f"✗ 产物对账不一致：仅工作区 {sorted(set(fa) - set(fb))}；仅评审库 {sorted(set(fb) - set(fa))}")
        common_diff = [k for k in set(fa) & set(fb) if fa[k] != fb[k]]
        if common_diff:
            print(f"  md5 不同：{common_diff}")
        return False
    print(f"✓ 产物对账一致（{len(fa)} 个文件）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", help="同步后附带 git commit 的消息（push 仍手动）")
    ap.add_argument("--skip-build", action="store_true", help="跳过前端构建（产物未变时省时间）")
    args = ap.parse_args()

    if not REPO.is_dir():
        print(f"✗ 评审库不存在：{REPO}")
        return 1

    print("① 后端全目录同步（脱敏）…")
    sync_backend()

    print("② 前端源码同步…")
    sync_frontend_ui()

    changed = git_changed()

    if not args.skip_build:
        print("③ 前端构建…")
        if not build_frontend():
            return 1
        print("④ 构建产物同步（工作区 + 评审库）…")
        sync_static()
    if not verify_static():
        return 1

    if changed:
        print("\n将提交的文件：")
        for c in changed[:20]:
            print(f"  {c}")
        if len(changed) > 20:
            print(f"  …共 {len(changed)} 项")
    else:
        print("\n评审库无内容变更（脱敏后与 HEAD 一致）")

    if args.commit:
        subprocess.run(["git", "-C", str(REPO), "add", "-A"], check=False)
        r = subprocess.run(
            ["git", "-C", str(REPO), "commit", "-m", args.commit],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            print(f"✗ commit 失败：{(r.stdout or '') + (r.stderr or '')}"[:400])
            return 1
        print(f"✓ 已提交（未推送——push 属对外发布，请手动确认后执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
