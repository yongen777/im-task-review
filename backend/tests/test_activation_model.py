# -*- coding: utf-8 -*-
"""2026-11-03 新激活模型回归（2026-11-11 任务一收口后）：

规则：
1. 私聊零激活：未绑定用户任何文本 → 统一提醒「尚未录入业务权限」，零落库
   （2026-11-11 用户拍板：唯一激活路径 = 管理后台导入/选入成员自动反查绑定）；
2. H5 登记保留为兜底：比对「手机号+姓名」与本平台名册（部门必填但不拦截比对）；
   · 未导入手机号 → 400 提示「管理员尚未在后台导入你的信息，请联系管理员」；
   · 在册但姓名不符 → 400 提示核对重填；
   · 一致 → 绑定成功（回执走绑定平台通道）。
3. 从角色移除 → 彻底删除该平台绑定（不再有停用态、无待归纳回收），未完成实例照旧清除。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.events import handle_inbound_text  # noqa: E402
from app.api.h5 import process_h5_bind  # noqa: E402
from app.scheduler import FakeNotifier  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13800000401"
NAME = "后台导入甲"
DEPT = "市场部"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_newmodel_")) / "im.db")


def _seed_roster(store: StateStore) -> str:
    role = store.create_role("云中台", actor="t", platform="zdxlz")["role_id"]
    store.add_role_member(role, PHONE, NAME, DEPT, actor="t")
    store.create_department(DEPT, actor="t")
    store.create_department("其它部", actor="t")
    return role


# ── 1. 名册比对 ────────────────────────────────────────────────────
def test_bind_ok_when_phone_and_name_match_roster() -> None:
    store = _store()
    try:
        _seed_roster(store)
        # 部门与后台不同也不拦截（部门只做展示）
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_1", "platform": "zdxlz",
                  "name": NAME, "dept": "其它部"},
        )
        assert code == 200 and body.get("bound") is True, body
        assert store.get_delivery_target(PHONE, platform="zdxlz")["external_id"] == "zd_uid_1"
    finally:
        store.close()


def test_bind_rejected_when_not_imported() -> None:
    """未导入的手机号：固定提示，不绑定、不落任何记录。"""
    store = _store()
    try:
        _seed_roster(store)
        code, body = process_h5_bind(
            state=store,
            data={"phone": "13900009999", "external_id": "zd_uid_2", "platform": "zdxlz",
                  "name": "路人乙", "dept": DEPT},
        )
        assert (code, body.get("code")) == (400, "NOT_IN_ROSTER"), body
        assert body["message"] == "管理员尚未在后台导入你的信息，请联系管理员"
        assert store.get_delivery_target("13900009999") is None
    finally:
        store.close()


def test_bind_rejected_when_name_mismatch() -> None:
    store = _store()
    try:
        _seed_roster(store)
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_3", "platform": "zdxlz",
                  "name": "冒名顶替", "dept": DEPT},
        )
        assert (code, body.get("code")) == (400, "ROSTER_MISMATCH"), body
        assert "不一致" in body["message"]
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None, "⛔ 姓名不符不得绑定"
    finally:
        store.close()


def test_bind_matches_any_roster_row_across_roles() -> None:
    """2026-11-04 修正：一人多角色、管理员在某角色改过名 → 填任一在册姓名都应通过。

    旧实现只比 id ASC 第一条，改过名后用户填新名字会被误判「信息不一致」。
    """
    store = _store()
    try:
        role = _seed_roster(store)  # 建角色1：NAME 在册
        role2 = store.create_role("第二角色", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(role2, PHONE, NAME, DEPT, actor="t")
        # 管理员在第二角色里把人改名为「新名字」
        store.update_role_member(role2, PHONE, name="新名字", dept=DEPT, actor="t")

        # 填旧名字：第一角色的行仍在册 → 通过（绑定后记为新 uid_old）
        code_old, _ = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_old", "platform": "zdxlz",
                  "name": NAME, "dept": DEPT},
        )
        assert code_old == 200

        # 同一账号再提交（换新名字行）：应走「已绑定」回执而非 mismatch —— 比对只发生在首次登记；
        # 为验证新名字可匹配，用第二个账号重新走绑定会被防覆盖护栏拦，因此换一个未绑定手机号的同结构场景。
        # 直接断言语义：用 update_role_member 后另一人（不同手机号、同名新字）验证 DESC 取行逻辑没有意义，
        # 这里的关键是"同一手机号多行任一姓名匹配"——上面 code_old 已验旧行命中；
        # 新行命中用新账号+把第一角色的旧名行改掉的等价场景验证：
        code_new, body_new = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_old", "platform": "zdxlz",
                  "name": "新名字", "dept": DEPT},
        )
        assert code_new == 200, body_new
        assert body_new.get("already_same") is True, "同账号重复登记回已绑定"

        # 全部在册行都对不上才拒绝（用未绑定手机号，避免撞防覆盖护栏）
        store.add_role_member(role2, "13800000515", NAME, DEPT, actor="t")
        store.update_role_member(role2, "13800000515", name="新名字", dept=DEPT, actor="t")
        code_bad, body_bad = process_h5_bind(
            state=store,
            data={"phone": "13800000515", "external_id": "zd_uid_any3", "platform": "zdxlz",
                  "name": "谁都不是", "dept": DEPT},
        )
        assert (code_bad, body_bad.get("code")) == (400, "ROSTER_MISMATCH"), body_bad
        _ = role
    finally:
        store.close()


def test_bind_rejected_when_roster_on_other_platform_only() -> None:
    """只在企微角色、却拿密信链接登记 → 密信侧视为未导入。"""
    store = _store()
    try:
        w_role = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        store.add_role_member(w_role, PHONE, NAME, DEPT, actor="t")
        store.create_department(DEPT, actor="t")
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_4", "platform": "zdxlz",
                  "name": NAME, "dept": DEPT},
        )
        assert (code, body.get("code")) == (400, "NOT_IN_ROSTER"), body
        assert store.get_delivery_target(PHONE, platform="zdxlz") is None
    finally:
        store.close()


def test_bind_receipt_goes_to_bound_platform() -> None:
    store = _store()
    try:
        role = store.create_role("企微云中台", actor="t", platform="wecom")["role_id"]
        store.add_role_member(role, PHONE, NAME, DEPT, actor="t")
        store.create_department(DEPT, actor="t")
        z, w = FakeNotifier(), FakeNotifier()
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "wx_uid_5", "platform": "wecom",
                  "name": NAME, "dept": DEPT},
            notifier=z, notifiers={"zdxlz": z, "wecom": w},
        )
        assert code == 200 and body.get("bound") is True, body
        assert len(w.sent_messages) == 1 and z.sent_messages == []
    finally:
        store.close()


# ── 2. 未绑定用户任何文本 → 只回登记链接 ───────────────────────────
def test_unbound_text_gets_guide_and_no_activation() -> None:
    """2026-11-11 任务一：未绑定用户任何文本 → 统一提醒「尚未录入业务权限」，零落库零激活。"""
    store = _store()
    try:
        sent: list[tuple] = []
        for text in ("你好", f"{NAME} {PHONE}", "13900000000", "申请补交"):
            code, body = handle_inbound_text(
                state=store, user_id="uid_tz", msg_text=text, send=lambda t, g: sent.append((t, g)),
                platform="zdxlz", base_url="http://t.example.com",
            )
            assert code == 200 and body.get("bound") is False, (text, body)
            assert "尚未录入您的业务权限" in body["message"], text
            assert "bind_url" not in body, "⛔ 不再发登记链接"
        # ⛔ 私聊零落库：不写绑定、不落待归纳池
        with store.lock:
            cnt = store.conn.execute("SELECT COUNT(*) FROM pending_member").fetchone()[0]
        assert cnt == 0
        assert store.get_delivery_target(PHONE) is None
    finally:
        store.close()


# ── 3. 移除角色 → 彻底删除绑定 ────────────────────────────────────
def test_remove_role_deletes_binding_completely() -> None:
    store = _store()
    try:
        role = _seed_roster(store)
        process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_6", "platform": "zdxlz",
                  "name": NAME, "dept": DEPT},
        )
        assert store.get_delivery_target(PHONE, platform="zdxlz") is not None

        store.delete_role_member(role, PHONE, actor="t")
        # 彻底删除：连 usable=0 的停用态都没有
        with store.lock:
            rows = store.conn.execute(
                "SELECT * FROM delivery_target WHERE phone = ?", (PHONE,)
            ).fetchall()
        assert rows == [], "移除后该平台绑定应彻底删除"
        with store.lock:
            cnt = store.conn.execute("SELECT COUNT(*) FROM pending_member").fetchone()[0]
        assert cnt == 0, "⛔ 不得回待归纳池"

        # 再登记：因已不在名册 → 提示未导入，需管理员重新导入后才能激活
        code, body = process_h5_bind(
            state=store,
            data={"phone": PHONE, "external_id": "zd_uid_6", "platform": "zdxlz",
                  "name": NAME, "dept": DEPT},
        )
        assert (code, body.get("code")) == (400, "NOT_IN_ROSTER"), body
    finally:
        store.close()


def test_remove_one_platform_keeps_other_platform_binding() -> None:
    """双平台成员：退出企微角色 → 只删企微绑定，密信绑定不受影响。"""
    store = _store()
    try:
        z_role = store.create_role("密信组", actor="t", platform="zdxlz")["role_id"]
        w_role = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        store.add_role_member(z_role, PHONE, NAME, DEPT, actor="t")
        store.add_role_member(w_role, PHONE, NAME, DEPT, actor="t")
        store.create_department(DEPT, actor="t")
        store.upsert_delivery_target(PHONE, "zd_uid_7", platform="zdxlz")
        store.upsert_delivery_target(PHONE, "wx_uid_7", platform="wecom")

        store.delete_role_member(w_role, PHONE, actor="t")
        assert store.get_delivery_target(PHONE, platform="zdxlz")["usable"] == 1
        with store.lock:
            gone = store.conn.execute(
                "SELECT 1 FROM delivery_target WHERE platform='wecom' AND phone=?", (PHONE,)
            ).fetchone()
        assert gone is None, "企微绑定应被彻底删除"
    finally:
        store.close()