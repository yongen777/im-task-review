#!/bin/bash
echo '=== TOP12 内存进程（当前） ==='
ps aux --sort=-rss | head -13 | tail -12 | awk '{printf "%-9s %8.0fMB  %s %s\n", $1, $6/1024, $11, $12}' | cut -c1-110
echo '=== 现存内核 OOM 痕迹（重启后 ring buffer） ==='
dmesg 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -6 || echo '(none)'
echo '=== journal 持久化里 11:00-13:00 的 OOM/死机记录 ==='
journalctl --since '2026-11-13 10:00' --until '2026-11-13 13:00' --no-pager 2>/dev/null | grep -iE 'out of memory|oom|hung task|panic|watchdog' | tail -8 || echo '(none)'
echo '=== imtask 崩溃前的 cgroup 峰值（如保留） ==='
cat /sys/fs/cgroup/system.slice/imtask.service/memory.peak 2>/dev/null || systemctl show imtask -p MemoryPeak 2>/dev/null || echo '(no peak data)'
echo '=== 宝塔内存面板数据源（最近快照） ==='
ls -t /www/server/panel/data/db/log.db 2>/dev/null && /www/server/panel/pyenv/bin/python3 - <<'PYEOF'
import sqlite3
try:
    c = sqlite3.connect('/www/server/panel/data/db/log.db')
    tbls = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print('tables:', tbls[:10])
except Exception as e:
    print('ERR', str(e)[:80])
PYEOF
