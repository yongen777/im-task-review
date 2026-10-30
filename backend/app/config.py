# -*- coding: utf-8 -*-
"""IMTask 配置模块（严格对齐规格书 v1.2 §4.1、§7.4、§12、§16 P1/P3/P7/P8/P9/P10/P11）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

try:
    from zoneinfo import ZoneInfo

    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows 无 tzdata 时回退固定东八区
    SHANGHAI_TZ = timezone(timedelta(hours=8))

TIME_FMT = "%Y-%m-%d %H:%M:%S"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MISSING_PORT_MSG = "未配置端口，请向运维申请后写入 env"
SAME_PORT_MSG = "业务端口与后台端口不能相同"


def load_project_dotenv(dotenv_path: Optional[Path] = None) -> bool:
    """§16 P1：启动时加载项目根 .env（python-dotenv）；文件不存在则只读系统环境变量。"""
    target = dotenv_path or (PROJECT_ROOT / ".env")
    if not target.is_file():
        return False
    if load_dotenv is not None:
        return bool(load_dotenv(dotenv_path=target, override=False))
    # 兜底解析（防极端环境未装 python-dotenv 时仍能读 .env）
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val
    return True


def resolve_data_dir(dotenv_path: Optional[Path] = None) -> Path:
    """加载 .env 并解析数据目录（供 CLI 脚本在不依赖端口配置时读取数据库路径）。"""
    load_project_dotenv(dotenv_path=dotenv_path)
    data_dir_raw = os.environ.get("IMT_DATA_DIR", "").strip() or "data"
    return Path(data_dir_raw)


def default_now() -> datetime:
    """返回固定 Asia/Shanghai 时区的当前时间。"""
    return datetime.now(SHANGHAI_TZ)


def format_dt(dt: datetime) -> str:
    """将 datetime 转为规格书 §4.1 规定的本地时间字符串 YYYY-MM-DD HH:MM:SS。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone(SHANGHAI_TZ)
    return dt.strftime(TIME_FMT)


def parse_dt(value: str) -> datetime:
    """解析 YYYY-MM-DD HH:MM:SS 为带 Asia/Shanghai 时区的 datetime。"""
    naive = datetime.strptime(value.strip(), TIME_FMT)
    return naive.replace(tzinfo=SHANGHAI_TZ)


def _abort_missing_port() -> None:
    """§16 P11：只保留 SystemExit，不额外 print，避免重复输出。"""
    raise SystemExit(MISSING_PORT_MSG)


def _abort_same_port() -> None:
    """§16 P10：业务端口与后台端口相同时启动失败退出。"""
    raise SystemExit(SAME_PORT_MSG)


def is_valid_public_base_url(url: str) -> bool:
    """§16 P9：校验 IMT_PUBLIC_BASE_URL 的 scheme ∈ {http, https} 且有非空 host。"""
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip()
    return scheme in ("http", "https") and bool(host)


@dataclass(frozen=True)
class Config:
    # §16 P8：public_port / admin_port / public_base_url 不设默认值，必须由运维分配后写入 env
    public_port: int
    admin_port: int
    public_base_url: str
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verify_token: str = ""
    api_base: str = "https://imtwo.zdxlz.com"
    data_dir: Path = Path("data")
    # 2026-10-30 通道可切：zdxlz（量子密信，现状）| wecom（企业微信自建应用，迁移目标）
    channel: str = "zdxlz"
    # 企业微信自建应用三件套（由企业管理员创建应用后提供；缺任意一项则通道不可用）
    wecom_corpid: str = ""
    wecom_agentid: str = ""
    wecom_secret: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.public_port, int) or isinstance(self.public_port, bool):
            _abort_missing_port()
        if not isinstance(self.admin_port, int) or isinstance(self.admin_port, bool):
            _abort_missing_port()
        if not (1 <= self.public_port <= 65535 and 1 <= self.admin_port <= 65535):
            _abort_missing_port()
        if not is_valid_public_base_url(self.public_base_url):
            _abort_missing_port()
        if self.public_port == self.admin_port:
            _abort_same_port()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "im_task.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def channel_configured(self) -> bool:
        """§7.4：核心通道凭证齐全才视为已配置；缺失时服务仍启动，但标记 unconfigured。"""
        return bool(self.app_id.strip() and self.app_secret.strip())

    @property
    def event_crypto_configured(self) -> bool:
        """§16 P3：事件回调验签与解密密钥是否已配置（取决于 IMT_ENCRYPT_KEY + IMT_VERIFY_TOKEN）。"""
        return bool(self.encrypt_key.strip() and self.verify_token.strip())

    @property
    def channel_status(self) -> str:
        return "ok" if self.channel_configured else "unconfigured"

    @property
    def crypto_status(self) -> str:
        """§16 P3：/healthz 的 crypto 字段（ok / unconfigured）。"""
        return "ok" if self.event_crypto_configured else "unconfigured"

    @property
    def is_loopback_public_url(self) -> bool:
        """§16 P7：判断 IMT_PUBLIC_BASE_URL 是否仍为 127.0.0.1 / localhost。"""
        try:
            host = (urlparse(self.public_base_url).hostname or "").strip().lower()
        except Exception:
            host = ""
        return host in ("127.0.0.1", "localhost", "::1", "")

    @classmethod
    def from_env(cls, dotenv_path: Optional[Path] = None) -> "Config":
        load_project_dotenv(dotenv_path=dotenv_path)
        pub_port_raw = os.environ.get("IMT_PUBLIC_PORT", "").strip()
        adm_port_raw = os.environ.get("IMT_ADMIN_PORT", "").strip()
        public_base = os.environ.get("IMT_PUBLIC_BASE_URL", "").strip().rstrip("/")

        if not pub_port_raw or not adm_port_raw or not is_valid_public_base_url(public_base):
            _abort_missing_port()

        try:
            public_port = int(pub_port_raw)
            admin_port = int(adm_port_raw)
        except ValueError:
            _abort_missing_port()

        if not (1 <= public_port <= 65535 and 1 <= admin_port <= 65535):
            _abort_missing_port()

        if public_port == admin_port:
            _abort_same_port()

        data_dir_raw = os.environ.get("IMT_DATA_DIR", "").strip() or "data"
        api_base_raw = (
            os.environ.get("IMT_API_BASE", "").strip() or "https://imtwo.zdxlz.com"
        ).rstrip("/")

        return cls(
            public_port=public_port,
            admin_port=admin_port,
            public_base_url=public_base,
            app_id=os.environ.get("IMT_APP_ID", "").strip(),
            app_secret=os.environ.get("IMT_APP_SECRET", "").strip(),
            # ⚠️ §7.3：encrypt_key 保留原始字符串，不做 base64 解码
            encrypt_key=os.environ.get("IMT_ENCRYPT_KEY", "").strip(),
            verify_token=os.environ.get("IMT_VERIFY_TOKEN", "").strip(),
            api_base=api_base_raw,
            data_dir=Path(data_dir_raw),
            channel=(os.environ.get("IMT_CHANNEL", "").strip().lower() or "zdxlz"),
            wecom_corpid=os.environ.get("IMT_WECOM_CORPID", "").strip(),
            wecom_agentid=os.environ.get("IMT_WECOM_AGENTID", "").strip(),
            wecom_secret=os.environ.get("IMT_WECOM_SECRET", "").strip(),
        )


NowFn = Callable[[], datetime]
