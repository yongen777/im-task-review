"""一次性通知的变量表模板、解析与名册校验。"""
import csv
import json
import re
from collections import Counter, defaultdict
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from openpyxl import Workbook, load_workbook
from openpyxl.styles import numbers
from openpyxl.utils.exceptions import InvalidFileException

from .state import BizError, IMPORT_ROW_LIMIT, clean_name_for_match, is_valid_phone


MAX_UPLOAD = 4 * 1024 * 1024
FIXED_COLUMNS = ("手机号", "姓名")
PLACEHOLDER = re.compile(r"\{([^{}\r\n]+)\}")
PLATFORM_SQL = "JSON_UNQUOTE(JSON_EXTRACT(IF(JSON_VALID(t.spec_json), t.spec_json, NULL), '$.variable_notice.platform'))"


class VariableTableError(BizError):
    def __init__(self, issues):
        super().__init__(400, "INVALID_VARIABLE_TABLE", "变量表校验失败，请修正表格后重新导入")
        self.issues = issues


def issue(row, reason, phone=""):
    return {"row": row, "phone": phone, "reason": reason}


def config_for(template):
    spec = template.get("spec_json")
    return spec.get("variable_notice") if isinstance(spec, dict) else None


def summarize_template(template):
    if template and (config := config_for(template)):
        summary = {"platform": config["platform"], "count": len(config["rows"])}
        return {**template, "spec_json": {**template["spec_json"], "variable_notice": summary}}
    return template


def normalize_body(body):
    if not isinstance(body, str):
        raise VariableTableError([issue(1, "请输入通知正文")])
    return body.replace("\\r\\n", "\n").replace("\\n", "\n").strip()


def columns_for(body):
    names = [match[1].strip() for match in PLACEHOLDER.finditer(normalize_body(body)) if match[1].strip()]
    if not names:
        raise VariableTableError([issue(1, "通知正文至少需要一个 {变量}，例如 {姓名}")])
    if any(name.startswith(("=", "+", "-", "@")) for name in names):
        raise VariableTableError([issue(1, "变量名不能以 =、+、- 或 @ 开头")])
    return list(dict.fromkeys((*FIXED_COLUMNS, *names)))


def make_template(body, file_type):
    columns = columns_for(body)
    if file_type == "csv":
        stream = StringIO(newline="")
        csv.writer(stream).writerow(columns)
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
    if file_type != "xlsx":
        raise BizError(400, "INVALID_TABLE_FORMAT", "请选择 XLSX 或 CSV 格式")
    book = Workbook()
    sheet = book.active
    sheet.title = "变量表格"
    sheet.column_dimensions["A"].number_format = numbers.FORMAT_TEXT
    sheet.column_dimensions["A"].width = 17
    for index, column in enumerate(columns, 1):
        cell = sheet.cell(1, index, column)
        cell.data_type = "s"
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _file_rows(raw, extension):
    if extension == ".csv":
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = raw.decode("gb18030")
        reader = csv.reader(StringIO(decoded, newline=""), strict=True)
        while True:
            row = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                return
            yield row, cells, set()
    else:
        with ZipFile(BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
                raise VariableTableError([issue(1, "XLSX 解压后超过大小上限，请拆分表格")])
        book = load_workbook(BytesIO(raw), read_only=True, data_only=True, keep_links=False)
        try:
            if len(book.worksheets) != 1:
                raise VariableTableError([issue(1, "请将本次收件人合并到一个工作表")])
            sheet = book.worksheets[0]
            sheet.reset_dimensions()
            for row, cells in enumerate(sheet.iter_rows(), 1):
                values = [str(cell.value).strip() if cell.value is not None else "" for cell in cells]
                invalid = {index for index, cell in enumerate(cells) if cell.data_type == "e"}
                yield row, values, invalid
        finally:
            book.close()


def read_table(raw, filename, body, platform):
    extension = Path(filename).suffix.lower()
    if extension not in (".csv", ".xlsx"):
        raise BizError(400, "INVALID_TABLE_FORMAT", "请选择 XLSX 或 CSV 文件")
    if len(raw) > MAX_UPLOAD:
        raise BizError(413, "VARIABLE_TABLE_TOO_LARGE", "变量表文件大小不得超过 4 MB")
    required = columns_for(body)
    rows = []
    try:
        stream = _file_rows(raw, extension)
        try:
            _, header, _ = next(stream, (1, [], set()))
            header = [cell.strip() for cell in header]
            duplicates = [name for name, count in Counter(header).items() if name and count > 1]
            missing = [name for name in required if name not in header]
            if missing or duplicates:
                raise VariableTableError(
                    [issue(1, f"缺少列「{name}」") for name in missing] +
                    [issue(1, f"重复列「{name}」") for name in duplicates]
                )
            indexes = {name: header.index(name) for name in required}
            for row, values, invalid in stream:
                if not any(str(value).strip() for value in values):
                    continue
                if len(rows) >= IMPORT_ROW_LIMIT:
                    raise VariableTableError([issue(row, f"单次最多导入 {IMPORT_ROW_LIMIT} 人")])
                cells = {name: values[index] if index < len(values) else "" for name, index in indexes.items()}
                row_issues = [f"「{name}」请填写普通值" for name, index in indexes.items() if index in invalid]
                if any(str(value).strip() for value in values[len(header):]):
                    row_issues.append("存在无对应表头的数据")
                rows.append({"row": row, "cells": cells, "file_issues": row_issues})
        finally:
            stream.close()
    except (BadZipFile, csv.Error, UnicodeError, InvalidFileException, ParseError,
            KeyError, IndexError, OSError, ValueError) as exc:
        raise VariableTableError([issue(1, "无法读取表格，请用 Excel 另存为 XLSX 或 CSV 后重新上传")]) from exc
    return {"platform": platform, "rows": rows}


def roster(state, owner_id, platform, phones, *, lock=False):
    """只查询本管理员在指定平台的名册与投递绑定。"""
    records = defaultdict(list)
    unique = list(dict.fromkeys(phones))
    for start in range(0, len(unique), 900):
        group = unique[start:start + 900]
        placeholders = ",".join("?" for _ in group)
        rows = state.conn.execute(
            "SELECT m.phone, m.name, m.dept, m.dept_path, m.enabled, m.is_left_org, "
            "dt.external_id, dt.external_type, "
            "EXISTS (SELECT 1 FROM delivery_target other WHERE other.platform=dt.platform "
            "AND other.external_id=dt.external_id AND other.usable=1 AND other.phone<>dt.phone) AS binding_conflict "
            "FROM member m JOIN role r ON r.role_id=m.role_id "
            "LEFT JOIN delivery_target dt ON dt.phone=m.phone AND dt.platform=r.platform AND dt.usable=1 "
            f"WHERE r.owner_id=? AND m.owner_id=? AND r.platform=? AND m.phone IN ({placeholders}) "
            "ORDER BY m.directory_checked_at DESC, m.is_left_org DESC, m.id DESC"
            + (" LOCK IN SHARE MODE" if lock else ""),
            (owner_id, owner_id, platform, *group),
        ).fetchall()
        for row in rows:
            records[row["phone"]].append(dict(row))
    return records


def eligible_member(records):
    if not records:
        return None, "未入册"
    if records[0]["is_left_org"]:
        return None, "已离职"
    active = next((row for row in records if row["enabled"] and not row["is_left_org"]), None)
    if not active:
        return None, "已停用"
    if not str(active["external_id"] or "").strip():
        return None, "未激活或不可投递"
    if active["binding_conflict"]:
        return None, "投递账号绑定了多个手机号，请核实名册"
    return active, ""


def validate(state, owner_id, body, config, *, lock=False):
    body = normalize_body(body)
    columns = columns_for(body)
    if not isinstance(config, dict):
        raise VariableTableError([issue(1, "请先导入变量表格")])
    platform = config.get("platform")
    if (not isinstance(platform, str) or platform not in state.ALL_PLATFORMS
            or platform not in state._owner_allowed_platforms(owner_id)
            or not state._template_owner_enabled(owner_id)):
        raise BizError(403, "PLATFORM_NOT_ALLOWED", "当前账号未被授权向该平台推送")
    rows = config.get("rows")
    if not isinstance(rows, list) or not rows:
        raise VariableTableError([issue(2, "表格没有有效数据行")])
    if len(rows) > IMPORT_ROW_LIMIT:
        raise VariableTableError([issue(1, f"单次最多导入 {IMPORT_ROW_LIMIT} 人")])

    def clean_cell(value):
        return value.strip() if isinstance(value, str) else ""

    parsed, issues, previous_row = [], [], 1
    for index, raw in enumerate(rows, 2):
        row = raw.get("row") if isinstance(raw, dict) else None
        raw_cells = raw.get("cells") if isinstance(raw, dict) else None
        if type(row) is not int or row <= previous_row or not isinstance(raw_cells, dict):
            raise VariableTableError([issue(index, "数据行格式无效，请重新导入变量表格")])
        previous_row = row
        cells = {name: clean_cell(raw_cells.get(name)) for name in columns}
        parsed.append((row, cells, raw.get("file_issues") or []))

    records = roster(state, owner_id, platform,
                     [cells["手机号"] for _, cells, _ in parsed if is_valid_phone(cells["手机号"])],
                     lock=lock)
    first_rows, members, normalized = {}, [], []
    for row, cells, file_issues in parsed:
        phone, name = cells["手机号"], clean_name_for_match(cells["姓名"])
        reasons = list(file_issues) if isinstance(file_issues, list) else []
        member = None
        if not phone:
            reasons.append("手机号不能为空")
        elif not is_valid_phone(phone):
            reasons.append("手机号格式非法")
        else:
            if phone in first_rows:
                reasons.append(f"手机号重复，首次出现在第 {first_rows[phone]} 行")
            first_rows.setdefault(phone, row)
            member, reason = eligible_member(records.get(phone))
            if reason:
                reasons.append(reason)
        if not name:
            reasons.append("姓名不能为空")
        elif member and name != clean_name_for_match(member["name"]):
            reasons.append("姓名与名册不一致")
        reasons.extend(f"变量「{key}」不能为空" for key in columns[2:] if not cells[key])
        if reasons:
            issues.extend(issue(row, str(reason), phone) for reason in reasons)
            continue
        normalized.append({"row": row, "cells": cells})
        rendered = PLACEHOLDER.sub(lambda match: cells.get(match[1].strip(), match[0]), body)
        members.append({**member, "body": rendered})
    if issues:
        raise VariableTableError(issues)
    result = {"platform": platform, "rows": normalized}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_UPLOAD:
        raise BizError(413, "VARIABLE_TABLE_TOO_LARGE", "变量数据超过 4 MB，请拆分表格")
    return result, members
