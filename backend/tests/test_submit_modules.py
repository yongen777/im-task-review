# -*- coding: utf-8 -*-
"""2026-10-30 用户口径四条：

1. 免审核任务：提交成功即「已完成」（status=approved，文案「已完成」）
2. 需审核任务：只有审核通过才是「审核通过」
3. 管理员勾了哪些 H5 模块，用户就必须完成哪些（图片/文件/文字说明缺一不可）
4. 详情接口要带回模板侧字段（kind / role_id / need_review / modules），
   否则前端只能退化成「业务角色」、且把所有区块都渲染出来

约定：全部自建临时库，不碰 data/im_task.db。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import create_admin_router
from app.state import BizError, StateStore

PHONE = "13900008001"


def _mk_store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp()) / "im.db")


def _role(store: StateStore) -> str:
    rid = store.create_role("云中台软研队伍", actor="t")["role_id"]
    store.add_role_member(rid, PHONE, "张三", "市场部", actor="t")
    return rid


def _tpl(store: StateStore, rid: str, *, modules=None, need_review: bool, kind: str = "form",
         allow_note: bool = False) -> Dict[str, Any]:
    spec = {"upload_modules": modules} if modules is not None else {}
    payload: Dict[str, Any] = {
        "name": "TeleAgent每日用量统计", "kind": kind, "role_ids": [rid],
        "text_tpl": "请提交今日数据", "remind_at": "17:30", "deadline": "22:00", "period": "day",
        "need_review": need_review, "allow_note": allow_note, "spec": spec,
    }
    if kind == "form":
        payload["schema_json"] = [
            {"key": "points", "label": "TeleAgent今日消耗积分", "type": "number"},
        ]
    return store.create_template(payload, actor="t")


def _inst(store: StateStore, tpl: Dict[str, Any]) -> Dict[str, Any]:
    from app.config import format_dt

    pkey = format_dt(store.now())[:10]
    deadline = format_dt(store.now().replace(hour=23, minute=59, second=0, microsecond=0))
    return store.ensure_template_instances(tpl["template_id"], pkey, deadline)[0]


def _ref(store: StateStore, tpl: Dict[str, Any], *, mime: str, name: str) -> str:
    import uuid

    r = f"u_{uuid.uuid4().hex[:10]}"
    store.create_upload_ref(
        ref=r, phone=PHONE, template_id=tpl["template_id"],
        file_name=name, mime=mime, size=1024, rel_path=f"uploads/{r}",
    )
    return r


def _submit(store: StateStore, tpl: Dict[str, Any], inst: Dict[str, Any], *,
            fields=None, refs=None, note=None):
    tok = store.issue_h5_token(PHONE, tpl["template_id"], inst["period_key"], int(inst["id"]))
    return store.submit_h5_task(
        raw_token=tok,
        fields=fields if fields is not None else {"points": "50"},
        refs=refs or [],
        note=note,
    )


# ── 1/2. 状态口径 ──────────────────────────────────────────────────
def test_no_review_submit_becomes_approved_immediately() -> None:
    """免审核：提交成功 = 已完成（status=approved），不进待审队列。"""
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=False, modules=["fields"])
        inst = _inst(store, tpl)
        res = _submit(store, tpl, inst)
        assert res["ok"] is True
        assert res["status"] == "approved"
        assert res["message"] == "已完成"
        assert store.get_instance(inst["id"])["status"] == "approved"
    finally:
        store.close()


def test_need_review_submit_waits_for_approval() -> None:
    """需审核：提交后是 submitted（待审核），审核通过才 approved。"""
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=True, modules=["fields"])
        inst = _inst(store, tpl)
        res = _submit(store, tpl, inst)
        assert res["status"] == "submitted"
        assert res["message"] == "已提交，等待审核"
        store.review_instance(int(inst["id"]), "approve", reviewer="admin")
        assert store.get_instance(inst["id"])["status"] == "approved"
    finally:
        store.close()


# ── 3. 勾了的模块必须真的完成 ──────────────────────────────────────
def test_image_module_requires_image() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=False, modules=["fields", "image"])
        inst = _inst(store, tpl)
        with pytest.raises(BizError) as err:
            _submit(store, tpl, inst)
        assert err.value.code == "VALIDATION_FAILED"
        assert "请上传图片" in err.value.message
        # 只传 PDF 不算图片
        with pytest.raises(BizError):
            _submit(store, tpl, inst, refs=[_ref(store, tpl, mime="application/pdf", name="a.pdf")])
        # 传了图片就放行
        res = _submit(store, tpl, inst, refs=[_ref(store, tpl, mime="image/png", name="a.png")])
        assert res["ok"] is True
    finally:
        store.close()


def test_file_module_requires_file() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=False, modules=["fields", "file"])
        inst = _inst(store, tpl)
        with pytest.raises(BizError) as err:
            _submit(store, tpl, inst)
        assert "请上传文件" in err.value.message
        # 图片不算文件
        with pytest.raises(BizError):
            _submit(store, tpl, inst, refs=[_ref(store, tpl, mime="image/png", name="a.png")])
        res = _submit(store, tpl, inst, refs=[_ref(store, tpl, mime="application/pdf", name="a.pdf")])
        assert res["ok"] is True
    finally:
        store.close()


def test_text_module_requires_note() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=False, modules=["fields", "text"])
        inst = _inst(store, tpl)
        with pytest.raises(BizError) as err:
            _submit(store, tpl, inst)
        assert "请填写文字说明" in err.value.message
        res = _submit(store, tpl, inst, note="今日已完成 12 户")
        assert res["ok"] is True
    finally:
        store.close()


def test_legacy_template_without_modules_not_blocked() -> None:
    """老模板 spec 里没有 upload_modules：⛔ 不能因为新规则突然交不了。"""
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=False, modules=None)
        inst = _inst(store, tpl)
        res = _submit(store, tpl, inst)  # 不传任何附件
        assert res["ok"] is True
    finally:
        store.close()


def test_fields_still_required_by_schema() -> None:
    """字段模块：schema 标了 required 的照旧必须填。"""
    store = _mk_store()
    try:
        rid = _role(store)
        payload = {
            "name": "必填字段", "kind": "form", "role_ids": [rid], "text_tpl": "x",
            "remind_at": "17:30", "deadline": "22:00", "period": "day",
            "schema_json": [{"key": "points", "label": "消耗积分", "type": "number", "required": True}],
            "spec": {"upload_modules": ["fields"]},
        }
        tpl = store.create_template(payload, actor="t")
        inst = _inst(store, tpl)
        with pytest.raises(BizError) as err:
            _submit(store, tpl, inst, fields={"points": ""})
        assert "请填写" in err.value.message
    finally:
        store.close()


# ── 4. 详情接口补模板侧字段 ────────────────────────────────────────
def _client(store: StateStore) -> TestClient:
    app = FastAPI()
    app.include_router(create_admin_router(store, notifier=None, public_base_url="https://t.example.com"))
    return TestClient(app)


def test_detail_returns_role_name_modules_and_need_review() -> None:
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=False, modules=["fields", "image"])
        inst = _inst(store, tpl)
        _submit(store, tpl, inst, refs=[_ref(store, tpl, mime="image/png", name="a.png")])

        uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
        sess = store.create_admin_session(uid)
        client = _client(store)
        r = client.get(f"/api/admin/instances/{inst['id']}", cookies={"sid": sess["sid"]})
        assert r.status_code == 200, r.text
        inst_out = r.json()["instance"]

        # 角色名不再退化成「业务角色」：详情必须带 role_id，前端才能查到名字
        assert inst_out["role_id"] == rid
        assert inst_out["role_ids"] == [rid]
        assert store.get_role(rid)["name"] == "云中台软研队伍"
        # 状态文案判据 + 模块清单
        assert inst_out["need_review"] is False
        assert inst_out["modules"] == ["fields", "image"]
        assert inst_out["kind"] == "form"
    finally:
        store.close()


def test_detail_modules_skip_unselected_file() -> None:
    """只选了字段+图片 → modules 里不应出现 file，前端据此不渲染文件区块。"""
    store = _mk_store()
    try:
        rid = _role(store)
        tpl = _tpl(store, rid, need_review=True, modules=["fields", "image"])
        inst = _inst(store, tpl)
        uid = store.upsert_admin_user("admin", "Imtask@2026", must_change=False)
        sess = store.create_admin_session(uid)
        client = _client(store)
        r = client.get(f"/api/admin/instances/{inst['id']}", cookies={"sid": sess["sid"]})
        mods = r.json()["instance"]["modules"]
        assert "file" not in mods
        assert mods == ["fields", "image"]
    finally:
        store.close()
