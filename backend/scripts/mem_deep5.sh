#!/bin/bash
echo '════ ry-admin 四进程的完整命令行与端口 ════'
ps -eo pid,rss,args | grep 'ry-admin.jar' | grep -v grep | while read pid rss rest; do
  port=$(echo "$rest" | grep -oE '\-\-server\.port=[0-9]+' | head -1)
  jar=$(echo "$rest" | grep -oE '/www/wwwroot/[^ ]*ry-admin\.jar')
  printf 'PID=%s RSS=%.0fMB %s %s\n' "$pid" "$$(echo "$rss/1024" | bc -l)" "$port" "$jar" 2>/dev/null || printf 'PID=%s RSS=%sKB %s %s\n' "$pid" "$rss" "$port" "$jar"
done
echo '════ 全机 java -jar 进程总数与 RSS 合计 ════'
ps -eo rss,args | grep 'java -jar' | grep -v grep | awk '{s+=$1} END {printf "java进程数=%d 总RSS=%.1fGB\n", NR, s/1048576}'
echo '════ 各 java 的服务名与内存（前 16） ════'
ps -eo rss,args | grep 'java' | grep -v grep | grep -oE '[0-9]+ [^ ]*(ry-admin|\.jar|-Dserver)' | head -20
systemctl list-units --type=service --state=running | grep -cE 'spring_'
