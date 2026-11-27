import hashlib
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from test_20261117_manual_mark_submitted import ctx
from test_20261127_data_inbox import BASE, SOURCE, TOKEN, csv_bytes, inbox, scheduled, upload
from test_20261124_variable_notice import TEMPLATES, payload


ADMIN_TOKEN = f"/api/admin/data-inbox/{SOURCE}/token"


def test_token_table_ddl_runs_on_sqlite():
    from app.db import DATA_INBOX_TOKEN_DDL

    with sqlite3.connect(":memory:") as conn:
        conn.execute(DATA_INBOX_TOKEN_DDL)
        columns = {column[1]: column for column in conn.execute("PRAGMA table_info(data_inbox_token)")}
        assert set(columns) == {"source_key", "owner_id", "token_hash", "token_prefix", "enabled",
                                "created_at", "updated_at", "last_used_at"}
        assert columns["source_key"][5] == 1 and columns["enabled"][4] == "1"


def test_generate_reset_disable_and_audit_without_plaintext(ctx, inbox, monkeypatch):
    state, _, notifier, super_admin, admin, *_ = ctx
    client, _ = inbox
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    assert admin.get(ADMIN_TOKEN).json() == {
        "configured": False, "enabled": False, "token_prefix": None, "updated_at": None, "last_used_at": None,
    }
    for method in (admin.post, admin.delete):
        assert method(ADMIN_TOKEN, headers={"X-CSRF-Token": ""}).json()["code"] == "CSRF_INVALID"
    assert super_admin.post(ADMIN_TOKEN).json()["code"] == "SUPER_ADMIN_READONLY"
    with TestClient(admin.app) as anonymous:
        assert anonymous.get(ADMIN_TOKEN).status_code == 401
    assert admin.post("/api/admin/data-inbox/%2E%2E%2Fother/token").status_code == 400

    generated = admin.post(ADMIN_TOKEN)
    assert generated.status_code == 200, generated.text
    assert generated.headers["cache-control"] == "no-store"
    first = generated.json()["token"]
    prefix = generated.json()["token_prefix"]
    assert re.fullmatch(r"imt_[A-Za-z0-9_-]{43}", first)
    assert prefix == first[:10] + "..."
    assert generated.json()["upload_url"] == f"http://test{BASE}"
    db_row = dict(state.conn.execute("SELECT * FROM data_inbox_token WHERE source_key=?", (SOURCE,)).fetchone())
    digest = hashlib.sha256(first.encode()).hexdigest()
    assert db_row["token_hash"] == digest and db_row["token_prefix"] == prefix
    assert first not in repr(db_row)
    status = admin.get(ADMIN_TOKEN)
    assert status.status_code == 200 and status.headers["cache-control"] == "no-store"
    assert status.json()["token_prefix"] == prefix and status.json()["last_used_at"] is None
    assert "token" not in status.json() and first not in status.text and digest not in status.text
    assert upload(client, csv_bytes()).status_code == 401
    assert upload(client, csv_bytes(), headers={"X-Data-Token": first}).status_code == 200
    first_used = admin.get(ADMIN_TOKEN).json()["last_used_at"]
    assert first_used == state.now_str()

    reset = admin.post(ADMIN_TOKEN)
    assert reset.status_code == 200
    second = reset.json()["token"]
    assert second != first and reset.json()["last_used_at"] is None
    assert upload(client, csv_bytes(), headers={"X-Data-Token": first}).status_code == 401
    assert upload(client, csv_bytes(), headers={"Authorization": f"Bearer {second}"}).status_code == 200
    disabled = admin.delete(ADMIN_TOKEN)
    assert disabled.status_code == 200
    assert disabled.json()["configured"] and not disabled.json()["enabled"]
    for token in (TOKEN, first, second):
        denied = upload(client, csv_bytes(), headers={"X-Data-Token": token})
        assert denied.status_code == 403 and denied.json()["code"] == "DATA_INBOX_TOKEN_DISABLED"
    latest = admin.get(ADMIN_TOKEN).json()
    assert not latest["enabled"] and latest["last_used_at"] == first_used
    audit = state.conn.execute(
        "SELECT action,detail FROM admin_audit WHERE target_type='data_inbox_token' AND target_id=? ORDER BY id",
        (SOURCE,),
    ).fetchall()
    assert [row["action"] for row in audit] == [
        "data_inbox.token.generate", "data_inbox.token.reset", "data_inbox.token.disable",
    ]
    assert first not in repr([dict(row) for row in audit]) and second not in repr([dict(row) for row in audit])
    assert not notifier.sent_messages


def test_owner_isolation_for_tokens_and_legacy_template_bindings(ctx):
    state, _, _, _, owner, create_user, *_ = ctx
    other_id, other = create_user("inbox_other_admin")
    scheduled(ctx)
    preclaim = other.post(ADMIN_TOKEN)
    assert preclaim.status_code == 403 and preclaim.json()["code"] == "FORBIDDEN_OWNER"
    assert not state.conn.execute("SELECT 1 FROM data_inbox_token").fetchone()
    first = owner.post(ADMIN_TOKEN)
    assert first.status_code == 200
    for method in (other.get, other.post, other.delete):
        forbidden = method(ADMIN_TOKEN)
        assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN_OWNER"
    draft = payload({"platform": "zdxlz"}, data_source_key=SOURCE)
    draft.update(is_manual=False, notice_times=["10:00"])
    forbidden = other.post(TEMPLATES, json=draft)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN_OWNER"
    assert owner.get(ADMIN_TOKEN).json()["token_prefix"] == first.json()["token_prefix"]
    other_key = SOURCE.upper()
    assert other.post(f"/api/admin/data-inbox/{other_key}/token").status_code == 200
    row = state.conn.execute("SELECT owner_id FROM data_inbox_token WHERE source_key=?", (other_key,)).fetchone()
    assert row["owner_id"] == other_id


def test_concurrent_first_generation_keeps_only_the_winning_owner(ctx):
    state, _, _, _, owner, create_user, *_ = ctx
    _, other = create_user("inbox_racing_admin")
    barrier = Barrier(2, timeout=10)

    def generate(client):
        barrier.wait()
        response = client.post(ADMIN_TOKEN)
        return client, response

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(generate, (owner, other)))
    assert sorted(response.status_code for _, response in results) == [200, 403]
    winner, accepted = next((client, response) for client, response in results if response.status_code == 200)
    assert winner.get(ADMIN_TOKEN).json()["token_prefix"] == accepted.json()["token_prefix"]
    assert state.conn.execute("SELECT COUNT(*) AS n FROM data_inbox_token").fetchone()["n"] == 1


def test_existing_environment_tokens_work_without_a_db_record(ctx, inbox, monkeypatch):
    state = ctx[0]
    client, _ = inbox
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    assert upload(client, csv_bytes()).status_code == 200
    monkeypatch.setenv("IMT_DATA_INBOX_TOKENS", f'{{"{SOURCE}":"mapped-legacy-token"}}')
    assert upload(client, csv_bytes()).status_code == 401
    assert upload(client, csv_bytes(), headers={"Authorization": "Bearer mapped-legacy-token"}).status_code == 200
    assert not state.conn.execute("SELECT 1 FROM data_inbox_token").fetchone()
