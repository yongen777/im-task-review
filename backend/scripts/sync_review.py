# -*- coding: utf-8 -*-
"""一键同步：工作区 → 评审库（含脱敏、构建对账、提交和可选推送）。

背景（2026-11-09 定稿，取代 .temp/sync_review.py 手工清单）：
  11-09 wb 任务耗时 4 小时，其中约 1 小时耗在「三地同步」结构性摩擦上——
  手工列 PAIRS 漏同步文件、忘了脱敏、前端源码与产物分开拷、部署闸门反复拦。
  本脚本把这些一次做完：任何人（teleagent / workbuddy / 用户）只需跑一条命令。

用法：
    python scripts/sync_review.py                 # 同步 + 展示将提交内容
    python scripts/sync_review.py --commit "msg"  # 同步 + git add/commit
    python scripts/sync_review.py --commit "msg" --push  # 推送、打印完整链接并导出桌面源码 diff

它做的事（顺序固定）：
  1. 后端按脱敏规则同步（app/ scripts/ tests/ 全目录比对，不是白名单 ——
     新增文件自动覆盖，彻底消灭「忘了加 PAIRS」这个事故源）
  2. 前端源码同步（im-task-admin/src → admin-ui/src，全目录）
  3. 前端产物构建并同步（vite build → backend/app/admin_static），
     dist 与 admin_static 以 dist 为准（先清后拷，不留陈旧 hash 产物）
  4. 产物对账：工作区 app/admin_static 必须与评审库 admin_static 一致
  5. 同步根目录 AGENTS.md 的验收规范，保留 data/、.env 等其他工作区文件

为什么「全目录」而不是白名单：白名单模式在 11-09 已实测漏文件（wb 只同步了
改动过的文件，漏掉被适配的既有测试 → 远程 pytest 27 条失败 → 二次部署）。
全目录 + 内容脱敏 + 闸门复核，漏项在物理上不可能发生。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

WS = Path(r"D:\im-task")
REPO = Path(r"D:\im-task-review")
UI_SRC = Path(r"D:\im-task-admin")
REVIEW_URL = "https://github.com/yongen777/im-task-review"
REVIEW_PATCH_PATH = Path.home() / "Desktop" / "imtask_review_latest.patch"
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
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".html", ".css", ".md", ".ini", ".example", ".txt", ".sh", ".ps1", ".cfg", ".sql"}
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

    agents_doc = WS / "AGENTS.md"
    if agents_doc.is_file():
        (REPO / agents_doc.name).write_bytes(scrub(agents_doc.read_text(encoding="utf-8")).encode("utf-8"))


def git_changed() -> list[str]:
    """读取逐文件状态，保留空格、中文与改名涉及的两个路径。"""
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(REPO), "status", "--porcelain", "-z",
         "--untracked-files=all", "--no-renames"],
        capture_output=True, check=True,
    )
    return [entry[3:] for entry in result.stdout.decode("utf-8").split("\0") if entry]


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


def export_review_patch(commit: str, destination: Path) -> Path:
    """导出完整提交统计和排除构建产物的源码 diff。"""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("导出源码 diff 需要完整的 40 位提交哈希")
    show = ["git", "-C", str(REPO), "show", "--no-color", "--no-ext-diff", "--no-textconv",
            "--encoding=UTF-8", commit]
    stats = subprocess.check_output([*show, "--stat"], encoding="utf-8-sig")
    patch = subprocess.check_output(
        [*show, "--", ":!*.js", ":!*.css", ":!*.map", ":!dist/*", ":(exclude,glob)**/dist/**"],
        encoding="utf-8-sig",
    )
    draft = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix=".imtask-review-", delete=False) as stream:
            draft = Path(stream.name)
            stream.write((stats.rstrip("\n") + "\n\n" + patch).encode("utf-8"))
        draft.replace(destination)
    finally:
        if draft is not None:
            draft.unlink(missing_ok=True)
    return destination


def push_review() -> int:
    """发布评审库 main 上的确切提交，成功后输出链接和桌面源码 diff。"""
    if git_changed():
        print("✗ 评审库有未提交改动，请先核对并提交")
        return 1
    git = ["git", "-C", str(REPO)]
    try:
        branch = subprocess.check_output([*git, "symbolic-ref", "--quiet", "--short", "HEAD"], text=True).strip()
        remotes = subprocess.check_output([*git, "remote", "get-url", "--push", "--all", "origin"], text=True).splitlines()
        commit = subprocess.check_output([*git, "rev-parse", "--verify", "HEAD^{commit}"], text=True).strip()
    except subprocess.CalledProcessError:
        print("✗ 无法核对评审库的分支、推送地址或提交")
        return 1
    allowed_remotes = {
        REVIEW_URL, "git@github.com:yongen777/im-task-review", "ssh://git@github.com/yongen777/im-task-review",
    }
    if branch != "main" or len(remotes) != 1 or remotes[0].rstrip("/").removesuffix(".git") not in allowed_remotes:
        print("✗ 推送目标必须是 IMTask 评审库的 origin/main")
        return 1
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        print("✗ 未读取到完整的 40 位提交哈希")
        return 1

    result = subprocess.run([*git, "push", "--no-follow-tags", "origin", f"{commit}:refs/heads/main"])
    if result.returncode != 0:
        print("✗ 推送失败")
        return result.returncode

    commit_url = f"{REVIEW_URL}/commit/{commit}"
    print(f"✓ 已推送评审库 main，完整提交哈希 {commit}")
    print(f"Commit URL {commit_url}")
    print(f"Patch URL {commit_url}.patch")
    try:
        path = export_review_patch(commit, REVIEW_PATCH_PATH)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        print(f"✗ 推送已成功，桌面源码 diff 导出失败（{exc}）")
        return 1
    print(f"本地源码 Diff {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", help="同步后创建提交并使用该消息")
    ap.add_argument("--push", action="store_true", help="推送评审库 main、打印验收链接并导出桌面源码 diff")
    ap.add_argument("--skip-build", action="store_true", help="跳过前端构建（产物未变时省时间）")
    ap.add_argument("--commit-paths", nargs="+", metavar="PATH",
                    help="限制本次提交范围；目录须以 / 结尾")
    args = ap.parse_args()

    if not REPO.is_dir():
        print(f"✗ 评审库不存在：{REPO}")
        return 1

    if (args.commit or args.push) and git_changed():
        print("✗ 评审库已有未提交改动，请核对后再同步")
        return 1

    print("① 后端全目录同步（脱敏）…")
    sync_backend()

    print("② 前端源码同步…")
    sync_frontend_ui()

    if not args.skip_build:
        print("③ 前端构建…")
        if not build_frontend():
            return 1
        print("④ 构建产物同步（工作区 + 评审库）…")
        sync_static()
    if not verify_static():
        return 1

    changed = git_changed()
    if changed:
        print("\n将提交的文件：")
        for c in changed[:20]:
            print(f"  {c}")
        if len(changed) > 20:
            print(f"  …共 {len(changed)} 项")
    else:
        print("\n评审库无内容变更（脱敏后与 HEAD 一致）")

    if args.commit and changed:
        outside = [
            path for path in changed if args.commit_paths and not any(
                path == allowed or (allowed.endswith("/") and path.startswith(allowed))
                for allowed in args.commit_paths
            )
        ]
        if outside:
            print(f"✗ 发现本次提交范围外的改动：{outside}")
            return 1
        subprocess.run(["git", "--literal-pathspecs", "-C", str(REPO), "add", "--", *changed], check=True)
        r = subprocess.run(
            ["git", "-C", str(REPO), "commit", "-m", args.commit],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            print(f"✗ commit 失败：{(r.stdout or '') + (r.stderr or '')}"[:400])
            return 1
        print("✓ 已提交")
    return push_review() if args.push else 0


if __name__ == "__main__":
    sys.exit(main())
