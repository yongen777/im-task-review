#!/bin/bash
echo '════ 1. ry-admin 死机前日志内容样本（10:50-11:15 随机 8 行） ════'
journalctl -u spring_78a4189c187e6456732dd7303825f9a5 --since '2026-11-13 10:50' --until '2026-11-13 11:15' --no-pager | sed -n '2000p;4000p;6000p;8000p' | cut -c1-190
echo '════ 2. ry-admin 日志内容 TOP 关键词（10:50-11:15） ════'
journalctl -u spring_78a4189c187e6456732dd7303825f9a5 --since '2026-11-13 10:50' --until '2026-11-13 11:15' --no-pager | sed 's/^[^ ]* [^ ]* [^ ]* //' | awk '{$1=$2=$3=""; print substr($0,4,70)}' | sort | uniq -c | sort -rn | head -8
echo '════ 3. ry-admin 当前 RSS ════'
ps aux | grep 'ry-admin.jar' | grep -v grep | awk '{printf "RSS=%.0fMB PID=%s\n", $6/1024, $2}'
echo '════ 4. docker 容器内存排名 TOP8（stats 一次采样） ════'
timeout 12 docker stats --no-stream --format '{{.Name}}  {{.MemUsage}}' 2>/dev/null | sort -t'/' -k1 -rh | head -8
echo '════ 5. junlun 项目目录最近修改时间（谁在动它） ════'
ls -lt /www/wwwroot/junlun_inspection/ 2>/dev/null | head -5
