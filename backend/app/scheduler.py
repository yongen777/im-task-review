# -*- coding: utf-8 -*-
"""周期与时间纯函数工具 + 实例惰性生成与调度循环（严格对齐规格书 §1、§5、§6、§8.1、§11、R10~R27、R47~R49）。"""
from __future__ import annotations

import calendar
import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union

from .channels.zdxlz import SendResult
from .config import SHANGHAI_TZ, NowFn, default_now, format_dt, parse_dt
from .state import StateStore, calc_resubmit_deadline, is_valid_hhmm

logger = logging.getLogger(__name__)

DateLike = Union[datetime, date, str]
PERIOD_DISPLAY_MAP = {"day": "今日", "week": "本周", "month": "本月"}


def _to_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return (value.astimezone(SHANGHAI_TZ) if value.tzinfo is not None else value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        s = value.strip()
        return datetime.strptime(s, "%Y-%m-%d").date() if len(s) == 10 else parse_dt(s).date()
    raise TypeError(f"不支持的日期类型: {type(value)!r}")


def _parse_hhmm(hhmm: str) -> Tuple[int, int]:
    s = (hhmm or "").strip()
    if not is_valid_hhmm(s):
        raise ValueError(f"非法的 HH:MM 时间格式: {hhmm!r}")
    h_str, m_str = s.split(":", 1)
    return int(h_str), int(m_str)


def period_key(period_or_dt: Union[str, DateLike], dt_or_period: Optional[Union[DateLike, str]] = None) -> str:
    """§1 计算周期归属键 period_key（day->YYYY-MM-DD, week->%04d-W%02d ISO 周, month->YYYY-MM）。"""
    if isinstance(period_or_dt, str) and period_or_dt.strip() in ("day", "week", "month"):
        period = period_or_dt.strip()
        target_dt = _to_date(dt_or_period if dt_or_period is not None else default_now())
    else:
        if not isinstance(dt_or_period, str):
            raise ValueError("必须指定 period ('day' | 'week' | 'month')")
        period = dt_or_period.strip()
        target_dt = _to_date(period_or_dt)

    if period == "day":
        return target_dt.strftime("%Y-%m-%d")
    if period == "week":
        iso_year, iso_week, _ = target_dt.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    if period == "month":
        return f"{target_dt.year:04d}-{target_dt.month:02d}"
    raise ValueError(f"未知周期类型: {period!r}")


calc_period_key = period_key


def effective_month_dom(year: int, month: int, anchor_dom: int) -> int:
    """R48：计算月度任务在指定年月的实际触发日（1~31，当月无此日取月末）。"""
    if not (1 <= int(anchor_dom) <= 31):
        raise ValueError(f"anchor_dom 必须在 1~31 之间: {anchor_dom!r}")
    return min(int(anchor_dom), calendar.monthrange(int(year), int(month))[1])


def is_anchor_day(
    period: str, dt: DateLike, anchor_weekday: Optional[int] = None, anchor_dom: Optional[int] = None
) -> bool:
    """R47~R48：判断给定日期 dt 是否命中周期锚点日。"""
    p = (period or "").strip()
    d = _to_date(dt)
    if p == "day":
        return True
    if p == "week":
        aw = 1 if anchor_weekday is None else int(anchor_weekday)
        if not (1 <= aw <= 7):
            raise ValueError(f"anchor_weekday 必须在 1~7 之间: {anchor_weekday!r}")
        return d.isoweekday() == aw
    if p == "month":
        ad = 1 if anchor_dom is None else int(anchor_dom)
        return d.day == effective_month_dom(d.year, d.month, ad)
    raise ValueError(f"未知周期类型: {period!r}")


def is_template_anchor_day(template: Dict[str, Any], dt: DateLike) -> bool:
    return is_anchor_day(template["period"], dt, template.get("anchor_weekday"), template.get("anchor_dom"))


def calc_deadline_at(anchor_dt: DateLike, deadline_hhmm: str, deadline_offset: int = 0, period: Optional[str] = None) -> str:
    """R47~R49：计算实例截止时间 YYYY-MM-DD HH:MM:SS（截止 = 锚点日 + deadline_offset 天的 deadline）。"""
    anchor_date = _to_date(anchor_dt)
    hour, minute = _parse_hhmm(deadline_hhmm)
    offset = 0 if period == "day" else int(deadline_offset or 0)
    if not (0 <= offset <= 6):
        raise ValueError(f"deadline_offset 必须在 0~6 天之间: {deadline_offset!r}")
    due_date = anchor_date + timedelta(days=offset)
    return datetime.combine(due_date, time(hour=hour, minute=minute, second=0)).strftime("%Y-%m-%d %H:%M:%S")


def format_deadline_display(deadline_at: str, ref_dt: Optional[DateLike] = None) -> str:
    """§11：{截止时间} 转为「今天 18:00」或「10月25日 18:00」。"""
    dt = parse_dt(deadline_at)
    hhmm = dt.strftime("%H:%M")
    if ref_dt is not None and dt.date() == _to_date(ref_dt):
        return f"今天 {hhmm}"
    return f"{dt.month}月{dt.day}日 {hhmm}"


def render_text_tpl(
    text_tpl: str,
    *,
    member_name: str,
    member_dept: Optional[str],
    template_name: str,
    deadline_at: Optional[str] = None,
    period: str = "day",
    ref_dt: Optional[DateLike] = None,
    extra_vars: Optional[Dict[str, str]] = None,
) -> str:
    """§11 渲染提醒文案变量（未知变量原样保留）：{姓名} / {部门} / {任务名} / {截止时间} / {周期}。
    2026-10-27 春促：extra_vars 注入业务数据变量（占位符 {key}，如 {completion_rate}），
    同样遵循未知变量原样保留，缺失数据不会把推送变乱码。"""
    deadline_disp = format_deadline_display(deadline_at, ref_dt=ref_dt) if deadline_at else ""
    replacements = {
        "{姓名}": member_name or "",
        "{部门}": member_dept or "",
        "{任务名}": template_name or "",
        "{截止时间}": deadline_disp,
        "{周期}": PERIOD_DISPLAY_MAP.get(period, "今日"),
    }
    for k, v in (extra_vars or {}).items():
        replacements["{%s}" % k] = str(v)
    rendered = text_tpl or ""
    for k, v in replacements.items():
        rendered = rendered.replace(k, v)
    return rendered


def make_idem_key(template_id: str, phone: str, period_key_val: str, kind: str, seq: int = 1, planned_str: str = "") -> str:
    """§6.2 幂等键：sha1(f"{template_id}|{phone}|{period_key}|{kind}|{seq}|{planned_at}")。

    ⚠️ 2026-10-28 修复：原键**不含计划时刻**，于是同一天内只要某个时刻被记为 missed
    （最常见场景：服务重启时已超过 30 分钟补偿窗口，`COMPENSATION_WINDOW_EXCEEDED`），
    管理员随后把发送时间改到稍后，也会被这条 missed 永久挡住 —— 表现就是「改了时间不发」。
    加入 planned_str 后：每个计划时刻各自独立幂等；同一时刻的重试仍复用同一个键。
    """
    raw = f"{template_id}|{phone}|{period_key_val}|{kind}|{int(seq)}"
    if planned_str:
        raw += f"|{planned_str}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class NotifierProtocol(Protocol):
    def send_text(self, receive_id: str, text: str, idem_key: str) -> SendResult: ...


class ImageSenderProtocol(Protocol):
    """看板图片投递依赖（上传一次 + 按人发送）。"""

    def prepare_image(self, file_path: str) -> Dict[str, Any]: ...

    def send_image_prepared(self, receive_id: str, prepared: Dict[str, Any], idem_key: str) -> SendResult: ...


class ZdxlzImageSender:
    """基于 ZdxlzChannel 的图片发送实现：upload_media 三步上传 + send_image 推送。"""

    def __init__(self, channel) -> None:
        self.channel = channel

    def prepare_image(self, file_path: str) -> Dict[str, Any]:
        file_key = self.channel.upload_media(file_path, category=1)
        width, height = self.channel._read_image_size(file_path)
        if not width or not height:
            from .channels.zdxlz import ChannelError

            raise ChannelError(f"读取图片宽高失败: {file_path}")
        return {"file_key": file_key, "width": int(width), "height": int(height), "mime_type": "png"}

    def send_image_prepared(self, receive_id: str, prepared: Dict[str, Any], idem_key: str) -> SendResult:
        return self.channel.send_image(
            receive_id,
            prepared["file_key"],
            width=prepared["width"],
            height=prepared["height"],
            mime_type=prepared.get("mime_type") or "png",
            alt_text="数据看板",
            push_content="你收到一条数据看板",
            idem_key=idem_key,
        )


@dataclass
class SentMessageRecord:
    receive_id: str
    text: str
    idem_key: str
    sent_at: str


class FakeNotifier:
    """内存替身发送器（代替真实网络发送，记录全部发送明细供单测断言）。"""

    def __init__(self, now_fn: NowFn = default_now) -> None:
        self._now_fn = now_fn
        self.sent_messages: List[SentMessageRecord] = []
        self.fail_receive_ids: set[str] = set()
        self.no_session_receive_ids: set[str] = set()

    def send_text(self, receive_id: str, text: str, idem_key: str) -> SendResult:
        rec_id = str(receive_id)
        if rec_id in self.no_session_receive_ids:
            return SendResult(False, "failed", 500, need_activate=True, retryable=False, error="NO_SESSION_500: simulated no session")
        if rec_id in self.fail_receive_ids:
            return SendResult(False, "failed", 400, need_activate=False, retryable=True, error="SIMULATED_SEND_ERROR")
        self.sent_messages.append(SentMessageRecord(rec_id, text, idem_key, format_dt(self._now_fn())))
        return SendResult(True, "sent", 200, need_activate=False, retryable=False, error=None)


class Scheduler:
    """§6 定时调度核心（惰性生成实例 + 真实 H5 令牌签发 + 提醒/逾期/驳回投递 + 退避重试 + 状态推进）。"""

    COMPENSATION_WINDOW_SECONDS = 30 * 60
    OVERDUE_NOTICE_DELAY_MINUTES = 10
    MAX_RETRIES = 3
    RETRY_DELAYS = (30, 120, 300)

    def __init__(
        self,
        state: StateStore,
        public_base_url: str,
        notifier: Optional[NotifierProtocol] = None,
        now_fn: Optional[NowFn] = None,
        image_sender: Optional[ImageSenderProtocol] = None,
    ) -> None:
        if not public_base_url or not str(public_base_url).strip():
            raise ValueError("public_base_url 必须从 Config 传入，不得为空")
        self.state = state
        self.public_base_url = str(public_base_url).strip().rstrip("/")
        self._now_fn = now_fn or state.now
        self.notifier: NotifierProtocol = notifier or FakeNotifier(now_fn=self._now_fn)
        self.image_sender = image_sender  # None 时跳过图片投递（文本照发，不阻塞主链路）

    def now(self) -> datetime:
        return self._now_fn()

    def build_h5_submit_url(self, *, phone: str, template_id: str, period_key_val: str, instance_id: int) -> Tuple[str, str]:
        """§8.1 签发真实 72h H5 令牌（库中只存 SHA-256），返回 (h5_url, raw_token)。"""
        raw_token = self.state.issue_h5_token(
            phone=phone, template_id=template_id, period_key=period_key_val, instance_id=int(instance_id), purpose="submit", ttl_hours=72
        )
        return f"{self.public_base_url}/h5/task#t={raw_token}", raw_token

    def ensure_instances_for_template(self, template: Dict[str, Any], now_dt: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not template.get("enabled") or template.get("deleted") or template.get("kind") == "notice":
            return []
        cur_dt = now_dt or self.now()
        if not is_template_anchor_day(template, cur_dt):
            return []
        p_key = period_key(template["period"], cur_dt)
        deadline_at = calc_deadline_at(cur_dt, template["deadline"], int(template.get("deadline_offset") or 0), template["period"])
        return self.state.ensure_template_instances(template["template_id"], p_key, deadline_at)

    def _build_planned_dt(self, ref_dt: datetime, hhmm: str) -> datetime:
        h, m = _parse_hhmm(hhmm)
        return datetime(ref_dt.year, ref_dt.month, ref_dt.day, h, m, 0, tzinfo=ref_dt.tzinfo or SHANGHAI_TZ)

    def _resolve_retry_slot(self, idem_key: str, cur_dt: Optional[datetime] = None) -> Optional[int]:
        """幂等与退避门控：
        - 已 sent/missed/skipped 或 NO_SESSION_500 或超 MAX_RETRIES -> 返回 None
        - 仅有 failed -> 计算下次 retry (1..3)，并校验距最近一次 attempted_at 是否已满 RETRY_DELAYS[retry_no-1] 秒
        """
        if self.state.has_sent_reminder(idem_key):
            return None
        attempts = self.state.list_reminder_attempts(idem_key)
        if not attempts:
            return 0
        for row in attempts:
            if row["result"] in ("sent", "missed", "skipped"):
                return None
            if row.get("error") and "NO_SESSION_500" in str(row.get("error")):
                return None

        latest_row = max(attempts, key=lambda r: (int(r["retry"]), int(r["id"])))
        next_retry = int(latest_row["retry"]) + 1
        if next_retry > self.MAX_RETRIES:
            return None

        last_ts_str = latest_row.get("attempted_at") or latest_row.get("sent_at") or latest_row.get("planned_at")
        if last_ts_str:
            now_dt = cur_dt or self.now()
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=SHANGHAI_TZ)
            elapsed = (now_dt - parse_dt(str(last_ts_str))).total_seconds()
            required_delay = self.RETRY_DELAYS[next_retry - 1]
            if elapsed < required_delay:
                return None

        return next_retry

    def _deliver_with_log(
        self,
        *,
        template_id: str,
        instance_id: Optional[int],
        phone: str,
        external_id: Optional[str],
        kind: str,
        planned_str: str,
        delay_sec: float,
        idem_key: str,
        msg_text_fn,
        cur_dt: datetime,
        now_s: str,
        stats: Dict[str, int],
        send_fn: Optional[Callable[[str, str, str], SendResult]] = None,
    ) -> None:
        retry_no = self._resolve_retry_slot(idem_key, cur_dt=cur_dt)
        if retry_no is None:
            return

        if retry_no == 0 and delay_sec > self.COMPENSATION_WINDOW_SECONDS:
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="missed", retry=0,
                error="COMPENSATION_WINDOW_EXCEEDED", idem_key=idem_key,
            )
            stats["missed"] += 1
            return

        if not external_id:
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="skipped", retry=retry_no,
                error="UNACTIVATED", idem_key=idem_key,
            )
            stats["skipped"] += 1
            return

        msg_text = msg_text_fn()
        active_send = send_fn or self.notifier.send_text
        send_res = active_send(external_id, msg_text, idem_key)
        self.state.record_reminder_log(
            template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
            planned_at=planned_str, attempted_at=now_s, sent_at=now_s if send_res.ok else None, result=send_res.result,
            retry=retry_no, error=send_res.error, idem_key=idem_key,
        )
        if send_res.ok:
            stats["sent"] += 1
        else:
            stats["failed"] += 1
            if send_res.need_activate:
                self.state.mark_delivery_target_unusable(phone)

    def send_reject_notification(
        self, instance: Dict[str, Any], reason: str, now_dt: Optional[datetime] = None
    ) -> Optional[SendResult]:
        """§11 & R27：审核驳回时向提交人发送重新提交通知（带新签发 H5 令牌与重交截止时间）。"""
        cur_dt = now_dt or self.now()
        now_s = format_dt(cur_dt)
        tpl = self.state.get_template(instance["template_id"])
        if not tpl:
            return None
        target = self.state.get_delivery_target(instance["phone"])
        reviewed_tag = (instance.get("reviewed_at") or now_s).replace(" ", "T")
        idem = hashlib.sha1(
            f"{instance['template_id']}|{instance['phone']}|{instance['period_key']}|reject|{reviewed_tag}".encode("utf-8")
        ).hexdigest()

        if not target or not target.get("external_id"):
            self.state.record_reminder_log(
                template_id=instance["template_id"], instance_id=int(instance["id"]), phone=instance["phone"],
                kind="reject", planned_at=now_s, attempted_at=now_s, sent_at=None, result="skipped", retry=0, error="UNACTIVATED", idem_key=idem,
            )
            return None

        h5_url, _ = self.build_h5_submit_url(
            phone=instance["phone"], template_id=instance["template_id"],
            period_key_val=instance["period_key"], instance_id=int(instance["id"]),
        )
        resubmit_disp = format_deadline_display(instance["resubmit_deadline"], ref_dt=cur_dt)
        msg_text = f"【{tpl['name']}】提交被驳回\n原因：{reason}\n请在 {resubmit_disp} 前重新提交。\n👉 重新提交：{h5_url}"
        send_res = self.notifier.send_text(target["external_id"], msg_text, idem)
        self.state.record_reminder_log(
            template_id=instance["template_id"], instance_id=int(instance["id"]), phone=instance["phone"],
            kind="reject", planned_at=now_s, attempted_at=now_s, sent_at=now_s if send_res.ok else None,
            result=send_res.result, retry=0, error=send_res.error, idem_key=idem,
        )
        if not send_res.ok and send_res.need_activate:
            self.state.mark_delivery_target_unusable(instance["phone"])
        return send_res

    def run_once(self, now_dt: Optional[datetime] = None) -> Dict[str, int]:
        """执行一轮调度扫描（§6.1 A/B/C/D + R14 逾期通知），返回本轮统计计数。"""
        cur_dt = now_dt or self.now()
        if cur_dt.tzinfo is None:
            cur_dt = cur_dt.replace(tzinfo=SHANGHAI_TZ)
        now_s = format_dt(cur_dt)
        stats = {"generated_instances": 0, "sent": 0, "skipped": 0, "missed": 0, "failed": 0, "to_overdue": 0, "to_expired": 0}

        for tpl in self.state.list_active_templates(include_notice=True):
            if not is_template_anchor_day(tpl, cur_dt):
                continue

            if tpl["kind"] == "notice":
                p_key = period_key(tpl["period"], cur_dt)
                members = self.state.get_dedup_enabled_members_for_roles(tpl["role_ids"])
                data_keys = tpl.get("data_keys") or []
                snap_values: Dict[str, str] = {}
                if data_keys:
                    snap_values = self.state.get_data_snapshot_values(tpl["template_id"], p_key)
                for seq_idx, nt_hhmm in enumerate(tpl.get("notice_times") or [], start=1):
                    planned_dt = self._build_planned_dt(cur_dt, nt_hhmm)
                    if cur_dt < planned_dt:
                        continue
                    planned_str = format_dt(planned_dt)
                    delay_sec = (cur_dt - planned_dt).total_seconds()
                    for m in members:
                        idem = make_idem_key(tpl["template_id"], m["phone"], p_key, "notice", seq_idx, planned_str)
                        ext_id = m["external_id"] if m["activated"] else None

                        def _make_notice_msg(member=m, template=tpl) -> str:
                            # 2026-10-26 业务方定档：notice 正文**不加**【模板名】前缀，
                            # 文案原样发出（后台模板名只用于列表展示，不进消息体）。
                            return render_text_tpl(
                                template["text_tpl"], member_name=member["name"], member_dept=member["dept"],
                                template_name=template["name"], deadline_at=None, period=template["period"], ref_dt=cur_dt,
                                extra_vars=snap_values,
                            )

                        self._deliver_with_log(
                            template_id=tpl["template_id"], instance_id=None, phone=m["phone"], external_id=ext_id,
                            kind="notice", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem,
                            msg_text_fn=_make_notice_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                        )

                    # 2026-10-27 春促数据看板：attach_image=1 且当日快照有值 -> 文本后追加图片投递
                    if tpl.get("attach_image") and data_keys and self.image_sender is not None:
                        has_any_value = any(
                            str(snap_values.get(str(dk.get("key") or "")) or "").strip() for dk in data_keys
                        )
                        if has_any_value:
                            prepared = None
                            try:
                                from .dashboard import render_snapshot_dashboard

                                # 2026-10-27 修：StateStore 本身没有 db_path 属性（路径在 state.db.db_path 上），
                                # 原来 getattr(self.state, "db_path", Path("data")) 永远走默认值，
                                # Path("data").parent 得到 "."，看板被写到 <cwd>/dashboards。
                                # 生产 unit 有 ReadWritePaths=/opt/imtask/data（其余只读），
                                # 于是出图 Permission denied → 图片静默不发。改为优先取真实库路径。
                                db_path = getattr(getattr(self.state, "db", None), "db_path", None)
                                data_dir = Path(db_path).parent if db_path else Path("data").resolve()
                                out_dir = data_dir / "dashboards"
                                out_png = str(out_dir / f"{tpl['template_id']}_{p_key}_{seq_idx}.png")
                                note_text = f"数据截至 {now_s} · 数据来源：人工填报"
                                render_snapshot_dashboard(
                                    title=f"{tpl['name']} · 数据看板",
                                    subtitle=f"{p_key} · 春促按角色提醒",
                                    data_keys=data_keys,
                                    values=snap_values,
                                    note=note_text,
                                    out_path=out_png,
                                )
                                prepared = self.image_sender.prepare_image(out_png)
                            except Exception as exc:  # 看板渲染/上传失败不影响文本主链路
                                logger.warning("看板渲染或上传失败 template=%s seq=%s: %s", tpl["template_id"], seq_idx, exc)
                            if prepared:
                                for m in members:
                                    idem_img = make_idem_key(tpl["template_id"], m["phone"], p_key, "notice_img", seq_idx, planned_str)
                                    ext_id = m["external_id"] if m["activated"] else None
                                    if not ext_id:
                                        continue  # 未激活成员文本链路已记 skipped，图片直接跳过避免重复记一条

                                    def _noop_text() -> str:
                                        return ""

                                    self._deliver_with_log(
                                        template_id=tpl["template_id"], instance_id=None, phone=m["phone"], external_id=ext_id,
                                        kind="notice_img", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem_img,
                                        msg_text_fn=_noop_text, cur_dt=cur_dt, now_s=now_s, stats=stats,
                                        send_fn=lambda rid, _t, idem, _prep=prepared: self.image_sender.send_image_prepared(rid, _prep, idem),
                                    )
                continue

            planned_dt = self._build_planned_dt(cur_dt, tpl["remind_at"])
            if cur_dt < planned_dt:
                continue

            planned_str = format_dt(planned_dt)
            delay_sec = (cur_dt - planned_dt).total_seconds()
            p_key = period_key(tpl["period"], cur_dt)

            before_cnt = len(self.state.list_instances_for_period(tpl["template_id"], p_key))
            instances = self.ensure_instances_for_template(tpl, cur_dt)
            stats["generated_instances"] += max(0, len(instances) - before_cnt)

            for inst in instances:
                if inst["status"] != "pending":
                    continue
                idem = make_idem_key(tpl["template_id"], inst["phone"], p_key, "remind", 1, planned_str)
                target = self.state.get_delivery_target(inst["phone"])
                ext_id = target["external_id"] if target else None

                def _make_remind_msg(instance=inst, template=tpl) -> str:
                    rendered = render_text_tpl(
                        template["text_tpl"], member_name=instance["name_snap"], member_dept=instance["dept_snap"],
                        template_name=template["name"], deadline_at=instance["deadline_at"], period=template["period"], ref_dt=cur_dt,
                    )
                    h5_url, _ = self.build_h5_submit_url(
                        phone=instance["phone"], template_id=template["template_id"],
                        period_key_val=instance["period_key"], instance_id=int(instance["id"]),
                    )
                    return f"【{template['name']}】{rendered}\n👉 提交入口：{h5_url}"

                self._deliver_with_log(
                    template_id=tpl["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                    kind="remind", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem,
                    msg_text_fn=_make_remind_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                )

        adv = self.state.advance_instance_statuses(cur_dt)
        stats["to_overdue"] = adv["to_overdue"]
        stats["to_expired"] = adv["to_expired"]

        # R14：deadline 后 10 分钟对未交者（overdue）发 1 次逾期通知（kind='overdue'，不重复）
        for inst in self.state.list_overdue_instances():
            deadline_dt = parse_dt(inst["deadline_at"])
            overdue_planned_dt = deadline_dt + timedelta(minutes=self.OVERDUE_NOTICE_DELAY_MINUTES)
            if cur_dt < overdue_planned_dt:
                continue
            tpl = self.state.get_template(inst["template_id"])
            if not tpl or not tpl.get("enabled") or tpl.get("deleted"):
                continue

            planned_str = format_dt(overdue_planned_dt)
            delay_sec = (cur_dt - overdue_planned_dt).total_seconds()
            idem = make_idem_key(inst["template_id"], inst["phone"], inst["period_key"], "overdue", 1, planned_str)
            target = self.state.get_delivery_target(inst["phone"])
            ext_id = target["external_id"] if target else None

            def _make_overdue_msg(instance=inst, template=tpl, dl_dt=deadline_dt) -> str:
                late_limit_str = format_dt(dl_dt + timedelta(hours=72))
                dl_disp = format_deadline_display(instance["deadline_at"], ref_dt=cur_dt)
                late_disp = format_deadline_display(late_limit_str, ref_dt=cur_dt)
                h5_url, _ = self.build_h5_submit_url(
                    phone=instance["phone"], template_id=template["template_id"],
                    period_key_val=instance["period_key"], instance_id=int(instance["id"]),
                )
                return f"【{template['name']}】已逾期\n截止时间 {dl_disp}，你尚未提交。可在 {late_disp} 前补交。\n👉 提交入口：{h5_url}"

            self._deliver_with_log(
                template_id=inst["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                kind="overdue", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem,
                msg_text_fn=_make_overdue_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
            )

        return stats

    tick = run_once
