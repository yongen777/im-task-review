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
from .api.h5 import create_h5_router
from .channels.zdxlz import ZdxlzChannel
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
    chan = channel or ZdxlzChannel(cfg)
    # 2026-10-30 通道可切：IMT_CHANNEL=wecom 时走企业微信自建应用（迁移目标），
    # 默认仍是 zdxlz（量子密信），保证现有生产行为一个字都不变。
    default_notifier: NotifierProtocol = chan
    if str(getattr(cfg, "channel", "zdxlz") or "zdxlz").strip().lower() == "wecom":
        from .channels.wecom import WecomNotifier

        default_notifier = WecomNotifier(cfg)
        logger.info("通道已切换为 企业微信自建应用（agentid=%s）", getattr(cfg, "wecom_agentid", ""))
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
