# -*- coding: utf-8 -*-
"""2026-11-05 超管权限收口 + 全平台成员池 + 停用/删除分级管理员：口径回归。

覆盖：
1. 停用分级管理员 → 名下模板停止调度（multi-tick 不建实例不发消息），启用即恢复
2. 停用期间补交申请返回 admin_disabled（不是 none——员工要知道是"人不在"）
3. 级联删除：账号+名下数据全清、绑定条件删（member 表反推）、附件路径越界防护、自动备份、审计
4. 定向转移 transfer_owner_between（删除前"先转移"选项；transfer_owner 是全平台归并函数不能复用）
5. 全平台成员池：按人聚合三态、全局启停/改名部门（手机号不可改）/全局删除、绑定条件删
6. 在册人数按人去重（/roles 响应 member_distinct_total + 账号卡片统计口径）
7. 部门增/改/删仅超管（middleware 白名单 + 接口内 _require_super 双层）
8. assume 代登录：超管切换、代管身份不能再次 assume、停用账号不可代
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

PHONE_X = "13900001001"
PHONE_Y = "13900001002"
PHONE_Z = "13900001003"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_super_")) / "im.db")


def _app(store: StateStore) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _sub(app: FastAPI, admin: TestClient, username: str) -> int:
    r = admin.post("/api/admin/accounts", json={"username": username, "display_name": username})
    assert r.status_code == 200, r.text
    return int(r.json()["account"]["id"])


def _tpl_payload(rid: str, **extra) -> dict:
    kind = extra.get("kind", "notice")
    p: dict = {"name": "超管口径-通知", "kind": kind, "role_ids": [rid], "period": "day"}
    if kind == "notice":
        p["text_tpl"] = "干活"
        p["notice_times"] = ["17:15"]
    else:
        p["text_tpl"] = "上报一下"
        p["remind_at"] = "09:00"
        p["deadline"] = "18:00"
        p["schema_json"] = [{"key": "summary", "label": "摘要", "type": "text"}]
    p.update(extra)
    return p


def _sub_ready(app: FastAPI, admin: TestClient, username: str) -> tuple:
    """建号 → 返回 (admin_id, 改密后的已登录 client)。业务数据只能由分级管理员建（超管只读）。"""
    aid = _sub(app, admin, username)
    c = _login(app, username, StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
    c.post("/api/admin/change-password",
           json={"old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    return aid, c


# ── 1. 停用管理员 = 名下模板停止调度（multi-tick 幂等，刷屏红线相邻路径）──
def test_disabled_admin_stops_scheduling_multi_tick() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        a_id = int(store.create_admin_account(username="sa", display_name="sa")["id"])
        b_id = int(store.create_admin_account(username="sb", display_name="sb")["id"])
        # 甲的模板（要停发）+ 乙的模板（不受影响，停用只作用于本人名下）
        ra = store.create_role("甲组", actor="sa", owner_id=a_id)["role_id"]
        store.add_role_member(ra, PHONE_X, "张三", "市场部", actor="sa", owner_id=a_id)
        store.upsert_delivery_target(PHONE_X, "uid_x")
        rb = store.create_role("乙组", actor="sb", owner_id=b_id)["role_id"]
        store.add_role_member(rb, PHONE_Y, "李四", "渠道部", actor="sb", owner_id=b_id)
        store.upsert_delivery_target(PHONE_Y, "uid_y")
        tpl_a = store.create_template(_tpl_payload(ra, name="甲的通知"), actor="sa", owner_id=a_id)
        tpl_b = store.create_template(_tpl_payload(rb, name="乙的通知"), actor="sb", owner_id=b_id)

        clock = {"now": datetime(2026, 11, 5, 17, 10, 0)}
        ntf = FakeNotifier()
        sched = Scheduler(state=store, public_base_url="http://e.com", notifier=ntf,
                          now_fn=lambda: clock["now"])

        # 停用甲 → 17:15 前后连跑 3 轮 tick：甲的模板零实例零发送（幂等验证）；乙不受影响
        store.update_admin_account(a_id, enabled=False, actor="admin")
        for _ in range(3):
            clock["now"] = datetime(2026, 11, 5, 17, 15, 30)
            sched.run_once()
            assert store.list_instances_for_period(tpl_a["template_id"], "2026-11-05") == []
        assert all(m.receive_id != "uid_x" for m in ntf.sent_messages)
        # 乙的照常生成并发送
        assert store.list_instances_for_period(tpl_b["template_id"], "2026-11-05")
        assert any(m.receive_id == "uid_y" for m in ntf.sent_messages)

        # 重新启用甲 → 恢复调度（可逆）
        store.update_admin_account(a_id, enabled=True, actor="admin")
        ntf2 = FakeNotifier()
        sched2 = Scheduler(state=store, public_base_url="http://e.com", notifier=ntf2,
                           now_fn=lambda: datetime(2026, 11, 6, 17, 16, 0))
        st2 = sched2.run_once()
        assert st2["generated_instances"] > 0 and st2["sent"] > 0, st2
        assert any(m.receive_id == "uid_x" for m in ntf2.sent_messages)
    finally:
        store.close()


# ── 2. 停用期间补交申请：admin_disabled，且说清"人不在" ──
def test_resubmit_returns_admin_disabled_when_owner_disabled() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        a_id = int(store.create_admin_account(username="sa", display_name="sa")["id"])
        rid = store.create_role("甲组", actor="sa", owner_id=a_id)["role_id"]
        store.add_role_member(rid, PHONE_X, "张三", "市场部", actor="sa", owner_id=a_id)
        store.upsert_delivery_target(PHONE_X, "uid_x")
        tpl = store.create_template(_tpl_payload(rid, name="甲的上报", kind="form"),
                                    actor="sa", owner_id=a_id)
        # 造一个逾期实例（截止取「2 小时前」——与真实运行时刻无关，永远落在 24h 申请窗口内。
        # ⛔ 原实现用「昨日 18:00」，窗口=截止+24h=当天 18:00 整点到期，一过 18:00 跑必挂
        # （2026-11-10 18:04 远程 pytest 实锤：17:46 本地绿、18:04 远程红，纯时间炸弹））
        now = store.now()
        overdue_dt = now - timedelta(hours=2)
        overdue_day = overdue_dt.strftime("%Y-%m-%d")
        store.conn.execute(
            "INSERT INTO task_instance(template_id, phone, name_snap, dept_snap, period_key, status, "
            "deadline_at, created_at, owner_id) VALUES (?, ?, '张三', '市场部', ?, 'overdue', ?, ?, ?)",
            (tpl["template_id"], PHONE_X, overdue_day,
             overdue_dt.strftime("%Y-%m-%d %H:%M:%S"),
             (overdue_dt - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"), a_id),
        )
        store.conn.commit()

        assert store.request_resubmit(PHONE_X)["result"] == "applied"
        # 重置回可申请状态，再停用管理员验证 admin_disabled
        store.conn.execute("UPDATE task_instance SET resubmit_status = NULL, resubmit_req_at = NULL WHERE phone = ?", (PHONE_X,))
        store.conn.commit()
        store.update_admin_account(a_id, enabled=False, actor="admin")
        res = store.request_resubmit(PHONE_X)
        assert res["result"] == "admin_disabled", res
        assert len(res["items"]) == 1
        # 停用期间不置 pending
        row = store.conn.execute("SELECT resubmit_status FROM task_instance WHERE phone = ?", (PHONE_X,)).fetchone()
        assert not row["resubmit_status"]
        # 启用后恢复受理
        store.update_admin_account(a_id, enabled=True, actor="admin")
        assert store.request_resubmit(PHONE_X)["result"] == "applied"
    finally:
        store.close()


# ── 3. 级联删除：数据全清、绑定条件删、路径越界防护、备份、审计 ──
def test_cascade_purge_admin_account() -> None:
    store = _store()
    tmp = Path(tempfile.mkdtemp(prefix="imtask_uploads_"))
    uploads = tmp / "uploads"
    uploads.mkdir()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        a_id = int(store.create_admin_account(username="sa", display_name="sa")["id"])
        b_id = int(store.create_admin_account(username="sb", display_name="sb")["id"])
        ra1 = store.create_role("甲1组", actor="sa", owner_id=a_id)["role_id"]
        ra2 = store.create_role("甲2组", actor="sa", owner_id=a_id)["role_id"]
        rb = store.create_role("乙组", actor="sb", owner_id=b_id)["role_id"]
        # X 同时在甲（两角色）和乙名下；Y 只在甲名下
        store.add_role_member(ra1, PHONE_X, "张三", "市场部", actor="sa", owner_id=a_id)
        store.add_role_member(ra2, PHONE_X, "张三丰", "渠道部", actor="sa", owner_id=a_id)  # 同人不同名 → 池要能看出
        store.add_role_member(rb, PHONE_X, "张三", "市场部", actor="sb", owner_id=b_id)
        store.add_role_member(ra1, PHONE_Y, "李四", "渠道部", actor="sa", owner_id=a_id)
        store.upsert_delivery_target(PHONE_X, "uid_x")
        store.upsert_delivery_target(PHONE_Y, "uid_y")
        tpl = store.create_template(_tpl_payload(ra1, name="甲的通知"), actor="sa", owner_id=a_id)
        inst_id = int(store.conn.execute(
            "INSERT INTO task_instance(template_id, phone, name_snap, period_key, status, deadline_at, created_at, owner_id) "
            "VALUES (?, ?, '张三', '2026-11-05', 'sent', '2026-11-05 18:00:00', '2026-11-05 09:00:00', ?)",
            (tpl["template_id"], PHONE_X, a_id),
        ).lastrowid)
        store.conn.execute(
            "INSERT INTO submission(instance_id, source, status, created_at) VALUES (?, 'h5', 'approved', '2026-11-05 19:00:00')",
            (inst_id,),
        )
        # 附件：正常文件 + 模板通知图 + 一个越界路径（必须跳过，不许删到 uploads 外）
        (uploads / "2026-11-05").mkdir()
        (uploads / "2026-11-05" / "ok.png").write_bytes(b"img")
        (uploads / "notice").mkdir()
        (uploads / "notice" / "banner.png").write_bytes(b"banner")
        (tmp / "evil.txt").write_bytes(b"must-survive")
        store.create_upload_ref(ref="ref1", phone=PHONE_X, template_id=tpl["template_id"],
                                file_name="ok.png", mime="image/png", size=3, rel_path="2026-11-05/ok.png")
        store.conn.execute("UPDATE task_template SET attach_image_path = 'notice/banner.png' WHERE template_id = ?", (tpl["template_id"],))
        store.conn.execute("INSERT INTO h5_token(token_id, phone, template_id, period_key, instance_id, purpose, created_at, expires_at) "
                           "VALUES ('tk1', ?, ?, '2026-11-05', ?, 'resubmit', '2026-11-05 08:00:00', '2026-11-05 20:00:00')",
                           (PHONE_X, tpl["template_id"], inst_id))
        store.conn.execute("INSERT INTO reminder_log(template_id, phone, kind, planned_at, result, idem_key) "
                           "VALUES (?, ?, 'notice', '2026-11-05 17:15:00', 'sent', 'idem1')",
                           (tpl["template_id"], PHONE_X))
        store.conn.commit()

        backup = store.backup_database(tag="purge-test")
        assert Path(backup).is_file()

        res = store.delete_admin_account(a_id, actor="admin", uploads_dir=uploads)
        assert res["deleted"]["role"] >= 2 and res["deleted"]["member"] >= 3
        assert res["deleted"]["task_template"] >= 1 and res["deleted"]["task_instance"] >= 1
        assert res["deleted"]["submission"] >= 1 and res["deleted"]["upload_ref"] >= 1
        assert res["deleted"]["h5_token"] >= 1 and res["deleted"]["reminder_log"] >= 1
        assert res["attachments_removed"] == 2  # ok.png + notice/banner.png
        assert PHONE_Y in res["bindings_removed"] and PHONE_X not in res["bindings_removed"]

        # 账号没了，业务数据全清
        assert store.conn.execute("SELECT COUNT(1) FROM admin_user WHERE id = ?", (a_id,)).fetchone()[0] == 0
        for sql in (
            "SELECT COUNT(1) FROM role WHERE owner_id = ?",
            "SELECT COUNT(1) FROM member WHERE owner_id = ?",
            "SELECT COUNT(1) FROM task_template WHERE owner_id = ?",
            "SELECT COUNT(1) FROM task_instance WHERE owner_id = ?",
        ):
            assert store.conn.execute(sql, (a_id,)).fetchone()[0] == 0
        # 乙的数据原样保留；X 在乙名下 → 绑定保留；Y 全平台无归属 → 绑定删除
        assert store.conn.execute("SELECT COUNT(1) FROM role WHERE owner_id = ?", (b_id,)).fetchone()[0] == 1
        assert store.is_phone_activated(PHONE_X) is True
        assert store.is_phone_activated(PHONE_Y) is False
        # 磁盘：正常附件与通知图已删；越界路径对应的文件必须还在（不许误删）
        assert not (uploads / "2026-11-05" / "ok.png").exists()
        assert not (uploads / "notice" / "banner.png").exists()
        # 删除甲后 X 的绑定保留 → 还能从乙名册看到激活
        assert (tmp / "evil.txt").read_bytes() == b"must-survive"
        # 审计留痕
        acts = [a["action"] for a in store.list_audit_logs(limit=20)]
        assert "admin.account.purge" in acts
    finally:
        store.close()


# ── 4. 定向转移（删除前"先转移"选项的后端支撑）──
def test_transfer_owner_between_moves_data_only() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        a_id = int(store.create_admin_account(username="sa", display_name="sa")["id"])
        b_id = int(store.create_admin_account(username="sb", display_name="sb")["id"])
        ra = store.create_role("甲组", actor="sa", owner_id=a_id)["role_id"]
        store.add_role_member(ra, PHONE_X, "张三", "市场部", actor="sa", owner_id=a_id)
        store.create_template(_tpl_payload(ra, name="甲的通知"), actor="sa", owner_id=a_id)

        res = store.transfer_owner_between(a_id, b_id, actor="admin")
        assert res["moved"]["role"] == 1 and res["moved"]["member"] == 1 and res["moved"]["task_template"] == 1
        for tbl in ("role", "member", "task_template"):
            assert store.conn.execute(f"SELECT COUNT(1) FROM {tbl} WHERE owner_id = ?", (a_id,)).fetchone()[0] == 0
            assert store.conn.execute(f"SELECT COUNT(1) FROM {tbl} WHERE owner_id = ?", (b_id,)).fetchone()[0] >= 1
        # 幂等：再转一次 0 行
        assert store.transfer_owner_between(a_id, b_id, actor="admin")["moved"]["role"] == 0
        # 目标是超管 → 拒绝；自转 → 拒绝
        with pytest.raises(BizError):
            store.transfer_owner_between(b_id, 1, actor="admin")
        with pytest.raises(BizError):
            store.transfer_owner_between(b_id, b_id, actor="admin")
    finally:
        store.close()


# ── 5. 全平台成员池 ──
def test_member_pool_aggregate_and_global_ops() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        a_id = int(store.create_admin_account(username="sa", display_name="sa")["id"])
        b_id = int(store.create_admin_account(username="sb", display_name="sb")["id"])
        ra1 = store.create_role("甲1组", actor="sa", owner_id=a_id)["role_id"]
        ra2 = store.create_role("甲2组", actor="sa", owner_id=a_id)["role_id"]
        rb = store.create_role("乙组", actor="sb", owner_id=b_id)["role_id"]
        store.add_role_member(ra1, PHONE_X, "张三", "市场部", actor="sa", owner_id=a_id)
        store.add_role_member(ra2, PHONE_X, "张三丰", "渠道部", actor="sa", owner_id=a_id)
        store.add_role_member(rb, PHONE_X, "张三", "市场部", actor="sb", owner_id=b_id)
        store.add_role_member(ra1, PHONE_Z, "王五", "综合部", actor="sa", owner_id=a_id)
        store.upsert_delivery_target(PHONE_X, "uid_x")

        pool = {p["phone"]: p for p in store.list_member_pool()}
        assert pool[PHONE_X]["row_count"] == 3 and pool[PHONE_X]["role_count"] == 3
        assert pool[PHONE_X]["admin_count"] == 2
        assert pool[PHONE_X]["name_inconsistent"] is True   # 张三 / 张三丰 分叉可见
        assert pool[PHONE_X]["status"] == "active"
        assert pool[PHONE_X]["activated_platforms"]  # 已激活平台可见
        assert pool[PHONE_Z]["admin_count"] == 1
        # 2026-11-06 新列口径：首次导入人 + 角色明细（含平台）
        assert pool[PHONE_X]["first_owner_name"] == "sa"
        assert pool[PHONE_Z]["first_owner_name"] == "sa"
        for role_name in ("甲1组", "甲2组", "乙组"):
            assert role_name in pool[PHONE_X]["role_detail"]

        # 全局停用：所有管理员的行一起停 → 三态变 disabled
        store.update_role_member(ra1, PHONE_X, enabled=False, actor="sa")  # 只停一个角色
        assert {p["phone"]: p for p in store.list_member_pool()}[PHONE_X]["status"] == "partial"
        res = store.member_pool_set_status(PHONE_X, enabled=False, actor="admin")
        assert res["rows"] == 3
        assert {p["phone"]: p for p in store.list_member_pool()}[PHONE_X]["status"] == "disabled"
        res = store.member_pool_set_status(PHONE_X, enabled=True, actor="admin")
        assert res["rows"] == 3

        # 全局改名/部门：所有行同步同一值；姓名校验生效
        with pytest.raises(BizError):
            store.member_pool_update_profile(PHONE_X, name="  ", dept=None, actor="admin")
        store.member_pool_update_profile(PHONE_X, name="张三", dept="渠道部", actor="admin")
        names = {r["name"] for r in store.conn.execute("SELECT name FROM member WHERE phone = ?", (PHONE_X,)).fetchall()}
        depts = {r["dept"] for r in store.conn.execute("SELECT dept FROM member WHERE phone = ?", (PHONE_X,)).fetchall()}
        assert names == {"张三"} and depts == {"渠道部"}
        # 同步后不再提示不一致
        assert {p["phone"]: p for p in store.list_member_pool()}[PHONE_X]["name_inconsistent"] is False

        # 全局删除：X 在乙名下也有行 → 一并删除；删完全平台无归属 → 绑定清除
        res = store.member_pool_delete(PHONE_X, actor="admin")
        assert res["rows"] == 3
        assert store.conn.execute("SELECT COUNT(1) FROM member WHERE phone = ?", (PHONE_X,)).fetchone()[0] == 0
        assert store.is_phone_activated(PHONE_X) is False
        assert "zdxlz" in res["bindings_removed"]
        # 不存在的手机号 → 404
        with pytest.raises(BizError):
            store.member_pool_delete("13900000000", actor="admin")
    finally:
        store.close()


def test_member_pool_api_super_only() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        a_id, sa = _sub_ready(app, admin, "sa")
        r = sa.post("/api/admin/roles", json={"name": "甲组"})
        assert r.status_code == 200, r.text
        rid = r.json()["role_id"]
        assert sa.post(f"/api/admin/roles/{rid}/members",
                       json={"phone": PHONE_X, "name": "张三", "dept": "市场部"}).status_code == 200

        r = admin.get("/api/admin/member-pool")
        assert r.status_code == 200 and len(r.json()["items"]) == 1
        # 超管全局启停（middleware 白名单放行 + 接口 _require_super）
        r = admin.put(f"/api/admin/member-pool/{PHONE_X}/status", json={"enabled": False})
        assert r.status_code == 200 and r.json()["rows"] == 1
        # 超管全局改名（不收手机号字段）
        r = admin.put(f"/api/admin/member-pool/{PHONE_X}", json={"name": "张三丰", "dept": "渠道部"})
        assert r.status_code == 200
        row = store.conn.execute("SELECT name, dept FROM member WHERE phone = ?", (PHONE_X,)).fetchone()
        assert row["name"] == "张三丰" and row["dept"] == "渠道部"
        # 分级管理员调用成员池 → 403（接口内校验，middleware 对 sub 不拦这个路径）
        assert sa.get("/api/admin/member-pool").status_code == 403
        assert sa.put(f"/api/admin/member-pool/{PHONE_X}/status", json={"enabled": True}).status_code == 403
        assert sa.delete(f"/api/admin/member-pool/{PHONE_X}").status_code == 403
    finally:
        store.close()


# ── 6. 在册人数去重口径 ──
def test_member_distinct_total_and_roles_response() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        a_id, sa = _sub_ready(app, admin, "sa")
        r1 = sa.post("/api/admin/roles", json={"name": "甲组"}).json()["role_id"]
        r2 = sa.post("/api/admin/roles", json={"name": "乙组"}).json()["role_id"]
        for rid in (r1, r2):
            sa.post(f"/api/admin/roles/{rid}/members", json={"phone": PHONE_X, "name": "张三", "dept": "市场部"})
        sa.post(f"/api/admin/roles/{r1}/members", json={"phone": PHONE_Y, "name": "李四", "dept": "渠道部"})

        res = admin.get("/api/admin/roles").json()
        assert res["member_distinct_total"] == 2  # X 两角色只算 1 人 + Y

        # 账号卡片：超管卡 = 全局汇总；成员数按人去重
        accounts = admin.get("/api/admin/accounts").json()["items"]
        super_card = next(a for a in accounts if a["role_level"] == "super_admin")
        sub_card = next(a for a in accounts if a["username"] != "admin")
        assert super_card["member_count"] == 2 and super_card["role_count"] == 2
        assert sub_card["member_count"] == 2
        # 2026-11-06 口径注解：平台分布 + 今日实例（卡片与面板能互相对账）
        assert sub_card["role_platforms"] == {"zdxlz": 2}
        assert super_card["role_platforms"] == {"zdxlz": 2}
        assert sub_card["instance_count_today"] == 0 and super_card["instance_count_today"] == 0
    finally:
        store.close()


# ── 7. 部门增/改/删仅超管 ──
def test_departments_write_super_only() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        _sub(app, admin, "sa")
        sub = _login(app, "sa", StateStore.DEFAULT_SUB_ADMIN_PASSWORD)
        sub.post("/api/admin/change-password", json={"old_password": StateStore.DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})

        # 超管：建/改/删全通
        r = admin.post("/api/admin/departments", json={"name": "市场部"})
        assert r.status_code == 200, r.text
        dept_id = r.json()["department"]["id"]
        assert admin.put(f"/api/admin/departments/{dept_id}", json={"name": "市场二部"}).status_code == 200
        assert admin.delete(f"/api/admin/departments/{dept_id}").status_code == 200
        # 分级管理员：全部 403（读列表仍可）
        assert sub.post("/api/admin/departments", json={"name": "私设部门"}).status_code == 403
        assert sub.put("/api/admin/departments/1", json={"name": "偷改"}).status_code == 403
        assert sub.delete("/api/admin/departments/1").status_code == 403
        assert sub.get("/api/admin/departments").status_code == 200
    finally:
        store.close()


# ── 8. assume 代登录 ──
def test_assume_login_flow() -> None:
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store)
        admin = _login(app, "admin", "Admin@2026")
        b_id = _sub(app, admin, "sb")

        # 超管 assume sb → 200，Set-Cookie 生效，此后该 client 以 sb 身份操作
        r = admin.post(f"/api/admin/accounts/{b_id}/assume")
        assert r.status_code == 200, r.text
        assert r.json()["profile"]["username"] == "sb"
        assert r.json()["profile"]["is_super_admin"] is False
        admin.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
        # 代管身份：me 接口带 assumed 标识（前端据此显示徽章与切回入口）
        me = admin.get("/api/admin/me").json()
        assert me["assumed"] is True
        # 代管身份：账号管理 403（不是超管）；不能再嵌套 assume；不能 assume 超管
        assert admin.get("/api/admin/accounts").status_code == 403
        assert admin.post(f"/api/admin/accounts/{b_id}/assume").status_code == 403
        assert admin.post("/api/admin/accounts/1/assume").status_code == 403
        # 审计留痕
        assert "admin.assume" in {a["action"] for a in store.list_audit_logs(limit=20)}

        # unassume：代管会话切回原超管（parent_sid 在服务端，前端拿不到 HttpOnly Cookie）
        r2 = admin.post("/api/admin/accounts/unassume")
        assert r2.status_code == 200, r2.text
        assert r2.json()["profile"]["is_super_admin"] is True
        admin.headers.update({"X-CSRF-Token": r2.json()["csrf_token"]})
        assert admin.get("/api/admin/me").json()["assumed"] is False
        assert admin.get("/api/admin/accounts").status_code == 200  # 超管权限回来了
        assert "admin.unassume" in {a["action"] for a in store.list_audit_logs(limit=20)}

        # 普通分级管理员（非代管）调 unassume → 400
        _c_id, sc = _sub_ready(app, admin, "sc")
        assert sc.post("/api/admin/accounts/unassume").status_code == 400

        # 超管新会话：assume 停用账号 → 400；assume 超管本体 → 400
        admin2 = _login(app, "admin", "Admin@2026")
        store.update_admin_account(b_id, enabled=False, actor="admin")
        assert admin2.post(f"/api/admin/accounts/{b_id}/assume").status_code == 400
        assert admin2.post("/api/admin/accounts/1/assume").status_code == 400
    finally:
        store.close()
