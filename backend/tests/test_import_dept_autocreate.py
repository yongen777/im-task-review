# -*- coding: utf-8 -*-
"""2026-11-04：CSV 导入的部门若「部门管理」里没有 → 自动建好，不拦截。

用户口径原文：「批量导入的部门，如果在部门管理我没有手动配置到，也不拦截，
在部门管理把这个部门给我创建好就行。」

覆盖边界：
  1. 新部门自动建档 + 成员入库；
  2. 同一批里多个新部门都建好；
  3. 同一新部门出现多行 → 只建一次（不重复、不报冲突）；
  4. 已有部门不重复创建；
  5. 部门名为空 → 允许（成员无部门）；
  6. 部门名超长（>30 字）→ 该行不入库、该部门不建，其余行照常；
  7. dry_run（预演）→ 不写库，只报告将要创建的部门。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.state import StateStore  # noqa: E402

PHONE = "13700005501"
OTHER = "13700005502"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_dept_")) / "im.db")


def _role(store: StateStore) -> str:
    return store.create_role("导入组", actor="t", platform="zdxlz")["role_id"]


def test_new_department_autocreated_and_member_imported() -> None:
    store = _store()
    try:
        rid = _role(store)
        res = store.import_role_members(
            rid, [{"row": 1, "phone": PHONE, "name": "张三", "dept": "渠道拓展部"}],
            dry_run=False, actor="t")
        assert res["will_add"] == 1 and not res["invalid"], res
        assert store.department_exists("渠道拓展部"), "新部门应自动建进部门管理"
        members = store.list_role_members(rid, size=50)["items"]
        assert members and members[0]["dept"] == "渠道拓展部"
    finally:
        store.close()


def test_multiple_new_departments_all_created() -> None:
    store = _store()
    try:
        rid = _role(store)
        res = store.import_role_members(
            rid,
            [{"row": 1, "phone": PHONE, "name": "张三", "dept": "甲部"},
             {"row": 2, "phone": OTHER, "name": "李四", "dept": "乙部"}],
            dry_run=False, actor="t")
        assert res["will_add"] == 2 and not res["invalid"], res
        assert store.department_exists("甲部") and store.department_exists("乙部")
    finally:
        store.close()


def test_same_new_department_twice_created_once() -> None:
    store = _store()
    try:
        rid = _role(store)
        res = store.import_role_members(
            rid,
            [{"row": 1, "phone": PHONE, "name": "张三", "dept": "重复部"},
             {"row": 2, "phone": OTHER, "name": "李四", "dept": "重复部"}],
            dry_run=False, actor="t")
        assert res["will_add"] == 2 and not res["invalid"], res
        names = [d["name"] for d in store.list_departments()]
        assert names.count("重复部") == 1, f"同批同名部门只应建一次：{names}"
    finally:
        store.close()


def test_existing_department_not_duplicated() -> None:
    store = _store()
    try:
        rid = _role(store)
        store.create_department("已有部", actor="t")
        res = store.import_role_members(
            rid, [{"row": 1, "phone": PHONE, "name": "张三", "dept": "已有部"}],
            dry_run=False, actor="t")
        assert res["will_add"] == 1 and not res["invalid"], res
        assert [d["name"] for d in store.list_departments()] == ["已有部"]
    finally:
        store.close()


def test_blank_department_allowed() -> None:
    store = _store()
    try:
        rid = _role(store)
        res = store.import_role_members(
            rid, [{"row": 1, "phone": PHONE, "name": "张三", "dept": ""}],
            dry_run=False, actor="t")
        assert res["will_add"] == 1 and not res["invalid"], res
        assert store.list_departments() == []
        members = store.list_role_members(rid, size=50)["items"]
        assert members and members[0]["dept"] in (None, "")
    finally:
        store.close()


def test_too_long_department_rejects_only_that_row() -> None:
    """2026-11-12 下午口径修正：部门名上限 30 → 200（官方通讯录部门名常超 30 字）。
    40 字不再是「过长」；超过 200 字才整批拒绝，且只拒绝超长那行。"""
    from app.state import MAX_DEPT_NAME_LEN

    store = _store()
    try:
        rid = _role(store)
        over = "超长" * 101  # 202 字 > MAX_DEPT_NAME_LEN(200)
        ok_long = "超长" * 20  # 40 字：旧口径判过长，新口径放行
        res = store.import_role_members(
            rid,
            [{"row": 1, "phone": PHONE, "name": "张三", "dept": over},
             {"row": 2, "phone": OTHER, "name": "李四", "dept": ok_long}],
            dry_run=False, actor="t")
        # 2026-11-09 口径保留：导入为**整批拒绝**（有错误行则整批不写入，待修复后再导）
        assert res["blocked"] is True, res
        assert res["will_add"] == 0, res
        assert any(x["row"] == 1 and "过长" in x["reason"] for x in res["invalid"]), res
        assert not store.department_exists(over), "超上限部门名不应建档"
        assert not store.department_exists(ok_long), "整批拒绝 → 合法那行也不该建档"
        assert not store.list_role_members(rid, size=50)["items"], "整批拒绝 → 一行都不该写入"
    finally:
        store.close()


def test_department_over_30_chars_now_imports() -> None:
    """2026-11-12 下午：30-200 字的部门名（含 40 字）正常导入 + 自动建档。"""
    store = _store()
    try:
        rid = _role(store)
        ok_long = "市场部华东大区第十分部市场营销处客户服务综合运营培训中心第一办公室"  # >30 字
        res = store.import_role_members(
            rid, [{"row": 1, "phone": PHONE, "name": "郑佳", "dept": ok_long}],
            dry_run=False, actor="t")
        assert res["will_add"] == 1 and not res["invalid"], res
        assert store.department_exists(ok_long), "超 30 字部门应自动建档"
        members = store.list_role_members(rid, size=50)["items"]
        assert members and members[0]["dept"] == ok_long
    finally:
        store.close()


def test_dry_run_does_not_write() -> None:
    store = _store()
    try:
        rid = _role(store)
        res = store.import_role_members(
            rid, [{"row": 1, "phone": PHONE, "name": "张三", "dept": "预演部"}],
            dry_run=True, actor="t")
        assert res["dry_run"] is True, res
        assert not store.department_exists("预演部"), "预演不得写库"
        assert store.list_role_members(rid, size=50)["items"] == []
    finally:
        store.close()
