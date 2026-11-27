"""定时接入、专属通知批次与可重试归档。"""
import hashlib
import logging
import os
from datetime import timedelta

from .config import format_dt
from .data_inbox import EXTENSIONS, MAX_UPLOAD_BYTES, checked_path, read_file, source_path
from .scheduler import batch_period_key, period_key
from .state import BizError, clean_name_for_match
from .variable_notice import VariableTableError, config_for, eligible_member, read_table, roster, validate, validate_platform

logger = logging.getLogger(__name__)
QUIET_SECONDS = 3


def _get_job(state, template_id, planned_at, *, lock=False):
    return state.conn.execute(
        "SELECT * FROM data_inbox_log WHERE template_id=? AND planned_at=?" + (" FOR UPDATE" if lock else ""),
        (template_id, planned_at),
    ).fetchone()


def _work_dir(state, job):
    key = hashlib.sha256(f"{job['template_id']}|{job['planned_at']}".encode()).hexdigest()[:24]
    return source_path(state, job["source_key"], "incoming", f".tmp_work_{key}")


def _candidate(path):
    if path.name.lower().startswith((".tmp", "~$")) or path.suffix.lower() not in EXTENSIONS:
        return None
    try:
        path = checked_path(path.parent, path.name)
        if path.is_file():
            return path.stat().st_mtime_ns, path.name, path
    except (BizError, FileNotFoundError):
        pass
    return None


def _claimed_file(work):
    if not work.exists():
        return None
    return next((item[2] for path in work.iterdir() if (item := _candidate(path))), None)


def _claim_file(state, job, now):
    work = _work_dir(state, job)
    claimed = _claimed_file(work)
    if claimed is not None:
        return claimed, False
    incoming = work.parent
    if not incoming.exists():
        return None, False
    newest = max((item for path in incoming.iterdir() if (item := _candidate(path))), default=None)
    if newest is None:
        return None, False
    mtime, name, path = newest
    if now.timestamp() - mtime / 1e9 < QUIET_SECONDS:
        return None, True
    work.mkdir(exist_ok=True)
    try:
        return path.rename(checked_path(work, name)), False
    except (FileNotFoundError, PermissionError):
        work.rmdir()
        return None, True


def _set_result(state, job, now_s, status, *, error=None, file_name=None, sha256=None, rows=None, batch=None):
    file_name = file_name or job["file_name"]
    state.conn.execute(
        "UPDATE data_inbox_log SET file_name=?,sha256=?,row_count=?,status=?,error_msg=?,created_at=?,"
        "batch_key=?,archive_pending=? WHERE template_id=? AND planned_at=?",
        (file_name, sha256 or job["sha256"], rows if rows is not None else job["row_count"], status,
         error[:512] if error else None, now_s, batch or job["batch_key"],
         int(bool(file_name) and status != "processing"), job["template_id"], job["planned_at"]),
    )


def _archive(state, template_id, planned_at):
    if not (job := _get_job(state, template_id, planned_at)) or not job["archive_pending"]:
        return
    with state.conn:
        job = _get_job(state, template_id, planned_at, lock=True)
        if not job or not job["archive_pending"]:
            return
        work = _work_dir(state, job)
        path = checked_path(work, job["file_name"])
        if path.is_file():
            folder = source_path(state, job["source_key"], "failed" if job["status"] == "failed" else "processed",
                                 job["created_at"][:10])
            folder.mkdir(parents=True, exist_ok=True)
            suffix = work.name.removeprefix(".tmp_work_")
            for name in (path.name, f"{path.stem[:50]}_{suffix}{path.suffix}"):
                target = checked_path(folder, name)
                try:
                    os.link(path, target)
                except FileExistsError:
                    if not path.samefile(target):
                        continue
                path.unlink()
                break
            else:
                raise FileExistsError("接入归档文件已存在")
        if work.exists():
            work.rmdir()
        state.conn.execute(
            "UPDATE data_inbox_log SET archive_pending=0 WHERE template_id=? AND planned_at=?",
            (template_id, planned_at),
        )


def _archive_safely(state, template_id, planned_at):
    try:
        _archive(state, template_id, planned_at)
    except (BizError, OSError):
        logger.exception("数据接入文件归档失败，将重试 template=%s planned_at=%s", template_id, planned_at)


def recover(scheduler, now):
    """补偿窗口结束后收口未完成批次，并重试未完成的归档。"""
    state = scheduler.state
    now_s = format_dt(now)
    cutoff = format_dt(now - timedelta(seconds=scheduler.COMPENSATION_WINDOW_SECONDS))
    expired = state.conn.execute(
        "SELECT template_id, planned_at FROM data_inbox_log "
        "WHERE status IN ('waiting','processing') AND archive_pending=0 AND planned_at < ?", (cutoff,),
    ).fetchall()
    for row in expired:
        with state.conn:
            job = _get_job(state, row["template_id"], row["planned_at"], lock=True)
            if job["status"] not in ("waiting", "processing"):
                continue
            name = job["file_name"]
            if not name:
                try:
                    path = _claimed_file(_work_dir(state, job))
                    name = path.name if path else None
                except (BizError, OSError):
                    logger.exception("数据接入工作目录读取失败 template=%s", job["template_id"])
            error = "部分或全部收件人未在补偿窗口内完成投递" if job["batch_key"] else "补偿窗口内无稳定可读文件"
            _set_result(state, job, now_s, "failed", error=error, file_name=name)
    for job in state.conn.execute(
        "SELECT template_id, planned_at FROM data_inbox_log "
        "WHERE status IN ('success','failed','skipped') AND archive_pending=1"
    ).fetchall():
        _archive_safely(state, job["template_id"], job["planned_at"])


def _existing_hash_status(state, job, sha256):
    prior = state.conn.execute(
        "SELECT j.status FROM data_inbox_log j WHERE j.template_id=? AND j.period_key=? AND j.sha256=? "
        "AND (j.status IN ('success','processing') OR EXISTS (SELECT 1 FROM reminder_log r "
        "JOIN task_instance i ON i.id=r.instance_id WHERE i.template_id=j.template_id "
        "AND i.period_key=j.batch_key AND r.result IN ('pending','sent'))) "
        "ORDER BY (j.status='success') DESC LIMIT 1",
        (job["template_id"], job["period_key"], sha256),
    ).fetchone()
    return prior["status"] if prior else None


def _reserve(state, tpl, planned_at, period, now_s):
    template_id = tpl["template_id"]
    if job := _get_job(state, template_id, planned_at):
        return job
    with state.conn:
        template = state.conn.execute("SELECT template_id FROM task_template WHERE template_id=? FOR UPDATE",
                                      (template_id,)).fetchone()
        if template and not (job := _get_job(state, template_id, planned_at, lock=True)):
            state.conn.execute(
                "INSERT INTO data_inbox_log(source_key,template_id,period_key,planned_at,status,created_at) "
                "VALUES (?, ?, ?, ?, 'waiting', ?)",
                (tpl["spec_json"]["data_source_key"], template_id, period, planned_at, now_s),
            )
            job = _get_job(state, template_id, planned_at, lock=True)
        return job


def _prepare(scheduler, template_id, planned, now, stats):
    state, planned_at, now_s = scheduler.state, format_dt(planned), format_dt(now)
    with state.conn:
        row = state.conn.execute("SELECT * FROM task_template WHERE template_id=? FOR UPDATE", (template_id,)).fetchone()
        job = _get_job(state, template_id, planned_at, lock=True)
        if job["status"] != "waiting":
            return
        tpl = state._deserialize_template_row(row) if row else {}
        spec = tpl.get("spec_json") or {}
        path = sha256 = rows = None
        try:
            if (not tpl.get("enabled") or tpl.get("deleted") or not isinstance(spec, dict)
                    or spec.get("data_source_key") != job["source_key"]
                    or planned.strftime("%H:%M") not in (tpl.get("notice_times") or [])):
                raise BizError(400, "DATA_INBOX_TEMPLATE_CHANGED", "模板已停用或数据源配置已变更")
            platform = config_for(tpl)["platform"]
            validate_platform(state, tpl["owner_id"], platform)
            if platform not in (scheduler.notifiers or {state.platform: scheduler.notifier}):
                raise BizError(503, "DATA_INBOX_PLATFORM_UNAVAILABLE", "当前平台的投递通道未就绪")
            path, changing = _claim_file(state, job, now)
            if changing:
                return
            if path is None:
                raise BizError(400, "DATA_INBOX_MISSING", "待消费目录没有可用文件")
            before = path.stat()
            age = now.timestamp() - before.st_mtime
            if age < QUIET_SECONDS:
                return
            raw = read_file(path)
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):
                return
            sha256 = hashlib.sha256(raw).hexdigest()
            if age > spec["max_file_age_hours"] * 3600:
                raise BizError(400, "DATA_INBOX_STALE", f"文件超过 {spec['max_file_age_hours']} 小时未更新")
            if prior := _existing_hash_status(state, job, sha256):
                _set_result(state, job, now_s, "skipped" if prior == "success" else "failed",
                            error="本周期相同文件已成功投递" if prior == "success" else "本周期相同文件已有投递记录",
                            file_name=path.name, sha256=sha256)
                return
            config = read_table(raw, path.name, tpl["text_tpl"], platform, max_upload=MAX_UPLOAD_BYTES)
            rows = len(config["rows"])
            if rows < spec["min_rows"]:
                raise BizError(400, "DATA_INBOX_MIN_ROWS", f"有效数据 {rows} 行，低于最低 {spec['min_rows']} 行")
            _, members = validate(state, tpl["owner_id"], tpl["text_tpl"], config, lock=True, max_config_bytes=None)
            batch = batch_period_key(job["period_key"], planned)
            deadline = format_dt(planned + timedelta(hours=24))
            instances = scheduler._save_variable_notice_batch(tpl, batch, now_s, deadline, platform, members)
        except (BizError, OSError) as exc:
            if isinstance(exc, VariableTableError):
                error = "；".join(f"第 {issue['row']} 行：{issue['reason']}" for issue in exc.issues[:5])
            elif isinstance(exc, BizError):
                error = exc.message
            else:
                error = "无法读取或认领数据源文件"
                logger.exception("%s template=%s", error, template_id)
            _set_result(state, job, now_s, "failed", error=error, file_name=path.name if path else None,
                        sha256=sha256, rows=rows)
            stats["failed"] += 1
            return
        _set_result(state, job, now_s, "processing", file_name=path.name, sha256=sha256, rows=rows, batch=batch)
        stats["generated_instances"] += len(instances)


def _deliver(scheduler, tpl, job, now, stats):
    state, current = scheduler.state, scheduler.state.get_template(tpl["template_id"], include_deleted=False)
    if not current or not current["enabled"] or (current["spec_json"] or {}).get("data_source_key") != job["source_key"]:
        return
    instances = scheduler._load_variable_notice_batch(job["template_id"], job["batch_key"])
    if not instances or not state._template_owner_enabled(tpl["owner_id"]):
        return
    platform = instances[0]["notice_view"]["variable_platform"]
    if platform not in state._owner_allowed_platforms(tpl["owner_id"]):
        return
    records = roster(state, tpl["owner_id"], platform, [inst["phone"] for inst in instances])
    recipients = []
    for inst in instances:
        member, _ = eligible_member(records.get(inst["phone"]))
        if (member and inst["status"] in ("pending", "sent")
                and clean_name_for_match(member["name"]) == clean_name_for_match(inst["name_snap"])):
            recipients.append(inst)
    completed = set()
    scheduler._deliver_notice(current, recipients, job["batch_key"], 1, job["planned_at"], now, {}, stats,
                              completed=completed)
    if len(completed) == job["row_count"]:
        with state.conn:
            job = _get_job(state, job["template_id"], job["planned_at"], lock=True)
            if job["status"] == "processing":
                _set_result(state, job, format_dt(now), "success")


def dispatch(scheduler, tpl, now, stats):
    state, template_id = scheduler.state, tpl["template_id"]
    period = period_key(tpl["period"], now)
    for at in sorted(tpl.get("notice_times") or []):
        planned = scheduler._build_planned_dt(now, at)
        if not 0 <= (now - planned).total_seconds() <= scheduler.COMPENSATION_WINDOW_SECONDS:
            continue
        planned_at = format_dt(planned)
        if not (job := _reserve(state, tpl, planned_at, period, format_dt(now))):
            continue
        if job["status"] == "waiting":
            _prepare(scheduler, template_id, planned, now, stats)
            job = _get_job(state, template_id, planned_at)
        if job["status"] == "processing":
            _deliver(scheduler, tpl, job, now, stats)
        if job["status"] in ("waiting", "processing") or job["archive_pending"]:
            _archive_safely(state, template_id, planned_at)
