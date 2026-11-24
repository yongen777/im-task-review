"""通知确认链接的快照、令牌与回执登记。"""
import json
import re
from pathlib import Path

from .state import BizError


PURPOSE = "notice_receipt"
PAGE = "/h5/notice-receipt"
MEDIA_ROOTS = {"notice_assets", "notice_attach", "dashboards"}
EXPIRED_MESSAGE = "链接已过期，请返回聊天框给德阳量密助手发送任意消息进入个人工作台继续查看确认。"


def load_view(state, instance_id, *, for_update=False):
    row = state.conn.execute("SELECT content_json FROM notice_receipt_view WHERE instance_id=?" +
                             (" FOR UPDATE" if for_update else ""),
                             (instance_id,)).fetchone()
    return json.loads(row["content_json"]) if row else None


def save_view(state, instance_id, content):
    state.conn.execute(
        "INSERT OR IGNORE INTO notice_receipt_view(instance_id,content_json) "
        "SELECT id,? FROM task_instance WHERE id=? AND receipt_mode='link'",
        (json.dumps(content, ensure_ascii=False), instance_id),
    )
    return load_view(state, instance_id, for_update=True)


def issue_link(state, instance):
    token = state.issue_h5_token(instance["phone"], instance["template_id"], instance["period_key"],
                                 instance["id"], purpose=PURPOSE)
    return f"{PAGE}#t={token}"


def read_target(state, raw):
    token = state.get_h5_token(raw) if re.fullmatch(r"[A-Za-z0-9_-]{32}", raw or "") else None
    if not token or token["purpose"] != PURPOSE or token["consumed"]:
        raise BizError(401, "INVALID_NOTICE_TOKEN", "通知确认链接无效")
    if token["expires_at"] <= state.now_str():
        raise BizError(401, "NOTICE_TOKEN_EXPIRED", EXPIRED_MESSAGE)
    instance = state.get_instance(token["instance_id"]) if token["instance_id"] else None
    if (not instance or instance["receipt_mode"] != "link" or not instance["receipt_prompt_at"]
            or any(instance[key] != token[key] for key in ("phone", "template_id", "period_key"))):
        raise BizError(404, "NOTICE_UNAVAILABLE", "通知已关闭或尚未送达")
    # 2026-11-19 需求三：重推新批次会把旧批次未回执的实例置 cancelled，
    # 旧链接必须直接失效（否则用户还能打开并确认一批已经作废的通知）。
    if str(instance.get("status") or "") == "cancelled":
        raise BizError(404, "NOTICE_UNAVAILABLE", "通知已关闭或尚未送达")
    template = state.get_template(instance["template_id"])
    view = load_view(state, instance["id"]) if template and template["kind"] == "notice" else None
    if view is None:
        raise BizError(404, "NOTICE_UNAVAILABLE", "通知已关闭或尚未送达")
    return instance, template, view


def confirm(state, instance_id):
    with state.conn:
        now_s = state.now_str()
        changed = state.conn.execute(
            "UPDATE task_instance SET receipt_at=?,receipt_text=receipt_phrase "
            "WHERE id=? AND receipt_mode='link' AND receipt_at IS NULL AND receipt_prompt_at IS NOT NULL "
            "AND status IN ('pending','sent') "
            # 2026-11-23：授权成员（pt_）发布的通知同样可 H5 链接确认。
            "AND (template_id IN (SELECT template_id FROM task_template WHERE kind='notice' AND enabled=1 AND deleted=0) "
            "     OR (LEFT(template_id, 3)='pt_' AND EXISTS "
            "         (SELECT 1 FROM portal_task pt WHERE pt.task_id=template_id)))",
            (now_s, instance_id),
        )
        if changed.rowcount:
            # 2026-11-20 切期改造二-3：回执完成态同步到同归属周期其余未回执实例
            _src = state.conn.execute(
                "SELECT template_id, phone, period_key FROM task_instance WHERE id=?", (instance_id,)
            ).fetchone()
            if _src is not None:
                state.sync_family_completion(
                    _src["template_id"], _src["phone"], _src["period_key"],
                    receipt_at=now_s, receipt_text=None, skip_instance_id=int(instance_id),
                )
        # 上面 WHERE 里加了 status 过滤 —— 已取消（cancelled）的实例写不进回执，
        # 这里按「实例是否已作废」明确回 404（其余场景保持原有 403 文案）。
        row = state.conn.execute("SELECT receipt_at, status FROM task_instance WHERE id=? FOR UPDATE",
                                 (instance_id,)).fetchone()
        if not row:
            raise BizError(403, "NOTICE_CLOSED", "通知已停用，暂时无法确认")
        if str(row["status"] or "") == "cancelled" and not row["receipt_at"]:
            raise BizError(404, "NOTICE_UNAVAILABLE", "通知已关闭或尚未送达")
        if not row["receipt_at"]:
            raise BizError(403, "NOTICE_CLOSED", "通知已停用，暂时无法确认")
        return row["receipt_at"]


def media_path(state, relative):
    if not relative:
        return None
    key = Path(relative)
    if not key.parts or key.parts[0] not in MEDIA_ROOTS:
        return None
    root = (state.data_dir / key.parts[0]).resolve()
    path = (state.data_dir / key).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None
