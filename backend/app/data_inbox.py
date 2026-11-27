"""文件队列、接入配置与接入日志的共享操作。"""
import hashlib
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from .config import default_now, resolve_data_inbox_dir
from .state import BizError
from .variable_notice import table_rows


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
EXTENSIONS = (".csv", ".xlsx")
SOURCE_KEY = re.compile(r"[A-Za-z0-9_-]{2,64}")
_publish_lock = Lock()


def source_key(value):
    if not isinstance(value, str) or not SOURCE_KEY.fullmatch(value):
        raise BizError(400, "INVALID_DATA_SOURCE_KEY", "数据源键须为 2–64 位英文字母、数字、下划线或连字符")
    return value


def normalize_settings(spec):
    """无源键时使用原有手工表格配置。"""
    key = spec.get("data_source_key")
    if key is None or key == "":
        return {}
    settings = {"data_source_key": source_key(key)}
    for field, default, label in (("max_file_age_hours", 24, "文件有效时长"), ("min_rows", 1, "最低行数")):
        value = spec.get(field, default)
        if type(value) is not int or not 1 <= value <= 2**53 - 1:
            raise BizError(400, "INVALID_DATA_INBOX_SETTING", f"{label}须为正整数")
        settings[field] = value
    return settings


def upload_token(key):
    mapping = os.environ.get("IMT_DATA_INBOX_TOKENS")
    if mapping is None:
        return os.environ.get("IMT_DATA_INBOX_TOKEN") or None
    try:
        tokens = json.loads(mapping)
    except ValueError:
        return None
    token = tokens.get(key) if isinstance(tokens, dict) else None
    return token if isinstance(token, str) and token else None


def checked_path(base, *parts):
    """拒绝越界、符号链接和目录联接，包括指向 inbox 内其他源的链接。"""
    path = base.joinpath(*parts)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise BizError(400, "INVALID_DATA_INBOX_PATH", "数据源路径无效或不可访问") from exc
    if resolved == base or not resolved.is_relative_to(base) or str(resolved) != str(path):
        raise BizError(400, "INVALID_DATA_INBOX_PATH", "数据源路径越界或使用了链接")
    return resolved


def source_path(state, key, *parts):
    try:
        root = resolve_data_inbox_dir(state.data_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BizError(400, "INVALID_DATA_INBOX_PATH", "接入根目录无效或不可访问") from exc
    return checked_path(root, source_key(key), *parts)


def read_file(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_UPLOAD_BYTES + 1)
    check_size(len(raw))
    return raw


def check_size(size):
    if size > MAX_UPLOAD_BYTES:
        raise BizError(413, "DATA_INBOX_FILE_TOO_LARGE", "文件大小不得超过 20 MB")


def preflight(raw, extension):
    with table_rows(raw, extension) as stream:
        _, header, _ = next(stream, (1, [], set()))
        columns = [value.strip() for value in header]
        rows = sum(any(value.strip() for value in values) for _, values, _ in stream)
    if not any(columns) or not rows:
        raise BizError(400, "EMPTY_DATA_INBOX_TABLE", "表格须包含表头和至少一行有效数据")
    return rows, columns


def publish_upload(state, key, filename, stream):
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS:
        raise BizError(400, "INVALID_DATA_INBOX_FILE_TYPE", "请选择 CSV 或 XLSX 文件")
    incoming = source_path(state, key, "incoming")
    incoming.mkdir(parents=True, exist_ok=True)
    incoming = source_path(state, key, "incoming")
    with NamedTemporaryFile(dir=incoming, prefix=".tmp_", mode="wb", delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            size = 0
            digest = hashlib.sha256()
            while chunk := stream.read(256 * 1024):
                size += len(chunk)
                check_size(size)
                digest.update(chunk)
                tmp.write(chunk)
            tmp.close()
            sha256 = digest.hexdigest()
            rows, columns = preflight(read_file(checked_path(incoming, temp_path.name)), extension)
            timestamp = default_now()
            with _publish_lock:
                while True:
                    name = f"{timestamp:%Y%m%d_%H%M%S}_{sha256[:8]}{extension}"
                    target = checked_path(incoming, name)
                    if not target.exists() or hashlib.sha256(read_file(target)).hexdigest() == sha256:
                        os.replace(temp_path, target)
                        break
                    timestamp += timedelta(seconds=1)
        finally:
            tmp.close()
            temp_path.unlink(missing_ok=True)
    return {"ok": True, "source_key": key, "file": name, "sha256": sha256, "rows": rows, "columns": columns}


def latest_record(state, template_id, key):
    row = state.conn.execute(
        "SELECT source_key, period_key, file_name, sha256, row_count, status, error_msg, created_at "
        "FROM data_inbox_log WHERE template_id=? AND source_key=? "
        "ORDER BY created_at DESC, planned_at DESC LIMIT 1", (template_id, key),
    ).fetchone()
    return dict(row) if row else None
