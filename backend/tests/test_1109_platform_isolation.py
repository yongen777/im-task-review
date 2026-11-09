"""2026-11-09 用户需求：全量池办公软件隔离 + 切换身份隐藏未授权分级管理员。

需求①  「用户全量池也要做办公软件隔离啊，这条是量子密信的郑佳信息，
        不能出现在企业微信全量检索列表中。」
        → search_global_activated 取 name/dept 必须按 platform 收口，
          否则企微视角显示的是密信那条的姓名/部门（实测串台：企微视角搜到「郑佳/云中台」）。

需求②  「超管现在选择的企业微信，所以向文晗这个分级管理员没有开放这里就不能跳转了，
          直接隐藏没开放权限的分级管理员。」
        → 后端 assume 端点加 platform 护栏：目标账号未授权该平台 → 403。
          前端同步隐藏（见 AdminUserCard.tsx switchableAccounts）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.state import StateStore  # noqa: E402


def _store():
    tmp = tempfile.mkdtemp(prefix="imt1109b_")
    os.environ["TEMP"] = tmp
    os.environ["TMP"] = tmp
    return StateStore(db_path=os.path.join(tmp, "t.db"))


def _bind(store, phone, external_id, platform, activated_at="2026-10-20 09:00:00"):
    store.upsert_delivery_target(phone, external_id, platform=platform, activated_at=activated_at)


def test_search_global_activated_name_dept_scoped_by_platform():
    """需求①：同名同号在两个平台各有一条名册时，检索结果必须显示**本平台**那条的姓名/部门。"""
    s = _store()
    # 密信组：郑佳 / 云中台
    z_role = s.create_role("云中台软研队伍", platform="zdxlz")["role_id"]
    s.add_role_member(z_role, "17313771297", "郑佳", "云中台", actor="t")
    # 企微组：同一个人另一个名册行，部门不同
    w_role = s.create_role("云中台软研队伍", platform="wecom")["role_id"]
    s.add_role_member(w_role, "17313771297", "郑佳", "市场部", actor="t")

    _bind(s, "17313771297", "zd_uid", "zdxlz")
    _bind(s, "17313771297", "wx_uid", "wecom")

    zx = s.search_global_activated(q="", platform="zdxlz")
    wx = s.search_global_activated(q="", platform="wecom")

    assert len(zx) == 1, f"密信应只查到 1 条，实际 {len(zx)}"
    assert zx[0]["dept"] == "云中台", f"密信视角应显示「云中台」，实际 {zx[0]['dept']!r}"
    assert len(wx) == 1, f"企微应只查到 1 条，实际 {len(wx)}"
    assert wx[0]["dept"] == "市场部", f"企微视角应显示「市场部」，实际 {wx[0]['dept']!r}"
    assert wx[0]["platform"] == "wecom"
    s.close()


def test_search_global_activated_platform_filter_isolates_rows():
    """需求①：平台过滤后，另一平台的激活记录不出现。"""
    s = _store()
    z_role = s.create_role("密信组", platform="zdxlz")["role_id"]
    s.add_role_member(z_role, "13800000001", "张三", "市场部", actor="t")
    w_role = s.create_role("企微组", platform="wecom")["role_id"]
    s.add_role_member(w_role, "13900000002", "李四", "渠道部", actor="t")
    _bind(s, "13800000001", "zd_1", "zdxlz")
    _bind(s, "13900000002", "wx_1", "wecom")

    zx = s.search_global_activated(q="", platform="zdxlz")
    wx = s.search_global_activated(q="", platform="wecom")
    assert [x["phone"] for x in zx] == ["13800000001"], f"密信视角混入了他平台：{zx}"
    assert [x["phone"] for x in wx] == ["13900000002"], f"企微视角混入了他平台：{wx}"
    s.close()


def test_search_global_activated_keyword_not_cross_platform():
    """需求①：按姓名搜索时，别的平台的姓名不能在本平台搜出来。

    ⚠️ 关键设计：必须是**同一个人**在两个平台各有一条名册行、且姓名不同 ——
    只有这样，去掉平台收口后 EXISTS 才会因为「同号」而命中，才测得出串台。
    （若用两个不同的人，去掉收口也搜不出来，用例就成了摆设。）
    """
    s = _store()
    z_role = s.create_role("密信组", platform="zdxlz")["role_id"]
    s.add_role_member(z_role, "13800000003", "密信专属姓名", "市场部", actor="t")
    w_role = s.create_role("企微组", platform="wecom")["role_id"]
    s.add_role_member(w_role, "13800000003", "企微专属姓名", "渠道部", actor="t")
    _bind(s, "13800000003", "zd_2", "zdxlz")
    _bind(s, "13800000003", "wx_2", "wecom")

    # 企微视角搜密信的姓名 → 不该搜到
    wx_hit = s.search_global_activated(q="密信专属姓名", platform="wecom")
    assert wx_hit == [], f"企微视角搜到了密信的姓名：{wx_hit}"

    # 密信视角搜自己的姓名 → 能搜到
    zx_hit = s.search_global_activated(q="密信专属姓名", platform="zdxlz")
    assert len(zx_hit) == 1, f"密信视角应搜到自己的姓名：{zx_hit}"
    assert zx_hit[0]["name"] == "密信专属姓名", zx_hit

    # 反向：企微视角搜企微自己的姓名 → 能搜到，且姓名取企微那条
    wx_ok = s.search_global_activated(q="企微专属姓名", platform="wecom")
    assert len(wx_ok) == 1, f"企微视角应搜到自己的姓名：{wx_ok}"
    assert wx_ok[0]["name"] == "企微专属姓名", wx_ok
    s.close()


def test_search_global_activated_no_member_row_returns_empty_profile():
    """边界：有激活绑定但本平台无名册行 → 姓名/部门为空（不借用别的平台的名册行）。"""
    s = _store()
    z_role = s.create_role("密信组", platform="zdxlz")["role_id"]
    s.add_role_member(z_role, "13800000005", "张三", "市场部", actor="t")
    _bind(s, "13800000005", "zd_3", "zdxlz")
    _bind(s, "13800000005", "wx_3", "wecom")  # 企微有绑定但企微没有名册行

    wx = s.search_global_activated(q="", platform="wecom")
    assert len(wx) == 1
    assert wx[0]["name"] == "" and wx[0]["dept"] == "", (
        f"企微无��册行时不应借用密信的姓名/部门，实际 {(wx[0]['name'], wx[0]['dept'])!r}"
    )
    s.close()


def test_search_global_activated_without_platform_still_global():
    """不传 platform 时保持「全平台」口径（老调用不受影响）。"""
    s = _store()
    z_role = s.create_role("密信组", platform="zdxlz")["role_id"]
    s.add_role_member(z_role, "13800000006", "张三", "市场部", actor="t")
    w_role = s.create_role("企微组", platform="wecom")["role_id"]
    s.add_role_member(w_role, "13900000007", "李四", "渠道部", actor="t")
    _bind(s, "13800000006", "zd_4", "zdxlz")
    _bind(s, "13900000007", "wx_4", "wecom")

    allrows = s.search_global_activated(q="", platform=None)
    assert len(allrows) == 2, f"全平台口径应返回 2 条，实际 {len(allrows)}"
    s.close()


def test_member_pool_delete_platform_clears_only_that_platform():
    """配合需求①的数据清理：删某平台的成员行时，只清该平台的激活绑定，另一平台不受影响。"""
    s = _store()
    z_role = s.create_role("密信组", platform="zdxlz")["role_id"]
    s.add_role_member(z_role, "17313771297", "郑佳", "云中台", actor="t")
    w_role = s.create_role("企微组", platform="wecom")["role_id"]
    s.add_role_member(w_role, "17313771297", "郑佳", "市场部", actor="t")
    _bind(s, "17313771297", "zd_uid", "zdxlz")
    _bind(s, "17313771297", "wx_uid", "wecom")

    res = s.member_pool_delete("17313771297", actor="admin", platform="wecom")
    assert res["rows"] == 1, f"应删 1 行企微名册，实际 {res}"
    assert "wecom" in res["bindings_removed"], f"应清掉企微绑定，实际 {res}"
    # 密信那侧完好
    assert s.is_phone_activated("17313771297", platform="zdxlz") is True
    assert s.is_phone_activated("17313771297", platform="wecom") is False
    # 密信视角仍能查到，企微视角查不到
    assert len(s.search_global_activated(q="", platform="zdxlz")) == 1
    assert len(s.search_global_activated(q="", platform="wecom")) == 0
    s.close()


def test_assume_endpoint_rejects_unauthorized_platform():
    """需求②：超管代登录到未授权当前办公软件的分级管理员 → 403 PLATFORM_NOT_ALLOWED。

    走真实 HTTP（TestClient）：这是"护栏真生效"的唯一可靠证明。
    ⚠️ 关键坑：get_admin_by_id 返回的 allowed_platforms 是 **JSON 字符串**，
    端点必须用 parse_admin_account 解析，否则迭代出的是单个字符、判定恒失效。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402

    s = _store()
    # 建超管账号（测试库里没有内置超管）
    s.upsert_admin_user("admin", "Admin@2026", must_change=False)
    app = FastAPI()
    register_admin_guard(app, s)
    app.include_router(create_admin_router(s, notifier=None, public_base_url="http://t.example.com"))

    admin_c = TestClient(app)
    r = admin_c.post("/api/admin/login", json={"username": "admin", "password": "Admin@2026"})
    assert r.status_code == 200, r.text
    admin_c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})

    # 建两个分级管理员：一个只授权 zdxlz，一个两个平台都有
    r1 = admin_c.post("/api/admin/accounts", json={
        "username": "subonlyzd", "display_name": "仅密信", "allowed_platforms": ["zdxlz"],
    })
    assert r1.status_code == 200, r1.text
    sub_only_zd = int(r1.json()["account"]["id"])
    r2 = admin_c.post("/api/admin/accounts", json={
        "username": "subboth", "display_name": "双平台", "allowed_platforms": ["zdxlz", "wecom"],
    })
    assert r2.status_code == 200, r2.text
    sub_both = int(r2.json()["account"]["id"])

    # 新建账号默认 must_change=1，先走「改密」再 assume（否则被 MUST_CHANGE_PASSWORD 拦）
    for uid in (sub_only_zd, sub_both):
        s.change_admin_password(uid, "NewPass@2026")

    # ① 未授权当前办公软件（wecom）→ 403
    resp = admin_c.post(f"/api/admin/accounts/{sub_only_zd}/assume?platform=wecom")
    assert resp.status_code == 403, f"未授权平台应 403，实际 {resp.status_code} {resp.text}"
    assert resp.json().get("code") == "PLATFORM_NOT_ALLOWED", resp.text

    # ② 授权了该平台 → 200，且 profile 带出 allowed_platforms
    resp = admin_c.post(f"/api/admin/accounts/{sub_only_zd}/assume?platform=zdxlz")
    assert resp.status_code == 200, f"授权平台应放行，实际 {resp.status_code} {resp.text}"
    assert resp.json()["profile"]["allowed_platforms"] == ["zdxlz"], resp.text

    # ⚠️ assume 成功后服务端下发了目标账号的会话 Cookie（切了身份），
    #    原超管会话要重新登录拿新的 csrf token 才能继续发请求。
    def _relogin_super() -> None:
        cc = TestClient(app)
        rr = cc.post("/api/admin/login", json={"username": "admin", "password": "Admin@2026"})
        assert rr.status_code == 200, rr.text
        admin_c.cookies.clear()
        admin_c.cookies.update(cc.cookies)
        admin_c.headers.update({"X-CSRF-Token": rr.json()["csrf_token"]})

    _relogin_super()

    # ③ 双平台账号在 wecom 下也应放行
    resp = admin_c.post(f"/api/admin/accounts/{sub_both}/assume?platform=wecom")
    assert resp.status_code == 200, f"双平台账号在 wecom 应放行，实际 {resp.status_code} {resp.text}"

    _relogin_super()

    # ④ 不传 platform → 保持旧行为（放行），不破坏既有调用
    resp = admin_c.post(f"/api/admin/accounts/{sub_only_zd}/assume")
    assert resp.status_code == 200, f"不传 platform 应保持放行，实际 {resp.status_code} {resp.text}"

    s.close()


def test_assume_platform_guard_uses_parsed_platforms_not_raw_json():
    """需求②：护栏必须基于 parse_admin_account 解析后的数组，而不是 DB 里的 JSON 字符串。"""
    s = _store()
    acc = s.create_admin_account(
        username="subraw", display_name="原始串",
        allowed_platforms=["zdxlz", "wecom"],
    )
    acc_id = int(acc["id"]) if isinstance(acc, dict) else int(acc)

    raw = s.get_admin_by_id(acc_id)
    raw_plats = raw.get("allowed_platforms")
    assert isinstance(raw_plats, str), f"DB 里的 allowed_platforms 应是 JSON 字符串，实际 {type(raw_plats)}"

    # 错误做法（我一开始写的）：直接迭代原始字符串 → 得到单字符列表
    wrong = [str(p).lower() for p in (raw_plats or [])]
    assert "wecom" not in wrong, "原始字符串迭代出来的应是单字符（错误做法）"

    # 正确做法：parse_admin_account 解析成数组
    right = [str(p).lower() for p in (s.parse_admin_account(raw).get("allowed_platforms") or [])]
    assert right == ["zdxlz", "wecom"], f"解析后应为平台数组，实际 {right}"
    assert "wecom" in right
    s.close()


def test_assume_platform_guard_matches_store_fallback():
    """需求②：allowed_platforms 为空时按 ['zdxlz'] 处理（与前端 store 兜底一致）。"""
    def allowed(target_plats, plat):
        tp = [str(p).lower() for p in (target_plats or [])] or ["zdxlz"]
        return plat in tp

    assert allowed([], "zdxlz") is True
    assert allowed([], "wecom") is False
    assert allowed(["wecom"], "wecom") is True
    assert allowed(["wecom"], "zdxlz") is False
