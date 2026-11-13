#!/bin/bash
SVC='spring_78a4189c187e6456732dd7303825f9a5'
echo '════ 1. 嫌疑服务身份（unit 描述/启动命令） ════'
systemctl cat $SVC.service 2>/dev/null | grep -E 'Description|ExecStart' | cut -c1-160
echo '════ 2. 死机前（11:10-11:50）该服务的 ERROR 内容频次 ════'
journalctl -u $SVC --since '2026-11-13 11:10' --until '2026-11-13 11:50' --no-pager 2>/dev/null | grep -iE 'error|exception|oom|outofmemory|gc overhead' | awk -F'\\\\|\\|\\|' '{print substr($0, index($0,":")+1, 90)}' | sort | uniq -c | sort -rn | head -12
echo '════ 3. 死机前该服务 ERROR 原文样本 3 条 ════'
journalctl -u $SVC --since '2026-11-13 11:10' --until '2026-11-13 11:50' --no-pager 2>/dev/null | grep -iE 'error|exception|oom|outofmemory|gc overhead' | head -3 | cut -c1-200
echo '════ 4. 它在 10:13 前后是否有行为变化（10:00-11:15 分段日志量） ════'
for w in '10:00 10:13' '10:13 10:30' '10:30 10:50' '10:50 11:15'; do set -- $w; printf '%s-%s: ' $1 $2; journalctl -u $SVC --since "2026-11-13 $1" --until "2026-11-13 $2" --no-pager 2>/dev/null | wc -l; done
echo '════ 5. 全窗口该服务的 OOM/内存类日志条数 ════'
journalctl -u $SVC --since '2026-11-13 09:30' --until '2026-11-13 11:50' --no-pager 2>/dev/null | grep -ciE 'outofmemory|gc overhead|heap space|unable to create native thread'
