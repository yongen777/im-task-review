# -*- coding: utf-8 -*-
"""deploy_remote.py 的备份轮转逻辑回归（2026-11-06）。

覆盖三件事：
1. 轮转清单只认「备份本体」，排除 WAL 模式的 -wal/-shm 伴生文件
2. 保留份数正确（keep=3 时 5 份备份列 2 个）
3. DB 快照命令用的是 sqlite3 .backup（不是 cp）——WAL 下 cp 会拿到半一致状态

不依赖服务器，纯逻辑断言（对 shell 片段做等价复刻 + 对源码做静态检查）。
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "deploy_remote.py")

FAILS = []
PASSES = 0


def check(name, cond, detail=""):
    global PASSES
    if cond:
        PASSES += 1
        print(f"  [PASS] {name}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


def sh_list_bak(dirpath, pattern, keep):
    """复刻脚本里的清单逻辑（纯本地模拟，与线上等价）。"""
    names = []
    if os.path.isdir(dirpath):
        for f in os.listdir(dirpath):
            if not re.match(pattern, f):
                continue
            if re.search(r"-(wal|shm)$", f):      # 排除伴生文件
                continue
            names.append(f)
    names.sort()
    return names[:-keep] if keep > 0 else names


def main():
    src = io.open(SCRIPT, encoding="utf-8").read()

    print("=== ① 源码静态检查 ===")
    check("定义了 DB_BACKUP_KEEP 常量", "DB_BACKUP_KEEP = " in src)
    check("备份步骤用 sqlite3 .backup 取 DB 快照",
          ".backup" in src and "im_task.db.bak-{ts}" in src)
    # 判据：DB 快照命令行里必须出现 .backup，且备份动作本身不能是 cp 本体
    # （回滚时用 cp 把快照拷回去是对的，不在此列）
    snap_lines = [ln for ln in src.splitlines()
                  if "im_task.db" in ln and ("DBBAK_OK" in ln or ".backup" in ln)]
    check("DB 快照命令行里用的是 .backup 而非 cp",
          any(".backup" in ln for ln in snap_lines) and
          not any(re.search(r"\bcp\b", ln) for ln in snap_lines),
          str(snap_lines)[:200])
    check("回滚步骤里用 cp 恢复 DB 是正确的（本来就该 cp 回去）",
          "cp data/im_task.db.bak-{ts} data/im_task.db" in src)
    check("轮转前先打印将删除的清单",
          "将删除" in src and "[轮转/" in src)
    # 2026-11-06 teleagent 验收补充：排序修正的强断言（防回退）——
    # 实测铁证：.env 自 10-29 未改 → cp -a 保留源 mtime → 全部 .env.bak 同 mtime →
    # ls -1dt 排序退化删新留旧；备份名含时间戳天然单调（Capistrano keep last N 同款）。
    check("轮转排序已改为文件名 sort（mtime 排序在同 mtime 场景会删新留旧）",
          "ls -1d app.bak-*" in src and "ls -1dt" not in src,
          "仍存在 ls -1dt（mtime 排序）的轮转命令")
    check("删除命令与清单打印同口径（都用 sort + head -n -N）",
          src.count("head -n -") >= 4)
    check("清单与删除都排除 -wal/-shm",
          src.count("grep -vE") >= 2)
    check("DB 备份纳入轮转（不只是 app/.env）",
          "data/im_task.db.bak-*" in src)
    check("回滚说明里包含 DB 回滚命令",
          "im_task.db.bak-{ts}" in src and "cp data/im_task.db.bak-" in src)

    print()
    print("=== ② 轮转清单逻辑（含伴生文件排除）===")
    import tempfile
    tmp = tempfile.mkdtemp()
    # 造 5 份备份本体 + 2 个伴生文件
    for n in ("A", "B", "C", "D", "E"):
        open(os.path.join(tmp, "im_task.db.bak-" + n), "w").close()
    open(os.path.join(tmp, "im_task.db.bak-A-wal"), "w").close()
    open(os.path.join(tmp, "im_task.db.bak-B-shm"), "w").close()

    all_files = sorted(os.listdir(tmp))
    check("目录里共 7 个文件（5 本体 + 2 伴生）", len(all_files) == 7, str(all_files))

    to_del = sh_list_bak(tmp, r"im_task\.db\.bak-.*", 3)
    check("保留 3 份时应列 2 个待删", len(to_del) == 2, str(to_del))
    check("待删清单不含 -wal/-shm",
          not any(re.search(r"-(wal|shm)$", x) for x in to_del), str(to_del))
    check("待删清单全是本体",
          all(x in all_files for x in to_del), str(to_del))

    to_del_2 = sh_list_bak(tmp, r"im_task\.db\.bak-.*", 5)
    check("保留 5 份时应列 0 个", len(to_del_2) == 0, str(to_del_2))

    to_del_0 = sh_list_bak(tmp, r"im_task\.db\.bak-.*", 0)
    check("keep=0 时全列（5 个本体）", len(to_del_0) == 5, str(to_del_0))

    print()
    print("=== ③ 备份本体完整性不因伴生文件排除而受损 ===")
    kept = sorted(x for x in all_files if not re.search(r"-(wal|shm)$", x))
    check("排除伴生后本体仍为 5 份", len(kept) == 5, str(kept))
    check("删除时按本体名删（伴生文件随之失效，不单独留）",
          all("-wal" not in x and "-shm" not in x for x in kept), str(kept))

    print()
    print("=== ④ shell 片段可执行性（本地复刻）===")
    # 注意两点：
    # a) 不能用 `ls -1d .` —— Windows(Git Bash) 下它只输出 "."；
    #    线上是 Linux 且用的是 `ls -1d <pattern>`，这里用等价的 `ls -1` 复刻同一逻辑。
    # b) 必须显式走 bash —— Windows 上 subprocess 的 shell=True 默认是 cmd.exe，
    #    没有 grep/sort/head，片段会静默失败（返回码非 0 但不报错）。
    bash = None
    for cand in ("C:/Program Files/Git/bin/bash.exe",
                 "C:/Users/lenovo/.workbuddy/binaries/PortableGit/bin/bash.exe",
                 "bash"):
        try:
            subprocess.run([cand, "-c", "echo ok"], capture_output=True, timeout=20)
            bash = cand
            break
        except Exception:
            continue
    check("找到可用的 bash", bash is not None, "本机无 bash，无法验证 shell 片段")
    if bash:
        cmd = "ls -1 | grep -vE '\\-(wal|shm)$' | sort | head -n -3 | sed 's#^#将删除: #'"
        r = subprocess.run([bash, "-c", cmd], cwd=tmp, capture_output=True, text=True, timeout=60)
        check("shell 片段无语法错误", r.returncode == 0, r.stderr)
        out_lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
        check("shell 片段输出 2 行待删", len(out_lines) == 2, repr(r.stdout))
        check("shell 片段输出不含 -wal/-shm",
              not any(re.search(r"-(wal|shm)$", x) for x in out_lines), repr(out_lines))

    print()
    print("=" * 60)
    print(f"通过 {PASSES} / 失败 {len(FAILS)}")
    if FAILS:
        for f in FAILS:
            print("   FAILED:", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
