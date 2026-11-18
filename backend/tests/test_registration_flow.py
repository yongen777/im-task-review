# -*- coding: utf-8 -*-
"""2026-11-03 登记流程改造（用户口径）回归：

1. 部门选项：后台配置（增/改名/删除），H5 登记页下拉共用；
2. 激活一律走 H5 表单（姓名/手机号/部门）：名册内直接绑定，名册外落待归纳池（带三要素）；
3. 对话文字激活彻底停用：未登记用户任何文本只回统一登记提示；
4. 任务提交成功 → H5 返回结果，密信、企微均无机器人文本回执。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402
from app.api.admin import create_admin_router  # noqa: E402
from app.api.events import handle_inbound_text  # noqa: E402
from app.api.h5 import create_h5_router, process_h5_bind  # noqa: E402
from app.config import format_dt  # noqa: E402
from app.scheduler import FakeNotifier  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

PHONE = "13800000111"
NAME = "王五"
DEPT = "云中台"


def _store() -> StateStore:
    return StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_reg_")))


def _admin_client(store: StateStore) -> TestClient:
    uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
    sess = store.create_admin_session(uid)
    app = FastAPI()
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    c = TestClient(app)
    c.cookies.set("sid", sess["sid"])
    c.headers.update({"X-CSRF-Token": sess["csrf"]})
    return c


def _form_template(store: StateStore, role_id: str) -> str:
    tpl = store.create_template(
        {
            "name": "数据上报任务",
            "kind": "form",
            "role_ids": [role_id],
            "schema_json": [{"key": "visit_count", "label": "上门户数", "type": "number", "required": True}],
            "text_tpl": "请按时完成今日数据上报",
            "remind_at": "08:00",
            "deadline": "18:00",
            "period": "day",
            "need_review": False,
            "enabled": True,
        },
        actor="t",
    )
    return tpl["template_id"]


# ── 1. 部门选项（后台配置）──────────────────────────────────────────
def test_department_api_read_only() -> None:
    store = _store()
    try:
        c = _admin_client(store)

        dept_id = store.create_department(DEPT, actor="fixture")["id"]
        assert c.post("/api/admin/departments", json={"name": "新增部"}).status_code == 403
        assert c.put(f"/api/admin/departments/{dept_id}", json={"name": "市场部"}).status_code == 403
        assert c.delete(f"/api/admin/departments/{dept_id}").status_code == 403
        items = c.get("/api/admin/departments").json()["items"]
        assert [d["name"] for d in items] == [DEPT]
    finally:
        store.close()


# ── 2/3. H5 登记 + 文字激活停用 ────────────────────────────────────
def _submit_once(store: StateStore, template_id: str, *, fields: dict, notifier=None, notifiers=None):
    now = store.now().replace(tzinfo=None, microsecond=0)
    p_key = format_dt(now)[:10]
    insts = store.ensure_template_instances(template_id, p_key, format_dt(now + timedelta(hours=8)))
    inst = insts[0]
    token = store.issue_h5_token(phone=inst["phone"], template_id=template_id, period_key=p_key, instance_id=int(inst["id"]))
    app = FastAPI()
    app.include_router(create_h5_router(store, store.data_dir / "uploads", notifier=notifier, notifiers=notifiers))
    with TestClient(app) as client:
        response = client.post("/api/h5/task/submit", json={"token": token, "fields": fields})
    return response.status_code, response.json()


def test_zdxlz_task_submit_returns_result_without_bot_reply() -> None:
    store = _store()
    try:
        role = store.create_role("密信任务组", actor="t", platform="zdxlz")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        store.upsert_delivery_target(PHONE, "zdxlz_uid_1", platform="zdxlz")
        tpl_id = _form_template(store, role["role_id"])

        z, w = FakeNotifier(), FakeNotifier()
        code, body = _submit_once(store, tpl_id, fields={"visit_count": 5}, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        assert code == 200, body
        assert body['ok'] and not z.sent_messages and not w.sent_messages

        # 状态机已消耗令牌：再次提交被拒，不产生第二条回推
        code2, body2 = _submit_once(store, tpl_id, fields={"visit_count": 6}, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        assert code2 == 400
        assert not z.sent_messages and not w.sent_messages
    finally:
        store.close()


def test_wecom_task_submit_returns_result_without_bot_reply() -> None:
    store = _store()
    try:
        role = store.create_role("企微任务组", actor="t", platform="wecom")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        store.upsert_delivery_target(PHONE, "zdxlz_uid_1", platform="zdxlz")
        store.upsert_delivery_target(PHONE, "wecom_uid_1", platform="wecom")
        tpl_id = _form_template(store, role["role_id"])

        z, w = FakeNotifier(), FakeNotifier()
        code, _body = _submit_once(store, tpl_id, fields={"visit_count": 3}, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        assert code == 200
        assert not w.sent_messages and not z.sent_messages
    finally:
        store.close()


def test_submit_feedback_missing_binding_sends_nothing() -> None:
    store = _store()
    try:
        role = store.create_role("未激活组", actor="t", platform="zdxlz")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)  # 不建立绑定
        tpl_id = _form_template(store, role["role_id"])

        # 2026-11-09 新口径：未激活成员**根本不生成任务实例**（比"不回推"更靠前一道）
        now = store.now().replace(tzinfo=None, microsecond=0)
        pkey = format_dt(now)[:10]
        insts = store.ensure_template_instances(tpl_id, pkey, format_dt(now + timedelta(hours=8)))
        assert insts == [], "未激活成员不生成任务实例"
    finally:
        store.close()
