# -*- coding: utf-8 -*-
"""2026-11-12 下午 · 删除语义新口径 + 批量移除 + 超长部门名 专项用例。

四块口径（当轮用户拍板，取代旧实现）：
  P1 模板编辑移除角色 → 被移除角色成员（模板剩余角色不含此人）的实例**物理删除**
     （此前零处理 → 图一实锤：tele使用上报的郑佳实例在角色移除后仍挂在列表）。
  P2 删除任务模板 = **物理清除**（行不留库；实例/提交/令牌/日志/快照/磁盘附件一起删），
     旧软删（deleted=1+实例置 expired）与本口径冲突，按用户「清库迁移」需求废除。
  P3 删除角色分组 = **级联清全组成员**（旧实现组里有成员就 409 拒删）；
     被模板引用时仍 409 拦截。
  P4 批量移除成员 + 部门名超 30 字可用（上限 200）。

全部用临时库，不碰 data/im_task.db；时刻通过模块级 CLOCK 控。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.state import BizError, StateStore, MAX_DEPT_NAME_LEN  # noqa: E402

CLOCK = {"now": datetime(2026, 11, 12, 9, 0, 0)}

LONG_DEPT = "市场部华东大区第十分部市场营销处客户服务综合运营与培训中心"  # 27 字
LONG_DEPT_35 = "市场部华东大区第十分部市场营销处客户服务综合运营培训中心第一办公室"  # 34 字（>30）
LONG_DEPT_40 = "综合运营管理部华东区域中心第十分部市场营销服务处客户服务中心办公室档案科"  # >30 字


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_p_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="imtask_p_"))


def _seed(store: StateStore, name: str, phone: str, *, dept: str = "市场部", bind: bool = True) -> str:
    rid = store.create_role(name, actor="t")["role_id"]
    store.add_role_member(rid, phone, f"用户{phone[-4:]}", dept, actor="t")
    if bind:
        store.upsert_delivery_target(phone, f"uid_zdxlz_{phone[-4:]}", platform="zdxlz")
    return rid


def _tpl(store: StateStore, rids, name: str = "tele使用上报") -> dict:
    return store.create_template(
        {
            "name": name, "kind": "form", "role_ids": list(rids), "period": "day",
            "remind_at": "09:00", "remind_at_deadline": None,
            "deadline": "18:00", "has_deadline": True, "deadline_type": "same_day", "deadline_time": "18:00",
            "text_tpl": "请提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        },
        actor="t",
    )


def _add_member(store: StateStore, rid: str, phone: str, *, dept: str = "测试部", bind: bool = True) -> None:
    store.add_role_member(rid, phone, f"用户{phone[-4:]}", dept, actor="t")
    if bind:
        store.upsert_delivery_target(phone, f"uid_zdxlz_{phone[-4:]}", platform="zdxlz")


def _inst_rows(store: StateStore, tid: str) -> list:
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            "SELECT id, phone, status FROM task_instance WHERE template_id = ? ORDER BY phone", (tid,)).fetchall()]


# ════════════════════ P1：模板编辑移除角色 → 实例物理删除 ════════════════════

def test_p1_tpl_remove_role_purges_orphan_member_instances() -> None:
    """图一 bug 主用例：模板从 [测试, A组] 改 [测试] ——
    只属于 A组 的成员：实例（未完成+已完成）、提交、令牌全部物理删除；
    还挂在 测试 组的人（一人多角色）：实例原样保留。"""
    store = _store()
    try:
        r_test = _seed(store, "测试", "17313771297")          # 角色测试：郑佳 17313771297
        r_a = _seed(store, "A组", "13900002002")             # 角色A组：只有“用户2002”
        both = "13900003003"                                  # 用户3003 同时挂 测试+A组
        _add_member(store, r_test, both)                      # 3003 挂进保留组「测试」
        _add_member(store, r_a, both)                         # 3003 也挂进将被移除的「A组」
        tpl = _tpl(store, [r_test, r_a], "tele使用上报")

        pkey = "2026-11-12"
        made = store.ensure_template_instances(tpl["template_id"], pkey, "2026-11-12 18:00:00")
        by_phone = {m["phone"]: m for m in made}
        assert set(by_phone) == {"17313771297", "13900002002", "13900003003"}, by_phone.keys()

        # 用户2002 在上一周期已 approved（已完成）+ 本周期 pending —— 两种都要清
        store.ensure_template_instances(tpl["template_id"], "2026-11-11", "2026-11-11 18:00:00")
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE task_instance SET status='approved' WHERE template_id=? AND period_key='2026-11-11'",
                (tpl["template_id"],))

        # 用户2002 有提交与令牌痕迹 → 一并清
        with store.lock, store.conn:
            _i2002 = store.conn.execute(
                "SELECT id FROM task_instance WHERE template_id=? AND phone='13900002002' AND period_key=?",
                (tpl["template_id"], pkey)).fetchone()
            store.conn.execute(
                "INSERT INTO submission(instance_id, fields_json, source, status, created_at) VALUES (?,?,?,?,?)",
                (int(_i2002["id"]), '{}', 'h5', 'approved', '2026-11-12 10:00:00'))
            store.conn.execute(
                "INSERT INTO h5_token(token_id, phone, template_id, period_key, instance_id, purpose,"
                " created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                ("tk_2002_1", "13900002002", tpl["template_id"], pkey, int(_i2002["id"]),
                 "submit", "2026-11-12 09:00:00", "2026-11-13 09:00:00"))

        # ── 移除 A组：模板角色 [测试, A组] → [测试] ──
        store.update_template(tpl["template_id"], {"role_ids": [r_test]}, actor="t")

        rows = _inst_rows(store, tpl["template_id"])
        phones = [r["phone"] for r in rows]
        # 只有 2002 的没了（含上一周期已完成实例）；郑佳与 双角色3003 保留
        assert "13900002002" not in phones, f"A组成员实例未清：{rows}"
        assert "17313771297" in phones and "13900003003" in phones, rows
        with store.lock:
            left_submission = store.conn.execute(
                "SELECT COUNT(1) FROM submission WHERE instance_id NOT IN (SELECT id FROM task_instance)").fetchone()[0]
            # 2026-11-12 下午（四-4）：被移除成员的 h5_token 行**保留**作 tombstone，
            # 旧链接点开统一提示「任务已关闭」（不再是无效令牌）
            left_token = store.conn.execute(
                "SELECT COUNT(1) FROM h5_token WHERE instance_id NOT IN (SELECT id FROM task_instance)").fetchone()[0]
        assert int(left_submission) == 0, "被删实例的提交残留"
        assert int(left_token) >= 1, "被移除成员的令牌必须保留作「任务已关闭」识别"
        with store.lock:
            purged = store.conn.execute(
                "SELECT COUNT(1) FROM task_instance WHERE template_id=? AND phone='13900002002'",
                (tpl["template_id"],)).fetchone()[0]
        assert purged == 0, "A组成员（含已完成）实例必须物理删除"
    finally:
        store.close()


def test_p1_tpl_role_edit_without_removal_keeps_instances() -> None:
    """不移除角色（原样保存 / 只加角色）→ 实例零变化（守卫只对“移除”生效）。"""
    store = _store()
    try:
        r1 = _seed(store, "测试", "17313771297")
        r2 = _seed(store, "A组", "13900002002")
        tpl = _tpl(store, [r1, r2])
        store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-12 18:00:00")

        # 原样保存（同一份 role_ids）
        store.update_template(tpl["template_id"], {"role_ids": [r1, r2]}, actor="t")
        # 只增不减（新增角色 r3）
        r3 = _seed(store, "B组", "13900004004")
        store.update_template(tpl["template_id"], {"role_ids": [r1, r2, r3]}, actor="t")
        rows = _inst_rows(store, tpl["template_id"])
        assert [r["phone"] for r in rows] == ["13900002002", "17313771297"], rows
    finally:
        store.close()


def test_p1_tpl_remove_role_keeps_instance_of_multi_role_member() -> None:
    """被移除角色的成员若仍挂在模板剩余角色 → 实例保留（与单删成员同判据）。"""
    store = _store()
    try:
        r1 = _seed(store, "测试", "17313771297")
        r2 = _seed(store, "A组", "13900002002")
        both = "13900003003"
        _add_member(store, r1, both)   # 3003 同时在 测试（保留组）与 A组（被移除组）
        _add_member(store, r2, both)
        tpl = _tpl(store, [r1, r2])
        made = store.ensure_template_instances(tpl["template_id"], "2026-11-12", "2026-11-12 18:00:00")
        assert {m["phone"] for m in made} == {"17313771297", "13900002002", "13900003003"}

        store.update_template(tpl["template_id"], {"role_ids": [r1]}, actor="t")
        rows = _inst_rows(store, tpl["template_id"])
        by_phone = {r["phone"]: r for r in rows}
        assert "13900003003" in by_phone and by_phone["13900003003"]["status"] == "pending", rows
    finally:
        store.close()


# ════════════════════ P2：删除模板 = 物理清除 ════════════════════

def test_p2_delete_template_purges_all_rows_and_files() -> None:
    """删除模板：模板行 / 实例（未完成+已完成）/ 提交 / 令牌 / 日志 / 快照 / 磁盘附件
    全部物理删除，行不在库里，文件不在盘上。"""
    store = _store()
    uploads = _dir()
    try:
        rid = _seed(store, "测试", "17313771297")
        tpl = _tpl(store, [rid], "tele使用上报")
        tid = tpl["template_id"]
        inst = store.ensure_template_instances(tid, "2026-11-12", "2026-11-12 18:00:00")[0]

        # 附件文件 + upload_ref + 提交 + 令牌 + 提醒日志 + 数据快照
        (uploads / "2026-11-12" / tid).mkdir(parents=True, exist_ok=True)
        file_rel = f"2026-11-12/{tid}/u_001.png"
        (uploads / file_rel).write_bytes(b"\x89PNG fake")
        store.issue_h5_token("17313771297", tid, "2026-11-12", inst["id"])
        with store.lock, store.conn:
            store.conn.execute(
                "INSERT INTO upload_ref(ref, phone, template_id, file_name, mime, size, rel_path, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                ("u_001", "17313771297", tid, "photo.png", "image/png", 10, file_rel, "2026-11-12 10:00:00"))
            store.conn.execute(
                "INSERT INTO submission(instance_id, file_ref, file_name, mime, size, source, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (int(inst["id"]), "u_001", "photo.png", "image/png", 10, "h5", "submitted", "2026-11-12 10:00:00"))
            store.conn.execute(
                "INSERT INTO reminder_log(template_id, instance_id, phone, kind, planned_at, result, idem_key)"
                " VALUES (?,?,?,?,?,?,?)",
                (tid, int(inst["id"]), "17313771297", "remind", "2026-11-12 09:00:00", "ok", f"{tid}|1|17313771297"))
            store.conn.execute(
                "INSERT INTO data_snapshot(source_key, period_key, values_json, updated_by, updated_at)"
                " VALUES (?,?,?,?,?)",
                (tid, "2026-11-12", '{}', "t", "2026-11-12 09:00:00"))

        res = store.delete_template(tid, actor="t", uploads_dir=uploads)
        assert res["ok"] is True and res["deleted"]["task_instance"] == 1, res
        with store.lock:
            counts: dict[str, int] = {}
            for t in ("task_template", "task_instance", "reminder_log", "upload_ref"):
                counts[t] = int(store.conn.execute(
                    f"SELECT COUNT(1) FROM {t} WHERE template_id = ?", (tid,)).fetchone()[0] or 0)
            counts["data_snapshot"] = int(store.conn.execute(
                "SELECT COUNT(1) FROM data_snapshot WHERE source_key = ?", (tid,)).fetchone()[0] or 0)
            counts["submission"] = int(store.conn.execute("SELECT COUNT(1) FROM submission").fetchone()[0] or 0)
            # 2026-11-12 下午（四-1）：h5_token 保留作「任务已关闭」识别（tombstone）
            counts["h5_token_kept"] = int(store.conn.execute(
                "SELECT COUNT(1) FROM h5_token WHERE template_id = ?", (tid,)).fetchone()[0] or 0)
        for k in ("task_template", "task_instance", "reminder_log", "upload_ref", "data_snapshot", "submission"):
            assert counts[k] == 0, f"删除模板后 {k} 仍有 {counts[k]} 行残留"
        assert counts["h5_token_kept"] >= 1, "令牌行必须保留作失效识别"
        assert store.get_template(tid, include_deleted=True) is None, "模板行必须物理删除"
        assert not (uploads / file_rel).exists(), "磁盘附件必须删除"
        with store.lock:
            audit = store.conn.execute(
                "SELECT COUNT(1) FROM admin_audit WHERE action='template.purge' AND target_id=?", (tid,)).fetchone()[0]
        assert int(audit) == 1, "必须有 template.purge 审计"
    finally:
        store.close()


def test_p2_delete_template_missing_404() -> None:
    store = _store()
    try:
        try:
            store.delete_template("t_nope", actor="t")
            raise AssertionError("不存在模板必须 404")
        except BizError as exc:
            assert exc.status_code == 404, exc
    finally:
        store.close()


# ════════════════════ P3：删除角色 = 级联清全组成员 ════════════════════

def test_p3_delete_role_purges_all_members() -> None:
    """删组即清全组：名册行、绑定（该平台无其它角色归属者）全部删；
    模板未被本组引用时才允许删；返回删除明细。"""
    store = _store()
    try:
        r_a = _seed(store, "A组", "13900002002")            # 2002 仅 A组
        r_c = _seed(store, "C组", "17313771297")            # 郑佳 在 C组（平台绑定保留的对照）
        dual = "13900003003"
        _add_member(store, r_a, dual)                        # 3003 同时在 A组 与 C组
        _add_member(store, r_c, dual)
        _add_member(store, r_a, "13900005005")              # 5005 仅 A组

        res = store.delete_role(r_a, actor="t")
        assert res["deleted_members"] == 3, res
        with store.lock:
            left = store.conn.execute(
                "SELECT COUNT(1) FROM member m JOIN role r ON r.role_id = m.role_id WHERE r.role_id = ?", (r_a,)).fetchone()[0]
            role_left = store.conn.execute("SELECT COUNT(1) FROM role WHERE role_id = ?", (r_a,)).fetchone()[0]
            bind_2002 = store.conn.execute(
                "SELECT COUNT(1) FROM delivery_target WHERE phone = '13900002002'").fetchone()[0]
            bind_5005 = store.conn.execute(
                "SELECT COUNT(1) FROM delivery_target WHERE phone = '13900005005'").fetchone()[0]
            bind_dual = store.conn.execute(
                "SELECT COUNT(1) FROM delivery_target WHERE phone = '13900003003'").fetchone()[0]
            audit = store.conn.execute(
                "SELECT COUNT(1) FROM admin_audit WHERE action='role.purge' AND target_id = ?", (r_a,)).fetchone()[0]
        assert int(left) == 0 and int(role_left) == 0, "A组名册与本体必须清空"
        assert int(bind_2002) == 0 and int(bind_5005) == 0, "仅 A组成员（无其它角色）绑定须删"
        assert int(bind_dual) == 1, "仍在 C组者的绑定必须保留"
        assert int(audit) == 1, "必须有 role.purge 审计"
        # C组的成员不动（级联只清本组）
        with store.lock:
            c_members = store.conn.execute(
                "SELECT COUNT(1) FROM member m JOIN role r ON r.role_id = m.role_id WHERE r.role_id = ?", (r_c,)).fetchone()[0]
        assert int(c_members) == 2, "C组成员不受影响（郑佳 + 3003）"
    finally:
        store.close()


def test_p3_delete_role_blocked_when_template_references() -> None:
    """仍有任务模板引用本组 → 409 拦下（引导先删模板），组与成员原样保留。"""
    store = _store()
    try:
        r_a = _seed(store, "A组", "13900002002")
        r_b = _seed(store, "B组", "17313771297")
        _tpl(store, [r_a, r_b], "引用A组")
        try:
            store.delete_role(r_a, actor="t")
            raise AssertionError("被模板引用时必须 409")
        except BizError as exc:
            assert exc.status_code == 409 and exc.code == "ROLE_IN_USE", (exc.status_code, exc.code)
        with store.lock:
            role_cnt = store.conn.execute("SELECT COUNT(1) FROM role WHERE role_id = ?", (r_a,)).fetchone()[0]
            mem_cnt = store.conn.execute(
                "SELECT COUNT(1) FROM member m JOIN role r ON r.role_id = m.role_id WHERE r.role_id = ?", (r_a,)).fetchone()[0]
        assert int(role_cnt) == 1 and int(mem_cnt) == 1, "拦截时组与成员必须原样保留"
    finally:
        store.close()


# ════════════════════ P4：批量移除成员 + 超长部门名 ════════════════════

def test_p4_batch_delete_members() -> None:
    store = _store()
    try:
        rid = _seed(store, "测试", "13900002002")
        _add_member(store, rid, "13900003003")
        _add_member(store, rid, "13900005005")

        res = store.delete_role_members(rid, ["13900002002", "13900003003", "17399999999"], actor="t")
        assert res["deleted"] == ["13900002002", "13900003003"], res
        assert [f["phone"] for f in res["failed"]] == ["17399999999"], res
        with store.lock:
            left = [r["phone"] for r in store.conn.execute(
                "SELECT m.phone FROM member m JOIN role r ON r.role_id = m.role_id WHERE r.role_id = ? ORDER BY m.phone",
                (rid,)).fetchall()]
            audit = store.conn.execute(
                "SELECT COUNT(1) FROM admin_audit WHERE action='member.batch_delete'").fetchone()[0]
        assert left == ["13900005005"], left
        assert int(audit) == 1, "必须有 member.batch_delete 审计"
    finally:
        store.close()


def test_p4_batch_delete_validation() -> None:
    store = _store()
    try:
        rid = _seed(store, "测试", "13900002002")
        for bad in ([], ["", "   "] * 2):
            try:
                store.delete_role_members(rid, bad, actor="t")
                raise AssertionError("空列表必须 400")
            except BizError as exc:
                assert exc.status_code == 400 and exc.code == "VALIDATION_FAILED", (exc.status_code, exc.code)
        try:
            store.delete_role_members(rid, [f"1390000{n:04d}" for n in range(501)], actor="t")
            raise AssertionError("超过 500 人必须 400")
        except BizError as exc:
            assert exc.status_code == 400 and exc.code == "VALIDATION_FAILED", (exc.status_code, exc.code)
    finally:
        store.close()


def test_p4_department_names_over_30_chars() -> None:
    """官方通讯录部门名常超 30：创建/改名/导入全部放行（上限 200）。"""
    store = _store()
    try:
        n35 = "市场部华东大区第十分部市场营销处客户服务综合运营培训中心第一办公室"  # 35 字
        assert len(n35) > 30 and len(n35) <= MAX_DEPT_NAME_LEN
        dept = store.create_department(n35, actor="t")
        got = store.list_departments()
        assert any(d["name"] == n35 for d in got), "超 30 字部门创建后必须能查到"
        store.update_department(dept["id"], f"{n35}改", actor="t")
        assert any(d["name"].endswith("改") for d in store.list_departments()), "超 30 字部门改名必须生效"

        # 上限放行 + 越界拦截
        n200 = "部" * MAX_DEPT_NAME_LEN
        store.create_department(n200, actor="t")
        try:
            store.create_department("部" * (MAX_DEPT_NAME_LEN + 1), actor="t")
            raise AssertionError("超过 200 字必须 400")
        except BizError as exc:
            assert exc.status_code == 400 and exc.code == "VALIDATION_FAILED", (exc.status_code, exc.code)

        # CSV 导入带超 30 字部门 → 行合法 + 自动建档
        rid = store.create_role("导入组", actor="t")["role_id"]
        res = store.import_role_members(rid, [
            {"row": 1, "name": "郑佳", "phone": "17313771297", "dept": LONG_DEPT_35},
        ], dry_run=False, actor="t")
        assert not res["invalid"], res
        assert any(d["name"] == LONG_DEPT_35 for d in store.list_departments()), "导入应自动建超长部门选项"
        with store.lock:
            member_dept = store.conn.execute(
                "SELECT dept FROM member m JOIN role r ON r.role_id = m.role_id WHERE r.role_id = ? AND m.phone = '17313771297'",
                (rid,)).fetchone()
        assert member_dept and member_dept["dept"] == LONG_DEPT_35, "成员行上的超长部门必须原样落库"
    finally:
        store.close()


# ════════════════════ 组织架构导入路径的超长部门 ════════════════════

def test_p4_org_import_accepts_long_dept() -> None:
    """组织架构导入（import_members_from_org 路径）同样接受超 30 字部门名。"""
    store = _store()
    try:
        assert len(LONG_DEPT_40) > 30
        rid = store.create_role("组织组", actor="t")["role_id"]
        res = store.import_members_from_org(
            rid,
            [{"phone": "17313771297", "name": "郑佳", "department": LONG_DEPT_40, "external_id": "uid_1297"}],
            actor="t",
        )
        assert not res["invalid"], res
        assert res["added"] >= 1, res
        with store.lock:
            row = store.conn.execute(
                "SELECT dept FROM member m JOIN role r ON r.role_id = m.role_id WHERE r.role_id = ? AND m.phone = '17313771297'",
                (rid,)).fetchone()
        assert row and row["dept"] == LONG_DEPT_40
    finally:
        store.close()
