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
def test_h5_registration_binds_roster_member_and_carries_dept() -> None:
    store = _store()
    try:
        role = store.create_role("云中台", actor="t", platform="wecom")
        store.add_role_member(role["role_id"], phone=PHONE, name=NAME)
        store.create_department(DEPT, actor="t")

        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "wecom_uid_1", "platform": "wecom", "name": NAME, "dept": DEPT},
        )
        assert code == 200 and body.get("bound") is True, body
        assert store.get_delivery_target(PHONE, platform="wecom")["external_id"] == "wecom_uid_1"

        # 管理员把待归纳人员移入角色时，部门从登记表单带入（无显式 dept 参数时）
        store.upsert_pending_member("wecom_uid_2", phone="13800000222", name="赵六", dept=DEPT, platform="wecom")
        store.assign_pending_member("wecom_uid_2", "13800000222", "赵六", role["role_id"], actor="t")
        members = store.list_role_members(role["role_id"], size=50)["items"]
        assert [m["name"] for m in members] == [NAME, "赵六"]
        assert [m["dept"] for m in members] == [None, DEPT]
    finally:
        store.close()


def test_h5_registration_without_roster_enters_pending_with_all_fields() -> None:
    store = _store()
    try:
        store.create_department(DEPT, actor="t")
        code, body = process_h5_bind(
            state=store,
            data={"phone": "13900001234", "external_id": "zdxlz_uid_9", "platform": "zdxlz", "name": "路人甲", "dept": DEPT},
        )
        assert code == 200 and body.get("pending") is True, body
        row = store.get_pending_member("zdxlz_uid_9")
        assert (row["name"], row["phone"], row["dept"]) == ("路人甲", "13900001234", DEPT)
        assert store.get_delivery_target("13900001234") is None
    finally:
        store.close()


def test_h5_registration_rejects_invalid_inputs() -> None:
    store = _store()
    try:
        store.create_department(DEPT, actor="t")
        base = {"phone": "13900009999", "external_id": "uid_x", "platform": "zdxlz"}
        # 缺姓名 / 缺部门 / 部门不存在
        for patch, want_msg in (
            ({"dept": DEPT}, "姓名"),
            ({"name": "某人"}, "部门"),
            ({"name": "某人", "dept": "不存在部门"}, "部门不存在"),
        ):
            code, body = process_h5_bind(state=store, data={**base, **patch})
            assert code == 400, (patch, body)
            assert want_msg in body["message"], (patch, body)
    finally:
        store.close()


def test_text_activation_dead_unbound_users_get_uniform_prompt() -> None:
    store = _store()
    try:
        store.create_role("测试角色", actor="t")
        store.create_department(DEPT, actor="t")
        sent: list[tuple] = []

        for text in ("你好", f"{NAME} {PHONE}", "13800000000", "申请补交"):
            code, body = handle_inbound_text(
                state=store, user_id="uid_newbie", msg_text=text, send=lambda t, g: sent.append((t, g)),
                platform="zdxlz", base_url="http://t.example.com",
            )
            assert code == 200 and body.get("bound") is False, (text, body)
            assert "你还未完成信息登记" in body["message"], text
            assert body["bind_url"].endswith("#uid=uid_newbie&plat=zdxlz"), text
        # 任何文字都不触发绑定；只留一条待归纳记录
        assert store.get_delivery_target(PHONE) is None
        assert store.get_pending_member("uid_newbie") is not None
        assert len(sent) == 4 and len({t for t, _ in sent}) == 1, "四次任意文本都回同一条提示"
    finally:
        store.close()


def test_registered_pending_user_gets_wait_reply_not_link() -> None:
    store = _store()
    try:
        store.create_department(DEPT, actor="t")
        store.upsert_pending_member("uid_filled", phone="13900005678", name="钱七", dept=DEPT, platform="zdxlz")
        code, body = handle_inbound_text(
            state=store, user_id="uid_filled", msg_text="怎么还不给我发任务", send=lambda t, g: None,
            platform="zdxlz", base_url="http://t.example.com",
        )
        assert code == 200
        assert "登记信息已收到" in body["message"]
        assert "bind_url" not in body
    finally:
        store.close()


# ── 4. 任务提交反馈回推 ─────────────────────────────────────────────
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