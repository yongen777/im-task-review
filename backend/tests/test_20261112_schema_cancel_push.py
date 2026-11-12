# -*- coding: utf-8 -*-
"""2026-11-12 上午新需求批 · B/C/D 三项专项用例（先红后修反向验证的守卫层）。

B —— 上传任务 schema_json 改可选：
    图一实锤：只勾「图片上传」建上传任务模板被 400「表单类型模板必须至少包含 1 个字段」
    拦死。根因有二：① 重构后前端统一传 kind="upload"，后端只认 form/image/file/notice，
    断链 400；② form 模板 schema 强制 ≥1 字段。修复：kind=upload 按勾选模块映射；
    schema 允许空；提交侧字段校验与 H5 实际渲染的 fields 模块对齐（修「schema 有字段
    但页面没渲染、提交却校验字段」的历史死锁）。

C —— 成员移除后未完成实例作废（新状态 cancelled）：
    图二实锤：人已移出「测试」角色，17:14 生成的实例还挂着待上传。修复：移除时未完成
    实例（pending/overdue/rejected）标 cancelled（留痕可审计、不再提醒/流转/可提交）；
    已完成实例（submitted/sent/approved/expired）保持 11-09「移除删干净」口径。

D —— 上传任务一键推送（创建者分级管理员专属）：
    用户拍板原话：9 点推送和 10 点定时各发一条一模一样的提醒（测试用按钮，管理员自行
    判断）。守卫：超管 403 / 模板属主校验 / 同模板 2 分钟软护栏 429 / 同一时刻幂等防抖 /
    只发 pending 且未截止 / cancelled 实例不打扰 / 审计 template.push_now。

全部相对时刻（fake clock），不碰 data/im_task.db。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.api.h5 import _check_submission_window  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

DEFAULT_SUB_ADMIN_PASSWORD = StateStore.DEFAULT_SUB_ADMIN_PASSWORD

PHONE_A = "13900001001"
PHONE_B = "13900001002"

CLOCK = {"now": datetime(2026, 11, 12, 8, 0, 0)}  # 早于 remind_at 09:00，避免激活切分干扰


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_bcd_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _app(store: StateStore, scheduler: Scheduler) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    app.state.scheduler = scheduler
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _new_sub_admin(app: FastAPI, super_admin: TestClient, username: str) -> TestClient:
    """超管建号 → 新号首登改密 → 返回可用业务 client（超管只读，业务数据必须由分级建）。"""
    r = super_admin.post("/api/admin/accounts", json={"username": username, "display_name": username})
    assert r.status_code == 200, r.text
    c = _login(app, username, DEFAULT_SUB_ADMIN_PASSWORD)
    r = c.post("/api/admin/change-password",
               json={"old_password": DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    assert r.status_code == 200, r.text
    return c


def _seed_role_and_members(store: StateStore, owner_id: int, phones=(PHONE_A, PHONE_B)) -> str:
    rid = store.create_role("测试组", actor="t", platform="zdxlz", owner_id=owner_id)["role_id"]
    for i, p in enumerate(phones):
        store.add_role_member(rid, p, f"成员{i}", "市场部", owner_id=owner_id)
        store.upsert_delivery_target(p, f"uid_{p[-4:]}")
    return rid


def _upload_tpl_payload(rid: str, name: str = "图一场景", modules=("image",), fields=None) -> dict:
    return {
        "name": name, "kind": "upload", "role_ids": [rid], "period": "day",
        "remind_at": "09:00", "deadline": "18:00", "text_tpl": "请提交",
        "enabled": True, "need_review": False,
        "spec": {"upload_modules": list(modules), "fields": list(fields or [])},
    }


# ═══════════════════════════ B：schema 可选 ═══════════════════════════

def test_b_upload_kind_image_only_creates_image_template() -> None:
    """图一实锤复现：只勾「图片上传」（无字段）建上传任务必须成功，映射 kind=image。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store, Scheduler(state=store, public_base_url="http://t.example.com"))
        sa = _login(app, "admin", "Admin@2026")
        sub = _new_sub_admin(app, sa, "sa")
        rid = _seed_role_and_members(store, owner_id=2)

        r = sub.post("/api/admin/templates", json=_upload_tpl_payload(rid))
        assert r.status_code == 200, r.text  # 修复前：400 VALIDATION_FAILED
        tpl = r.json()["template"]
        assert tpl["kind"] == "image", tpl["kind"]
        assert not (tpl.get("schema_json") or []), tpl.get("schema_json")
    finally:
        store.close()


def test_b_upload_kind_fields_module_maps_form_and_derives_schema() -> None:
    """勾了「字段报数」→ kind=form，且 schema 从 spec.fields 推导（重构前端不传 schema_json）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store, Scheduler(state=store, public_base_url="http://t.example.com"))
        sa = _login(app, "admin", "Admin@2026")
        sub = _new_sub_admin(app, sa, "sa")
        rid = _seed_role_and_members(store, owner_id=2)

        r = sub.post("/api/admin/templates", json=_upload_tpl_payload(
            rid, name="字段场景", modules=("fields", "image"),
            fields=[{"key": "visit_count", "label": "上门户数", "type": "number", "required": True}],
        ))
        assert r.status_code == 200, r.text
        tpl = r.json()["template"]
        assert tpl["kind"] == "form", tpl["kind"]
        schema = tpl.get("schema_json") or []
        assert len(schema) == 1 and schema[0]["key"] == "visit_count" and schema[0]["required"], schema
    finally:
        store.close()


def test_b_form_accepts_empty_schema_variants() -> None:
    """B 核心：form 模板 schema_json 允许空（缺省 / [] / '' / None 全部放行）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store, Scheduler(state=store, public_base_url="http://t.example.com"))
        sa = _login(app, "admin", "Admin@2026")
        sub = _new_sub_admin(app, sa, "sa")
        rid = _seed_role_and_members(store, owner_id=2)

        for i, schema in enumerate([None, [], "", "[]"]):
            payload = _upload_tpl_payload(rid, name=f"空schema-{i}", modules=("text",))
            payload["kind"] = "form"  # 直传 form（老前端/测试路径）
            if schema is not None:
                payload["schema_json"] = schema
            r = sub.post("/api/admin/templates", json=payload)
            assert r.status_code == 200, f"schema={schema!r} -> {r.text}"
            assert not (r.json()["template"].get("schema_json") or [])
    finally:
        store.close()


def test_b_schema_guard_still_rejects_invalid() -> None:
    """守卫不放松：非法 JSON / 非数组 / 字段缺 key / type 非法仍然 400。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        app = _app(store, Scheduler(state=store, public_base_url="http://t.example.com"))
        sa = _login(app, "admin", "Admin@2026")
        sub = _new_sub_admin(app, sa, "sa")
        rid = _seed_role_and_members(store, owner_id=2)

        bad_cases = [
            {"kind": "form", "schema_json": "not json"},
            {"kind": "form", "schema_json": {"a": 1}},
            {"kind": "form", "schema_json": [{"label": "x", "type": "text"}]},
            {"kind": "form", "schema_json": [{"key": "a", "label": "x", "type": "emoji"}]},
        ]
        for extra in bad_cases:
            payload = _upload_tpl_payload(rid, modules=("text",))
            payload.update(extra)
            r = sub.post("/api/admin/templates", json=payload)
            assert r.status_code == 400, f"{extra} -> {r.text}"
            assert r.json()["code"] == "VALIDATION_FAILED"
    finally:
        store.close()


def test_b_submit_skips_field_validation_when_fields_not_rendered() -> None:
    """渲染/校验对齐：H5 没渲染 fields 模块 → schema 必填字段不再死锁；渲染了 → 必填照拦。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        rid = store.create_role("组", actor="t", platform="zdxlz", owner_id=1)["role_id"]
        store.add_role_member(rid, PHONE_A, "张三", owner_id=1)
        store.upsert_delivery_target(PHONE_A, "uid_a")
        base = {"name": "老模板", "role_ids": [rid], "remind_at": "09:00", "deadline": "18:00",
                "text_tpl": "x", "period": "day", "enabled": True, "need_review": False}

        # 场景1：schema 有必填字段，但 upload_modules 只勾 text（H5 无字段输入框）→ 提交不死锁
        tpl1 = store.create_template({**base, "kind": "form",
                                      "schema_json": [{"key": "n", "label": "户数", "type": "number", "required": True}],
                                      "spec": {"upload_modules": ["text"]}}, actor="t")
        inst1 = store.ensure_template_instances(tpl1["template_id"], "2026-11-12", "2026-11-12 23:59:59")
        tok1 = store.issue_h5_token(PHONE_A, tpl1["template_id"], "2026-11-12", inst1[0]["id"])
        res = store.submit_h5_task(raw_token=tok1, note="今日已完成")  # 修复前：400 请填写户数
        assert res["ok"] and res["status"] == "approved", res
        row = store.conn.execute("SELECT fields_json FROM submission WHERE instance_id = ?",
                                 (inst1[0]["id"],)).fetchone()
        assert row["fields_json"] is None, row["fields_json"]

        # 场景2：upload_modules 含 fields（H5 渲染字段）→ 必填校验保持原行为
        tpl2 = store.create_template({**base, "name": "字段模板", "kind": "form",
                                      "schema_json": [{"key": "n", "label": "户数", "type": "number", "required": True}],
                                      "spec": {"upload_modules": ["fields"]}}, actor="t")
        inst2 = store.ensure_template_instances(tpl2["template_id"], "2026-11-12", "2026-11-12 23:59:59")
        tok2 = store.issue_h5_token(PHONE_A, tpl2["template_id"], "2026-11-12", inst2[0]["id"])
        try:
            store.submit_h5_task(raw_token=tok2)  # 必填没填 → 400（守卫仍在）
            raise AssertionError("fields 模块渲染时必填校验失效（BUG）")
        except BizError as e:
            assert e.status_code == 400 and "户数" in e.message, (e.status_code, e.message)
    finally:
        store.close()


# ═══════════════════ C：移除作废（cancelled）═══════════════════

def test_c_remove_member_cancels_open_and_deletes_done() -> None:
    """移除成员：未完成实例 → cancelled（留痕）；已完成实例 → 删除（11-09 口径保持）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        rid = store.create_role("测试", actor="t", platform="zdxlz", owner_id=1)["role_id"]
        store.add_role_member(rid, PHONE_A, "马玲", owner_id=1)
        store.upsert_delivery_target(PHONE_A, "uid_a")
        base = {"name": "T", "role_ids": [rid], "remind_at": "09:00", "deadline": "18:00",
                "text_tpl": "x", "period": "day", "enabled": True, "need_review": False}
        tpl = store.create_template({**base, "kind": "form", "schema_json": []}, actor="t")

        # 当期 pending + 次期 pending + 当期已提交置 approved（复用同一实例提交）
        i_today = store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-12 23:59:59")
        i_next = store.ensure_template_instances(tpl["template_id"], "2026-11-13", "2026-11-13 23:59:59")
        assert len(i_today) == 1 and len(i_next) == 1
        tok = store.issue_h5_token(PHONE_A, tpl["template_id"], "2026-11-12", i_today[0]["id"])
        store.submit_h5_task(raw_token=tok)
        assert store.get_instance(i_today[0]["id"])["status"] == "approved"

        store.delete_role_member(rid, PHONE_A, actor="t")

        # 已完成 → 删干净（历史口径不回归）
        assert store.get_instance(i_today[0]["id"]) is None
        # 未完成 → cancelled 留痕
        kept = store.get_instance(i_next[0]["id"])
        assert kept is not None and kept["status"] == "cancelled", (kept or {}).get("status")
        # 审计记录两种处置的数量
        audit = store.conn.execute(
            "SELECT detail FROM admin_audit WHERE action = 'member.delete' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        import json as _json
        detail = _json.loads(audit["detail"])
        assert detail.get("removed_instances") == 1 and detail.get("cancelled_instances") == 1, detail
    finally:
        store.close()


def test_c_cancelled_not_resurrected_not_advanced_not_delivered() -> None:
    """cancelled 的三重隔离：生成侧不复活 / 流转不带动 / 调度不投递 / 旧链接不可提交。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        rid = store.create_role("测试", actor="t", platform="zdxlz", owner_id=1)["role_id"]
        store.add_role_member(rid, PHONE_A, "马玲", owner_id=1)
        store.upsert_delivery_target(PHONE_A, "uid_a")
        base = {"name": "T", "role_ids": [rid], "remind_at": "09:00", "deadline": "18:00",
                "text_tpl": "x", "period": "day", "enabled": True, "need_review": False}
        tpl = store.create_template({**base, "kind": "form", "schema_json": []}, actor="t")
        insts = store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-12 23:59:59")
        inst_id = insts[0]["id"]
        tok = store.issue_h5_token(PHONE_A, tpl["template_id"], "2026-11-12", inst_id)

        store.delete_role_member(rid, PHONE_A, actor="t")
        assert store.get_instance(inst_id)["status"] == "cancelled"

        # ① 生成侧不复活（唯一键 + cancelled 状态保留）
        again = store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-12 23:59:59")
        assert len(again) == 1 and again[0]["status"] == "cancelled", [(x["status"]) for x in again]

        # ② 状态推进不带动（advance 只流转 pending/overdue/rejected）
        store.advance_instance_statuses(datetime(2026, 11, 14, 10, 0))
        assert store.get_instance(inst_id)["status"] == "cancelled"

        # ③ 调度不投递：到点跑一轮（run_once 内部 advance 也不会把它变 overdue）
        ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
        sched = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                          now_fn=lambda: CLOCK["now"])
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 30)
        sched.run_once()
        assert ntf.sent_messages == [], ntf.sent_messages
        assert store.get_instance(inst_id)["status"] == "cancelled"

        # ④ 旧链接提交被服务端硬拦截 + H5 窗口检查关闭
        # 2026-11-12 下午新口径（四-3）：成员被移出角色 → cancelled 统一「任务已关闭」（403 TASK_CLOSED）
        try:
            store.submit_h5_task(raw_token=tok)
            raise AssertionError("cancelled 实例仍可提交（BUG）")
        except BizError as e:
            assert (e.status_code, e.code, e.message) == (403, "TASK_CLOSED", "任务已关闭"), (e.status_code, e.code, e.message)
        window_open, reason = _check_submission_window(store, store.get_instance(inst_id), tok)
        assert window_open is False and "取消" in (reason or ""), (window_open, reason)
    finally:
        store.close()


def test_c_sibling_role_membership_keeps_instance_alive() -> None:
    """一人多角色：模板挂 A/B 两角色，从 A 移除但仍在 B → 实例保留（不误作废）。"""
    store = _store()
    try:
        store.upsert_admin_user("admin", "Admin@2026", must_change=False)
        ra = store.create_role("甲组", actor="t", platform="zdxlz", owner_id=1)["role_id"]
        rb = store.create_role("乙组", actor="t", platform="zdxlz", owner_id=1)["role_id"]
        store.add_role_member(ra, PHONE_A, "张三", owner_id=1)
        store.add_role_member(rb, PHONE_A, "张三", owner_id=1)
        store.upsert_delivery_target(PHONE_A, "uid_a")
        tpl = store.create_template(
            {"name": "双角色", "kind": "form", "schema_json": [], "role_ids": [ra, rb],
             "remind_at": "09:00", "deadline": "18:00", "text_tpl": "x", "period": "day",
             "enabled": True, "need_review": False}, actor="t")
        insts = store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-12 23:59:59")
        assert len(insts) == 1

        store.delete_role_member(ra, PHONE_A, actor="t")  # 移出甲组，仍在乙组
        kept = store.get_instance(insts[0]["id"])
        assert kept is not None and kept["status"] == "pending", (kept or {}).get("status")
    finally:
        store.close()


# ═══════════════════ D：一键推送 ═══════════════════

def _d_setup(owner="sa"):
    """通用脚手架：超管 + 属主分级 sa + 前端同款 upload 模板 + Fake 调度。返回 (store, app, sa, sub, sched, ntf, tpl_id)。"""
    CLOCK["now"] = datetime(2026, 11, 12, 8, 0, 0)  # 重置：绑定时刻须早于 09:00 定时（CLOCK 是模块级共享）
    store = _store()
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
    sched = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                      now_fn=lambda: CLOCK["now"])
    app = _app(store, sched)
    sa = _login(app, "admin", "Admin@2026")
    sub = _new_sub_admin(app, sa, owner)
    rid = _seed_role_and_members(store, owner_id=2)
    r = sub.post("/api/admin/templates", json=_upload_tpl_payload(
        rid, name="推送测试", modules=("fields",),
        fields=[{"key": "n", "label": "户数", "type": "number", "required": False}]))
    assert r.status_code == 200, r.text
    return store, app, sa, sub, sched, ntf, r.json()["template_id"]


def test_d_push_now_permission_matrix_and_delivery() -> None:
    """权限矩阵（超管 403 / 跨属主 403 / 匿名 401 / notice 400）+ 属主推送全链路。"""
    store, app, sa, sub, sched, ntf, tpl_id = _d_setup()
    try:
        # notice 模板挂属主自己的角色（sub 新建角色，owner_id=2）
        rid2 = sub.post("/api/admin/roles", json={"name": "通知组"}).json()["role_id"]
        r_notice = sub.post("/api/admin/templates", json={
            "name": "通知", "kind": "notice", "role_ids": [rid2], "period": "day",
            "text_tpl": "干活", "notice_times": ["09:00"]})
        assert r_notice.status_code == 200, r_notice.text
        notice_id = r_notice.json()["template_id"]

        # 匿名 401
        anon = TestClient(app)
        assert anon.post(f"/api/admin/templates/{tpl_id}/push-now").status_code == 401
        # 超管 403 SUPER_ADMIN_READONLY
        r = sa.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r.status_code == 403 and r.json()["code"] == "SUPER_ADMIN_READONLY", r.text
        # 其他分级管理员（非属主）403 FORBIDDEN_OWNER（middleware 路径归属）
        other = _new_sub_admin(app, sa, "sb")
        r = other.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_OWNER", r.text
        # notice 模板 400 且不占冷却 marker
        r = sub.post(f"/api/admin/templates/{notice_id}/push-now")
        assert r.status_code == 400 and r.json()["code"] == "VALIDATION_FAILED", r.text
        with store.lock:
            assert store.conn.execute(
                "SELECT COUNT(1) FROM reminder_log WHERE template_id = ? AND kind = 'manual_push'",
                (notice_id,)).fetchone()[0] == 0

        # 属主推送：2 名已激活成员 → 各一条，实例状态仍 pending
        CLOCK["now"] = datetime(2026, 11, 12, 10, 0, 0)
        r = sub.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r.status_code == 200, r.text
        stats = r.json()["stats"]
        assert stats["sent"] == 2 and stats["pushed_instances"] == 2, stats
        assert len(ntf.sent_messages) == 2
        insts = store.list_instances_for_period(tpl_id, "2026-11-12")
        assert [i["status"] for i in insts] == ["pending", "pending"]
        # 消息版式与定时提醒一致（【任务提醒】标题 + H5 链接）
        for m in ntf.sent_messages:
            assert m.text.startswith("【任务提醒】推送测试"), m.text[:30]
            assert "/h5/task#t=" in m.text
        # reminder_log 留痕 kind='manual_push'（2 实例行 + 1 marker 行）
        with store.lock:
            kinds = store.conn.execute(
                "SELECT kind, COUNT(*) FROM reminder_log WHERE template_id = ? GROUP BY kind",
                (tpl_id,)).fetchall()
        assert ("manual_push", 3) in [tuple(k) for k in kinds], kinds
        # 审计
        audit = store.conn.execute(
            "SELECT COUNT(1) FROM admin_audit WHERE action = 'template.push_now' AND target_id = ?",
            (tpl_id,)).fetchone()[0]
        assert audit >= 1
    finally:
        store.close()


def test_d_push_now_double_send_with_scheduled_and_same_second_dedupe() -> None:
    """拍板双发语义：定时 09:00 一条 + 手动 10:00 一条（内容同版式、互不幂等）；
    同一秒重复调用 scheduler.push_now → 幂等 0 新增（快速双击防抖由幂等键挡住）。"""
    store, app, sa, sub, sched, ntf, tpl_id = _d_setup()
    try:
        # 定时链路先发（09:00:30 tick）
        CLOCK["now"] = datetime(2026, 11, 12, 9, 0, 30)
        sched.run_once()
        assert len(ntf.sent_messages) == 2  # 每人一条
        scheduled_texts = {m.receive_id: m.text for m in ntf.sent_messages}

        # 手动推送（10:00）→ 与定时各发一条（用户拍板的特批双发）
        CLOCK["now"] = datetime(2026, 11, 12, 10, 0, 0)
        stats = sched.push_now(tpl_id, actor="sa")
        assert stats["sent"] == 2 and stats["pushed_instances"] == 2, stats
        assert len(ntf.sent_messages) == 4
        manual_texts = {m.receive_id: m.text for m in ntf.sent_messages[2:]}
        for rid_key in scheduled_texts:
            # 版式一致：除 H5 token 外逐字相同（token 是每次新签的）
            assert scheduled_texts[rid_key].split("\n")[:3] == manual_texts[rid_key].split("\n")[:3]

        # 同一秒重复 push（调度层幂等）：第二次 0 条（idem 命中已 sent）
        stats2 = sched.push_now(tpl_id, actor="sa")
        assert stats2["sent"] == 0 and stats2["pushed_instances"] == 0, stats2
        assert len(ntf.sent_messages) == 4

        # reminder_log：remind 2 行 + manual_push 2 实例行 + 1 marker（本用例直接走调度层，无 marker）
        with store.lock:
            kinds = dict((k, c) for k, c in store.conn.execute(
                "SELECT kind, COUNT(*) FROM reminder_log WHERE template_id = ? GROUP BY kind", (tpl_id,)))
        assert kinds.get("remind") == 2 and kinds.get("manual_push") == 2, kinds
    finally:
        store.close()


def test_d_push_now_cooldown_window_429() -> None:
    """软护栏：同模板 2 分钟内重复推送 → 429 PUSH_COOLDOWN；满 2 分钟放行。"""
    store, app, sa, sub, sched, ntf, tpl_id = _d_setup()
    try:
        CLOCK["now"] = datetime(2026, 11, 12, 10, 0, 0)
        r1 = sub.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r1.status_code == 200, r1.text

        CLOCK["now"] = datetime(2026, 11, 12, 10, 1, 59)  # 119s < 120s
        r2 = sub.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r2.status_code == 429 and r2.json()["code"] == "PUSH_COOLDOWN", (r2.status_code, r2.text)

        CLOCK["now"] = datetime(2026, 11, 12, 10, 2, 1)  # 121s ≥ 120s 放行
        r3 = sub.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r3.status_code == 200, r3.text
        # 429 那次被护栏挡住：消息总数 = 首次 2 + 放行后 2
        assert len(ntf.sent_messages) == 4, len(ntf.sent_messages)
    finally:
        store.close()


def test_d_push_now_skips_cancelled_and_unactivated() -> None:
    """cancelled 实例不推、未激活成员记 skipped 不崩；全 cancelled → 0 发送。"""
    store, app, sa, sub, sched, ntf, tpl_id = _d_setup()
    try:
        # 绑定有效时先建好当期实例（未激活成员也要有实例才能记 skipped）
        store.ensure_template_instances(tpl_id, "2026-11-12", "2026-11-12 23:59:59")
        # 成员A 移出角色 + 其实例置 cancelled；成员B 摘绑定（usable=0 → 发送时 skipped）
        with store.lock, store.conn:
            store.conn.execute("DELETE FROM member WHERE phone = ?", (PHONE_A,))
            store.conn.execute(
                "UPDATE task_instance SET status = 'cancelled' WHERE template_id = ? AND phone = ?",
                (tpl_id, PHONE_A))
            store.conn.execute("UPDATE delivery_target SET usable = 0 WHERE phone = ?", (PHONE_B,))

        CLOCK["now"] = datetime(2026, 11, 12, 10, 0, 0)
        stats = sched.push_now(tpl_id, actor="sa")
        assert stats["sent"] == 0 and stats["skipped"] == 1 and stats["pushed_instances"] == 0, stats
        assert ntf.sent_messages == []
    finally:
        store.close()
