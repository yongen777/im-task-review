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
from io import BytesIO
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOOPBACK = "127.0.0.1"
DB_NAME = "im_task_test"
DEMO_USER = "imt_local_preview"
DEMO_MEMBERS = (
    ("13900009761", "周宁（演示）", "网络运营部", "preview-network"),
    ("13900009762", "李青（演示）", "交换维护组", "preview-switch"),
    ("13900009763", "陈明（演示）", "服务保障部", "preview-service"),
    ("13900009764", "许文（演示）", "交换维护组", "preview-switch"),
)
DEMO_DEPARTMENTS = {
    "preview-root": ("本地演示组织", None),
    "preview-network": ("网络运营部", "preview-root"),
    "preview-switch": ("交换维护组", "preview-network"),
    "preview-service": ("服务保障部", "preview-root"),
}
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
        if dept_id not in DEMO_DEPARTMENTS:
            return None
        name, parent = DEMO_DEPARTMENTS[dept_id]
        path = self.get_department(parent)["full_name"] + "/" + name if parent else name
        return {"dept_id": dept_id, "name": name, "full_name": path, "parent_id": parent,
                "user_count": len(self.dept_users_all(dept_id))}

    def department_children(self, dept_id):
        return [self.get_department(key) for key, (_, parent) in DEMO_DEPARTMENTS.items() if parent == dept_id]

    def department_parents(self, dept_id):
        result = []
        while (parent := DEMO_DEPARTMENTS[dept_id][1]):
            result.append(self.get_department(parent))
            dept_id = parent
        return result

    def dept_users_all(self, dept_id):
        return [
            {"phone": phone, "name": name, "union_id": "local-preview-" + phone}
            for phone, name, _, department in DEMO_MEMBERS if department == dept_id
        ]

    def search_by_phone_strict(self, phone):
        for number, name, dept, dept_id in DEMO_MEMBERS:
            if number == phone:
                path = self.get_department(dept_id)["full_name"]
                return "ok", {"name": name, "department": dept, "dept_path": path,
                              "union_id": "local-preview-" + phone}
        return "ok", None


def demo_files():
    from PIL import Image, ImageDraw

    drawing = Image.new("RGB", (800, 480), "#eef2f5")
    pen = ImageDraw.Draw(drawing)
    pen.text((48, 48), "IMTask / Equipment inspection / Demo", fill="#172e43")
    for row in range(5):
        y = 104 + row * 60
        pen.rounded_rectangle((48, y, 752, y + 42), radius=4, fill="#244c6a")
        for led in range(6):
            x = 552 + led * 24
            pen.ellipse((x, y + 16, x + 8, y + 24), fill="#a3e8b3")
    png, pdf = BytesIO(), BytesIO()
    drawing.save(png, format="PNG")
    drawing.save(pdf, format="PDF")
    return "机柜巡检照片.png", png.getvalue(), "交接明细.pdf", pdf.getvalue()


def seed(store, password):
    from app.api.h5 import prepare_task_upload, process_h5_task_upload, task_upload_limits
    from app.config import format_dt
    from app.portal import PortalService
    from app.scheduler import period_key

    account = store.get_admin_by_username(DEMO_USER)
    if not account:
        account = store.create_admin_account(DEMO_USER, "本地演示管理员", ["zdxlz"], password=password)
    owner = account["id"]
    store.change_admin_password(owner, password)
    roles = {item["name"]: item for item in store.list_roles(platform="zdxlz", owner_id=owner)}

    def role(title, members):
        row = roles.get(title) or store.create_role(title, owner_id=owner)
        rid = row["role_id"]
        registered = store.role_member_phones(rid)
        for phone, name, _, _ in members:
            if phone not in registered:
                store.add_role_member(rid, phone, name, owner_id=owner)
            store.upsert_delivery_target(phone, "local-preview-" + phone, platform="zdxlz")
        return rid

    role_id = role("本地预览 · 交换值守", DEMO_MEMBERS)
    cross_role = role("本地预览 · 跨组联查", [DEMO_MEMBERS[0], DEMO_MEMBERS[2]])
    leader, employee, cross, standby = DEMO_MEMBERS
    ident = {"phone": leader[0], "platform": "zdxlz"}
    portal = PortalService(store)
    grants = portal.grant_options("zdxlz", owner, leader[0])
    portal.save_grant("zdxlz", owner, leader[0], {
        "enabled": True, "dept_ids": [*grants["default_dept_ids"], "preview-service"], "role_ids": [cross_role],
    }, DEMO_USER)
    templates = {item["name"]: item for item in store.list_templates(owner_id=owner, size=100)["items"]}
    now = store.now()
    today, yesterday = period_key("day", now), period_key("day", now - timedelta(days=1))
    deadline = format_dt(now + timedelta(days=2))
    past_deadline = format_dt(now - timedelta(hours=2))
    run_id = secrets.token_hex(8)
    upload_dir = store.data_dir / "uploads"
    image_name, image, pdf_name, pdf = demo_files()
    schema = [
        {"key": "qty", "label": "核查设备数", "type": "number", "required": True},
        {"key": "position", "label": "设备位置", "type": "text"},
        {"key": "date", "label": "巡检日期", "type": "date"},
        {"key": "normal", "label": "设备是否正常", "type": "boolean"},
        {"key": "shift", "label": "班次", "type": "select", "options": ["早班", "中班", "晚班"]},
    ]
    fields = {"qty": 6, "position": "机房 A 区第 3 排", "date": today, "normal": True, "shift": "晚班"}

    def template(title, description, modules, *, review=False, adhoc=False):
        # Keep earlier local runs intact while still creating fresh states on restart.
        name = title if title not in templates else f"{title} · {now:%H%M%S}"
        row = store.create_template({
            "name": name, "text_tpl": description, "role_ids": [role_id], "kind": "form",
            "period": "day", "remind_at": "08:00", "has_deadline": False,
            "schema_json": schema if "fields" in modules else [], "allow_note": True,
            "is_adhoc": adhoc, "need_review": review, "spec": {"upload_modules": modules},
        }, owner_id=owner)
        return row["template_id"]

    def create_instance(tid, member, *, period=today, due=deadline):
        return store.ensure_instance(tid, member[0], member[1], member[2], period, due)

    def token(row):
        return store.issue_h5_token(row["phone"], row["template_id"], row["period_key"], row["id"], ttl_hours=168)

    def submit(row, *, data=None, note, attachments=(), raw=None):
        raw = raw or token(row)
        refs = []
        for name, content in attachments:
            code, uploaded = process_h5_task_upload(state=store, uploads_dir=upload_dir,
                                                    raw_token=raw, filename=name, content_bytes=content)
            if code != 200:
                raise RuntimeError(f"演示附件写入失败：{uploaded}")
            refs.append(uploaded["ref"])
        store.submit_h5_task(raw_token=raw, fields=data, note=note, refs=refs)
        return raw

    broad = template("光交箱巡检回报", "核对设备数量、机柜状态，附现场图及交接明细。", ["fields", "text", "image", "file"])
    review = template("值班交接记录", "登记未结事项，交由班长确认。", ["text", "image"], review=True)
    late = template("逾期演示 · 机房温湿度", "示例：此任务已经截止。", ["text"])
    adhoc_text = template("机房环境异常上报", "描述环境异常位置及情况。", ["text"], adhoc=True)
    adhoc_image = template("交换设备隐患上报", "填写设备情况并拍照。", ["text", "image"], adhoc=True)

    pending = token(create_instance(broad, leader))
    completed = submit(create_instance(broad, employee), data=fields,
                       note="设备运行正常。巡检记录和交接明细已附。", attachments=[(image_name, image), (pdf_name, pdf)])
    delegated = create_instance(broad, cross)
    with BytesIO(image) as stream:
        upload = prepare_task_upload(stream, len(image), task_upload_limits(store.get_template(broad), image_name))
        store.manual_mark_submitted(delegated["id"], owner, DEMO_USER, uploads_dir=upload_dir,
                                    fields=fields, note="管理员代交，现场回执已核对。", uploads=(upload,))
    delegated_token = token(delegated)

    awaiting = submit(create_instance(review, employee), note="晚班交接已记录，等待审核。", attachments=[(image_name, image)])
    needs_retry = create_instance(review, leader)
    submit(needs_retry, note="交接初稿。", attachments=[(image_name, image)])
    store.review_instance(needs_retry["id"], "reject", DEMO_USER, "请补充巡检点位和处理时间。")
    rejected = token(needs_retry)
    overdue = token(create_instance(late, leader, due=past_deadline))

    # A completed report for history and a separate entry that has no task instance yet.
    original_clock = store._now_fn
    try:
        store._now_fn = lambda: now - timedelta(hours=1)
        earlier = store.issue_adhoc_report(adhoc_text, leader[0], "zdxlz")
        store.submit_h5_task(raw_token=earlier, note="机房空调温度已恢复正常。")
    finally:
        store._now_fn = original_clock
    adhoc_open = store.issue_adhoc_report(adhoc_image, leader[0], "zdxlz")

    for mode, title in (("once", "晚班设备联查"), ("week", "每周交换机检查")):
        issued = portal.create_task(ident, {
            "name": title, "description": "核对机房设备状态并提交文字和现场图片。",
            "phones": [member[0] for member in DEMO_MEMBERS], "mode": mode,
            "has_deadline": False, "request_id": f"preview-{run_id}-{mode}", "upload_modules": ["text", "image"],
        })
        tid = issued["task_id"]
        period = "once" if mode == "once" else period_key("week", now)
        create_instance(tid, leader, period=period)
        submit(create_instance(tid, employee, period=period), note="交换机运行正常，机柜温度 24°C。",
               attachments=[(image_name, image)])
        create_instance(tid, cross, period=period, due=past_deadline)
        create_instance(tid, standby, period=period)
        if mode == "week":
            previous = period_key("week", now - timedelta(days=7))
            submit(create_instance(tid, employee, period=previous), note="上一周期：完成设备核验。",
                   attachments=[(image_name, image)])

    create_instance(broad, standby, period=yesterday, due=past_deadline)
    store.advance_instance_statuses(now)
    expired = store.issue_h5_token(leader[0], "", "zdxlz", None, purpose="portal", ttl_hours=-1)
    routes = {
        "H5 工作台 A · 授权领导 (168h)": "/h5/portal#t=" + portal.issue(leader[0], "zdxlz"),
        "H5 工作台 B · 普通成员 (168h)": "/h5/portal#t=" + portal.issue(employee[0], "zdxlz"),
        "H5 工作台 C · 已过期": "/h5/portal#t=" + expired,
        "H5 填报 · 待提交": "/h5/task#t=" + pending,
        "H5 填报 · 免审已完成": "/h5/task#t=" + completed,
        "H5 填报 · 待审核": "/h5/task#t=" + awaiting,
        "H5 填报 · 已驳回待重交": "/h5/task#t=" + rejected,
        "H5 填报 · 已逾期只读": "/h5/task#t=" + overdue,
        "H5 填报 · 管理员代交": "/h5/task#t=" + delegated_token,
        "H5 填报 · 自主上报": "/h5/task#t=" + adhoc_open,
    }
    return owner, routes


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
                            for label, url in urls.items():
                                if "工作台 A" in label or "待提交" in label or url == origin + "/":
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
        owner, routes = seed(state, "Preview@" + secrets.token_hex(12))
        key = secrets.token_urlsafe(24)
        urls = {label: backend + path for label, path in routes.items()}
        urls["PC admin (Vite HMR)"] = origin + "/"
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
