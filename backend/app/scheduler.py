# -*- coding: utf-8 -*-
"""周期与时间纯函数工具 + 实例惰性生成与调度循环（严格对齐规格书 §1、§5、§6、§8.1、§11、R10~R27、R47~R49）。"""
from __future__ import annotations

import asyncio
import calendar
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union

from .channels.zdxlz import SendResult
from .config import SHANGHAI_TZ, NowFn, default_now, format_dt, parse_dt
from .state import (  # calc_resubmit_deadline 在此处 re-export：旧用例与文档都从 scheduler 导入
    H5_TOKEN_TTL_HOURS,
    NO_DEADLINE_SENTINEL,
    StateStore,
    BizError,
    calc_h5_token_ttl_hours,
    calc_resubmit_deadline,
    effective_instance_deadline,
    is_no_deadline,
    is_valid_hhmm,
)
# 2026-11-13 冲刺模块二：受控并发投递（8 线程 + 令牌桶 + 熔断）
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from .throttle import (  # noqa: E402
    CircuitBreaker,
    TokenBucket,
    breaker_threshold,
    delivery_qps,
    delivery_threads,
)

logger = logging.getLogger(__name__)

DateLike = Union[datetime, date, str]
PERIOD_DISPLAY_MAP = {"day": "今日", "week": "本周", "month": "本月"}


async def run_upload_cleanup_loop(state: StateStore, stop_event: asyncio.Event) -> None:
    """每天上海时间 03:00 执行，独立于派发扫描。"""
    async def clean_replaced() -> None:
        try:
            removed = await asyncio.to_thread(state.cleanup_replaced_uploads, state.data_dir / 'uploads')
            if removed:
                logger.info('替换附件清理完成，移除 %d 条', removed)
        except Exception:
            logger.exception('替换附件清理失败')

    if not stop_event.is_set():
        await clean_replaced()
    while not stop_event.is_set():
        now = datetime.now(SHANGHAI_TZ)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run < now:
            next_run += timedelta(days=1)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=(next_run - now).total_seconds())
            return
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        await clean_replaced()
        try:
            removed = await asyncio.to_thread(state.cleanup_orphan_uploads)
            logger.info("孤儿上传清理完成，移除 %d 条", removed)
        except Exception:
            logger.exception("孤儿上传清理失败")


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
    """模板「今天是否需要执行」。

    2026-10-29 执行日：period=="day" 时按 exec_weekdays（1=周一…7=周日）判断；
    未配置 / 解析失败 / 空数组 → **全周执行**（fail-open：宁可多催一天，⛔ 不许业务静默失联）。
    2026-11-10 月度执行日：exec_dom_days 非空时**优先**按每月几号判定（1~31 多选）；
    选中 31 = 当月最后一天也执行（自动适配 2 月 28/29、小月 30、大月 31）。
    ⛔ 这是唯一的执行日判定点：run_once 循环顶部已调用，非执行日 continue 后
    实例生成、发送、missed、逾期流转全部天然跳过，禁止在别处再开第二个判定。
    """
    if (template.get("period") or "") == "day":
        # ── 月度模式（优先）──
        raw_dom = template.get("exec_dom_days")
        dom_days: Optional[List[int]] = None
        if isinstance(raw_dom, list):
            dom_days = [int(x) for x in raw_dom if str(x).strip().isdigit() and 1 <= int(x) <= 31]
        elif isinstance(raw_dom, str) and raw_dom.strip():
            try:
                parsed_dom = json.loads(raw_dom)
                if isinstance(parsed_dom, list):
                    dom_days = [int(x) for x in parsed_dom if str(x).strip().isdigit() and 1 <= int(x) <= 31]
            except Exception:
                dom_days = None
        if dom_days:
            today = _to_date(dt)
            dom_set = set(dom_days)
            if today.day in dom_set:
                return True
            # 月底适配：31 在列表且今天恰为当月最后一天（next 月 1 号 - 1 天）
            if 31 in dom_set:
                last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                if today == last_day:
                    return True
            return False
        # ── 每周模式（既有）──
        raw = template.get("exec_weekdays")
        days: Optional[List[int]] = None
        if isinstance(raw, list):
            days = [int(x) for x in raw if str(x).strip().isdigit() and 1 <= int(x) <= 7]
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    days = [int(x) for x in parsed if str(x).strip().isdigit() and 1 <= int(x) <= 7]
            except Exception:
                days = None
        if days:
            return _to_date(dt).isoweekday() in set(days)
        return True
    return is_anchor_day(template["period"], dt, template.get("anchor_weekday"), template.get("anchor_dom"))


def calc_deadline_at(anchor_dt: DateLike, deadline_hhmm: str, deadline_offset: int = 0, period: Optional[str] = None) -> str:
    """R47~R49：计算实例截止时间 YYYY-MM-DD HH:MM:SS（截止 = 锚点日 + deadline_offset 天的 deadline）。

    2026-11-12 下午（用户拍板，图三报错修复）：截止时刻 **00:00 = 当天 24 点**
    （即次日 0 点）——当天 0 点就是当天的 24 点，任何提醒时刻都早于它，应可正常创建。
    """
    anchor_date = _to_date(anchor_dt)
    hour, minute = _parse_hhmm(deadline_hhmm)
    offset = 0 if period == "day" else int(deadline_offset or 0)
    if not (0 <= offset <= 6):
        raise ValueError(f"deadline_offset 必须在 0~6 天之间: {deadline_offset!r}")
    due_date = anchor_date + timedelta(days=offset)
    if hour == 0 and minute == 0:
        due_date = due_date + timedelta(days=1)  # 00:00 = 当天 24 点 = 次日 0 点
    return datetime.combine(due_date, time(hour=hour, minute=minute, second=0)).strftime("%Y-%m-%d %H:%M:%S")


def calc_instance_deadline(template: Dict[str, Any], anchor_dt: DateLike) -> str:
    """2026-11-12 任务E1：按模板截止配置计算实例截止时间（用户拍板口径）。

    · has_deadline=0（无截止）→ 哨兵 9999-12-31 23:59:59（NOT NULL 列不动，字符串比较天然
      永不过期；展示层统一特判「不限」）；
    · deadline_type=same_day → 锚点日 + deadline_time；
    · deadline_type=weekend  → 锚点日所在 ISO 周的周日 + deadline_time；
    · deadline_type=month_end→ 锚点日所在月最后一天 + deadline_time；
    · deadline_type 为空（旧模板）→ 回落旧算法 calc_deadline_at(deadline + deadline_offset)。

    2026-11-12 下午（用户拍板，图三报错修复）：三种类型的截止时刻 **00:00 都按
    「该基准日的 24 点」**（= 次日 0 点）计算——当天 0 点就是当天的 24 点，
    应允许创建并在次日 0 点才过期（与 calc_deadline_at 旧口径同步）。
    """
    # has_deadline 显式为 0/False → 无截止；None/缺键（旧调用方、raw dict）→ 按有截止处理
    if template.get("is_manual"):
        return template.get("manual_deadline_at") or NO_DEADLINE_SENTINEL
    _hd = template.get("has_deadline")
    if _hd is not None and not int(_hd):
        return NO_DEADLINE_SENTINEL
    deadline_type = str(template.get("deadline_type") or "").strip()
    if deadline_type:
        hhmm = str(template.get("deadline_time") or template.get("deadline") or "").strip()
        if not is_valid_hhmm(hhmm):
            raise ValueError(f"模板截止时刻非法: {hhmm!r}")
        anchor = _to_date(anchor_dt)
        if deadline_type == "weekend":
            due_date = anchor + timedelta(days=7 - anchor.isoweekday())  # isoweekday: 周日=7
        elif deadline_type == "month_end":
            due_date = date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])
        elif deadline_type == "same_day":
            due_date = anchor
        else:
            raise ValueError(f"未知 deadline_type: {deadline_type!r}")
        hour, minute = _parse_hhmm(hhmm)
        if hour == 0 and minute == 0:
            due_date = due_date + timedelta(days=1)  # 00:00 = 基准日 24 点 = 次日 0 点
        return datetime.combine(due_date, time(hour=hour, minute=minute, second=0)).strftime("%Y-%m-%d %H:%M:%S")
    # 旧模板兼容：沿用 R47~R49 算法（deadline + deadline_offset，行为与历史完全一致）
    return calc_deadline_at(
        anchor_dt,
        str(template.get("deadline") or "").strip(),
        int(template.get("deadline_offset") or 0),
        template.get("period"),
    )


def format_deadline_display(deadline_at: str, ref_dt: Optional[DateLike] = None) -> str:
    """§11：{截止时间} 统一显示为具体日期「11月30日 18:00」。

    2026-11-12 任务E1：无截止哨兵统一显示「不限」（消息文案/H5/后台同口径）。
    2026-11-13 用户拍板：连当天也统一 `M月D日 HH:MM`（废除「今天 18:00」措辞），
    与 state.format_deadline_rule / 前端徽章三端一字不差。
    """
    del ref_dt  # 2026-11-13：不再需要「当天」特判，参数保留兼容旧调用方
    if is_no_deadline(deadline_at):
        return "不限"
    dt = parse_dt(deadline_at)
    return f"{dt.month}月{dt.day}日 {dt.strftime('%H:%M')}"


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
    # 2026-10-28：从 JSON / 文档粘贴的文案常把换行写成字面量 "\n"，密信会原样显示成「\n1、…」。
    # 统一在渲染出口还原成真换行。
    rendered = rendered.replace("\r\n", "\n").replace("\\n", "\n")
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
    """2026-11-10 方案A：receive_type 标识 receive_id 的体系（user=IM userId / union_id=通讯录键）。
    企微通道忽略该参数；zdxlz 通道仅对 union_id 显式传 receive_type="union_id"。"""

    def send_text(self, receive_id: str, text: str, idem_key: str, receive_type: str = "user") -> SendResult: ...


class ImageSenderProtocol(Protocol):
    """看板图片投递依赖（上传一次 + 按人发送）。"""

    def prepare_image(self, file_path: str) -> Dict[str, Any]: ...

    def send_image_prepared(self, receive_id: str, prepared: Dict[str, Any], idem_key: str) -> SendResult: ...


class ZdxlzImageSender:
    """基于 ZdxlzChannel 的图片发送实现：upload_media 三步上传 + send_image 推送。"""

    def __init__(self, channel) -> None:
        self.channel = channel

    @staticmethod
    def _flatten_to_jpeg(src_path: str) -> Optional[str]:
        """把任意图片转成「RGB baseline JPEG」（压平 alpha、丢掉 ICC/EXIF/gamma 等 chunk）。

        2026-10-29：附图多是 Pillow 生成的 **RGBA PNG**，PC 端浏览器能正常显示，
        但手机端量子密信里是「看得到缩略图、点不开大图」。移动端解码器/缩略图管线
        对 alpha 通道和异常 chunk 更严格，统一转 JPEG 是最稳的公共分母。
        失败（无 Pillow / 源文件损坏）返回 None，调用方回退成原图上传。
        """
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(src_path) as im:
                im.load()
                if im.mode in ("RGBA", "LA", "P"):
                    src = im.convert("RGBA")
                    canvas = Image.new("RGB", src.size, (255, 255, 255))
                    canvas.paste(src, mask=src.split()[-1])
                    out_img = canvas
                else:
                    out_img = im.convert("RGB")
                fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="imtask_img_")
                os.close(fd)
                out_img.save(tmp_path, format="JPEG", quality=88, optimize=True, progressive=False)
            return tmp_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("附流转 JPEG 失败，回退原图上传: %s (%s)", src_path, exc)
            return None

    def prepare_image(self, file_path: str) -> Dict[str, Any]:
        upload_path = self._flatten_to_jpeg(file_path)
        mime_type = "image/jpeg"
        try:
            path_to_use = upload_path or file_path
            if not upload_path:
                mime_type = "image/png" if str(file_path).lower().endswith(".png") else "image/jpeg"
            file_key = self.channel.upload_media(path_to_use, category=1)
            width, height = self.channel._read_image_size(path_to_use)
            file_size = os.path.getsize(path_to_use)
        finally:
            if upload_path:
                try:
                    os.unlink(upload_path)
                except OSError:
                    pass
        if not width or not height:
            from .channels.zdxlz import ChannelError

            raise ChannelError(f"读取图片宽高失败: {file_path}")
        # 2026-10-29 D/E/F 实测修复：name/size/ext 必须随 image 消息 content 一起发，
        # 客户端凭它们识别图片（缺省时手机点击转圈、PC 走文件下载）。
        # name 用原文件名主名 + 实际上传格式后缀，保证可读且扩展名与真实文件一致。
        file_ext = "jpg" if upload_path else (Path(file_path).suffix.lstrip(".").lower() or "png")
        file_name = f"{Path(file_path).stem or '数据看板'}.{file_ext}"
        return {
            "file_key": file_key,
            "width": int(width),
            "height": int(height),
            "mime_type": mime_type,
            "file_name": file_name,
            "file_size": int(file_size),
            "file_ext": file_ext,
        }

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
            file_name=prepared.get("file_name"),
            file_size=prepared.get("file_size"),
            file_ext=prepared.get("file_ext"),
        )


@dataclass
class SentMessageRecord:
    receive_id: str
    text: str
    idem_key: str
    sent_at: str
    receive_type: str = "user"  # 2026-11-10 方案A：记录该消息按哪个 id 体系发出


class FakeNotifier:
    """内存替身发送器（代替真实网络发送，记录全部发送明细供单测断言）。"""

    def __init__(self, now_fn: NowFn = default_now) -> None:
        self._now_fn = now_fn
        self.sent_messages: List[SentMessageRecord] = []
        self.fail_receive_ids: set[str] = set()
        self.no_session_receive_ids: set[str] = set()

    def send_text(self, receive_id: str, text: str, idem_key: str, receive_type: str = "user") -> SendResult:
        rec_id = str(receive_id)
        rt = (receive_type or "user").strip().lower() or "user"
        if rec_id in self.no_session_receive_ids:
            return SendResult(False, "failed", 500, need_activate=True, retryable=False, error="NO_SESSION_500: simulated no session")
        if rec_id in self.fail_receive_ids:
            return SendResult(False, "failed", 400, need_activate=False, retryable=True, error="SIMULATED_SEND_ERROR")
        self.sent_messages.append(SentMessageRecord(rec_id, text, idem_key, format_dt(self._now_fn()), rt))
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
        notifiers: Optional[Dict[str, NotifierProtocol]] = None,
    ) -> None:
        if not public_base_url or not str(public_base_url).strip():
            raise ValueError("public_base_url 必须从 Config 传入，不得为空")
        self.state = state
        self.public_base_url = str(public_base_url).strip().rstrip("/")
        self._now_fn = now_fn or state.now
        self.notifier: NotifierProtocol = notifier or FakeNotifier(now_fn=self._now_fn)
        # 2026-11-02 多通道并行：platform -> notifier。为空时退回旧单通道逻辑（测试兼容）。
        self.notifiers: Dict[str, NotifierProtocol] = dict(notifiers or {})
        self._delivery_bucket = TokenBucket(rate_per_sec=delivery_qps())
        self._delivery_breaker = CircuitBreaker(fail_threshold=breaker_threshold())
        self.image_sender = image_sender  # None 时跳过图片投递（文本照发，不阻塞主链路）

    def _targets_for(self, phone: str, only_platform: Optional[str] = None) -> List[Tuple[str, Optional[str], str, NotifierProtocol]]:
        """该成员可用的投递目标 [(platform, external_id, external_type, notifier)]。

        · 2026-11-03 用户口径（取代 11-02 的「双绑定双发」）：任务属于哪个办公软件，就只往那个
          办公软件发 ——密信的任务不再串到企微，企微的任务也不串到密信；
          · only_platform 传任务归属平台：只查该平台绑定；
          · only_platform 为 None（任务平台判断不出来）时 fail-open 查全部启用通道。
        · 单通道/测试（notifiers 为空）：退回旧逻辑（当前 store.platform + self.notifier）。
        · 2026-11-10 方案A：external_type 取绑定的 external_type（user/union_id），
          发送时交给 notifier 决定 receive_type；空值一律按 user（向后兼容历史行）。
        """
        if not self.notifiers:
            t = self.state.get_delivery_target(phone)
            ext_type = str((t or {}).get("external_type") or "user").strip().lower() or "user"
            return [("", (t or {}).get("external_id"), ext_type, self.notifier)]
        want = (only_platform or "").strip().lower()
        out: List[Tuple[str, Optional[str], str, NotifierProtocol]] = []
        for platform, nt in self.notifiers.items():
            if want and platform != want:
                continue
            t = self.state.get_delivery_target(phone, platform=platform)
            ext_type = str((t or {}).get("external_type") or "user").strip().lower() or "user"
            out.append((platform, (t or {}).get("external_id"), ext_type, nt))
        return out

    def _template_platform(self, tpl: Dict[str, Any]) -> Optional[str]:
        """任务归属平台 = 模板所选角色的平台（角色已按办公软件隔离，创建时禁止混选）。

        角色缺失或跨平台混选（理论上不会发生）→ None = 不过滤，fail-open 全通道。
        """
        if tpl.get("portal_task"):
            return tpl["platform"]
        plats = self.state.role_platforms(list(tpl.get("role_ids") or []))
        if len(plats) == 1:
            return plats.pop()
        return None

    @staticmethod
    def _idem_for(base: str, platform: str) -> str:
        """幂等键加通道维度：zdxlz 保持旧格式（历史记录继续命中，不重发）。"""
        return base if not platform or platform == "zdxlz" else f"{base}|{platform}"

    def now(self) -> datetime:
        return self._now_fn()

    def build_h5_submit_url(self, *, phone: str, template_id: str, period_key_val: str, instance_id: int) -> Tuple[str, str]:
        """§8.1 签发 H5 令牌（库中只存 SHA-256），返回 (h5_url, raw_token)。

        2026-11-12 任务E1：有效期不再硬编码 72h，按实例动态计算（保守策略）：
        · 无截止（哨兵）→ 720h（30 天）；
        · 有截止 → min(截止 + 24h, 720h)；
        · rejected 驳回重交 → 以重交截止 + 24h 封顶（覆盖整个重交窗口）。
        """
        inst = self.state.get_instance(int(instance_id))
        ttl_hours = calc_h5_token_ttl_hours(inst, self.now()) if inst else float(H5_TOKEN_TTL_HOURS)
        raw_token = self.state.issue_h5_token(
            phone=phone, template_id=template_id, period_key=period_key_val, instance_id=int(instance_id),
            purpose="submit", ttl_hours=ttl_hours,
        )
        return f"{self.public_base_url}/h5/task#t={raw_token}", raw_token

    def ensure_instances_for_template(self, template: Dict[str, Any], now_dt: Optional[datetime] = None) -> List[Dict[str, Any]]:
        # 2026-10-28 方案A：notice 也生成实例（pending → sent），与上传类统一在「任务实例」页展示。
        if not template.get("enabled") or template.get("deleted") or template.get("is_manual"):
            return []
        cur_dt = now_dt or self.now()
        if not is_template_anchor_day(template, cur_dt):
            return []
        p_key = period_key(template["period"], cur_dt)
        deadline_at = calc_instance_deadline(template, cur_dt)
        return self.state.ensure_template_instances(template["template_id"], p_key, deadline_at)

    def _build_planned_dt(self, ref_dt: datetime, hhmm: str) -> datetime:
        h, m = _parse_hhmm(hhmm)
        return datetime(ref_dt.year, ref_dt.month, ref_dt.day, h, m, 0, tzinfo=ref_dt.tzinfo or SHANGHAI_TZ)

    def _resolve_retry_slot(self, idem_key: str, cur_dt: Optional[datetime] = None) -> Optional[int]:
        """幂等与退避门控：
        - 已 sent/missed/skipped 或 NO_SESSION_500 或超 MAX_RETRIES -> 返回 None
        - 仅有 failed -> 计算下次 retry (1..3)，并校验距最近一次 attempted_at 是否已满 RETRY_DELAYS[retry_no-1] 秒

        2026-11-16 方案B：去掉冗余的 has_sent_reminder 预查（sent ⊂ 循环判定集，一次
        list_reminder_attempts 全覆盖）——MySQL 下 2665 人批量投递少 2665 次往返。
        """
        attempts = self.state.list_reminder_attempts(idem_key)
        if not attempts:
            return 0
        for row in attempts:
            if row["result"] in ("sent", "missed", "skipped", "pending"):
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

    def _publish_image_link(self, src_path: str, image_name: Optional[str] = None) -> Optional[str]:
        """把附图复制成公网可直链访问的副本，返回 URL（失败返回 None）。

        2026-10-29 起因：平台媒体下载接口给出的是 **application/octet-stream** 的预签名
        S3 链接，且 32KB 的图要 5s+ 才下载完 —— 手机端图片消息一直转圈点不开（PC 端有缓存才正常）。
        这条链路完全在我们自己服务器上（同域、秒开、Content-Type 正确），作为移动端兜底。
        文件名用 uuid4 hex，不可枚举；目录只放附图副本，不含任何业务数据。

        2026-10-29 下午 H5 图文页：同时写一个 ``{name}.title`` 文本文件存图片名称，
        /h5/img/{name} 渲染 HTML 页时读它做标题（与图片同生同死，随 30 天清理一起删）。
        """
        try:
            src = Path(src_path)
            if not src.is_file():
                return None
            data_dir = Path(self.state.data_dir)
            pub_dir = data_dir / "public_img"
            pub_dir.mkdir(parents=True, exist_ok=True)
            suffix = src.suffix.lower()
            if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                suffix = ".jpg"
            name = f"{uuid.uuid4().hex}{suffix}"
            shutil.copyfile(src, pub_dir / name)
            # 图片名称写入同名 .title 文件（h5 页面标题用；旧直链无此文件时页面回退默认标题）
            img_title = (image_name or "").strip()
            if img_title:
                try:
                    (pub_dir / f"{name}.title").write_text(img_title, encoding="utf-8")
                except OSError:  # noqa: BLE001
                    pass
            # 顺手清理 30 天前的副本，避免目录无界增长（清不动也不影响本次发布）
            try:
                cutoff = time.time() - 30 * 86400
                for old in pub_dir.iterdir():
                    if old.is_file() and old.stat().st_mtime < cutoff:
                        old.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            base = (self.public_base_url or "").rstrip("/")
            if not base:
                return None
            return f"{base}/h5/img/{name}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("附图公网副本发布失败 %s: %s", src_path, exc)
            return None

    @staticmethod
    def _link_idem(base_idem: str) -> str:
        """附图直链文本的幂等键：由基础 idem 派生，发送侧与门控侧必须用同一份。"""
        return hashlib.sha1(f"{base_idem}|link".encode("utf-8")).hexdigest()

    def _send_notice_media(
        self, *, external_id: str, send_native: Callable[[], SendResult], idem: str,
        template_id: str, instance_id: int, phone: str,
        planned_str: str, now_s: str, stats: Dict[str, int], kind: str,
        once: bool = False,
    ) -> Tuple[bool, bool]:
        """返回（是否继续使用原生路径，本次是否送达）。明确失败才切换投递方式。"""
        previous = self.state.get_reminder_by_idem_key(idem, sent_only=False)
        if previous and str(previous.get("error") or "").startswith(("IMAGE_LINK_FALLBACK:", "MEDIA_LINK_FALLBACK:")):
            return False, False
        prior_attempt = self.state.get_reminder_by_idem_key(idem, sent_only=False)
        if prior_attempt and (prior_attempt.get("error") or "").startswith("MEDIA_LINK_FALLBACK:"):
            return False, False
        task = self._prepare_delivery(
            template_id=template_id, instance_id=instance_id, phone=phone, external_id=external_id,
            kind=kind, planned_str=planned_str, delay_sec=0, idem_key=idem,
            msg_text_fn=lambda: "", cur_dt=parse_dt(now_s), now_s=now_s, stats=stats, claim=True, once=once,
        )
        if task is None:
            return True, False
        fallback = False
        try:
            self._delivery_bucket.acquire()
            if not self._instance_still_deliverable(instance_id, "notice"):
                res = SendResult(ok=False, result="skipped", error="TASK_CLOSED")
            else:
                res = send_native()
                fallback = not res.ok and not getattr(res, "retryable", False)
        except Exception as exc:  # noqa: BLE001
            res = SendResult(ok=False, result="failed", error=f"EXC:{exc}"[:120])
        error = None if res.ok else (res.error or "SEND_FAILED")
        if fallback:
            error = f"MEDIA_LINK_FALLBACK:{error}"
        self.state.complete_claimed_reminder(task["claim_id"], sent_at=now_s if res.ok else None,
                                            result=res.result, error=error[:255] if error else None)
        if fallback:
            stats["failed"] += 1
        return not fallback, res.ok

    def _send_image_link(self, external_id: str, image_url_fn: Callable[[], str], image_name: Optional[str], base_idem: str,
                         template_id: str, instance_id: int, phone: str,
                         planned_str: str, now_s: str, stats: Dict[str, int],
                         notifier: Optional[NotifierProtocol] = None, external_type: str = "user",
                         once: bool = False, *, kind: str = "notice_img_link",
                         action: str = "点击查看") -> bool:
        """同一素材先占位再生成兜底链接和发信。"""
        link_idem = self._link_idem(base_idem)
        task = self._prepare_delivery(
            template_id=template_id, instance_id=instance_id, phone=phone, external_id=external_id,
            kind=kind, planned_str=planned_str, delay_sec=0, idem_key=link_idem,
            msg_text_fn=lambda: "", cur_dt=parse_dt(now_s), now_s=now_s, stats=stats, claim=True, once=once,
        )
        if task is None:
            return False
        try:
            if not self._instance_still_deliverable(instance_id, "notice"):
                res = SendResult(ok=False, result="skipped", error="TASK_CLOSED")
            elif not (url := image_url_fn()):
                res = SendResult(ok=False, result="failed", error="NOTICE_MEDIA_UNAVAILABLE")
            else:
                self._delivery_bucket.acquire()
                display = (image_name or "").strip() or "数据图片"
                res = (notifier or self.notifier).send_text(external_id, f"{display}（{action}）：\n{url}",
                                                           link_idem, receive_type=external_type)
        except Exception as exc:  # noqa: BLE001
            res = SendResult(ok=False, result="failed", error=f"EXC:{exc}"[:120])
        self.state.complete_claimed_reminder(task["claim_id"], sent_at=now_s if res.ok else None,
                                            result=res.result, error=None if res.ok else (res.error or "SEND_FAILED")[:255])
        if not res.ok and res.result != "skipped":
            stats["failed"] += 1
        return res.ok

    def _prepare_delivery(
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
        claim: bool = False,
        once: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """主线程 DB 预处理（幂等占位 + 前置记账），返回「待网络发送任务」或 None（已记账/已跳过）。

        2026-11-13 冲刺模块二：从 _deliver_with_log 拆出前半（纯 DB 部分）——
        多线程并发投递时 **worker 只做网络 IO**，查重占位与记账全部留在主线程串行完成，
        规避 sqlite 单连接跨线程 + 保证「查重→发送→记录」的原子互斥（幂等红线）。
        """
        retry_no = 0 if once else self._resolve_retry_slot(idem_key, cur_dt=cur_dt)
        if retry_no is None:
            return None
        if retry_no == 0 and delay_sec > self.COMPENSATION_WINDOW_SECONDS:
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="missed", retry=0,
                error="COMPENSATION_WINDOW_EXCEEDED", idem_key=idem_key,
            )
            stats["missed"] += 1
            return None
        if not external_id:
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="skipped", retry=retry_no,
                error="UNACTIVATED", idem_key=idem_key,
            )
            stats["skipped"] += 1
            return None
        claim_id = None
        if claim:
            claim_id = self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="pending", error=None,
                retry=retry_no, idem_key=idem_key,
            )
            if claim_id is None:
                return None
        return {
            "template_id": template_id, "instance_id": instance_id, "phone": phone,
            "external_id": external_id, "kind": kind, "planned_str": planned_str,
            "idem_key": idem_key, "retry_no": retry_no, "msg_text": msg_text_fn(),
            "now_s": now_s, "claim_id": claim_id,
        }

    def _instance_still_deliverable(self, instance_id: int, kind: str) -> bool:
        instance = self.state.get_instance(instance_id)
        if not instance:
            return False
        statuses = {
            "notice": ("pending", "sent"),
            "notice_receipt": ("pending", "sent"),
            "remind": ("pending",),
            "overdue": ("overdue",),
            "manual_push": ("pending", "overdue", "rejected", "submitted", "approved"),
        }
        if instance["status"] not in statuses[kind]:
            return False
        template = self.state.get_template(instance["template_id"])
        if template and template.get("is_manual"):
            if effective_instance_deadline(instance) <= format_dt(self.now()):
                return False
            if not self.state._template_owner_enabled(template["owner_id"]) or not self.state._template_platform_allowed(template):
                return False
        return bool(template and not template.get("is_adhoc")
                    and self.state.instance_close_reason(instance, template) is None)

    def _record_delivery(self, task: Dict[str, Any], send_res: Optional[SendResult], stats: Dict[str, int]) -> bool:
        """主线程记账（worker 纯网络 IO，DB 全部回主线程串行写）。返回是否真的发出。"""
        ok = bool(send_res is not None and send_res.ok)
        outcome = {"sent_at": task["now_s"] if ok else None, "result": send_res.result if send_res else "failed",
                   "error": send_res.error if send_res else "CIRCUIT_OPEN"}
        if task.get("claim_id") is not None:
            with self.state.conn:
                self.state.complete_claimed_reminder(task["claim_id"], **outcome)
                if ok and task.get("receipt_prompt"):
                    self.state.mark_receipt_prompt_sent(task["instance_id"], task["now_s"])
        else:
            self.state.record_reminder_log(
                template_id=task["template_id"], instance_id=task["instance_id"], phone=task["phone"],
                kind=task["kind"], planned_at=task["planned_str"], attempted_at=task["now_s"],
                retry=task["retry_no"], idem_key=task["idem_key"], **outcome,
            )
        if send_res is not None and send_res.result == "skipped":
            stats["skipped"] += 1
        elif ok:
            stats["sent"] += 1
        else:
            stats["failed"] += 1
            if send_res is not None and send_res.need_activate:
                self.state.mark_delivery_target_unusable(task["phone"])
        return ok

    def _deliver_batch_concurrent(self, tasks: List[Dict[str, Any]], stats: Dict[str, int]) -> List[bool]:
        """2026-11-13 冲刺模块二：**受控并发投递引擎**（定时链与一键推送共用）。

        · 8 工作线程（IMT_DELIVERY_THREADS，规范 8~10）：worker 只做网络发送，规避 sqlite 跨线程；
        · 全局令牌桶 25 QPS（IMT_DELIVERY_QPS）：2665 人总投递 ≤ ~107s（< 120s 闸门），流量平滑不触发 429；
        · 连续失败熔断（IMT_DELIVERY_BREAKER_N，默认 10）：下游通道连续超时/不可用时短路剩余批次
          （剩余任务记 failed/CIRCUIT_OPEN，防慢连接耗尽线程拖垮后台与 H5）；
        · 幂等红线：retry slot 在主线程预处理阶段已占定（每任务唯一），记录在主线程串行完成——
          任何情况下（含连跑多周期/中途重启）同周期同成员绝不重复收到两条。
        """
        if not tasks:
            return []
        bucket = getattr(self, "_delivery_bucket", None)
        breaker = getattr(self, "_delivery_breaker", None)
        if bucket is None or breaker is None:
            bucket = TokenBucket(rate_per_sec=delivery_qps())
            breaker = CircuitBreaker(fail_threshold=breaker_threshold())
            self._delivery_bucket, self._delivery_breaker = bucket, breaker

        def _send_worker(task: Dict[str, Any]) -> Optional[SendResult]:
            # 熔断开启 → 剩余任务快速短路（不占桶、不睡网络），主线程按 CIRCUIT_OPEN 记账
            if breaker.is_open():
                return None
            # 令牌桶挡在**真实网络发送前**（worker 侧全局限流；主线程只负责喂任务不阻塞）
            bucket.acquire()
            if (task.get("instance_id") is not None
                    and task["kind"] in ("notice", "notice_receipt", "remind", "overdue", "manual_push")
                    and not self._instance_still_deliverable(int(task["instance_id"]), task["kind"])):
                return SendResult(ok=False, result="skipped", error="TASK_CLOSED")
            res = task["send_fn"](task["external_id"], task["msg_text"], task["idem_key"])
            if res and res.ok and task.get("receipt_prompt"):
                try:
                    self.state.mark_receipt_prompt_sent(task["instance_id"], task["now_s"])
                except Exception:  # noqa: BLE001
                    logger.exception("通知回执投递时间写入失败 instance=%s", task["instance_id"])
            return res

        results: List[Optional[Tuple[Dict[str, Any], Optional[SendResult]]]] = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=delivery_threads()) as pool:
            futures = [pool.submit(_send_worker, t) for t in tasks]
            for idx, fut in enumerate(futures):
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001 - 通道抛异常按发送失败记账（不拖垮整批）
                    logger.warning("delivery worker exception idem=%s: %s", tasks[idx]["idem_key"], exc)
                    res = SendResult(ok=False, result="failed", error=f"EXC:{exc}"[:120], need_activate=False)
                if res is not None and res.ok:
                    breaker.record_success()
                elif res is None and breaker.is_open():
                    breaker.record_failure()  # 熔断开启后的剩余任务按失败记账（保持节奏统计）
                elif res is not None and not res.ok and res.result != "skipped":
                    breaker.record_failure()
                results[idx] = (tasks[idx], res)

        # 2026-11-16 方案B：MySQL 下逐条「INSERT+commit」每条 ~6ms fsync（SQLite 同进程写
        # 几乎零开销，2665 条批量投递曾从 ~110s 涨到 ~150s）——记账改走**同线程事务段 +
        # 每 500 条分片提交**：常规路径 fsync 从 2665 次降到 ~6 次；崩溃窗口（已发送未记账
        # 的最大行数）从单条扩到 ≤500 条，幂等护栏（发送前的查重判断）不受影响。
        if not results:
            return []
        ok_flags: List[bool] = []
        n_in_tx = 0
        with self.state.conn:
            for t, res in results:
                ok_flags.append(self._record_delivery(t, res, stats))
                n_in_tx += 1
                if n_in_tx >= 500:
                    self.state.conn.commit()  # 分片落盘（控崩溃窗口）
                    n_in_tx = 0
        # `with` 退出时最后一片自动 commit
        return ok_flags

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
    ) -> bool:
        """发一条并记账；返回是否**真的发出去了**（True=result 为 sent）。

        2026-10-28 方案A：notice 实例需要据此把状态从 pending 改成 sent。
        2026-11-13 冲刺模块二：实现重构为「主线程预处理 → 单任务直发 → 主线程记账」，
        与 _deliver_batch_concurrent 共用同一套幂等/记账语义。
        """
        task = self._prepare_delivery(
            template_id=template_id, instance_id=instance_id, phone=phone, external_id=external_id,
            kind=kind, planned_str=planned_str, delay_sec=delay_sec, idem_key=idem_key,
            msg_text_fn=msg_text_fn, cur_dt=cur_dt, now_s=now_s, stats=stats,
        )
        if task is None:
            return False
        active_send = send_fn or self.notifier.send_text
        send_res = active_send(task["external_id"], task["msg_text"], task["idem_key"])
        return self._record_delivery(task, send_res, stats)

    def send_reject_notification(
        self, instance: Dict[str, Any], reason: str, now_dt: Optional[datetime] = None
    ) -> Optional[SendResult]:
        """§11 & R27：审核驳回时向提交人发送重新提交通知（带新签发 H5 令牌与重交截止时间）。"""
        cur_dt = now_dt or self.now()
        now_s = format_dt(cur_dt)
        tpl = self.state.get_template(instance["template_id"])
        if not tpl:
            return None
        reviewed_tag = (instance.get("reviewed_at") or now_s).replace(" ", "T")
        base_idem = hashlib.sha1(
            f"{instance['template_id']}|{instance['phone']}|{instance['period_key']}|reject|{reviewed_tag}".encode("utf-8")
        ).hexdigest()

        h5_url, _ = self.build_h5_submit_url(
            phone=instance["phone"], template_id=instance["template_id"],
            period_key_val=instance["period_key"], instance_id=int(instance["id"]),
        )
        # 2026-11-12 任务E1：无截止（哨兵）模板被驳回 → 重交窗口同样「不限」，
        # 文案不带「请在 X 前重交」，避免「请在 不限 前重新提交」的病句。
        if is_no_deadline(str(instance.get("resubmit_deadline") or instance.get("deadline_at") or "")):
            window_line = "本任务无截止时间，请按审核意见尽快重新提交。"
        else:
            resubmit_disp = format_deadline_display(instance["resubmit_deadline"], ref_dt=cur_dt)
            window_line = f"请在 {resubmit_disp} 前重新提交。"
        msg_text = f"【{tpl['name']}】提交被驳回\n原因：{reason}\n{window_line}\n👉 重新提交：{h5_url}"

        # 多通道：任务属于哪个办公软件就只发哪个（2026-11-03 口径），各通道独立幂等。
        # 2026-11-10 方案A：按绑定的 external_type 传 receive_type（union_id 不依赖会话）。
        last_res: Optional[SendResult] = None
        any_target = False
        for platform, ext_id, ext_type, nt in self._targets_for(instance["phone"], only_platform=self._template_platform(tpl)):
            any_target = True
            idem = self._idem_for(base_idem, platform)
            if not ext_id:
                self.state.record_reminder_log(
                    template_id=instance["template_id"], instance_id=int(instance["id"]), phone=instance["phone"],
                    kind="reject", planned_at=now_s, attempted_at=now_s, sent_at=None, result="skipped",
                    retry=0, error="UNACTIVATED", idem_key=idem,
                )
                continue
            # 发送前查重：重复触发（重复点驳回、重放）不会二次打扰提交人。
            if self.state.has_sent_reminder(idem):
                continue
            send_res = nt.send_text(ext_id, msg_text, idem, receive_type=ext_type)
            last_res = send_res
            self.state.record_reminder_log(
                template_id=instance["template_id"], instance_id=int(instance["id"]), phone=instance["phone"],
                kind="reject", planned_at=now_s, attempted_at=now_s, sent_at=now_s if send_res.ok else None,
                result=send_res.result, retry=0, error=send_res.error, idem_key=idem,
            )
            if not send_res.ok and send_res.need_activate:
                self.state.mark_delivery_target_unusable(instance["phone"], platform)
        if not any_target:
            self.state.record_reminder_log(
                template_id=instance["template_id"], instance_id=int(instance["id"]), phone=instance["phone"],
                kind="reject", planned_at=now_s, attempted_at=now_s, sent_at=None, result="skipped",
                retry=0, error="UNACTIVATED", idem_key=base_idem,
            )
        return last_res

    def push_now(self, template_id: str, actor: str = "system", now_dt: Optional[datetime] = None) -> Dict[str, int]:
        """2026-11-12 任务D：上传任务**一键推送**（创建者分级管理员专属测试按钮）。

        用户拍板语义（原话）：手动推送一条 + 当期定时提醒一条**内容一模一样的双发**
        ——测试场景用，管理员自行判断。实现刻意分链防重，两互不干扰、各自幂等：
          · 定时链 run_once：kind='remind'，planned_str=当天 remind_at 执行时刻
          · 手动链 push_now：kind='manual_push'，幂等键含「推送时刻 + 手机号序号」
        守卫：
          · 仅上传类模板（form/image/file）；notice 按 notice_times 自动发，拒绝手动推
          · 绕过锚点日/「推送时刻 < 提醒时刻」门：非锚点日也允许补建当期实例（软护栏另设）
          · 2026-11-12 下午：覆盖 pending/overdue/rejected/**submitted/approved**（用户拍板：
            立即推送支持已提交任务再次推送，便于催办/自测）；cancelled/expired/已过截止/成员停用不发送
          · 同一次调用内每 (phone,seq) 只发一次（_deliver_with_log 首试 retry=0）；
            发送失败留给 reminder_log 退避机制，不在本轮重试
          · 同模板冷却护栏在 state.check_manual_push_cooldown（API 层调用），本方法不重复判

        2026-11-13 修复一（2600 人推送防卡死）：拆成 prepare（校验+当期实例生成）+
        deliver（实际投递）两段；本方法 = 两段顺序执行，行为与历史完全一致（调度层直调、
        既有用例零改动）。API 层改为「请求内 prepare + 后台线程 deliver」——25QPS 下
        2600 人投递约 108 秒，绝不放进事件循环（否则 H5 白屏、网关 60s 超时）。
        """
        prep = self.push_now_prepare(template_id, actor=actor, now_dt=now_dt)
        return self.push_now_deliver(template_id, prep, actor=actor)

    def push_now_prepare(self, template_id: str, actor: str = "system",  # noqa: ARG002
                         now_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """2026-11-13 修复一：一键推送·准备段（校验 + 当期实例生成，**不发送任何消息**）。

        返回 {template_id, pushed_at, period_key, deadline_at, instance_count}：
          · pushed_at = 时间基线，也是幂等键种子——deliver 必须用它保持时间一致；
          · 幂等键语义不变：同一个 prep 重放只会命中既有幂等记录，绝不二次发送。
        本段只做毫秒~秒级 DB 操作，供 API 请求内安全执行（asyncio.to_thread）。
        """
        cur_dt = now_dt or self.now()
        if cur_dt.tzinfo is None:
            cur_dt = cur_dt.replace(tzinfo=SHANGHAI_TZ)
        now_s = format_dt(cur_dt)
        tpl = self.state.get_template(template_id, include_deleted=False)
        if not tpl or not tpl.get("enabled") or tpl.get("deleted"):
            raise BizError(404, "NOT_FOUND", "任务模板不存在或未启用")
        manual = tpl.get("is_manual", False)
        if tpl["kind"] == "notice" and not manual:
            raise BizError(400, "VALIDATION_FAILED", "通知类模板按通知时刻自动发送，不支持手动推送")
        # 2026-11-12 晚 · 不定期任务（菜单自选）：Pull 模式，与定时调度物理隔离——
        # 绝不主动推送催报，成员只能通过私聊【菜单】自主获取填报链接。
        if tpl.get("is_adhoc"):
            raise BizError(400, "VALIDATION_FAILED", "自主上报不支持推送，请在个人工作台发起上报")
        p_key = "once" if manual else period_key(tpl["period"], cur_dt)
        deadline_at = calc_instance_deadline(tpl, cur_dt)
        if tpl["kind"] == "notice":
            deadline_at = format_dt(cur_dt + timedelta(hours=24))
        if manual and deadline_at <= now_s:
            raise BizError(400, "INVALID_DEADLINE", "任务已到截止时间，无法推送")
        # 非锚点日也补建当期缺的实例（绕过 is_template_anchor_day / planned_dt 时间门）
        instances = self.state.ensure_template_instances(
            tpl["template_id"], p_key, deadline_at, now_str=now_s, trigger_at=now_s,
        )
        return {
            "template_id": tpl["template_id"], "pushed_at": now_s, "period_key": p_key,
            "deadline_at": deadline_at, "instance_count": len(instances),
        }

    def push_now_deliver(self, template_id: str, prep: Dict[str, Any],
                         actor: str = "system") -> Dict[str, int]:  # noqa: ARG002
        """2026-11-13 修复一：一键推送·投递段（后台线程执行，绝不占用事件循环）。

        prep 来自 push_now_prepare；p_key/pushed_at 固定（重放同 prep → 幂等 0 新增）。
        投递走 _deliver_batch_concurrent（8 线程 + 25QPS 桶 + 熔断）：2665 人 ≈ 107s，
        这段在网络线程池里等待，调用方（后台线程）阻塞无妨，事件循环完全自由。
        """
        cur_dt = parse_dt(str(prep.get("pushed_at") or ""))
        if cur_dt.tzinfo is None:
            cur_dt = cur_dt.replace(tzinfo=SHANGHAI_TZ)
        now_s = format_dt(cur_dt)
        p_key = str(prep.get("period_key") or "")
        tpl = self.state.get_template(template_id, include_deleted=False)
        if not tpl or not tpl.get("enabled") or tpl.get("deleted"):
            raise BizError(404, "NOT_FOUND", "任务模板不存在或未启用")
        manual = tpl.get("is_manual", False)
        if tpl["kind"] == "notice" and not manual:
            raise BizError(400, "VALIDATION_FAILED", "通知类模板按通知时刻自动发送，不支持手动推送")
        if (manual and p_key != "once") or tpl.get("is_adhoc"):
            raise BizError(400, "VALIDATION_FAILED", "任务的推送配置已变更")
        instances = self.state.list_instances_for_period(tpl["template_id"], p_key)

        if tpl["kind"] == "notice":
            return self._push_manual_notice(tpl, instances, cur_dt)

        stats = {"sent": 0, "skipped": 0, "failed": 0, "pushed_instances": 0}
        push_seq: Dict[str, int] = {}
        for inst in instances:
            inst_status = str(inst["status"] or "")
            # 2026-11-12 下午（用户拍板，二-1）：立即推送**覆盖已提交/已审核通过**——
            # pending / overdue / rejected / submitted / approved 均正常发送并签发有效链接
            # （催办与自测场景）；cancelled（任务已关闭）与 expired（过期归档）不打扰。
            if inst_status not in ("pending", "overdue", "rejected", "submitted", "approved"):
                continue
            # 已过截止不追发（矩阵）；rejected 以「驳回 + 24h」重交窗口为有效截止
            eff_dl = (calc_resubmit_deadline(str(inst["deadline_at"] or ""), inst.get("reviewed_at"))
                      if inst_status == "rejected" else str(inst["deadline_at"] or ""))
            if eff_dl <= now_s:
                continue  # 已过截止：不追发
            # 成员账号被停用/移出 → 不推送（四-5 口径）
            if not self.state.is_member_active_for_roles(str(inst["phone"]), tpl.get("role_ids") or []):
                continue
            phone = str(inst["phone"])
            seq = push_seq.get(phone, 0) + 1
            push_seq[phone] = seq

            def _make_push_msg(instance=inst, template=tpl) -> str:
                rendered = render_text_tpl(
                    template["text_tpl"], member_name=instance["name_snap"], member_dept=instance["dept_snap"],
                    template_name=template["name"], deadline_at=instance["deadline_at"], period=template["period"], ref_dt=cur_dt,
                )
                h5_url, _ = self.build_h5_submit_url(
                    phone=instance["phone"], template_id=template["template_id"],
                    period_key_val=instance["period_key"], instance_id=int(instance["id"]),
                )
                dl_disp = format_deadline_display(instance["deadline_at"], ref_dt=cur_dt)
                # 与 run_once 定时提醒同版式（同名内容双发是用户拍板的特批场景）
                return (
                    f"【任务提醒】{template['name']}\n{rendered.rstrip()}\n\n"
                    f"截止时间：{dl_disp}\n"
                    f"提交入口（点击链接打开上传任务）：\n{h5_url}"
                )

            # 2026-11-13 冲刺模块二：一键推送改走受控并发引擎（8 线程 + 25QPS 桶 + 熔断）——
            # 2665 人整点高峰单轮投递压进 2 分钟内；幂等占位/消息渲染/记账全在主线程。
            # 2026-11-16 方案B：prepare 段包事务（同连接复用）；⛔ 保持「逐实例投递 +
            # 逐实例 pushed_instances 计数」语义不变（分片聚合会把计数压成每批 1，
            # test_d_push_now / livesync / fix1_split 三组用例实锤拦截）。
            batch: List[Dict[str, Any]] = []
            with self.state.conn:
                for platform, ext_id, ext_type, nt in self._targets_for(inst["phone"], only_platform=self._template_platform(tpl)):
                    # 幂等键带推送时刻 + 同号序号：同一次推送内不同实例不撞键；
                    # 同一秒内的重复调用彼此命中同一键 → 被 _resolve_retry_slot 拦下（快速双击防抖）。
                    base_idem = make_idem_key(tpl["template_id"], phone, p_key, "manual_push", seq,
                                              "" if manual else f"{now_s}|push|{seq}")
                    idem = self._idem_for(base_idem, platform)
                    task = self._prepare_delivery(
                        template_id=tpl["template_id"], instance_id=int(inst["id"]), phone=inst["phone"],
                        external_id=ext_id, kind="manual_push", planned_str=now_s, delay_sec=0,
                        idem_key=idem, msg_text_fn=_make_push_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                        claim=manual, once=manual,
                    )
                    if task is None:
                        continue
                    task["send_fn"] = functools.partial(nt.send_text, receive_type=ext_type)
                    batch.append(task)
            oks = self._deliver_batch_concurrent(batch, stats)
            if any(oks):
                stats["pushed_instances"] += 1
        return stats

    @staticmethod
    def _notice_body(template, instance, now, values):
        return render_text_tpl(
            template["text_tpl"], member_name=instance["name_snap"], member_dept=instance.get("dept_snap"),
            template_name=template["name"], deadline_at=None, period=template["period"], ref_dt=now, extra_vars=values,
        ).strip()

    def _notice_images(self, tpl, period, seq, now_s, values):
        """优先使用通知条目中的所有上传图片，再兼容旧字段和旧数据看板。"""
        from .notice_media import asset_path, get_asset

        root = Path(self.state.data_dir).resolve()
        primary = str(tpl.get("attach_image_path") or "").strip()
        spec = tpl.get("spec_json")
        items = spec.get("notice_items") if isinstance(spec, dict) else None
        images = {}
        emitted = set()
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("format") == "image" and item.get("asset_id"):
                    asset_id = item["asset_id"]
                    asset = get_asset(self.state, asset_id)
                    path = asset_path(self.state, asset) if asset and asset["owner_id"] == tpl["owner_id"] else None
                    if asset_id not in emitted:
                        emitted.add(asset_id)
                        yield str(path) if path else None, asset["file_name"] if asset else None, asset_id, False
                if isinstance(item, dict) and item.get("format") in ("file", "image") and item.get("image_path"):
                    rel = str(item["image_path"]).strip()
                    if re.fullmatch(r"notice_attach/[A-Za-z0-9_.-]+\.(?:png|jpe?g|webp)", rel, re.IGNORECASE):
                        if (root / rel).resolve().is_relative_to((root / "notice_attach").resolve()):
                            images.setdefault(rel, item.get("file_caption") or item.get("file_name"))
        if not images and primary:
            images[primary] = tpl.get("attach_image_name")
        if images or emitted:
            for rel, title in images.items():
                target = (root / rel).resolve()
                if (target.is_relative_to(root) and target.is_file()
                        and target.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")):
                    yield str(target), title, rel, rel == primary
            return

        data_keys = tpl.get("data_keys") or []
        if not tpl.get("attach_image") or not any(str(values.get(dk["key"]) or "").strip() for dk in data_keys):
            return
        try:
            from .dashboard import render_snapshot_dashboard

            target = root / "dashboards" / f"{tpl['template_id']}_{period}_{seq}.png"
            render_snapshot_dashboard(title=f"{tpl['name']} · 数据看板", subtitle=f"{period} · 春促按角色提醒",
                                      data_keys=data_keys, values=values, out_path=str(target),
                                      note=f"数据截至 {now_s} · 数据来源：人工填报")
        except Exception as exc:
            logger.warning("通知看板生成失败 template=%s: %s", tpl["template_id"], exc)
            return
        yield str(target), tpl.get("attach_image_name"), "dashboard", True

    def _notice_asset_url(self, instance, asset_id: str) -> str:
        if not self.public_base_url:
            return ""
        token = self.state.issue_h5_token(instance["phone"], instance["template_id"], asset_id, int(instance["id"]),
                                          purpose="notice_asset", ttl_hours=168)
        return f"{self.public_base_url}/h5/notice-media?token={token}"

    @staticmethod
    def _native_notice_sender(notifier, source, asset, external_id, external_type, idem, prepared, channel):
        kind = asset["kind"]
        prepare = getattr(notifier, "prepare_notice_attachment", None)
        if callable(prepare):
            def send_prepared():
                if channel not in prepared:
                    try:
                        prepared[channel] = prepare(source, asset)
                    except Exception as exc:  # noqa: BLE001
                        prepared[channel] = SendResult(False, "failed", retryable=False, error=f"UPLOAD_FAILED:{exc}")
                fields = prepared[channel]
                if isinstance(fields, SendResult):
                    return fields
                return notifier.send_prepared_notice(external_id, kind, fields, idem, external_type)

            return send_prepared
        if kind == "image" and channel != "zdxlz" and callable(getattr(notifier, "send_image", None)):
            return functools.partial(notifier.send_image, external_id, source, idem)
        send = getattr(notifier, "send_notice_attachment", None)
        return functools.partial(send, external_id, source, asset, idem, external_type) if callable(send) else None

    def _notice_media_parts(self, tpl, period, seq, now_s, values):
        from .notice_media import asset_path, get_asset

        for source, title, key, legacy in self._notice_images(tpl, period, seq, now_s, values):
            yield source, {"kind": "image", "file_name": title or "数据图片"}, key, legacy

        items = (tpl.get("spec_json") or {}).get("notice_items") or []
        for kind in ("file", "video"):
            for item in items:
                if item.get("format") != kind or not item.get("asset_id"):
                    continue
                key = item["asset_id"]
                asset = get_asset(self.state, key)
                source = asset_path(self.state, asset) if asset and asset["owner_id"] == tpl["owner_id"] else None
                yield str(source) if source else None, asset or {"kind": kind}, key, False

    def _deliver_notice_media_part(self, tpl, targets, period, seq, planned_str, now_s, stats, once,
                                   source, asset, key, legacy):
        prepared, delivered = {}, set()
        kind = asset["kind"]
        if not source:
            logger.warning("通知附件不可用 template=%s asset=%s", tpl["template_id"], key)
            targets.clear()
            return delivered

        link_url = None

        def publish_image_link():
            nonlocal link_url
            if link_url is None:
                link_url = self._publish_image_link(source, asset["file_name"]) or ""
            return link_url

        media_kind = "notice_img" if kind == "image" else f"notice_{kind}"
        link_kind, action = {
            "image": ("notice_img_link", "点击查看"),
            "file": ("notice_file_link", "点击下载"),
            "video": ("notice_vid_link", "点击播放"),
        }[kind]
        plan = f"{'once' if once else planned_str}|{key}"
        for target, (inst, external_id, external_type, notifier) in list(targets.items()):
            instance_id, channel = target

            def idem_for(part, when):
                return self._idem_for(make_idem_key(tpl["template_id"], inst["phone"], period,
                                                    part, seq, when), channel)

            media_idem = idem_for(media_kind, plan)
            link_base = idem_for(link_kind, plan) if kind == "image" else media_idem
            known_sent = self.state.has_sent_reminder
            if known_sent(media_idem) or known_sent(self._link_idem(link_base)):
                continue
            if legacy and not once and (known_sent(idem_for(media_kind, planned_str))
                                        or known_sent(self._link_idem(idem_for(link_kind, planned_str)))):
                continue
            native = self._native_notice_sender(
                notifier, source, asset, external_id, external_type, media_idem, prepared, channel,
            )
            handled, sent = False, False
            if native:
                handled, sent = self._send_notice_media(
                    external_id=external_id, send_native=native, idem=media_idem, template_id=tpl["template_id"],
                    instance_id=instance_id, phone=inst["phone"], planned_str=planned_str, now_s=now_s,
                    stats=stats, kind=media_kind, once=once,
                )
            if not handled:
                url = (functools.partial(self._notice_asset_url, inst, key)
                       if re.fullmatch(r"[0-9a-f]{32}", key) else publish_image_link)
                sent = self._send_image_link(
                    external_id, url, asset["file_name"], link_base, tpl["template_id"], instance_id,
                    inst["phone"], planned_str, now_s, stats, notifier, external_type, once,
                    kind=link_kind, action=action,
                )
            if sent:
                if once:
                    stats["sent"] += 1
                delivered.add(instance_id)
            elif not (known_sent(media_idem) or known_sent(self._link_idem(link_base))):
                targets.pop(target)
        return delivered

    def _deliver_notice_text_part(self, tpl, targets, period, seq, planned_str, now, stats, *,
                                  values, once, receipt=False):
        batch, batch_targets = [], []
        now_s = format_dt(now)
        kind = "notice_receipt" if receipt else "notice"
        pending = list(targets.items())
        for offset in range(0, len(pending), 400):
            with self.state.conn:
                for target, (inst, external_id, external_type, notifier) in pending[offset:offset + 400]:
                    instance_id, channel = target
                    if receipt:
                        phrase = inst.get("receipt_phrase")
                        prior_prompt = inst.get("receipt_prompt_at")
                        if not phrase or (prior_prompt and (once or prior_prompt >= planned_str)):
                            continue
                        message = (f"【德阳量密助手】以上为《{tpl['name']}》通知内容，"
                                   f"查收后请直接回复「{phrase}」即可完成确认登记。")
                    else:
                        message = self._notice_body(tpl, inst, now, values)
                        if not message:
                            continue
                    # 一次性通知正文沿用旧键，已发出的历史通知保持去重。
                    base = (make_idem_key(tpl["template_id"], inst["phone"], period, kind)
                            if once else make_idem_key(tpl["template_id"], inst["phone"], period, kind, seq, planned_str))
                    idem = self._idem_for(base, channel)
                    task = self._prepare_delivery(
                        template_id=tpl["template_id"], instance_id=instance_id, phone=inst["phone"],
                        external_id=external_id, kind=kind, planned_str=planned_str, delay_sec=0,
                        idem_key=idem, msg_text_fn=lambda: message, cur_dt=now, now_s=now_s,
                        stats=stats, claim=True, once=once,
                    )
                    if task:
                        task["send_fn"] = functools.partial(notifier.send_text, receive_type=external_type)
                        task["receipt_prompt"] = receipt
                        batch.append(task)
                        batch_targets.append(target)
                    elif not self.state.has_sent_reminder(idem):
                        targets.pop(target)
        delivered = set()
        for target, sent in zip(batch_targets, self._deliver_batch_concurrent(batch, stats)):
            if sent:
                delivered.add(target[0])
            else:
                targets.pop(target)
        return delivered

    def _deliver_notice(self, tpl, instances, period, seq, planned_str, now, values, stats, *, once=False):
        platform = self._template_platform(tpl)
        targets = {
            (int(inst["id"]), channel): (inst, external_id, external_type, notifier)
            for inst in instances
            for channel, external_id, external_type, notifier in self._targets_for(inst["phone"], only_platform=platform)
            if external_id
        }
        delivered = self._deliver_notice_text_part(
            tpl, targets, period, seq, planned_str, now, stats, values=values, once=once,
        )
        now_s = format_dt(now)
        if targets:
            for part in self._notice_media_parts(tpl, period, seq, now_s, values):
                delivered.update(self._deliver_notice_media_part(
                    tpl, targets, period, seq, planned_str, now_s, stats, once, *part,
                ))
                if not targets:
                    break
        delivered.update(self._deliver_notice_text_part(
            tpl, targets, period, seq, planned_str, now, stats, values=values, once=once, receipt=True,
        ))
        for instance_id in delivered:
            self.state.update_instance_status(instance_id, "sent")
        return len(delivered)

    def _push_manual_notice(self, tpl, instances, now):
        stats = {"sent": 0, "skipped": 0, "failed": 0, "pushed_instances": 0}
        instances = [inst for inst in instances if inst["status"] in ("pending", "sent")
                     and self.state.instance_close_reason(inst, tpl) is None]
        values = self.state.get_data_snapshot_values(tpl["template_id"], period_key(tpl["period"], now)) if tpl.get("data_keys") else {}
        stats["pushed_instances"] = self._deliver_notice(
            tpl, instances, "once", 1, format_dt(now), now, values, stats, once=True,
        )
        return stats

    def run_once(self, now_dt: Optional[datetime] = None) -> Dict[str, int]:
        """执行一轮调度扫描（§6.1 A/B/C/D + R14 逾期通知），返回本轮统计计数。"""
        cur_dt = now_dt or self.now()
        if cur_dt.tzinfo is None:
            cur_dt = cur_dt.replace(tzinfo=SHANGHAI_TZ)
        now_s = format_dt(cur_dt)
        stats = {"generated_instances": 0, "sent": 0, "skipped": 0, "missed": 0, "failed": 0, "to_overdue": 0, "to_expired": 0}

        from .portal import run_portal_tasks
        run_portal_tasks(self, cur_dt)

        for tpl in self.state.list_active_templates(include_notice=True):
            if not tpl["role_ids"] or not is_template_anchor_day(tpl, cur_dt):
                continue

            if tpl["kind"] == "notice":
                p_key = period_key(tpl["period"], cur_dt)
                members = self.state.get_dedup_enabled_members_for_roles(tpl["role_ids"])
                data_keys = tpl.get("data_keys") or []
                snap_values: Dict[str, str] = {}
                if data_keys:
                    snap_values = self.state.get_data_snapshot_values(tpl["template_id"], p_key)

                # 2026-10-28 方案A（按业务方新要求调整）：通知类**当天一进来就按人生成实例**（pending），
                # 到点发出后置 sent。这样「任务实例」页能看到今天所有通知归了谁，
                # 不会只剩已完成的那几条。deadline 用当天 23:59 占位，不参与 overdue 流转；
                # 到当天结束仍未发出的，由 advance_instance_statuses 标成 expired（未送达）。
                notice_deadline = calc_deadline_at(cur_dt, "23:59", 0, "day")
                before_cnt = len(self.state.list_instances_for_period(tpl["template_id"], p_key))
                instances = self.state.ensure_template_instances(tpl["template_id"], p_key, notice_deadline)
                stats["generated_instances"] += max(0, len(instances) - before_cnt)
                enabled_phones = {member["phone"] for member in members}
                instances = [inst for inst in instances if inst["phone"] in enabled_phones
                             and inst["status"] in ("pending", "sent")]

                for seq_idx, nt_hhmm in enumerate(tpl.get("notice_times") or [], start=1):
                    planned_dt = self._build_planned_dt(cur_dt, nt_hhmm)
                    if cur_dt < planned_dt:
                        continue
                    planned_str = format_dt(planned_dt)
                    delay_sec = (cur_dt - planned_dt).total_seconds()

                    # 整跳已超出补偿窗口：按原语义记 missed（实例已在上面按天建好，
                    # 到当天结束由 advance_instance_statuses 置 expired=未送达）。
                    # 2026-10-29 P1-4：这里每 30s 会被重扫一次，必须按 idem_key 去重，
                    # 否则同一时点会反复写 missed，日志与 stats["missed"] 一路膨胀。
                    if delay_sec > self.COMPENSATION_WINDOW_SECONDS:
                        for m in members:
                            idem = make_idem_key(tpl["template_id"], m["phone"], p_key, "notice", seq_idx, planned_str)
                            if self.state.list_reminder_attempts(idem):
                                continue  # 该时点已记过（无论 missed/sent/failed），不重复记
                            self.state.record_reminder_log(
                                template_id=tpl["template_id"], instance_id=None, phone=m["phone"], kind="notice",
                                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="missed", retry=0,
                                error="COMPENSATION_WINDOW_EXCEEDED", idem_key=idem,
                            )
                            stats["missed"] += 1
                        continue

                    self._deliver_notice(tpl, instances, p_key, seq_idx, planned_str, cur_dt, snap_values, stats)
                continue

            planned_dt = self._build_planned_dt(cur_dt, tpl["remind_at"])
            p_key = period_key(tpl["period"], cur_dt)

            # 2026-10-29：上传任务也改成「当天一进来就按人生成实例」，
            # 不再等到提醒时刻才建行——否则后台「任务实例与审核」里看不到还没到点的任务
            # （用户反馈：缺陷/上传类模板只有触发后才出现，通知类却是提前就有）。
            before_cnt = len(self.state.list_instances_for_period(tpl["template_id"], p_key))
            instances = self.ensure_instances_for_template(tpl, cur_dt)
            stats["generated_instances"] += max(0, len(instances) - before_cnt)

            if cur_dt < planned_dt:
                continue

            planned_str = format_dt(planned_dt)
            delay_sec = (cur_dt - planned_dt).total_seconds()

            # 2026-11-13 冲刺模块二：定时提醒主链改走受控并发引擎（8 线程 + 25QPS 桶 + 熔断）。
            # 消息渲染（含 H5 链接签发）与幂等占位在主线程完成，worker 只做网络发送。
            # 2026-11-16 方案B：prepare 段改**分片事务**（每 400 实例一批）——逐条借还/逐实例
            # commit 在 MySQL 下每 commit ~6ms fsync，2665 人曾把 prepare 段拖到 23s；
            # 整批单事务又把崩溃窗口（已发消息的 token 未落盘）放大到全批，取 400 分片平衡。
            remind_batch: List[Dict[str, Any]] = []
            _pending = [i for i in instances if i["status"] == "pending"]
            for _c0 in range(0, len(_pending), 400):
                with self.state.conn:
                    for inst in _pending[_c0:_c0 + 400]:

                        def _make_remind_msg(instance=inst, template=tpl) -> str:
                            rendered = render_text_tpl(
                                template["text_tpl"], member_name=instance["name_snap"], member_dept=instance["dept_snap"],
                                template_name=template["name"], deadline_at=instance["deadline_at"], period=template["period"], ref_dt=cur_dt,
                            )
                            h5_url, _ = self.build_h5_submit_url(
                                phone=instance["phone"], template_id=template["template_id"],
                                period_key_val=instance["period_key"], instance_id=int(instance["id"]),
                            )
                            # 2026-10-28：密信不支持文本超链接，链接单独占一行并提示点击打开；
                            # 版式与后台「实时预览」严格一致（预览为纯文本，不再有模块徽章/卡片）。
                            dl_disp = format_deadline_display(instance["deadline_at"], ref_dt=cur_dt)
                            return (
                                f"【任务提醒】{template['name']}\n{rendered.rstrip()}\n\n"
                                f"截止时间：{dl_disp}\n"
                                f"提交入口（点击链接打开上传任务）：\n{h5_url}"
                            )

                        # 多通道：任务属于哪个办公软件就只发哪个（2026-11-03 口径），各通道独立幂等。
                        for platform, ext_id, ext_type, nt in self._targets_for(inst["phone"], only_platform=self._template_platform(tpl)):
                            idem = self._idem_for(
                                make_idem_key(tpl["template_id"], inst["phone"], p_key, "remind", 1, planned_str),
                                platform,
                            )
                            task = self._prepare_delivery(
                                template_id=tpl["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                                kind="remind", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem,
                                msg_text_fn=_make_remind_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                            )
                            if task is None:
                                continue
                            task["send_fn"] = functools.partial(nt.send_text, receive_type=ext_type)
                            remind_batch.append(task)
            self._deliver_batch_concurrent(remind_batch, stats)

        adv = self.state.advance_instance_statuses(cur_dt)
        stats["to_overdue"] = adv["to_overdue"]
        stats["to_expired"] = adv["to_expired"]

        # R14：deadline 后 10 分钟对未交者（overdue）发 1 次逾期通知（kind='overdue'，不重复）
        # 2026-11-13 冲刺模块二：同样走受控并发引擎（批量收集 → 并发投递）。
        overdue_batch: List[Dict[str, Any]] = []
        _overdue = [i for i in self.state.list_overdue_instances()]
        for _c0 in range(0, len(_overdue), 400):
            # 2026-11-16 方案B：prepare 段分片事务（每 400 实例一批 commit；同 remind/push/notice）。
            with self.state.conn:
                for inst in _overdue[_c0:_c0 + 400]:
                    deadline_dt = parse_dt(inst["deadline_at"])
                    overdue_planned_dt = deadline_dt + timedelta(minutes=self.OVERDUE_NOTICE_DELAY_MINUTES)
                    if cur_dt < overdue_planned_dt:
                        continue
                    tpl = self.state.get_template(inst["template_id"])
                    if not tpl or not tpl.get("enabled") or tpl.get("deleted") or tpl.get("is_manual"):
                        continue

                    planned_str = format_dt(overdue_planned_dt)
                    delay_sec = (cur_dt - overdue_planned_dt).total_seconds()
                    def _make_overdue_msg(instance=inst, template=tpl, dl_dt=deadline_dt) -> str:
                        # 2026-11-12 任务E3：「申请补交」全链路下线，逾期通知改为纯截止告知
                        # （不引导任何补交通道）；文案口径与 H5/后端校验统一
                        # 「当前任务已过期（截止时间：…），无法提交」，不区分链接过期与任务截止。
                        return (
                            f"【{template['name']}】已逾期\n"
                            f"你尚未提交。当前任务已过期（截止时间：{instance['deadline_at']}），无法提交。"
                        )

                    # 多通道：任务属于哪个办公软件就只发哪个（2026-11-03 口径），各通道独立幂等。
                    for platform, ext_id, ext_type, nt in self._targets_for(inst["phone"], only_platform=self._template_platform(tpl)):
                        idem_platform = self._idem_for(
                            make_idem_key(inst["template_id"], inst["phone"], inst["period_key"], "overdue", 1, planned_str),
                            platform,
                        )
                        task = self._prepare_delivery(
                            template_id=inst["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                            kind="overdue", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem_platform,
                            msg_text_fn=_make_overdue_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                        )
                        if task is None:
                            continue
                        task["send_fn"] = functools.partial(nt.send_text, receive_type=ext_type)
                        overdue_batch.append(task)
        self._deliver_batch_concurrent(overdue_batch, stats)

        return stats

    tick = run_once
