from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..notice_receipts import PAGE, confirm, media_path, read_target
from ..state import BizError


API = "/api/h5/notice-receipt"
HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff"}


def create_notice_receipt_router(state):
    router = APIRouter()

    @router.get(PAGE)
    def page():
        html = (Path(__file__).parents[1] / "h5" / "notice-receipt.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers=HEADERS)

    @router.post(API + "/{action}")
    def endpoint(action: str, request: Request):
        try:
            authorization = request.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            instance, template, view = read_target(state, token)
            if action == "confirm":
                return JSONResponse({"ok": True, "receipt_at": confirm(state, instance["id"])}, headers=HEADERS)
            if action != "info":
                raise BizError(404, "NOT_FOUND", "接口不存在")
            return JSONResponse({
                "title": view["title"], "body": view["body"], "published_at": view["published_at"],
                "delivered_at": instance["receipt_prompt_at"], "confirmed_at": instance["receipt_at"],
                "confirmation_open": bool(template["enabled"] and not instance["receipt_at"]),
                "button_label": instance["receipt_phrase"],
                "media": [{"id": index, "kind": item["kind"], "name": item["name"], "size": item["size"]}
                          for index, item in enumerate(view["media"])],
            }, headers=HEADERS)
        except BizError as exc:
            return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status_code,
                                headers=HEADERS)

    @router.get(API + "/media")
    def media(token: str, id: int, download: bool = False):
        try:
            _, _, view = read_target(state, token)
            if not 0 <= id < len(view["media"]):
                raise BizError(404, "NOT_FOUND", "附件不可用")
            item = view["media"][id]
            source = media_path(state, item["relative"])
            if not source:
                raise BizError(404, "NOT_FOUND", "附件不可用")
            inline = item["kind"] in ("image", "video") and not download
            return FileResponse(source, filename=item["name"], media_type=item["mime"],
                                content_disposition_type="inline" if inline else "attachment", headers=HEADERS)
        except BizError as exc:
            return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status_code,
                                headers=HEADERS)

    return router
