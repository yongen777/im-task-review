import hashlib
import json
import re

from fastapi.testclient import TestClient

from test_20261117_manual_mark_submitted import ctx
from test_20261124_variable_notice import TEMPLATES, payload
from test_20261127_data_inbox import SOURCE, TOKEN, csv_bytes, inbox, upload


PERSONAL = "/api/admin/me/data-inbox-token"
INDEPENDENT = f"/api/admin/data-inbox/{SOURCE}/token"


def template(client, source_key):
    draft = payload({"platform": "zdxlz"}, mode="none", data_source_key=source_key)
    draft.update(is_manual=False, notice_times=["10:00"])
    response = client.post(TEMPLATES, json=draft)
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def send(client, source_key, credential):
    return upload(client, csv_bytes(), path=f"/api/data-inbox/{source_key}/upload",
                  headers={"Authorization": f"Bearer {credential}"})


def issue(admin):
    response = admin.post(PERSONAL + "/rotate")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["token"]
    assert re.fullmatch(r"imt_u_[A-Za-z0-9_-]{43}", token)
    assert token.startswith(response.json()["token_prefix"].removesuffix("..."))
    return token


def test_personal_lifecycle_hash_audit_and_no_restart(ctx, inbox, monkeypatch):
    state, _, notifier, _, admin, _, existing, _ = ctx
    client, _ = inbox
    for key in (SOURCE, "scores_02"):
        template(admin, key)
    with TestClient(admin.app) as anonymous:
        denied = anonymous.get(PERSONAL)
        assert denied.status_code == 401 and denied.headers["cache-control"] == "no-store"
    status = admin.get(PERSONAL)
    assert status.headers["cache-control"] == "no-store"
    assert status.json() == {"configured": False, "enabled": False, "token_prefix": None,
                             "updated_at": None, "last_used_at": None}
    for action in ("rotate", "disable"):
        denied = admin.post(f"{PERSONAL}/{action}", headers={"X-CSRF-Token": ""})
        assert denied.status_code == 403 and denied.json()["code"] == "CSRF_INVALID"
        assert denied.headers["cache-control"] == "no-store"

    first = issue(admin)
    row = dict(state.conn.execute("SELECT * FROM data_inbox_admin_token WHERE admin_id=?",
                                 (existing["owner_id"],)).fetchone())
    assert row["token_hash"] == hashlib.sha256(first.encode()).hexdigest() and first not in repr(row)
    assert row["last_used_at"] is None
    for key in (SOURCE, "scores_02"):
        assert send(client, key, first).status_code == 200
    status = admin.get(PERSONAL)
    assert status.headers["cache-control"] == "no-store" and status.json()["last_used_at"] == state.now_str()
    assert "token" not in status.json() and first not in status.text and row["token_hash"] not in status.text

    second = issue(admin)
    assert second != first and admin.get(PERSONAL).json()["last_used_at"] is None
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", first)
    assert send(client, SOURCE, first).status_code == 401
    assert send(client, SOURCE, second).status_code == 200
    stopped = admin.post(PERSONAL + "/disable")
    assert stopped.status_code == 200 and stopped.headers["cache-control"] == "no-store"
    assert stopped.json()["configured"] and not stopped.json()["enabled"] and "token" not in stopped.json()
    monkeypatch.setenv("IMT_DATA_INBOX_TOKENS", json.dumps({SOURCE: second}))
    denied = send(client, SOURCE, second)
    assert denied.status_code == 403 and denied.json()["code"] == "DATA_INBOX_TOKEN_DISABLED"
    audit = [dict(row) for row in state.conn.execute(
        "SELECT action, target_type, target_id,detail FROM admin_audit WHERE action LIKE 'data_inbox.%' ORDER BY id"
    ).fetchall()]
    assert [row["action"] for row in audit if row["target_type"] == "data_inbox_admin_token"] == [
        "data_inbox.personal_token.generate", "data_inbox.personal_token.reset", "data_inbox.personal_token.disable",
    ]
    assert [json.loads(row["detail"])["auth_source"] for row in audit if row["target_type"] == "data_inbox_source"] == [
        "personal_token", "personal_token", "personal_token",
    ]
    assert first not in repr(audit) and second not in repr(audit) and row["token_hash"] not in repr(audit)
    assert not notifier.sent_messages


def test_personal_token_owner_isolation_and_super_admin(ctx, inbox, monkeypatch):
    state, _, _, super_admin, owner, sub, *_ = ctx
    client, root = inbox
    _, other = sub("personal_inbox_other")
    template(owner, SOURCE)
    other_tpl = template(other, "other_scores")
    own, foreign = issue(owner), issue(other)
    assert owner.get(PERSONAL).json()["token_prefix"] != other.get(PERSONAL).json()["token_prefix"]
    takeover = payload({"platform": "zdxlz"}, mode="none", data_source_key=SOURCE)
    takeover.update(is_manual=False, notice_times=["10:00"])
    rejected = other.post(TEMPLATES, json=takeover)
    assert rejected.status_code == 403 and rejected.json()["code"] == "FORBIDDEN_OWNER"
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", own)
    assert owner.post("/api/admin/data-inbox/token_only/token").status_code == 200
    for key, credential in (("other_scores", own), ("unbound_scores", own), ("token_only", own), (SOURCE, foreign)):
        denied = send(client, key, credential)
        assert denied.status_code == 403 and denied.json()["code"] == "FORBIDDEN_OWNER"
    assert not list(root.rglob("*.csv"))

    # 旧版本可留下多个管理员绑定同一数据源的模板，个人令牌必须拒绝这种歧义。
    with state.conn:
        state.conn.execute(
            "UPDATE task_template SET spec_json=JSON_SET(spec_json, '$.data_source_key', ?) WHERE template_id=?",
            (SOURCE, other_tpl),
        )
    for credential in (own, foreign):
        denied = send(client, SOURCE, credential)
        assert denied.status_code == 403 and denied.json()["code"] == "FORBIDDEN_OWNER"
    root_token = issue(super_admin)
    for key in (SOURCE, "unbound_scores"):
        assert send(client, key, root_token).status_code == 200
    assert super_admin.post(PERSONAL + "/disable").status_code == 200
    assert send(client, "unbound_scores", root_token).status_code == 403


def test_single_source_personal_and_environment_token_precedence(ctx, inbox, monkeypatch):
    state, _, _, _, owner, *_ = ctx
    client, _ = inbox
    template(owner, SOURCE)
    template(owner, "legacy_scores")
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    independent = owner.post(INDEPENDENT).json()["token"]
    personal = issue(owner)
    assert send(client, SOURCE, independent).status_code == 200
    assert owner.get(PERSONAL).json()["last_used_at"] is None
    assert send(client, SOURCE, personal).status_code == 200
    assert send(client, "legacy_scores", TOKEN).status_code == 200
    # 旧版 imt_ 随机串碰巧以 u_ 开头时，仍应按旧环境变量处理。
    old_style = "imt_u_" + "a" * 41
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", old_style)
    assert send(client, "legacy_scores", old_style).status_code == 200
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    assert send(client, SOURCE, TOKEN).status_code == 401

    assert owner.delete(INDEPENDENT).status_code == 200
    assert send(client, SOURCE, personal).status_code == 200
    for credential in (independent, TOKEN):
        assert send(client, SOURCE, credential).status_code == 403

    replacement = owner.post(INDEPENDENT).json()["token"]
    assert owner.post(PERSONAL + "/disable").status_code == 200
    assert send(client, SOURCE, replacement).status_code == 200
    assert send(client, SOURCE, personal).status_code == 403
    sources = [json.loads(row["detail"])["auth_source"] for row in state.conn.execute(
        "SELECT detail FROM admin_audit WHERE action='data_inbox.upload.authorized' ORDER BY id"
    ).fetchall()]
    assert sources == ["source_token", "personal_token", "environment", "environment", "personal_token", "source_token"]
