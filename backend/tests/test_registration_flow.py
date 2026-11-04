# -*- coding: utf-8 -*-
"""2026-11-03 登记流程改造（用户口径）回归：

1. 部门选项：后台配置（增/改名/删除），H5 登记页下拉共用；
2. 激活一律走 H5 表单（姓名/手机号/部门）：名册内直接绑定，名册外落待归纳池（带三要素）；
3. 对话文字激活彻底停用：未登记用户任何文本只回统一登记提示；
4. 任务提交成功 → 按任务所属平台回推「【任务名】提交成功」，一次提交只推一条。
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

from app.api.admin import create_admin_router  # noqa: E402
from app.api.events import handle_inbound_text  # noqa: E402
from app.api.h5 import process_h5_bind, process_h5_task_submit  # noqa: E402
from app.config import format_dt  # noqa: E402
from app.scheduler import FakeNotifier  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

PHONE = "13800000111"
NAME = "王五"
DEPT = "云中台"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_reg_")) / "im.db")


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
def test_department_crud_api() -> None:
    store = _store()
    try:
        c = _admin_client(store)

        created = c.post("/api/admin/departments", json={"name": DEPT})
        assert created.status_code == 200, created.text
        dept_id = created.json()["department"]["id"]

        dup = c.post("/api/admin/departments", json={"name": DEPT})
        assert (dup.status_code, dup.json()["code"]) == (409, "DEPT_NAME_TAKEN")

        items = c.get("/api/admin/departments").json()["items"]
        assert [d["name"] for d in items] == [DEPT]

        upd = c.put(f"/api/admin/departments/{dept_id}", json={"name": "市场部"})
        assert upd.status_code == 200
        assert c.get("/api/admin/departments").json()["items"][0]["name"] == "市场部"

        assert c.delete(f"/api/admin/departments/{dept_id}").status_code == 200
        assert c.get("/api/admin/departments").json()["items"] == []
        assert c.delete(f"/api/admin/departments/{dept_id}").status_code == 404
    finally:
        store.close()


# ── 2/3. H5 登记 + 文字激活停用 ────────────────────────────────────
def _submit_once(store: StateStore, template_id: str, *, fields: dict, notifier=None, notifiers=None):
    now = store.now().replace(tzinfo=None, microsecond=0)
    p_key = format_dt(now)[:10]
    insts = store.ensure_template_instances(template_id, p_key, format_dt(now + timedelta(hours=8)))
    inst = insts[0]
    token = store.issue_h5_token(phone=inst["phone"], template_id=template_id, period_key=p_key, instance_id=int(inst["id"]))
    return process_h5_task_submit(
        state=store, raw_token=token, data={"token": token, "fields": fields},
        notifier=notifier, notifiers=notifiers,
    )


def test_submit_feedback_pushed_once_on_task_platform() -> None:
    store = _store()
    try:
        role = store.create_role("密信任务组", actor="t", platform="zdxlz")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        store.upsert_delivery_target(PHONE, "zdxlz_uid_1", platform="zdxlz")
        tpl_id = _form_template(store, role["role_id"])

        z, w = FakeNotifier(), FakeNotifier()
        code, body = _submit_once(store, tpl_id, fields={"visit_count": 5}, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        assert code == 200, body
        assert len(z.sent_messages) == 1, "密信任务应回推到密信通道"
        assert z.sent_messages[0].text == "【数据上报任务】提交成功"
        assert w.sent_messages == [], "⛔ 企微通道不该收到密信任务的回推"

        # 状态机已消耗令牌：再次提交被拒，不产生第二条回推
        code2, body2 = _submit_once(store, tpl_id, fields={"visit_count": 6}, notifier=z, notifiers={"zdxlz": z, "wecom": w})
        assert code2 == 400
        assert len(z.sent_messages) == 1, "重复提交不得重复回推"
    finally:
        store.close()


def test_submit_feedback_on_wecom_task_goes_wecom_only() -> None:
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
        assert len(w.sent_messages) == 1 and w.sent_messages[0].receive_id == "wecom_uid_1"
        assert z.sent_messages == []
    finally:
        store.close()


def test_submit_feedback_missing_binding_sends_nothing() -> None:
    store = _store()
    try:
        role = store.create_role("未激活组", actor="t", platform="zdxlz")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)  # 不建立绑定
        tpl_id = _form_template(store, role["role_id"])

        z = FakeNotifier()
        code, _body = _submit_once(store, tpl_id, fields={"visit_count": 1}, notifier=z)
        assert code == 200
        assert z.sent_messages == [], "未激活绑定的成员不回推，提交本身照常成功"
    finally:
        store.close()