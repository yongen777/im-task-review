from __future__ import annotations

import io
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from unicodedata import east_asian_width


def _safe_text(value: Any) -> str:
    text = '' if value is None else re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F]', '', str(value))
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


def _field_value(value: Any, field_type: str | None) -> Any:
    if field_type == 'number' and value is not None and not isinstance(value, bool):
        try:
            number = float(value)
            if math.isfinite(number) and abs(number) < 10**15:
                return number
        except (ValueError, TypeError, OverflowError):
            pass
    return _safe_text(value)


def _status_label(status: str, need_review: bool) -> str:
    if status == 'submitted':
        return '已提交（待审核）' if need_review else '已完成'
    if status == 'approved':
        return '已完成（审核通过）' if need_review else '已完成'
    return {
        'pending': '待上传', 'rejected': '已驳回待重交', 'overdue': '已逾期未交',
        'expired': '已过期', 'cancelled': '已取消', 'sent': '已提醒',
    }.get(status, status)


def export_filename(dept_path: str, task_name: str, exported_at: datetime) -> str:
    dept_name = dept_path.strip().strip('/').rsplit('/', 1)[-1] or '全部覆盖部门'
    parts = [re.sub(r'[\\/:*?"<>|\x00-\x1F]', '_', name).strip(' .') for name in (dept_name[:70], task_name[:90])]
    stamp = exported_at.strftime('%Y／%m／%d %H：%M')
    return f'{parts[0]}_{parts[1]}_{stamp}.xlsx'


def _thumbnail(root: Path, rel_path: str):
    from PIL import Image, ImageOps
    from openpyxl.drawing.image import Image as ExcelImage

    try:
        path = (root / rel_path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            return None
        with Image.open(path) as original:
            original.draft('RGB', (960, 720))
            image = ImageOps.exif_transpose(original)
            image.thumbnail((960, 720))
            buf = io.BytesIO()
            image.convert('RGB').save(buf, format='JPEG', quality=82)
        buf.seek(0)
        thumb = ExcelImage(buf)
        scale = min(1, 136 / thumb.width, 92 / thumb.height)
        thumb.width = max(1, round(thumb.width * scale))
        thumb.height = max(1, round(thumb.height * scale))
        return thumb
    except (OSError, SyntaxError, ValueError, RuntimeError, Image.DecompressionBombError):
        return None


def _wrapped_lines(value: Any, width: int) -> int:
    return sum(max(1, math.ceil(sum(2 if east_asian_width(char) in ('W', 'F') else 1 for char in line) / (width - 2)))
               for line in str(value or '').splitlines())


def build_stats_xlsx(bundle: dict, uploads_dir: Path) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = bundle['rows']
    fields = bundle['content_fields']
    has_note = bundle['has_note_col']
    images_by_row = [[upload for upload in row['uploads'] if str(upload['mime']).startswith('image/')] for row in rows]
    has_file_col = 'file' in bundle['modules'] or any(len(row['uploads']) > len(images) for row, images in zip(rows, images_by_row))
    image_columns = max([int('image' in bundle['modules']), *(len(images) for images in images_by_row)])
    headers = ['序号', '姓名', '手机号', '所属部门（末级）', '完整部门路径', '完成状态', '提交时间']
    widths = [6, 16, 15, 20, 44, 24, 19]
    headers.extend(str(field.get('label') or field['key']) for field in fields)
    widths.extend([18] * len(fields))
    if has_note:
        headers.append('补充说明')
        widths.append(40)
    if image_columns:
        headers.append('图片数量')
        headers.extend('上报图片' if i == 1 else f'上报图片 {i}' for i in range(1, image_columns + 1))
        widths.extend([10] + [22] * image_columns)
    if has_file_col:
        headers.append('文件是否上传')
        widths.append(16)

    wb = Workbook()
    ws = wb.active
    ws.title = '统计数据'
    ws.freeze_panes = 'A2'
    ws.append([_safe_text(header) for header in headers])
    for cell, width in zip(ws[1], widths):
        cell.font = Font(bold=True, color='183153')
        cell.fill = PatternFill('solid', fgColor='E8F0FE')
        ws.column_dimensions[cell.column_letter].width = width
    ws.row_dimensions[1].height = 23

    root = uploads_dir.resolve()
    need_review = bool(bundle['template'].get('need_review'))
    wrap = Alignment(vertical='center', wrap_text=True)
    for index, (row, images) in enumerate(zip(rows, images_by_row), start=1):
        path = str(row.get('dept_path') or '')
        values = [index, _safe_text(row.get('name')), _safe_text(row.get('phone')), _safe_text(path.rsplit('/', 1)[-1]),
                  _safe_text(path), _status_label(row['status'], need_review), str(row.get('submitted_at') or '')[:16]]
        values.extend(_field_value(row.get('fields', {}).get(str(field['key'])), field.get('type')) for field in fields)
        if has_note:
            values.append(_safe_text(row.get('note')))
        first_image_column = len(values) + 2
        if image_columns:
            values.extend([len(images)] + [None] * image_columns)
        if has_file_col:
            values.append('已上传' if len(row['uploads']) > len(images) else '未上传')
        ws.append(values)
        excel_row = index + 1
        for cell in ws[excel_row]:
            cell.alignment = wrap
        for offset, upload in enumerate(images):
            cell = ws.cell(row=excel_row, column=first_image_column + offset)
            image = _thumbnail(root, str(upload.get('rel_path') or ''))
            if image is None:
                cell.value = '图片无法读取'
            else:
                ws.add_image(image, cell.coordinate)
        line_count = max(_wrapped_lines(value, width) for value, width in zip(values, widths))
        if images or line_count > 1:
            ws.row_dimensions[excel_row].height = min(409, max(76 if images else 17, 14 * line_count + 5))
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{len(rows) + 1}'
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
