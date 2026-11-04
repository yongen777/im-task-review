# -*- coding: utf-8 -*-
"""2026-11-03 下午修复：CSV 批量导入部门校验 + 登记页防重复提交（覆盖）。

背景（用户/验收方现场）：
1. 生产 CSV 导入 100% 400：前端真实请求发的是 csv_text，`realApi.ts` 的 csv_text→csv_rows
   转换分支被前面的 roles 分支吞掉（死代码），后端读不到 csv_rows 直接拒绝。
   ——前端顺序修复走 tsc/构建验证，这里补后端「部门校验」的行为测试。
2. 信息登记链接退出重进可重复提交：已绑定账号换手机号会新增绑定、待分配账号会被改资料。
   新增护栏：同一 external_id 已绑定 → 只认同一手机号（换号拒绝）；已提交待分配 → 原样保留。
3. 登记提交成功但无任何消息反馈 → 待分配分支按平台回推一条确认（仅首次）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.h5 import create_h5_router, process_h5_bind  # noqa: E402
from app.scheduler import FakeNotifier  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13800000301"
OTHER_PHONE = "13800000302"
NAME = "导入甲"
DEPT = "市场部"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_imp_")) / "im.db")


# ── 1. CSV 导入：部门校验 ───────────────────────────────────────────
def test_import_validates_department_and_allows_blank() -> None:
    """2026-11-04 用户口径变更：CSV 里的部门若部门管理里没有，**不拦截**——自动建好该部门。

    旧口径（2026-11-03）是把"部门不存在"的行判 invalid 不入库；现改为自动建档后正常导入。
    部门为空仍允许（成员无部门）。
    """
    store = _store()
    try:
        rid = store.create_role("导入组", actor="t", platform="zdxlz")["role_id"]
        store.create_department(DEPT, actor="t")

        res = store.import_role_members(
            rid,
            [
                {"row": 1, "phone": PHONE, "name": NAME, "dept": DEPT},
                {"row": 2, "phone": OTHER_PHONE, "name": "火星人", "dept": "火星部"},
                {"row": 3, "phone": "13800000303", "name": "无部门", "dept": ""},
            ],
            dry_run=False,
            actor="t",
        )
        assert res["will_add"] == 3, res
        assert not res["invalid"], res
        assert "火星部" in (res.get("departments_created") or []), res

        members = {m["phone"]: m for m in store.list_role_members(rid, size=50)["items"]}
        assert members[PHONE]["dept"] == DEPT
        assert members["13800000303"]["dept"] in (None, "")
        assert members[OTHER_PHONE]["dept"] == "火星部", "新部门应随成员一起入库"
        # 新部门已自动建进「部门管理」，后续 H5 登记/成员表单下拉都能选到
        assert store.department_exists("火星部"), "新部门应自动创建"
        assert [d["name"] for d in store.list_departments()] == [DEPT, "火星部"]
    finally:
        store.close()


# ── 2. 登记页防重复提交 ─────────────────────────────────────────────
def _wecom_role_with_phone(store: StateStore) -> None:
    role = store.create_role("企微登记组", actor="t", platform="wecom")["role_id"]
    store.add_role_member(role, PHONE, NAME, DEPT, actor="t")
    store.create_department(DEPT, actor="t")


def test_bound_account_cannot_rebind_with_another_phone() -> None:
    store = _store()
    try:
        _wecom_role_with_phone(store)

        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "wx_uid_1", "platform": "wecom", "name": NAME, "dept": DEPT},
        )
        assert code == 200 and body.get("bound") is True, body

        # 同账号同手机号：回「无需重复登记」，不产生任何变更
        code2, body2 = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "wx_uid_1", "platform": "wecom", "name": NAME, "dept": DEPT},
        )
        assert code2 == 200 and body2.get("already_same") is True, body2

        # 同账号换手机号：拒绝（否则一个账号会挂两个手机号，双份提醒）
        code3, body3 = process_h5_bind(
            state=store,
            data={"phone": OTHER_PHONE, "external_id": "wx_uid_1", "platform": "wecom", "name": NAME, "dept": DEPT},
        )
        assert (code3, body3.get("code")) == (409, "DUPLICATE"), body3
        assert store.get_delivery_target(OTHER_PHONE, platform="wecom") is None
    finally:
        store.close()

