# -*- coding: utf-8 -*-
"""2026-11-04 补测：match_roster_member「任一本平台在册行姓名匹配即通过」。

为什么单独一个文件：
`test_activation_model.py::test_bind_matches_any_roster_row_across_roles` 里
  · 填旧名 → 旧实现（只比 id ASC 第一条）同样能通过（最早行就是旧名那行）；
  · 填新名 → 第一次已绑定成功，第二次走 already_same 分支，根本不会走到姓名比对；
  → 把实现改回「只看最早一条」，那 9 条用例仍然全绿（workbuddy 实测：9 passed），
    也就是说本次修复的核心场景**此前没有有区分力的用例**。

本文件的 4 条都刻意用「未绑定的手机号」，确保真的走进姓名比对分支：
  1. 多角色异名 → 填新名通过，且返回的是**匹配行**的部门；
  2. 异名且全都不符 → 仍拒绝（校验没被改松）；
  3. 名字只在另一平台的名册里 → 本平台链接被拒（平台隔离没被放宽）；
  4. 同名多行 → 取最新一行（部门不会用过时旧值）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.h5 import process_h5_bind  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13300007777"
DEPT_A = "市场部"
DEPT_B = "渠道部"


def _store() -> StateStore:
    store = StateStore(Path(tempfile.mkdtemp(prefix="imtask_roster_")) / "im.db")
    for d in (DEPT_A, DEPT_B):
        store.create_department(d, actor="t")
    return store


def test_multi_role_different_name_matches_new_row() -> None:
    """1. 一人多角色且名字不同 → 填其中任一在册名字都应通过，且返回匹配行的部门。"""
    store = _store()
    try:
        r1 = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        r2 = store.create_role("乙组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(r1, PHONE, "张三", DEPT_A, actor="t")
        store.add_role_member(r2, PHONE, "张三丰", DEPT_B, actor="t")

        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "z_uid", "platform": "zdxlz",
                  "name": "张三丰", "dept": DEPT_B},
        )
        assert code == 200 and body.get("bound") is True, body
        hit = store.match_roster_member(PHONE, "张三丰", platform="zdxlz")
        assert hit and hit["dept"] == DEPT_B, f"应返回匹配行（乙组/{DEPT_B}）的部门，实际 {hit}"
    finally:
        store.close()


def test_all_names_mismatch_still_rejected() -> None:
    """2. 多角色异名且全都不符 → 仍拒绝。"""
    store = _store()
    try:
        r1 = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        r2 = store.create_role("乙组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(r1, PHONE, "张三", DEPT_A, actor="t")
        store.add_role_member(r2, PHONE, "张三丰", DEPT_A, actor="t")

        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "z_uid", "platform": "zdxlz",
                  "name": "完全对不上的名字", "dept": DEPT_A},
        )
        assert code == 400 and body.get("code") == "ROSTER_MISMATCH", body
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None
    finally:
        store.close()


def test_other_platform_name_not_accepted() -> None:
    """3. 名字只在另一平台名册里 → 本平台登记必须被拒（平台隔离未被放宽）。"""
    store = _store()
    try:
        w = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        store.add_role_member(w, PHONE, "李四", DEPT_A, actor="t")

        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "z_uid", "platform": "zdxlz",
                  "name": "李四", "dept": DEPT_A},
        )
        assert code == 400 and body.get("code") == "NOT_IN_ROSTER", body
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None
    finally:
        store.close()


def test_same_name_multiple_rows_returns_latest() -> None:
    """4. 同名多行 → 返回最新一行，部门不会用过时旧值。"""
    store = _store()
    try:
        r1 = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        r2 = store.create_role("乙组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(r1, PHONE, "张三", DEPT_A, actor="t")
        store.add_role_member(r2, PHONE, "张三", DEPT_B, actor="t")

        hit = store.match_roster_member(PHONE, "张三", platform="zdxlz")
        assert hit and hit["dept"] == DEPT_B, f"同名应取最新行（乙组/{DEPT_B}），实际 {hit}"
    finally:
        store.close()
