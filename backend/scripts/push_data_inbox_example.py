"""从 Windows 或其他机器把 CSV/XLSX 投递到 IMTask。

PowerShell 示例（令牌须与服务端配置一致）：
    $env:IMT_DATA_INBOX_TOKEN = '<上传令牌>'
    python scripts/push_data_inbox_example.py http://imtask.example:9020 scores_01 "C:\\Exports\\scores.xlsx"

服务端使用 IMT_DATA_INBOX_TOKEN，或通过 IMT_DATA_INBOX_TOKENS 提供按源键配置的 JSON。
设置映射时，仅接受该数据源对应的令牌。同机可先写入 incoming 下的 .tmp 文件，
再重命名为 .csv 或 .xlsx。默认工作区为 data/data_inbox，可用 IMT_DATA_INBOX_DIR 覆盖。
"""
import argparse
import http.client
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", help="IMTask 业务端口地址，例如 http://imtask.example:9020")
    parser.add_argument("source_key", help="模板绑定的数据源键")
    parser.add_argument("file", type=Path, help="CSV 或 XLSX 文件路径")
    args = parser.parse_args()
    base = urlsplit(args.base_url)
    if base.scheme not in ("http", "https") or not base.hostname or base.username or base.query or base.fragment:
        parser.error("base_url 必须是有效的 HTTP(S) 服务地址")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", args.source_key):
        parser.error("source_key 须为 2–64 位英文字母、数字、下划线或连字符")
    extension = args.file.suffix.lower()
    if extension not in (".csv", ".xlsx"):
        parser.error("只支持 CSV 和 XLSX")
    token = os.environ.get("IMT_DATA_INBOX_TOKEN")
    if not token:
        parser.error("请设置 IMT_DATA_INBOX_TOKEN")

    boundary = uuid4().hex
    start = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"data{extension}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    end = f"\r\n--{boundary}--\r\n".encode()
    connection_type = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
    limit = 20 * 1024 * 1024
    with args.file.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        parser.error("文件大小不得超过 20 MB")
    connection = connection_type(base.hostname, base.port, timeout=60)
    try:
        path = f"{base.path.rstrip('/')}/api/data-inbox/{args.source_key}/upload"
        connection.request("POST", path, body=(start, raw, end), headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(start) + len(raw) + len(end)),
            "X-Data-Token": token,
        })
        result = connection.getresponse()
        print(result.read().decode("utf-8", errors="replace"))
        return 0 if 200 <= result.status < 300 else 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
