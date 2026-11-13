#!/bin/bash
echo '════ A. 死机前最后时刻全量（11:40-11:52 尾部 50 行） ════'
journalctl --since '2026-11-13 11:40' --until '2026-11-13 11:55' --no-pager | tail -50 | cut -c1-140
echo '════ B. 11:15-11:55 按进程聚合的日志频次 TOP15（谁在狂打日志=谁在出事） ════'
journalctl --since '2026-11-13 11:15' --until '2026-11-13 11:55' --no-pager | awk -F'schj-z ' '{print $2}' | awk -F'[' '{print $1}' | awk -F':' '{print $NF}' | sort | uniq -c | sort -rn | head -15
echo '════ C. 窗口内 kernel 的 OOM/memory/hung 记录 ════'
journalctl -k --since '2026-11-13 11:00' --until '2026-11-13 12:00' --no-pager | grep -iE 'oom|out of memory|killed process|hung|blocked for more|memory cgroup' | tail -20 | cut -c1-160
echo '════ D. Failed with result 的服务清单 ════'
journalctl --since '2026-11-13 10:00' --until '2026-11-13 12:00' --no-pager | grep -B1 "Failed with result 'exit-code'" | grep -oE '^[^ ]+ [^ ]+ [a-z_-]+\[[0-9]+\]' | awk '{print $3}' | sort | uniq -c | sort -rn | head -10
