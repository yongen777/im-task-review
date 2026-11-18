"""Local UI preview using real routers, synthetic data and no channel clients."""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOOPBACK = "127.0.0.1"
DB_NAME = "im_task_test"
DEMO_USER = "imt_local_preview"
DEMO_MEMBERS = (
    ("13900009761", "周宁（演示）", "网络运营部", "preview-root"),
    ("13900009762", "李青（演示）", "交换维护组", "preview-switch"),
    ("13900009763", "陈明（演示）", "交换维护组", "preview-switch"),
)
H5_FILES = {"portal": "portal.html", "task": "task.html", "image": "image.html"}


def local_database():
    host = os.getenv("IMT_TEST_MYSQL_HOST", LOOPBACK)
    database = os.getenv("IMT_TEST_MYSQL_DATABASE", DB_NAME)
    if host not in {LOOPBACK, "localhost"} or database != DB_NAME:
        raise SystemExit("Preview requires local im_task_test.")
    return {
        "host": LOOPBACK,
        "port": int(os.getenv("IMT_TEST_MYSQL_PORT", "3306")),
        "user": os.getenv("IMT_TEST_MYSQL_USER", "root"),
        "password": os.getenv("IMT_TEST_MYSQL_PASSWORD", "123456"),
        "database": DB_NAME,
        "charset": "utf8mb4",
    }


def isolate_network():
    """Block outbound network at the socket boundary in this preview process."""
    def audit(event, args):
        if event == "socket.getaddrinfo" and args[0] not in {LOOPBACK, "localhost"}:
            raise PermissionError("External DNS is disabled in local preview.")
        if event == "socket.connect":
            address = args[1]
            # Windows asyncio uses an ephemeral loopback socket for its wakeup pipe.
            if not isinstance(address, tuple) or address[0] not in {LOOPBACK, "::1"}:
                raise PermissionError("External connections are disabled in local preview.")
    sys.addaudithook(audit)


class DemoDirectory:
    def get_department(self, dept_id):
        for key, name in (("preview-root", "网络运营部"), ("preview-switch", "交换维护组")):
            if dept_id == key:
                return {"dept_id": key, "name": name, "user_count": 2, "full_name": name}
        return None

    def department_children(self, dept_id):
        return [self.get_department("preview-switch")] if dept_id == "preview-root" else []

    def dept_users_all(self, dept_id):
        return [
            {"phone": phone, "name": name, "union_id": "local-preview-" + phone}
            for phone, name, _, department in DEMO_MEMBERS if department == dept_id
        ]

    def search_by_phone_strict(self, phone):
        for number, name, dept, dept_id in DEMO_MEMBERS:
            if number == phone:
                path = dept if dept_id == "preview-root" else "网络运营部/" + dept
                return "ok", {"name": name, "department": dept, "dept_path": path,
                              "union_id": "local-preview-" + phone}
        return "ok", None


def seed(store, scheduler, password):
    from app.config import format_dt
    from app.portal import PortalService, run_portal_tasks

    account = store.get_admin_by_username(DEMO_USER)
    if not account:
        account = store.create_admin_account(DEMO_USER, "本地演示管理员", ["zdxlz"], password=password)
    owner = account["id"]
    store.change_admin_password(owner, password)
    role_name = "本地预览 · 交换值守"
    roles = store.list_roles(platform="zdxlz", owner_id=owner)
    role = next((item for item in roles if item["name"] == role_name), None)
    role = role or store.create_role(role_name, owner_id=owner)
    role_id = role["role_id"]
    registered = store.role_member_phones(role_id)
    for phone, name, _, _ in DEMO_MEMBERS:
        if phone not in registered:
            store.add_role_member(role_id, phone, name, owner_id=owner)
        store.upsert_delivery_target(phone, "local-preview-" + phone, platform="zdxlz")

    phone, name, dept, _ = DEMO_MEMBERS[0]
    ident = {"phone": phone, "platform": "zdxlz"}
    portal = PortalService(store)
    portal.save_grant("zdxlz", owner, phone, {
        "enabled": True, "dept_ids": ["preview-root"], "role_ids": [role_id],
    }, DEMO_USER)
    templates = {item["name"]: item for item in store.list_templates(owner_id=owner, size=100)["items"]}
    now = store.now()
    deadline = format_dt(now + timedelta(days=7))
    run_id = secrets.token_hex(8)
    task_url = None
    for title, description, modules, adhoc in (
        ("光交箱巡检回报", "检查箱门、标签和周边环境，填写说明并上传现场图。", ["text", "image"], False),
        ("值班交接记录", "记录未结事项和下一班需要跟进的工作。", ["text"], False),
        ("机房环境异常上报", "记录异常位置、时间与现场情况。", ["text"], True),
        ("交换设备隐患上报", "描述异常设备及受影响范围。", ["text", "image"], True),
    ):
        template = templates.get(title) or store.create_template({
            "name": title, "text_tpl": description, "role_ids": [role_id], "kind": "form",
            "period": "day", "remind_at": "08:00", "has_deadline": False,
            "schema_json": [], "allow_note": True, "is_adhoc": adhoc,
            "spec_json": {"upload_modules": modules},
        }, owner_id=owner)
        if not adhoc:
            inst = store.ensure_instance(template["template_id"], phone, name, dept, "preview-" + run_id, deadline)
            task_url = task_url or portal.task_link(ident, inst["id"])["url"]

    published = portal.create_task(ident, {
        "name": "晚班交换设备巡检", "description": "核对机房设备状态并回报现场情况。",
        "phones": [member[0] for member in DEMO_MEMBERS], "mode": "once",
        "deadline_at": deadline, "request_id": "preview-" + run_id, "upload_modules": ["text"],
    })
    run_portal_tasks(scheduler, now)
    for row in store.list_instances_for_period(published["task_id"], "once"):
        if row["phone"] == DEMO_MEMBERS[1][0]:
            raw = portal.task_link({"phone": row["phone"], "platform": "zdxlz"}, row["id"])["url"].split("#t=")[1]
            store.submit_h5_task(raw_token=raw, note="交换机运行正常，机柜温度 24°C。")
    return owner, "/h5/portal#t=" + portal.issue(phone, "zdxlz"), task_url


def make_app(store, scheduler, owner, open_key):
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from app.api.admin import COOKIE_NAME, create_admin_router, register_admin_guard
    from app.api.h5 import create_h5_router

    app = FastAPI()
    app.state.scheduler = scheduler
    register_admin_guard(app, store)

    @app.middleware("http")
    async def disable_channels(request: Request, call_next):
        if request.url.path.startswith("/api/admin/channel-config"):
            return JSONResponse({"message": "本地预览已关闭通道配置及真实发送"}, status_code=403)
        return await call_next(request)

    @app.get("/__preview/open/{key}")
    def preview_login(key: str):
        if not secrets.compare_digest(key, open_key):
            return JSONResponse({"message": "本地预览入口无效"}, status_code=403)
        session = store.create_admin_session(owner)
        response = RedirectResponse("/", headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE_NAME, session["sid"], httponly=True, samesite="lax")
        return response

    @app.get("/__preview/version")
    def version():
        stamp = ":".join(str((ROOT / "app/h5" / f).stat().st_mtime_ns) for f in H5_FILES.values())
        return JSONResponse({"version": stamp}, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    def ready():
        return {"mode": "local-preview", "db": DB_NAME, "delivery": "fake"}

    # Register first so just these two HTML responses can receive the reload script.
    def live_page(filename):
        def page():
            html = (ROOT / "app/h5" / filename).read_text(encoding="utf-8")
            return HTMLResponse(html.replace("</body>", RELOAD_SCRIPT + "</body>"),
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return page

    for route in ("portal", "task"):
        app.add_api_route("/h5/" + route, live_page(H5_FILES[route]), methods=["GET"])
    app.include_router(create_h5_router(store, store.data_dir / "uploads", notifier=scheduler.notifier,
                                        scheduler=scheduler))
    app.include_router(create_admin_router(store, notifier=scheduler.notifier,
                                          public_base_url=scheduler.public_base_url))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[LOOPBACK])
    return app


RELOAD_SCRIPT = """<script>
let previewVersion;
async function checkPreviewUpdate() {
  try {
    const response = await fetch('/__preview/version', { cache: 'no-store' });
    const { version } = await response.json();
    if (previewVersion && version !== previewVersion) location.reload();
    previewVersion = version;
  } catch (_) { /* Reload check resumes when the preview server responds. */ }
}
checkPreviewUpdate();
setInterval(checkPreviewUpdate, 1000);
</script>"""


def show_when_ready(server, vite, origin, urls, login, no_open):
    http = build_opener(ProxyHandler({}))
    for _ in range(150):
        if vite.poll() is not None or server.should_exit:
            break
        try:
            if server.started:
                with http.open(origin + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        print("\nLOCAL PREVIEW READY (im_task_test, simulated delivery)")
                        for label, url in urls.items():
                            print(f"{label}: {url}")
                        if not no_open:
                            for url in urls.values():
                                webbrowser.open(login if url == origin + "/" else url, new=2)
                        return
        except OSError:
            pass
        time.sleep(.2)
    print("Preview startup failed: Vite or backend did not become ready.", file=sys.stderr)
    server.should_exit = True


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--backend-port", required=True, type=int)
    arguments.add_argument("--vite-port", required=True, type=int)
    arguments.add_argument("--node", required=True)
    arguments.add_argument("--no-open", action="store_true")
    args = arguments.parse_args()
    mysql = local_database()
    ports = {mysql["port"], args.backend_port, args.vite_port}
    if len(ports) != 3 or not all(1024 <= port <= 65535 for port in ports):
        raise SystemExit("Choose three distinct valid ports.")
    for port in (args.backend_port, args.vite_port):
        with socket.socket() as probe:
            probe.bind((LOOPBACK, port))
    for name in list(os.environ):
        if name.startswith(("IMT_", "VITE_")):
            os.environ.pop(name)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", IMT_ORG_ROOT_DEPT_ID="preview-root", DISABLE_HMR="false")
    isolate_network()

    import pymysql
    import uvicorn
    from app.scheduler import FakeNotifier, Scheduler
    from app.state import StateStore

    bootstrap = {key: value for key, value in mysql.items() if key != "database"}
    with pymysql.connect(**bootstrap, autocommit=True) as conn, conn.cursor() as cursor:
        cursor.execute("CREATE DATABASE IF NOT EXISTS im_task_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    backend = f"http://{LOOPBACK}:{args.backend_port}"
    origin = f"http://{LOOPBACK}:{args.vite_port}"
    data_dir = Path(tempfile.gettempdir()) / "imtask-local-preview-data"
    state = StateStore(mysql_cfg=mysql, data_dir=data_dir)
    try:
        state.contacts = DemoDirectory()
        scheduler = Scheduler(state, backend, notifier=FakeNotifier())
        owner, portal, task = seed(state, scheduler, "Preview@" + secrets.token_hex(12))
        key = secrets.token_urlsafe(24)
        urls = {"H5 portal (168h)": backend + portal, "H5 task": backend + task, "PC admin (Vite HMR)": origin + "/"}
        login = origin + "/__preview/open/" + key
        print("Local admin auto-login:", login)
        app = make_app(state, scheduler, owner, key)
        server = uvicorn.Server(uvicorn.Config(app, host=LOOPBACK, port=args.backend_port, access_log=False))
        vite = subprocess.Popen([
            args.node, str(Path(__file__).with_name("dev_preview_vite.mjs")),
            str(ROOT.with_name("im-task-admin")), str(args.vite_port), str(args.backend_port),
        ])
        try:
            threading.Thread(target=show_when_ready, args=(server, vite, origin, urls, login, args.no_open),
                             daemon=True).start()
            server.run()
        finally:
            if vite.poll() is None:
                vite.terminate()
                vite.wait(timeout=10)
    finally:
        state.close()


if __name__ == "__main__":
    main()
