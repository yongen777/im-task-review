"""数据源的公共令牌上传入口。"""
import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from ..data_inbox import MAX_UPLOAD_BYTES, authorize_upload, check_size, publish_upload, source_key as validate_key
from ..state import BizError
from ..variable_notice import VariableTableError

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024


class UploadRequestTooLarge(MultiPartException):
    pass


def create_data_inbox_router(state):
    router = APIRouter()

    @router.post("/api/data-inbox/{source_key:path}/upload")
    async def upload(source_key: str, request: Request):
        def error(status, code, message):
            return JSONResponse({"ok": False, "code": code, "message": message}, status_code=status)

        async def bounded_stream():
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_REQUEST_BYTES:
                    raise UploadRequestTooLarge("upload exceeds request limit")
                yield chunk

        try:
            key = validate_key(source_key)
            scheme, _, bearer = request.headers.get("Authorization", "").partition(" ")
            token = request.headers.get("X-Data-Token") or (bearer if scheme.lower() == "bearer" else "")
            await run_in_threadpool(authorize_upload, state, key, token)
            content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "multipart/form-data":
                raise BizError(400, "INVALID_DATA_INBOX_UPLOAD", "请使用 multipart/form-data 上传 file 字段")
            length = request.headers.get("Content-Length", "")
            if length.isascii() and length.isdecimal() and (len(length) > 16 or int(length) > MAX_REQUEST_BYTES):
                raise UploadRequestTooLarge("upload exceeds request limit")
            form = await MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=0).parse()
            try:
                file = form.get("file")
                if not isinstance(file, UploadFile) or not file.filename:
                    raise BizError(400, "INVALID_DATA_INBOX_UPLOAD", "请上传 file 字段")
                check_size(file.size or 0)
                return await run_in_threadpool(publish_upload, state, key, file.filename, file.file)
            finally:
                await form.close()
        except UploadRequestTooLarge:
            return error(413, "DATA_INBOX_FILE_TOO_LARGE", "文件大小不得超过 20 MB")
        except MultiPartException:
            return error(400, "INVALID_DATA_INBOX_UPLOAD", "无法读取上传的文件，请检查 multipart/form-data 请求")
        except VariableTableError as exc:
            return error(400, exc.code, exc.issues[0]["reason"])
        except BizError as exc:
            return error(exc.status_code, exc.code, exc.message)
        except OSError:
            logger.exception("数据接入文件写入失败 source_key=%s", source_key)
            return error(503, "DATA_INBOX_STORAGE_UNAVAILABLE", "数据接入目录暂不可写")

    return router
