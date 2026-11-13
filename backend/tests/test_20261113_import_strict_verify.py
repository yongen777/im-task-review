# -*- coding: utf-8 -*-
"""2026-11-13 专项：严格实名校验导入（遇错全批中断、零写入）+ 5000 行上限 + 16 线程核验缓存。

用户拍板口径（2026-11-13，2600 人导入/激活前）：
1. CSV 导入行数硬上限 500 → **5000**（`state.IMPORT_ROW_LIMIT`；组织架构选人入口同步 5000）；
2. 单人快速添加（姓名+手机号）写库前必须官方通讯录核验：
   ① 手机号查不到 → 拦截（0 写入）：`手机号 {phone} 不存在，请确定手机号是否正确`
   ② 姓名与官方不一致 → 拦截（0 写入）：`{name} 姓名不正确，请核对后重试`
   ③ 全匹配才入册并激活；反查结果**一次查询**三处复用（校验/部门回填/激活绑定）；
3. CSV 批量导入：格式全过后 16 线程核验全部行；**按传入顺序**扫描，
   遇第一处「手机号不存在 / 姓名不符」立即中断，前面通过的行也绝不写库（整批 0 写入）；
   错误文案（前端按「第 {row} 行：{reason}」展示）：
   · `{phone} 不存在，请确定手机号是否正确（本次导入已取消，未写入任何数据）`
   · `{name}（{phone}）姓名不正确（本次导入已取消，未写入任何数据）`
4. 通讯录手机号查询 TTL 内存缓存（默认 600s；命中/查无缓存，失败不缓存）；
   核验线程池 16（`IMT_CONTACTS_VERIFY_THREADS` 可调）；
5. 查询失败（网络/权限/凭证）绝不当作「不存在」：单人拦截、批量中断并提示查询失败；
6. resolver 无严格核验能力（降级环境 / 企微仅 MCP）→ 保持旧 fail-open，不误伤存量流程。

⚠️ 全部内存假客户端，测试绝不发起真实网络请求。
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.channels.contacts_cache import TtlCache  # noqa: E402
from app.channels.wecom import WecomClient  # noqa: E402
from app.channels.wecom_contacts import WecomContacts  # noqa: E402
from app.channels.zdxlz_contacts import ZdxlzContacts  # noqa: E402
from app.channels.zdxlz_crypto import build_response_envelope_for_test  # noqa: E402
from app.state import BizError, StateStore  # noqa: E402

P_A = "13800002001"
P_B = "13800002002"
P_C = "13800002003"
P_D = "13800002004"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_strict_verify_")) / "im.db")


def _z_user(union_id: str, phone: str, name: str, department: str = "市公司本部",
            dept_path: str = "四川/德阳/市公司本部") -> Dict[str, Any]:
    """官方通讯录返回形状（与 ZdxlzContacts._pick_user 对齐）。"""
    return {"union_id": union_id, "phone": phone, "name": name, "customer_id": "",
            "department": department, "dept_path": dept_path}


def _row(row_no: int, phone: str, name: str, dept: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"row": row_no, "phone": phone, "name": name}
    if dept is not None:
        out["dept"] = dept
    return out


def _members(store: StateStore, role_id: str) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
    with store.lock:
        rows = store.conn.execute(
            "SELECT phone, name, dept, dept_path FROM member WHERE role_id = ? ORDER BY phone",
            (role_id,),
        ).fetchall()
    return [(r["phone"], r["name"], r["dept"], r["dept_path"]) for r in rows]


def _count(store: StateStore, table: str) -> int:
    with store.lock:
        return int(store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class StrictFakeContacts:
    """zdxlz 通讯录假客户端：严格接口 + 旧接口；可注入慢速/错误/任意手机号放行。"""

    def __init__(self, users: Optional[List[Dict[str, Any]]] = None,
                 error_phones: Optional[List[str]] = None,
                 allow_any: bool = False, delay: float = 0.0) -> None:
        self.users = {(u.get("phone") or "").strip(): u for u in (users or [])}
        self.error_phones = set(error_phones or [])
        self.allow_any = allow_any
        self.delay = delay
        self.strict_calls = 0
        self.search_calls = 0
        self._lock = threading.Lock()
        self._inflight = 0
        self.max_inflight = 0

    def search_by_phone_strict(self, phone: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        with self._lock:
            self.strict_calls += 1
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            if self.delay:
                time.sleep(self.delay)
            clean = (phone or "").strip()
            if clean in self.error_phones:
                return ("error", None)
            if self.allow_any:
                return ("ok", _z_user(f"u_{clean}", clean, "批量测试"))
            return ("ok", self.users.get(clean))
        finally:
            with self._lock:
                self._inflight -= 1

    # 旧接口（历史激活路径）；严格核验命中后业务不应再调它。
    def search_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.search_calls += 1
        return self.users.get((phone or "").strip())

    def search_by_name(self, name: str) -> List[Dict[str, Any]]:
        return [u for u in self.users.values() if u.get("name") == name]

    def get_by_union_id(self, union_id: str) -> Optional[Dict[str, Any]]:
        for u in self.users.values():
            if u.get("union_id") == union_id:
                return u
        return None


class StrictFakeWecomContacts:
    """企微通讯录假客户端：带严格手机号核验（getuserid 语义）。"""

    def __init__(self, users: Optional[List[Dict[str, Any]]] = None,
                 error_phones: Optional[List[str]] = None, configured: bool = True) -> None:
        self.users = {(u.get("phone") or "").strip(): u for u in (users or [])}
        self.error_phones = set(error_phones or [])
        self.configured = configured
        self.strict_calls = 0
        self.resolve_calls = 0

    def supports_strict_phone_verify(self) -> bool:
        return self.configured

    def resolve_user_strict(self, phone: str, name: str = "",
                            dept: str = "") -> Tuple[str, Optional[Dict[str, Any]]]:
        del name, dept  # getuserid 口径：只按手机号精确反查
        self.strict_calls += 1
        clean = (phone or "").strip()
        if clean in self.error_phones:
            return ("error", None)
        return ("ok", self.users.get(clean))

    def resolve_userid(self, phone: str, name: str = "", dept: str = "") -> Optional[str]:
        return str((self.resolve_user(phone, name, dept) or {}).get("external_id") or "")

    def resolve_user(self, phone: str, name: str = "", dept: str = "") -> Optional[Dict[str, Any]]:
        self.resolve_calls += 1
        return self.users.get((phone or "").strip())


class LegacyWecomContacts:
    """仅 MCP 姓名匹配（无 getuserid）：不具备严格手机号核验能力。"""

    def __init__(self, users: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.users = users or {}

    def resolve_userid(self, phone: str, name: str = "", dept: str = "") -> Optional[str]:
        return str((self.resolve_user(phone, name, dept) or {}).get("external_id") or "")

    def resolve_user(self, phone: str, name: str = "", dept: str = "") -> Optional[Dict[str, Any]]:
        return self.users.get((phone or "").strip())


# ── 一、单人快速添加：两步严格核验（不匹配不写库）──────────────────
def test_add_member_phone_missing_blocked() -> None:
    """手机号在官方通讯录查不到 → 拦截（0 写入），文案精确。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(users=[_z_user("u_A", P_A, "张三")])
        store.contacts = fake
        with pytest.raises(BizError) as ei:
            store.add_role_member(rid, P_B, "李四", actor="t")
        assert ei.value.status_code == 400 and ei.value.code == "PHONE_NOT_IN_CONTACTS", ei.value
        assert ei.value.message == f"手机号 {P_B} 不存在，请确定手机号是否正确"
        assert _members(store, rid) == []
        assert store.get_delivery_target(P_B, platform="zdxlz") is None
        assert fake.strict_calls == 1
    finally:
        store.close()


def test_add_member_name_mismatch_blocked() -> None:
    """手机号存在但姓名不一致 → 拦截（0 写入）；官方姓名带括号部门后缀也要正确比对。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(users=[
            _z_user("u_LB", P_A, "林波（德阳云中台）", "德阳云中台", "四川/德阳/云中台")])
        store.contacts = fake
        with pytest.raises(BizError) as ei:
            store.add_role_member(rid, P_A, "林涛", actor="t")
        assert ei.value.status_code == 400 and ei.value.code == "NAME_MISMATCH", ei.value
        assert ei.value.message == "林涛 姓名不正确，请核对后重试"
        assert _members(store, rid) == []
        assert store.get_delivery_target(P_A, platform="zdxlz") is None
    finally:
        store.close()


def test_add_member_official_name_suffix_cleaned_and_single_lookup() -> None:
    """官方姓名「林波（德阳云中台）」/「简睿骁_德阳市」→ 剥后缀后匹配成功；
    且一次查询三处复用：核验 1 次、激活不再查（旧实现至少 2 次）。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(users=[
            _z_user("u_LB", P_A, "林波（德阳云中台）", "德阳云中台", "四川/德阳/云中台"),
            _z_user("u_JR", P_B, "简睿骁_德阳市", "德阳市", "四川/德阳"),
        ])
        store.contacts = fake
        m1 = store.add_role_member(rid, P_A, "林波", actor="t")
        m2 = store.add_role_member(rid, P_B, "简睿骁", actor="t")
        assert m1["phone"] == P_A and m2["phone"] == P_B
        rows = _members(store, rid)
        assert (P_A in [r[0] for r in rows]) and (P_B in [r[0] for r in rows])
        row_a = next(r for r in rows if r[0] == P_A)
        assert row_a[2] == "德阳云中台" and row_a[3] == "四川/德阳/云中台", row_a  # 官方部门回填
        dt = store.get_delivery_target(P_A, platform="zdxlz")
        assert dt and dt["external_id"] == "u_LB" and dt["external_type"] == "union_id", dt
        assert fake.strict_calls == 2, "每个手机号只应查 1 次（核验复用给回填/激活）"
        assert fake.search_calls == 0, "严格核验后不得回退旧接口重复查询"
    finally:
        store.close()


def test_add_member_contacts_query_error_blocked() -> None:
    """通讯录查询失败（网络/凭证）→ 按「查询失败」拦截，绝不当作姓名不符/不存在。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        store.contacts = StrictFakeContacts(users=[], error_phones=[P_A])
        with pytest.raises(BizError) as ei:
            store.add_role_member(rid, P_A, "张三", actor="t")
        assert ei.value.code == "CONTACTS_QUERY_FAILED", ei.value
        assert "查询失败" in ei.value.message
        assert _members(store, rid) == []
    finally:
        store.close()


def test_add_member_missing_role_still_404() -> None:
    """角色不存在时优先 404，不能被通讯录核验的文案盖掉。"""
    store = _store()
    try:
        store.contacts = StrictFakeContacts(users=[])
        with pytest.raises(BizError) as ei:
            store.add_role_member("r_not_exist", P_A, "张三", actor="t")
        assert ei.value.status_code == 404 and ei.value.code == "NOT_FOUND", ei.value
    finally:
        store.close()


# ── 二、CSV 批量导入：按顺序核验、首错中断、整批零写入 ──────────────
def test_csv_import_first_error_aborts_whole_batch() -> None:
    """第 2 行手机号不存在 → 整批取消：第 1 行（含部门自动建档）也绝不写库。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(users=[
            _z_user("u_A", P_A, "安一"),
            _z_user("u_C", P_C, "王五"),
        ])
        store.contacts = fake
        res = store.import_role_members(rid, [
            _row(1, P_A, "安一", "新部门X"),      # 合法（含待自动建档部门）
            _row(2, P_B, "李四"),                  # 手机号不存在 → 第一处错误
            _row(3, P_C, "王六"),                  # 姓名不符（更后面的错误不再提示）
        ], dry_run=False, actor="t")
        assert res["blocked"] is True and res["will_add"] == 0, res
        assert len(res["invalid"]) == 1, res["invalid"]  # 只报第一处错误
        err = res["invalid"][0]
        assert err["row"] == 2 and err["phone"] == "", err
        assert err["reason"] == f"{P_B} 不存在，请确定手机号是否正确（本次导入已取消，未写入任何数据）"
        # 严格原子：0 成员、0 绑定、0 部门（第 1 行的部门自动建档一并回滚）
        assert _members(store, rid) == []
        assert store.get_delivery_target(P_A, platform="zdxlz") is None
        assert _count(store, "dept_option") == 0
    finally:
        store.close()


def test_csv_import_name_mismatch_message_exact() -> None:
    """姓名不符错误文案与行号精确（前端展示为「第 N 行：{reason}」）。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        store.contacts = StrictFakeContacts(users=[_z_user("u_A", P_A, "张三")])
        res = store.import_role_members(rid, [
            _row(7, P_A, "张叁"),  # 行号以传入 row 字段为准
        ], dry_run=False, actor="t")
        assert res["blocked"] is True and res["will_add"] == 0, res
        assert res["invalid"][0]["row"] == 7
        assert res["invalid"][0]["phone"] == ""
        assert res["invalid"][0]["reason"] == (
            f"张叁（{P_A}）姓名不正确（本次导入已取消，未写入任何数据）")
        assert _members(store, rid) == []
    finally:
        store.close()


def test_csv_import_all_valid_writes_once_and_activates() -> None:
    """全部 100% 匹配 → 单事务写入 + 激活；每手机号只查一次通讯录（缓存/复用）。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(users=[
            _z_user("u_A", P_A, "张三（德阳云中台）", "德阳云中台", "四川/德阳/云中台"),
            _z_user("u_B", P_B, "简睿骁_德阳市", "德阳市", "四川/德阳"),
            _z_user("u_C", P_C, "王五"),
        ])
        store.contacts = fake
        res = store.import_role_members(rid, [
            _row(1, P_A, "张三"),
            _row(2, P_B, "简睿骁"),
            _row(3, P_C, "王五"),
        ], dry_run=False, actor="t")
        assert res["blocked"] is False and res["will_add"] == 3, res
        assert res["contacts_verified"] == 3, res
        rows = _members(store, rid)
        assert len(rows) == 3
        assert next(r for r in rows if r[0] == P_A)[2] == "德阳云中台"
        for p, uid in ((P_A, "u_A"), (P_B, "u_B"), (P_C, "u_C")):
            dt = store.get_delivery_target(p, platform="zdxlz")
            assert dt and dt["external_id"] == uid and dt["external_type"] == "union_id", (p, dt)
        assert res["auto_activation"]["activated"] == 3, res["auto_activation"]
        assert fake.strict_calls == 3, "每个手机号应恰好查 1 次"
        assert fake.search_calls == 0
    finally:
        store.close()


def test_csv_import_query_error_aborts() -> None:
    """批量核验遇到查询失败 → 中断并提示查询失败，不误报「不存在」。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        store.contacts = StrictFakeContacts(
            users=[_z_user("u_A", P_A, "安一")], error_phones=[P_B])
        res = store.import_role_members(rid, [
            _row(1, P_A, "安一"),
            _row(2, P_B, "李四"),
        ], dry_run=False, actor="t")
        assert res["blocked"] is True and res["will_add"] == 0, res
        assert len(res["invalid"]) == 1
        assert "官方通讯录查询失败" in res["invalid"][0]["reason"], res["invalid"]
        assert _members(store, rid) == []
    finally:
        store.close()


def test_csv_import_format_errors_skip_contacts_calls() -> None:
    """格式/重复类错误在收集阶段整批拦截，不触发通讯录核验（省接口调用）。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(users=[_z_user("u_A", P_A, "张三")])
        store.contacts = fake
        res = store.import_role_members(rid, [
            _row(1, P_A, "张三"),
            _row(2, P_A, "张三"),  # 本批内重复
        ], dry_run=False, actor="t")
        assert res["blocked"] is True and res["will_add"] == 0, res
        assert fake.strict_calls == 0, "格式错误阶段不得打通讯录接口"
        assert _members(store, rid) == []
    finally:
        store.close()


def test_csv_import_row_limit_5000() -> None:
    """行数上限 5000：5001 行直接拒绝；5000 行放行（dry-run 走完整核验流水线）。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        store.contacts = StrictFakeContacts(allow_any=True)
        too_many = [_row(i, f"139{i:08d}", "批量测试") for i in range(1, 5002)]
        with pytest.raises(BizError) as ei:
            store.import_role_members(rid, too_many, dry_run=False, actor="t")
        assert ei.value.code == "CSV_TOO_LARGE", ei.value
        assert ei.value.message == "单次最多导入 5000 人（超出请分批）"
        full = [_row(i, f"139{i:08d}", "批量测试") for i in range(1, 5001)]
        res = store.import_role_members(rid, full, dry_run=True, actor="t")
        assert res["blocked"] is False and res["will_add"] == 5000, res
        assert res["contacts_verified"] == 5000, res
    finally:
        store.close()


def test_csv_import_verification_concurrency() -> None:
    """批量核验并发 ≥ 8（线程池 16；旧实现 4 线程且串行化会被该用例抓住）。"""
    store = _store()
    try:
        rid = store.create_role("甲组", actor="t", platform="zdxlz")["role_id"]
        fake = StrictFakeContacts(allow_any=True, delay=0.02)
        store.contacts = fake
        rows = [_row(i, f"137{i:08d}", "批量测试") for i in range(1, 33)]
        res = store.import_role_members(rid, rows, dry_run=False, actor="t")
        assert res["blocked"] is False and res["will_add"] == 32, res
        assert fake.max_inflight >= 8, f"核验并发不足：{fake.max_inflight}"
        assert fake.strict_calls == 32
    finally:
        store.close()


def test_contacts_verify_threads_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """核验线程数默认 16，环境变量可调并夹紧。"""
    import app.state as st
    assert st.contacts_verify_threads() == 16
    monkeypatch.setenv("IMT_CONTACTS_VERIFY_THREADS", "32")
    assert st.contacts_verify_threads() == 32
    monkeypatch.setenv("IMT_CONTACTS_VERIFY_THREADS", "99")
    assert st.contacts_verify_threads() == 64
    monkeypatch.setenv("IMT_CONTACTS_VERIFY_THREADS", "abc")
    assert st.contacts_verify_threads() == 16


# ── 三、企微通道：严格手机号反查 + MCP-only 降级不误伤 ───────────────
def test_wecom_add_member_phone_missing_blocked() -> None:
    """企微：手机号查不到 → 拦截（0 写入），文案与密信一致。"""
    store = _store()
    try:
        rid = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        store.wecom_contacts = StrictFakeWecomContacts(users=[])
        with pytest.raises(BizError) as ei:
            store.add_role_member(rid, P_A, "张三", actor="t")
        assert ei.value.code == "PHONE_NOT_IN_CONTACTS", ei.value
        assert ei.value.message == f"手机号 {P_A} 不存在，请确定手机号是否正确"
        assert _members(store, rid) == []
        assert store.get_delivery_target(P_A, platform="wecom") is None
    finally:
        store.close()


def test_wecom_csv_import_verified_binds_userid() -> None:
    """企微 CSV 导入：严格核验通过 → 单事务入册 + userid 绑定（external_type='user'）。"""
    store = _store()
    try:
        rid = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        fake = StrictFakeWecomContacts(users=[
            {"phone": P_A, "external_id": "wx_a", "department": "", "dept_path": ""},
            {"phone": P_B, "external_id": "wx_b", "department": "", "dept_path": ""},
        ])
        store.wecom_contacts = fake
        res = store.import_role_members(rid, [
            _row(1, P_A, "企微甲"),
            _row(2, P_B, "企微乙"),
        ], dry_run=False, actor="t")
        assert res["blocked"] is False and res["will_add"] == 2, res
        assert fake.strict_calls == 2
        dt = store.get_delivery_target(P_A, platform="wecom")
        assert dt and dt["external_id"] == "wx_a" and dt["external_type"] == "user", dt
    finally:
        store.close()


def test_wecom_mcp_only_fail_open() -> None:
    """企微仅 MCP（无手机号精确反查）→ 不具备严格核验能力，保持旧 fail-open 流程。"""
    store = _store()
    try:
        rid = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        store.wecom_contacts = LegacyWecomContacts(users={
            P_A: {"external_id": "wx_mcp", "department": "市场部", "dept_path": "总部/市场部"}})
        res = store.import_role_members(rid, [_row(1, P_A, "张三")],
                                        dry_run=False, actor="t")
        assert res["blocked"] is False and res["will_add"] == 1, res
        dt = store.get_delivery_target(P_A, platform="wecom")
        assert dt and dt["external_id"] == "wx_mcp", dt
    finally:
        store.close()


# ── 四、缓存与真实客户端契约 ────────────────────────────────────────
def test_ttl_cache_unit_semantics() -> None:
    """TTL 缓存：命中/过期/容量淘汰；值为 None 保留为未命中哨兵。"""
    clock = {"t": 0.0}
    cache = TtlCache(ttl=10.0, clock=lambda: clock["t"])
    assert cache.get("k") is None
    cache.set("k", ("ok", None))  # 元组值（含 None 成员）必须原样命中
    assert cache.get("k") == ("ok", None)
    clock["t"] = 9.999
    assert cache.get("k") == ("ok", None)
    clock["t"] = 10.0
    assert cache.get("k") is None, "到 TTL 边界即过期"
    small = TtlCache(ttl=100.0, max_entries=3, clock=lambda: 0.0)
    for i in range(5):
        small.set(f"k{i}", ("ok", i))
    assert small.get("k0") is None and small.get("k1") is None
    assert small.get("k4") == ("ok", 4)


def test_zdxlz_contacts_phone_cache_and_strict_states() -> None:
    """真实 ZdxlzContacts：命中/查无进缓存、失败不缓存；strict 区分查无与失败。"""
    app_id, app_secret = "appid_913", "sec_913"
    calls = {"n": 0}
    found, missing = "13800003001", "13800003002"

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content.decode("utf-8"))
        phone = str(body.get("phone") or "")
        if phone == found:
            payload = {"users": [{"union_id": "u913", "phone": phone, "name": "缓存甲",
                                  "user_departments": []}]}
            return httpx.Response(200, json={
                "code": 200, "msg": "OK",
                "data": build_response_envelope_for_test(app_id, app_secret, payload)})
        if phone == missing:
            payload = {"users": []}
            return httpx.Response(200, json={
                "code": 200, "msg": "OK",
                "data": build_response_envelope_for_test(app_id, app_secret, payload)})
        return httpx.Response(200, json={"code": 50001, "msg": "服务繁忙", "data": None})

    class _Chan:
        def get_access_token(self, force_refresh: bool = False) -> str:  # noqa: ARG002
            return "tok"

    class _Cfg:
        def __init__(self) -> None:
            self.app_id = app_id
            self.app_secret = app_secret

    contacts = ZdxlzContacts(_Cfg(), _Chan(),
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    got = contacts.search_by_phone(found)
    assert got and got["union_id"] == "u913"
    got2 = contacts.search_by_phone(found)
    assert got2 and got2["union_id"] == "u913"
    assert calls["n"] == 1, "同一手机号第二次必须命中缓存"
    assert contacts.search_by_phone_strict(missing) == ("ok", None), "查无 = 确定性结果"
    assert contacts.search_by_phone_strict(missing) == ("ok", None)
    assert calls["n"] == 2, "查无结果也要进缓存"
    assert contacts.search_by_phone_strict("13800003009") == ("error", None)
    assert contacts.search_by_phone_strict("13800003009") == ("error", None)
    assert calls["n"] == 4, "查询失败不得缓存（必须可重试）"
    contacts.cache_clear()
    assert contacts.search_by_phone(found) is not None
    assert calls["n"] == 5, "手动清缓存后重新拉取"


def test_wecom_client_strict_errcode_classification() -> None:
    """WecomClient：errcode 0=命中 / 60111=查无 / 60011=查询失败（不得当作查无）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if "gettoken" in request.url.path:
            return httpx.Response(200, json={"errcode": 0, "access_token": "tok", "expires_in": 7200})
        mobile = json.loads(request.content.decode("utf-8")).get("mobile")
        if mobile == "13800004001":
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "userid": "wx401"})
        if mobile == "13800004002":
            return httpx.Response(200, json={"errcode": 60111, "errmsg": "userid not found"})
        return httpx.Response(200, json={"errcode": 60011, "errmsg": "no permission"})

    client = WecomClient("corp", "1000", "sec",
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.get_userid_by_mobile_strict("13800004001") == ("ok", "wx401")
    assert client.get_userid_by_mobile_strict("13800004002") == ("ok", None)
    assert client.get_userid_by_mobile_strict("13800004003") == ("error", None)
    # 旧接口语义保持不变（None 表示查不到/失败，调用方按 fail-open 处理）
    assert client.get_userid_by_mobile("13800004001") == "wx401"


def test_real_clients_expose_strict_verification_api() -> None:
    """生产装配守卫：真实客户端必须提供严格核验接口（防止测试假件掩盖缺失实现）。"""
    assert hasattr(ZdxlzContacts, "search_by_phone_strict")
    assert hasattr(WecomContacts, "resolve_user_strict")
    assert hasattr(WecomContacts, "supports_strict_phone_verify")
    assert hasattr(WecomClient, "get_userid_by_mobile_strict")