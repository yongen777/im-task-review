#!/bin/bash
# 深挖 2026-11-13 12:40 死机窗口：找出内存增长真凶
echo '════ 1. 死机窗日志总量与最后时间戳 ════'
journalctl --since '2026-11-13 12:00' --until '2026-11-13 12:45' --no-pager | wc -l
journalctl --since '2026-11-13 12:00' --until '2026-11-13 12:45' --no-pager | tail -5 | cut -c1-130
echo '════ 2. 窗口内所有 kernel 级异常（OOM/memory/swap/hung/drop） ════'
journalctl -k --since '2026-11-13 12:00' --until '2026-11-13 12:45' --no-pager | grep -viE 'NMI watchdog.*disabled' | tail -25 | cut -c1-150
echo '════ 3. 窗口内全部服务的 ERROR/WARN 采样（按频次排序） ════'
journalctl --since '2026-11-13 12:00' --until '2026-11-13 12:45' --no-pager -p warning 2>/dev/null | awk -F': ' '{print $3}' | sort | uniq -c | sort -rn | head -10
echo '════ 4. 死机前最后 2 分钟全量日志（截断 130 列） ════'
journalctl --since '2026-11-13 12:38' --until '2026-11-13 12:41' --no-pager | tail -40 | cut -c1-130
