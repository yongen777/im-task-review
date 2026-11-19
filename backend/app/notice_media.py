from __future__ import annotations

import codecs
import re
import secrets
import unicodedata
import zipfile
from pathlib import Path


MIB = 1024 * 1024
LIMITS = {"image": 9 * MIB, "file": 64 * MIB, "video": 100 * MIB}


def receipt_key(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def notice_receipt_config(template: dict) -> tuple[str | None, str | None]:
    if template.get("kind") != "notice":
        return None, None
    spec = template.get("spec_json") or {}
    config = spec.get("notice_receipt") or {} if isinstance(spec, dict) else {}
    if not isinstance(config, dict) or not config.get("enabled"):
        return None, None
    if config.get("mode") == "link":
        return "link", config.get("button_label") or "我已知晓"
    return "text", config.get("phrase") or "我已知晓"


def _has_movie_boxes(source, size: int) -> bool:
    required = {b"moov", b"mdat"}
    offset = 0
    try:
        while offset + 8 <= size and required:
            source.seek(offset)
            header = source.read(8)
            length = int.from_bytes(header[:4], "big")
            if length == 1:
                length = int.from_bytes(source.read(8), "big")
                if length < 16:
                    return False
            if length == 0:
                length = size - offset
            if not 8 <= length <= size - offset:
                return False
            if length > 8:
                required.discard(header[4:8])
            offset += length
        return not required
    finally:
        source.seek(0)


def _mime_for(kind: str, extension: str, source, size: int) -> tuple[str, str] | None:
    from .api.h5 import sniff_magic_mime

    header = source.read(64)
    source.seek(0)
    if kind == "image":
        mime = sniff_magic_mime(extension, header) if extension in ("png", "jpg", "jpeg", "webp") else None
        return (mime.rsplit("/", 1)[1].replace("jpeg", "jpg"), mime) if mime and mime.startswith("image/") else None
    if kind == "video":
        if extension not in ("mp4", "mov", "m4v") or header[4:8] != b"ftyp" or len(header) < 16:
            return None
        box_size = int.from_bytes(header[:4], "big")
        if not 16 <= box_size <= size or header[8:12] not in (
            b"qt  ", b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1",
            b"M4V ", b"MSNV", b"dash", b"hvc1", b"hev1",
        ):
            return None
        if not _has_movie_boxes(source, size):
            return None
        return extension, "video/quicktime" if extension == "mov" else "video/mp4"
    if extension == "txt":
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        try:
            for block in iter(lambda: source.read(256 * 1024), b""):
                if any(ord(c) < 32 and c not in "\t\r\n" for c in decoder.decode(block)):
                    return None
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            return None
        finally:
            source.seek(0)
        return extension, "text/plain; charset=utf-8"
    if extension not in ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip"):
        return None
    mime = sniff_magic_mime(extension, header)
    if not mime:
        return None
    if extension in ("zip", "docx", "xlsx", "pptx"):
        try:
            with zipfile.ZipFile(source) as archive:
                parts = archive.namelist()
                prefix = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}.get(extension)
                if prefix and ("[Content_Types].xml" not in parts or not any(p.startswith(prefix) for p in parts)):
                    return None
        except (ValueError, zipfile.BadZipFile):
            return None
        finally:
            source.seek(0)
    return extension, mime


def asset_path(store, asset: dict) -> Path | None:
    root = (store.data_dir / "notice_assets").resolve()
    name = str(asset.get("storage_name") or "")
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", name):
        return None
    target = (root / name).resolve()
    return target if target.is_relative_to(root) and target.is_file() else None


def get_asset(store, asset_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", asset_id):
        return None
    row = store.conn.execute("SELECT * FROM notice_asset WHERE asset_id=?", (asset_id,)).fetchone()
    return dict(row) if row else None


def store_asset(store, owner: int, kind: str, filename: str, source, size: int) -> dict:
    from .state import BizError

    if kind not in LIMITS:
        raise BizError(400, "INVALID_ASSET_KIND", "请选择图片、文件或视频")
    if not 0 < size <= LIMITS[kind]:
        raise BizError(400, "FILE_SIZE_REJECTED", f"附件必须非空且不超过 {LIMITS[kind] // MIB} MB")
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    suffix = Path(filename).suffix.lower().lstrip(".")
    content = _mime_for(kind, suffix, source, size)
    if not content:
        raise BizError(400, "FILE_TYPE_REJECTED", "附件格式或实际内容与所选类型不匹配")
    extension, mime = content
    if len(filename) > 240:
        filename = f"{Path(filename).stem[:220]}.{suffix}"
    asset_id = secrets.token_hex(16)
    stored = f"{asset_id}.{extension}"
    root = store.data_dir / "notice_assets"
    root.mkdir(parents=True, exist_ok=True)
    target = root / stored
    output = target.open("xb")
    try:
        with output:
            written = 0
            for block in iter(lambda: source.read(1024 * 1024), b""):
                written += len(block)
                if written > size:
                    raise BizError(400, "FILE_SIZE_REJECTED", "附件大小发生变化，请重新上传")
                output.write(block)
        if written != size:
            raise BizError(400, "FILE_SIZE_REJECTED", "附件大小发生变化，请重新上传")
        store.conn.execute(
            "INSERT INTO notice_asset(asset_id,owner_id,kind,storage_name,file_name,mime,size,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (asset_id, owner, kind, stored, filename, mime, size, store.now_str()),
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"asset_id": asset_id, "kind": kind, "file_name": filename, "file_mime": mime, "file_size": size}
