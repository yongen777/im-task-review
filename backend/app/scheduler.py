# -*- coding: utf-8 -*-
"""周期与时间纯函数工具 + 实例惰性生成与调度循环（严格对齐规格书 §1、§5、§6、§8.1、§11、R10~R27、R47~R49）。"""
from __future__ import annotations

import calendar
import functools
import hashlib
import json
import logging
import os
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
    is_no_deadline,
    is_valid_hhmm,
)

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
    """§11：{截止时间} 转为「今天 18:00」或「10月25日 18:00」。

    2026-11-12 任务E1：无截止哨兵统一显示「不限」（消息文案/H5/后台同口径）。
    """
    if is_no_deadline(deadline_at):
        return "不限"
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
        if not template.get("enabled") or template.get("deleted"):
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
            db_path = getattr(getattr(self.state, "db", None), "db_path", None)
            data_dir = Path(db_path).parent if db_path else Path("data").resolve()
            pub_dir = data_dir / "public_img"
            pub_dir.mkdir(parents=True, exist_ok=True)
            suffix = src.suffix.lower()
            if suffix not in (".jpg", ".jpeg", ".png"):
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

    def _send_image_media(
        self, *, external_id: str, image_path: str, idem: str,
        template_id: str, instance_id: int, phone: str,
        planned_str: str, now_s: str, stats: Dict[str, int],
        notifier: Optional[NotifierProtocol] = None,
    ) -> bool:
        """发一条**原生图片消息**（企微智能机器人，2026-11-02 起支持）。

        返回 True = 这一轮不用再走链接兜底（已发出，或失败但可重试、留给下一轮）；
        返回 False = 永久失败，调用方应降级发 H5 链接，别让用户什么都收不到。

        ⚠️ 幂等门控必须在**发送之前**（2026-11-02 刷屏事故的根因就是这个顺序错了）。
        """
        try:
            if self.state.has_sent_reminder(idem):
                return True
            res = (notifier or self.notifier).send_image(external_id, image_path, idem)
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind="notice_img",
                planned_at=planned_str, attempted_at=now_s,
                sent_at=now_s if res.ok else None, result="sent" if res.ok else "failed",
                retry=0, error=None if res.ok else (res.error or "SEND_FAILED"), idem_key=idem,
            )
            if res.ok:
                return True
            if getattr(res, "retryable", False):
                # 长连接抖动 / 上传频率限制：交给下一个 tick 重试，此时别补发链接（会重复）
                return True
            stats["failed"] = stats.get("failed", 0) + 1
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("附图图片消息发送异常 external=%s: %s", external_id, exc)
            return False

    def _send_image_link(self, external_id: str, image_url: str, image_name: Optional[str], base_idem: str,
                         template_id: str, instance_id: Optional[int], phone: str,
                         planned_str: str, now_s: str, stats: Dict[str, int],
                         notifier: Optional[NotifierProtocol] = None, external_type: str = "user") -> None:
        """附图唯一发送通道：发一条含直链的短文本（图片消息在手机端打不开，2026-10-30 起不再发）。失败只记日志，绝不影响主链路。

        2026-10-29 H5.5：文本改为「{图片名称}（点击查看）：\n{链接}」，
        名称由管理员在模板编辑时填写（attach_image_name），无名称时回退「数据图片」。
        2026-11-10 方案A：external_type 转为 receive_type（union_id 绑定不依赖会话）。
        """
        try:
            link_idem = self._link_idem(base_idem)
            # 2026-11-02 P0 刷屏修复：**发送动作之前**先查重。
            # 原实现是「先发、后记账」，reminder_log 的 UNIQUE 只拦住记录，
            # 消息在 30 秒一轮的调度里已经被发出去了 —— 到点后每轮都发一条。
            if self.state.has_sent_reminder(link_idem):
                return
            display = (image_name or "").strip() or "数据图片"
            text = f"{display}（点击查看）：\n{image_url}"
            res = (notifier or self.notifier).send_text(external_id, text, link_idem, receive_type=external_type)
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind="notice_img_link",
                planned_at=planned_str, attempted_at=now_s,
                sent_at=now_s if res.ok else None, result="sent" if res.ok else "failed",
                retry=0, error=None if res.ok else (res.error or "SEND_FAILED"), idem_key=link_idem,
            )
            if not res.ok:
                stats["failed"] = stats.get("failed", 0) + 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("附图直链文本发送异常 external=%s: %s", external_id, exc)

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
        """
        retry_no = self._resolve_retry_slot(idem_key, cur_dt=cur_dt)
        if retry_no is None:
            return False

        if retry_no == 0 and delay_sec > self.COMPENSATION_WINDOW_SECONDS:
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="missed", retry=0,
                error="COMPENSATION_WINDOW_EXCEEDED", idem_key=idem_key,
            )
            stats["missed"] += 1
            return False

        if not external_id:
            self.state.record_reminder_log(
                template_id=template_id, instance_id=instance_id, phone=phone, kind=kind,
                planned_at=planned_str, attempted_at=now_s, sent_at=None, result="skipped", retry=retry_no,
                error="UNACTIVATED", idem_key=idem_key,
            )
            stats["skipped"] += 1
            return False

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
        return bool(send_res.ok)

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
        """
        cur_dt = now_dt or self.now()
        if cur_dt.tzinfo is None:
            cur_dt = cur_dt.replace(tzinfo=SHANGHAI_TZ)
        now_s = format_dt(cur_dt)
        tpl = self.state.get_template(template_id, include_deleted=False)
        if not tpl or not tpl.get("enabled") or tpl.get("deleted"):
            raise BizError(404, "NOT_FOUND", "任务模板不存在或未启用")
        if tpl["kind"] == "notice":
            raise BizError(400, "VALIDATION_FAILED", "通知类模板按通知时刻自动发送，不支持手动推送")

        p_key = period_key(tpl["period"], cur_dt)
        deadline_at = calc_instance_deadline(tpl, cur_dt)

        # 非锚点日也补建当期缺的实例（绕过 is_template_anchor_day / planned_dt 时间门）
        self.state.ensure_template_instances(tpl["template_id"], p_key, deadline_at, now_str=now_s, trigger_at=now_s)
        instances = self.state.list_instances_for_period(tpl["template_id"], p_key)

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

            for platform, ext_id, ext_type, nt in self._targets_for(inst["phone"], only_platform=self._template_platform(tpl)):
                # 幂等键带推送时刻 + 同号序号：同一次推送内不同实例不撞键；
                # 同一秒内的重复调用彼此命中同一键 → 被 _resolve_retry_slot 拦下（快速双击防抖）。
                base_idem = make_idem_key(
                    tpl["template_id"], inst["phone"], p_key, "manual_push", seq, f"{now_s}|push|{seq}"
                )
                idem = self._idem_for(base_idem, platform)
                ok = self._deliver_with_log(
                    template_id=tpl["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                    kind="manual_push", planned_str=now_s, delay_sec=0, idem_key=idem,
                    msg_text_fn=_make_push_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                    send_fn=functools.partial(nt.send_text, receive_type=ext_type),
                )
                if ok:
                    stats["pushed_instances"] += 1
        return stats

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

                # 2026-10-28 方案A（按业务方新要求调整）：通知类**当天一进来就按人生成实例**（pending），
                # 到点发出后置 sent。这样「任务实例」页能看到今天所有通知归了谁，
                # 不会只剩已完成的那几条。deadline 用当天 23:59 占位，不参与 overdue 流转；
                # 到当天结束仍未发出的，由 advance_instance_statuses 标成 expired（未送达）。
                notice_deadline = calc_deadline_at(cur_dt, "23:59", 0, "day")
                before_cnt = len(self.state.list_instances_for_period(tpl["template_id"], p_key))
                instances = self.state.ensure_template_instances(tpl["template_id"], p_key, notice_deadline)
                stats["generated_instances"] += max(0, len(instances) - before_cnt)

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

                    # 2026-11-02 方案A：实例已在上方按天预生成，这里只负责「到点发送 + 置 sent」。
                    for inst in instances:

                        def _make_notice_msg(instance=inst, template=tpl, planned=planned_dt) -> str:
                            # 2026-10-29 用户定档：通知类消息**只发管理员配置的正文**。
                            # ⛔ 不加「今日通知：」标题、不加【模板名】、不加发送时间、不加任何其它内容。
                            body = render_text_tpl(
                                template["text_tpl"], member_name=instance["name_snap"],
                                member_dept=instance.get("dept_snap"),
                                template_name=template["name"], deadline_at=None, period=template["period"], ref_dt=cur_dt,
                                extra_vars=snap_values,
                            )
                            return body.strip()

                        # 多通道：任务属于哪个办公软件就只发哪个（2026-11-03 口径），各通道独立幂等。
                        sent_any = False
                        for platform, ext_id, ext_type, nt in self._targets_for(inst["phone"], only_platform=self._template_platform(tpl)):
                            idem = self._idem_for(
                                make_idem_key(tpl["template_id"], inst["phone"], p_key, "notice", seq_idx, planned_str),
                                platform,
                            )
                            ok = self._deliver_with_log(
                                template_id=tpl["template_id"], instance_id=int(inst["id"]), phone=inst["phone"],
                                external_id=ext_id, kind="notice", planned_str=planned_str, delay_sec=delay_sec,
                                idem_key=idem, msg_text_fn=_make_notice_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                                send_fn=functools.partial(nt.send_text, receive_type=ext_type),
                            )
                            sent_any = sent_any or ok
                        if sent_any:
                            self.state.update_instance_status(int(inst["id"]), "sent")

                    # 2026-10-28 通知类附图：优先推管理员上传的图片（attach_image_path），
                    # 没上传图时才退回「数据看板自动出图」的旧路径（依赖当天人工填报的快照值）。
                    attach_rel = str(tpl.get("attach_image_path") or "").strip()
                    want_dash = bool(tpl.get("attach_image")) and bool(data_keys)
                    # 2026-11-02 订正：量子密信那条「图片消息手机端点不开」的结论，
                    # **不适用于企业微信** —— 企微机器人发的是原生图片消息，实测点开正常，
                    # 所以企微通道优先发图片（_send_image_media），失败再降级 H5 链接。
                    # 量子密信没有 send_image，仍然走链接，行为不变。
                    if attach_rel or want_dash:
                        img_src_path: Optional[str] = None
                        if attach_rel:
                            try:
                                db_path = getattr(getattr(self.state, "db", None), "db_path", None)
                                base_dir = Path(db_path).parent if db_path else Path("data").resolve()
                                # 2026-10-29 P2-1：运行时再兜一道，越界路径直接跳过，不外读
                                _target = (base_dir / attach_rel).resolve()
                                # 2026-10-29 补丁3：is_relative_to 比字符串前缀更可靠（data_other 不会被误判在 data 内）
                                if not _target.is_relative_to(base_dir.resolve()):
                                    logger.warning("通知附图路径越界已拦截 template=%s path=%s", tpl["template_id"], attach_rel)
                                    raise ValueError("path_out_of_data_dir")
                                img_src_path = str(_target)
                            except Exception as exc:
                                logger.warning("通知附图加载失败 template=%s seq=%s: %s", tpl["template_id"], seq_idx, exc)
                        if img_src_path is None and want_dash:
                            has_any_value = any(
                                str(snap_values.get(str(dk.get("key") or "")) or "").strip() for dk in data_keys
                            )
                            if has_any_value:
                                try:
                                    from .dashboard import render_snapshot_dashboard

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
                                    img_src_path = out_png
                                except Exception as exc:  # 看板渲染/上传失败不影响文本主链路
                                    logger.warning("看板渲染或上传失败 template=%s seq=%s: %s", tpl["template_id"], seq_idx, exc)
                        if img_src_path:
                            # 2026-10-30 用户口径：企业微信发原生图片，量子密信没有 send_image 仍发 H5 链接。
                            # 2026-11-02 多通道：按「该成员的每个绑定通道」分别判断形态与幂等键。
                            img_title = str(tpl.get("attach_image_name") or "").strip() or None
                            # 先筛「本轮真正还需要发的人」：一个人都不需要就既不发布副本、也不发消息。
                            pending: List[tuple] = []
                            for inst in instances:
                                for platform, ext_id, ext_type, nt in self._targets_for(inst["phone"], only_platform=self._template_platform(tpl)):
                                    idem_link = self._idem_for(make_idem_key(
                                        tpl["template_id"], inst["phone"], p_key, "notice_img_link", seq_idx, planned_str
                                    ), platform)
                                    idem_media = self._idem_for(make_idem_key(
                                        tpl["template_id"], inst["phone"], p_key, "notice_img", seq_idx, planned_str
                                    ), platform)
                                    # 两种形态都要查：换形态之后，历史记录也得认，避免切换当天补发。
                                    if (self.state.has_sent_reminder(self._link_idem(idem_link))
                                            or self.state.has_sent_reminder(idem_media)):
                                        continue
                                    if not ext_id:
                                        continue  # 未激活成员文本链路已记 skipped，附图同样跳过
                                    pending.append((inst, ext_id, ext_type, nt, platform, idem_link, idem_media))
                            if pending:
                                link_url: Optional[str] = None
                                for inst, ext_id, ext_type, nt, platform, idem_link, idem_media in pending:
                                    handled = False
                                    # 2026-11-03：量子密信永远不发原生图片（手机端点不开，10-29 实测口径），
                                    # 直接走 H5 链接。此前密信侧 send_image 签名不匹配，每次靠 TypeError
                                    # 异常兜底才落到链接（08:15 日志实锤），现在显式跳过。
                                    if platform != "zdxlz" and callable(getattr(nt, "send_image", None)):
                                        handled = self._send_image_media(
                                            external_id=ext_id, image_path=img_src_path, idem=idem_media,
                                            template_id=tpl["template_id"], instance_id=int(inst["id"]),
                                            phone=inst["phone"], planned_str=planned_str, now_s=now_s, stats=stats,
                                            notifier=nt,
                                        )
                                    if handled:
                                        continue
                                    # 降级：H5 链接（发布副本按需做，只做一次）
                                    if link_url is None:
                                        link_url = self._publish_image_link(img_src_path, img_title) or ""
                                    if not link_url:
                                        break
                                    self._send_image_link(
                                        external_id=ext_id, image_url=link_url, image_name=img_title or "",
                                        base_idem=idem_link,
                                        template_id=tpl["template_id"], instance_id=int(inst["id"]),
                                        phone=inst["phone"], planned_str=planned_str, now_s=now_s, stats=stats,
                                        notifier=nt, external_type=ext_type,
                                    )
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

            for inst in instances:
                if inst["status"] != "pending":
                    continue

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
                    self._deliver_with_log(
                        template_id=tpl["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                        kind="remind", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem,
                        msg_text_fn=_make_remind_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                        send_fn=functools.partial(nt.send_text, receive_type=ext_type),
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
                self._deliver_with_log(
                    template_id=inst["template_id"], instance_id=int(inst["id"]), phone=inst["phone"], external_id=ext_id,
                    kind="overdue", planned_str=planned_str, delay_sec=delay_sec, idem_key=idem_platform,
                    msg_text_fn=_make_overdue_msg, cur_dt=cur_dt, now_s=now_s, stats=stats,
                    send_fn=functools.partial(nt.send_text, receive_type=ext_type),
                )

        return stats

    tick = run_once
