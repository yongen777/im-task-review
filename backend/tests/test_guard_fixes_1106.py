# -*- coding: utf-8 -*-
"""2026-11-06 修补两处纵深防御缺口（workbuddy，分支 1af1f3b；teleagent 合并加固）。

缺口①：部门删除无引用检查 → 超管删掉在用部门后，成员表单里选不到自己当前的部门。
缺口②：ensure_template_instances 未查 owner 状态 → 直调它可绕过「停用账号即停发」。

两条都按「先写用例 → 红 → 修 → 绿」走，确保用例真的有区分力。
2026-11-06 teleagent 合并加固：原版 check() 只记录/打印、无断言 —— pytest 下守卫失效
用例仍然全绿（原红灯走的是 run_all 退出码）。现改为 check 即断言（AssertionError），
pytest 与独立执行两条路都真正红得出来；其余逻辑与原提交一致。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.state import BizError, StateStore

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    """条件不满足立即断言失败（红线：反向验证必须红在断言）。"""
    if not cond:
        raise AssertionError(f"{name}" + (f" ← {detail}" if detail else ""))
    PASS.append(name)
    print("  [PASS] " + name)


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_guard_")) / "im.db")


def _admin(store: StateStore, username: str = "admin") -> int:
    store.upsert_admin_user(username, "Admin@2026", must_change=False)
    return 1


def test_dept_delete_refuses_when_in_use() -> None:
    """缺口①：部门下还有成员时，删除必须被拒绝并提示先迁移。"""
    print("\n【缺口①】部门删除引用检查")
    store = _store()
    try:
        aid = _admin(store)
        rid = store.create_role("甲组", actor="t", platform="zdxlz", owner_id=aid)["role_id"]
        store.add_role_member(rid, "13700000001", "张三", "市场部", owner_id=aid)

        # 建一个在用部门
        store.conn.execute("INSERT INTO dept_option(name, created_at) VALUES ('市场部', '2026-11-06 00:00:00')")
        store.conn.commit()
        row = store.conn.execute("SELECT id, name FROM dept_option WHERE name = '市场部'").fetchone()
        did = int(row["id"])

        # 删除在用部门 → 应抛 BizError（且部门还在）
        raised = None
        try:
            store.delete_department(did, actor="admin")
        except BizError as exc:
            raised = exc
        check("删除在用部门被拒绝", raised is not None,
              f"实际未抛异常（部门被删了）")
        if raised is not None:
            check("错误码为 DEPT_IN_USE", getattr(raised, "code", "") == "DEPT_IN_USE",
                  f"实际 {getattr(raised, 'code', '')} / {raised}")
            check("提示里带出受影响成员数", "1" in str(raised.message),
                  f"实际 {raised.message}")
        left = store.conn.execute("SELECT COUNT(1) FROM dept_option WHERE id = ?", (did,)).fetchone()[0]
        check("部门仍在库里（未被删除）", int(left) == 1, f"剩 {left}")

        # 成员迁走后 → 应能删
        store.conn.execute("UPDATE member SET dept = '运维部' WHERE dept = '市场部'")
        store.conn.commit()
        ok = True
        try:
            store.delete_department(did, actor="admin")
        except BizError as exc:
            ok = False
            print("    迁移后仍被拒:", exc)
        check("迁移完后可正常删除", ok)

        # 空引用部门（无人使用）→ 应能删
        store.conn.execute("INSERT INTO dept_option(name, created_at) VALUES ('空部门', '2026-11-06 00:00:00')")
        store.conn.commit()
        eid = int(store.conn.execute("SELECT id FROM dept_option WHERE name='空部门'").fetchone()["id"])
        ok2 = True
        try:
            store.delete_department(eid, actor="admin")
        except BizError:
            ok2 = False
        check("无人使用的部门可删除", ok2)
    finally:
        store.close()


def test_ensure_instances_respects_disabled_owner() -> None:
    """缺口②：模板所属管理员被停用后，直调 ensure_template_instances 也不应生成实例。"""
    print("\n【缺口②】ensure_template_instances 的 owner 停用校验")
    store = _store()
    try:
        aid = _admin(store)
        sub_id = int(store.create_admin_account(
            username="a1", display_name="甲", allowed_platforms=["zdxlz"], actor="admin")["id"])
        store.upsert_delivery_target("13700000001", "uid_z", platform="zdxlz")
        rid = store.create_role("甲组", actor="t", platform="zdxlz", owner_id=sub_id)["role_id"]
        store.add_role_member(rid, "13700000001", "张三", "市场部", owner_id=sub_id)
        tpl = store.create_template({
            "name": "T", "kind": "form", "role_ids": [rid], "text_tpl": "x",
            "remind_at": "09:00", "deadline": "18:00", "period": "day",
            "schema_json": [{"key": "a", "label": "A", "type": "text"}],
        }, owner_id=sub_id)

        # 启用时：能生成
        inst = store.ensure_template_instances(tpl["template_id"], "2026-11-06", "2026-11-06 23:59:00")
        check("管理员启用时能生成实例", len(inst) >= 1, f"生成 {len(inst)}")

        # 停用管理员后：直调也必须返回空（纵深防御）
        store.update_admin_account(sub_id, enabled=False, actor="admin")
        n_before = int(store.conn.execute(
            "SELECT COUNT(1) FROM task_instance WHERE template_id = ?", (tpl["template_id"],)).fetchone()[0])
        inst2 = store.ensure_template_instances(tpl["template_id"], "2026-11-07", "2026-11-07 23:59:00")
        n_after = int(store.conn.execute(
            "SELECT COUNT(1) FROM task_instance WHERE template_id = ?", (tpl["template_id"],)).fetchone()[0])
        check("owner 停用后直调 ensure 不生成实例", len(inst2) == 0 and n_after == n_before,
              f"返回 {len(inst2)} 条，实例数 {n_before} → {n_after}")

        # 恢复后可继续生成（可逆）
        store.update_admin_account(sub_id, enabled=True, actor="admin")
        inst3 = store.ensure_template_instances(tpl["template_id"], "2026-11-08", "2026-11-08 23:59:00")
        check("owner 恢复启用后可继续生成（可逆）", len(inst3) >= 1, f"生成 {len(inst3)}")
    finally:
        store.close()


def test_owner_missing_treated_as_enabled() -> None:
    """回归：owner 缺失的历史数据（owner_id 指向不存在的账号）不应被误伤。"""
    print("\n【回归】owner 缺失时按启用处理")
    store = _store()
    try:
        aid = _admin(store)
        rid = store.create_role("甲组", actor="t", platform="zdxlz", owner_id=aid)["role_id"]
        store.upsert_delivery_target("13700000001", "uid_z", platform="zdxlz")
        store.add_role_member(rid, "13700000001", "张三", "市场部", owner_id=aid)
        tpl = store.create_template({
            "name": "T", "kind": "form", "role_ids": [rid], "text_tpl": "x",
            "remind_at": "09:00", "deadline": "18:00", "period": "day",
            "schema_json": [{"key": "a", "label": "A", "type": "text"}],
        }, owner_id=999)   # 指向不存在的账号
        inst = store.ensure_template_instances(tpl["template_id"], "2026-11-06", "2026-11-06 23:59:00")
        check("owner 不存在时仍可生成（不误伤孤儿数据）", len(inst) >= 1, f"生成 {len(inst)}")
    finally:
        store.close()


def run_all() -> int:
    for fn in (test_dept_delete_refuses_when_in_use,
               test_ensure_instances_respects_disabled_owner,
               test_owner_missing_treated_as_enabled):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            FAIL.append(fn.__name__)
            print(f"  [ERROR] {fn.__name__}: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   - " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(run_all())
