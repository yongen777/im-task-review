# -*- coding: utf-8 -*-
"""IMTask 双端口同进程启动入口（严格对齐规格书 §0.3、§6.1、§9.9、§10、§12、v1.2 §16 P2/P3/P7/P8~P11）。"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional, Tuple

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.admin import create_admin_router, register_admin_guard
from .api.events import create_events_router
from .api.wecom_callback import create_wecom_router
from .api.h5 import create_h5_router
from .channels.zdxlz import ZdxlzChannel
from .channel_config import apply_db_channel_config
from .config import Config, NowFn, default_now
from .scheduler import NotifierProtocol, Scheduler, ZdxlzImageSender
from .state import StateStore

logger = logging.getLogger(__name__)

SCHEDULER_TICK_SECONDS = 30
ADMIN_STATIC_DIR = Path(__file__).resolve().parent / "admin_static"


def load_admin_index_html() -> str:
    target = ADMIN_STATIC_DIR / "index.html"
    if target.is_file():
        return target.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>IMTask Admin</h1></body></html>"


def setup_logging(config: Config) -> None:
    """§12 结构化日志，按天切分到 data/logs/im_task.log。"""
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = config.logs_dir / "im_task.log"
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _register_healthz(
    app: FastAPI, state: StateStore, channel: ZdxlzChannel, config: Config
) -> None:
    @app.get("/healthz")
    async def healthz():
        db_ok = state.ping()
        status = "ok" if db_ok else "degraded"
        code = 200 if db_ok else 503
        return JSONResponse(
            status_code=code,
            content={
                "status": status,
                "db": "ok" if db_ok else "error",
                "channel": "ok" if channel.configured else "unconfigured",
                "crypto": config.crypto_status,
            },
        )


def _register_admin_spa_pages(admin_app: FastAPI) -> None:
    """托管管理后台页面外壳（/、/login、/templates、/roles、/instances、/logs）。

    2026-10-27：后台换成 AI Studio 复刻版（Vite 构建产物），
    JS/CSS 在 admin_static/assets/ 下，需要挂静态目录，否则页面白屏。
    """
    assets_dir = ADMIN_STATIC_DIR / "assets"
    if assets_dir.is_dir():
        admin_app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="admin_assets")

    @admin_app.get("/", response_class=HTMLResponse)
    @admin_app.get("/login", response_class=HTMLResponse)
    @admin_app.get("/templates", response_class=HTMLResponse)
    @admin_app.get("/roles", response_class=HTMLResponse)
    @admin_app.get("/instances", response_class=HTMLResponse)
    @admin_app.get("/logs", response_class=HTMLResponse)
    async def serve_admin_spa():
        return HTMLResponse(content=load_admin_index_html())


def create_apps(
    config: Optional[Config] = None,
    state: Optional[StateStore] = None,
    channel: Optional[ZdxlzChannel] = None,
    notifier: Optional[NotifierProtocol] = None,
    now_fn: NowFn = default_now,
) -> Tuple[FastAPI, FastAPI, StateStore, ZdxlzChannel]:
    """创建同进程共享状态的双 FastAPI 实例：(public_app, admin_app, state, channel)。"""
    cfg = config or Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(cfg)

    store = state or StateStore(cfg.db_path, now_fn=now_fn)
    # 2026-10-30 通道配置向导：DB 里配过就用 DB（加密存储），没配过照样用 env —— 现网零变化。
    # ⛔ 只在这里合并一次：配置改动重启后生效，不做热更新（避免运行中换凭证导致半截状态）。
    if channel is None and state is None:
        cfg = apply_db_channel_config(cfg, store)
    chan = channel or ZdxlzChannel(cfg)
    # 2026-10-30 通道可切：IMT_CHANNEL=wecom 时走企业微信自建应用（迁移目标），
    # 默认仍是 zdxlz（量子密信），保证现有生产行为一个字都不变。
    default_notifier: NotifierProtocol = chan
    _channel = str(getattr(cfg, "channel", "zdxlz") or "zdxlz").strip().lower()
    # 2026-10-30 通道隔离：投递绑定（谁用什么 ID 能收到）按通道分开存，
    # store 读写默认跟着当前通道走；默认 zdxlz 时与历史行为完全一致。
    #
    # ⚠️ platform 映射（不是漏改）：企业微信**两条路线**——自建应用（wecom）与智能机器人（wecom_aibot）——
    #    用的是同一套成员 userid 体系，所以绑定统一记在 'wecom' 下。
    #    好处：先激活智能机器人，将来域名到位切自建应用时，绑定直接复用，员工不用重新激活。
    store.platform = "wecom" if _channel in ("wecom", "wecom_aibot") else _channel
    if _channel == "wecom":
        # 企业微信自建应用（HTTP 主动推送 + 回调接收）：暂缓，等回调域名可用
        from .channels.wecom import WecomNotifier

        default_notifier = WecomNotifier(cfg)
        logger.info("通道已切换为 企业微信自建应用（agentid=%s）", getattr(cfg, "wecom_agentid", ""))
    elif _channel in ("wecom_aibot", "aibot"):
        # 2026-10-30 当前路线：企业微信智能机器人（长连接）。
        # 官方原文：长连接「无需域名或 IP 即可接收消息并返回结果」→ 绕开自建应用的回调域名+可信 IP 死锁。
        # 收消息复用 handle_inbound_text（绑定/待归纳/申请补交/ack 同一套规则，⛔ 这里只做格式转换）。
        from .api.events import handle_inbound_text
        from .channels.wecom_aibot import WecomAibotNotifier

        def _aibot_inbound(user_id: str, msg_text: str, send) -> None:
            handle_inbound_text(state=store, user_id=user_id, msg_text=msg_text, send=send)

        default_notifier = WecomAibotNotifier(cfg, on_text=_aibot_inbound)
        logger.info(
            "通道已切换为 企业微信智能机器人（长连接 bot_id=%s；同一机器人只能一条连接）",
            getattr(cfg, "wecom_bot_id", "")[:12],
        )
    active_notifier: NotifierProtocol = notifier if notifier is not None else default_notifier

    # §16 P7：IMT_PUBLIC_BASE_URL 仍为 127.0.0.1/localhost 且通道已配置时，启动打 WARN
    if chan.configured and cfg.is_loopback_public_url:
        logger.warning(
            "IMT_PUBLIC_BASE_URL (%s) 仍为 127.0.0.1/localhost 且通道已配置；"
            "阶段二要用它拼 H5 链接，必须公网可访问。",
            cfg.public_base_url,
        )

    # 2026-10-27 春促数据看板：通道已配置时装配图片投递链路（upload_media + send_image）
    image_sender = ZdxlzImageSender(chan) if chan.configured else None

    scheduler = Scheduler(
        state=store,
        public_base_url=cfg.public_base_url,
        notifier=active_notifier,
        now_fn=now_fn,
        image_sender=image_sender,
    )

    public_app = FastAPI(title="IMTask Public Service", version="1.2.0")
    admin_app = FastAPI(title="IMTask Admin Service", version="1.2.0")

    public_app.state.config = cfg
    public_app.state.store = store
    public_app.state.channel = chan
    public_app.state.scheduler = scheduler

    admin_app.state.config = cfg
    admin_app.state.store = store
    admin_app.state.channel = chan
    admin_app.state.scheduler = scheduler

    _register_healthz(public_app, store, chan, cfg)
    _register_healthz(admin_app, store, chan, cfg)

    # 业务端口挂载：POST /api/app-events 与 H5 提交/绑定路由
    public_app.include_router(
        create_events_router(
            state=store,
            channel=chan,
            public_base_url=cfg.public_base_url,
            notifier=active_notifier,
        )
    )
    public_app.include_router(
        create_h5_router(
            state=store,
            uploads_dir=cfg.uploads_dir,
            notifier=active_notifier,
        )
    )
    # 2026-10-30 企业微信自建应用接收回调（独立于量子密信的 /api/app-events）：
    # 未配置 Token/AESKey 时路由仍在，但一律 503，便于部署后一眼看出「路由在、配置没填」。
    public_app.include_router(
        create_wecom_router(
            store,
            active_notifier,
            token=getattr(cfg, "wecom_token", ""),
            aes_key=getattr(cfg, "wecom_aes_key", ""),
            corpid=getattr(cfg, "wecom_corpid", ""),
        )
    )

    # 管理后台端口挂载：权限守卫 + /api/admin/* + 静态托管外壳 (/login 与 4 个管理页面)
    register_admin_guard(admin_app, store)
    admin_app.include_router(
        create_admin_router(store, notifier=active_notifier, public_base_url=cfg.public_base_url)
    )
    _register_admin_spa_pages(admin_app)

    return public_app, admin_app, store, chan


async def _scheduler_loop(scheduler: Scheduler, stop_event: asyncio.Event) -> None:
    """§6.1 后台每 30 秒执行一轮调度扫描。"""
    while not stop_event.is_set():
        try:
            # 2026-10-29 P1-3：run_once 内含同步 HTTP 投递，直接在事件循环里跑会把
            # H5 / 后台接口一起卡住（最长数十秒）。丢到线程池，保持事件循环可响应。
            await asyncio.to_thread(scheduler.run_once)
        except Exception as exc:
            logger.exception("调度循环执行异常: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SCHEDULER_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def serve_dual_ports(config: Optional[Config] = None) -> None:
    """在同一事件循环内同时监听业务端口 (IMT_PUBLIC_PORT) 与管理后台端口 (IMT_ADMIN_PORT)，并启动 30s 调度循环。"""
    cfg = config or Config.from_env()
    public_app, admin_app, store, chan = create_apps(cfg)
    scheduler: Scheduler = public_app.state.scheduler

    pub_cfg = uvicorn.Config(
        app=public_app,
        host="0.0.0.0",
        port=cfg.public_port,
        log_level="info",
    )
    adm_cfg = uvicorn.Config(
        app=admin_app,
        host="0.0.0.0",
        port=cfg.admin_port,
        log_level="info",
    )

    pub_server = uvicorn.Server(pub_cfg)
    adm_server = uvicorn.Server(adm_cfg)
    stop_event = asyncio.Event()
    sched_task = asyncio.create_task(_scheduler_loop(scheduler, stop_event))

    try:
        await asyncio.gather(pub_server.serve(), adm_server.serve())
    finally:
        stop_event.set()
        sched_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sched_task
        chan.close()
        store.close()


if __name__ == "__main__":
    asyncio.run(serve_dual_ports())
