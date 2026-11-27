import hashlib
import os
import sqlite3
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.config import SHANGHAI_TZ
from app.scheduler import SendResult
from test_20261124_variable_notice import FIRST, HEADER, TEMPLATES, VALID, add_second, payload
from test_20261117_manual_mark_submitted import NOW, ctx


SOURCE = "scores_01"
TOKEN = "test-inbox-token"
BASE = f"/api/data-inbox/{SOURCE}/upload"
NOW = NOW.replace(tzinfo=SHANGHAI_TZ)


@pytest.fixture
def inbox(ctx, monkeypatch):
    from app.api.data_inbox import create_data_inbox_router

    state = ctx[0]
    state.update_template(ctx[6]["template_id"], {"enabled": False})
    root = state.data_dir / "integration_inbox"
    monkeypatch.setenv("IMT_DATA_INBOX_DIR", str(root))
    monkeypatch.delenv("IMT_DATA_INBOX_TOKEN", raising=False)
    monkeypatch.delenv("IMT_DATA_INBOX_TOKENS", raising=False)
    app = FastAPI()
    app.include_router(create_data_inbox_router(state))
    with TestClient(app) as client:
        yield client, root


def csv_bytes(table=VALID):
    return table.encode("utf-8-sig")


def xlsx_bytes():
    book = Workbook()
    for row in VALID.splitlines():
        book.active.append(row.split(","))
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def upload(client, raw, filename="scores.csv", *, path=BASE, headers=None):
    return client.post(path, files={"file": (filename, raw)}, headers=headers or {"X-Data-Token": TOKEN})


def scheduled(ctx, **spec):
    draft = payload({"platform": "zdxlz"}, mode=spec.pop("mode", "none"), data_source_key=SOURCE, **spec)
    draft.update(is_manual=False, notice_times=["10:00", "10:05"])
    response = ctx[4].post(TEMPLATES, json=draft)
    assert response.status_code == 200, response.text
    return response.json()["template_id"]


def put_local(root, raw, *, name="scores.csv", at=NOW - timedelta(seconds=5)):
    folder = root / SOURCE / "incoming"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_bytes(raw)
    os.utime(target, (at.timestamp(), at.timestamp()))
    return target


def test_upload_auth_and_path_validation(inbox, monkeypatch):
    client, root = inbox
    assert upload(client, csv_bytes()).status_code == 403
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    assert upload(client, csv_bytes(), headers={"X-Data-Token": "incorrect"}).status_code == 401
    for invalid in ("%2E%2E%2Fother", "a%5Cb", "a/b", "ab..cd", "a"):
        assert upload(client, csv_bytes(), path=f"/api/data-inbox/{invalid}/upload").status_code == 400
    assert upload(client, csv_bytes(), "scores.xls").status_code == 400
    assert upload(client, csv_bytes(HEADER)).status_code == 400
    assert upload(client, b"not a workbook", "bad.xlsx").status_code == 400
    assert upload(client, b"x" * (20 * 1024 * 1024 + 1)).status_code == 413
    assert not list(root.rglob("*.*"))


def test_log_schema_runs_on_sqlite():
    from app.db import DATA_INBOX_DDL

    with sqlite3.connect(":memory:") as conn:
        conn.execute(DATA_INBOX_DDL)
        conn.execute(
            "INSERT INTO data_inbox_log(source_key,template_id,period_key,planned_at,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (SOURCE, "template_1", "2026-11-17", "2026-11-17 10:00:00", "waiting", "2026-11-17 10:00:00"),
        )
        assert conn.execute("SELECT status, archive_pending FROM data_inbox_log").fetchone() == ("waiting", 0)


def test_per_source_token_map_does_not_fall_back(inbox, monkeypatch):
    client, _ = inbox
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    monkeypatch.setenv("IMT_DATA_INBOX_TOKENS", '{"scores_01":"source-only"}')
    assert upload(client, csv_bytes()).status_code == 401
    assert upload(client, csv_bytes(), path="/api/data-inbox/other/upload").status_code == 403
    result = upload(client, csv_bytes(), headers={"Authorization": "Bearer source-only"})
    assert result.status_code == 200, result.text


def test_resolved_upload_path_must_stay_in_source(inbox, monkeypatch):
    client, root = inbox
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    expected = root / SOURCE / "incoming"
    outside = root.parent / "outside"
    outside.mkdir()
    resolve = Path.resolve

    def resolve_with_link(path, *args, **kwargs):
        return outside if path == expected else resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_link)
    response = upload(client, csv_bytes())
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_DATA_INBOX_PATH"
    assert not list(outside.iterdir())


@pytest.mark.parametrize(("extension", "contents"), [("csv", csv_bytes), ("xlsx", xlsx_bytes)])
def test_http_upload_and_scheduled_delivery(ctx, inbox, monkeypatch, extension, contents):
    state, scheduler, notifier, _, owner, *_ = ctx
    client, root = inbox
    monkeypatch.setenv("IMT_DATA_INBOX_TOKEN", TOKEN)
    add_second(ctx)
    template_id = scheduled(ctx, mode="link")
    raw = contents()
    response = upload(client, raw, f"cross-machine.{extension}", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    data = response.json()
    sha = hashlib.sha256(raw).hexdigest()
    assert data == {"ok": True, "source_key": SOURCE, "file": data["file"], "sha256": sha, "rows": 2,
                    "columns": ["手机号", "姓名", "得分", "金额"]}
    assert data["file"].endswith(f"_{sha[:8]}.{extension}")
    incoming = root / SOURCE / "incoming"
    target = incoming / data["file"]
    assert list(incoming.iterdir()) == [target]
    assert target.read_bytes() == raw
    stamp = (NOW - timedelta(seconds=5)).timestamp()
    os.utime(target, (stamp, stamp))
    for seconds in (0, 30, 60):
        scheduler.run_once(NOW + timedelta(seconds=seconds))
    assert len(notifier.sent_messages) == 4
    assert sum("/h5/notice-receipt" in message.text for message in notifier.sent_messages) == 2
    assert any("测试成员您好，得分 95" in message.text for message in notifier.sent_messages)
    assert any("第二成员您好，得分 88" in message.text for message in notifier.sent_messages)
    assert (root / SOURCE / "processed" / "2026-11-17" / data["file"]).read_bytes() == raw
    assert not list(incoming.iterdir())
    assert state.conn.execute("SELECT COUNT(*) AS n FROM data_inbox_log").fetchone()["n"] == 1
    record = owner.get(f"{TEMPLATES}/{template_id}").json()["data_inbox_latest"]
    assert (record["status"], record["row_count"], record["sha256"]) == ("success", 2, sha)


def test_local_newest_waits_for_quiet_and_same_hash_skips_next_slot(ctx, inbox):
    _, scheduler, notifier, _, owner, *_ = ctx
    _, root = inbox
    add_second(ctx)
    template_id = scheduled(ctx)
    raw = csv_bytes()
    old = put_local(root, csv_bytes(HEADER + f"{FIRST},测试成员,1,2\n"), name="older.csv")
    newest = put_local(root, raw, at=NOW)
    put_local(root, raw, name=".tmp_staged.csv", at=NOW + timedelta(seconds=1))
    put_local(root, raw, name="~$lock.csv", at=NOW + timedelta(seconds=1))
    scheduler.run_once(NOW)
    assert not notifier.sent_messages and newest.exists()
    for seconds in (30, 60, 90):
        scheduler.run_once(NOW + timedelta(seconds=seconds))
    assert len(notifier.sent_messages) == 2 and old.exists()
    assert (root / SOURCE / "processed" / "2026-11-17" / newest.name).read_bytes() == raw
    put_local(root, raw, at=NOW + timedelta(minutes=4))
    for seconds in (300, 330, 360):
        scheduler.run_once(NOW + timedelta(seconds=seconds))
    assert len(notifier.sent_messages) == 2 and old.exists()
    assert owner.get(f"{TEMPLATES}/{template_id}").json()["data_inbox_latest"]["status"] == "skipped"


def test_partial_delivery_retries_only_unsent_part(ctx, inbox, monkeypatch):
    _, scheduler, notifier, _, owner, *_ = ctx
    _, root = inbox
    template_id = scheduled(ctx, mode="link")
    filename = put_local(root, csv_bytes(HEADER + f"{FIRST},测试成员,1,2\n")).name
    send = notifier.send_text
    link_attempts = []

    def fail_first_link(receive_id, body, idem_key, receive_type):
        if "/h5/notice-receipt" in body:
            link_attempts.append(idem_key)
            if len(link_attempts) == 1:
                return SendResult(ok=False, result="failed", retryable=True, error="TEST_LINK_FAILURE")
        return send(receive_id, body, idem_key, receive_type)

    monkeypatch.setattr(notifier, "send_text", fail_first_link)
    scheduler.run_once(NOW)
    assert len(notifier.sent_messages) == 1
    assert owner.get(f"{TEMPLATES}/{template_id}").json()["data_inbox_latest"]["status"] == "processing"
    assert not (root / SOURCE / "processed").exists()
    for seconds in (30, 60, 90):
        scheduler.run_once(NOW + timedelta(seconds=seconds))
    assert len(notifier.sent_messages) == 2
    assert len(link_attempts) == 2 and link_attempts[0] == link_attempts[1]
    assert owner.get(f"{TEMPLATES}/{template_id}").json()["data_inbox_latest"]["status"] == "success"
    assert (root / SOURCE / "processed" / "2026-11-17" / filename).is_file()


def test_committed_success_recovers_archive_without_resending(ctx, inbox, monkeypatch):
    state, scheduler, notifier, *_ = ctx
    _, root = inbox
    scheduled(ctx)
    filename = put_local(root, csv_bytes(HEADER + f"{FIRST},测试成员,1,2\n")).name

    def unavailable(*_):
        raise PermissionError("TEST_ARCHIVE_UNAVAILABLE")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", unavailable)
        scheduler.run_once(NOW)
    record = state.conn.execute("SELECT status, archive_pending FROM data_inbox_log").fetchone()
    assert (record["status"], record["archive_pending"]) == ("success", 1)
    for seconds in (30, 60, 90):
        scheduler.run_once(NOW + timedelta(seconds=seconds))
    assert len(notifier.sent_messages) == 1
    assert state.conn.execute("SELECT archive_pending FROM data_inbox_log").fetchone()["archive_pending"] == 0
    assert (root / SOURCE / "processed" / "2026-11-17" / filename).is_file()


@pytest.mark.parametrize(("kind", "why"), [
    ("missing", "没有可用文件"),
    ("stale", "超过"),
    ("rows", "最低"),
    ("roster", "姓名与名册不一致"),
    ("duplicate", "手机号重复"),
    ("required", "变量「得分」不能为空"),
])
def test_guards_archive_failed_without_delivery(ctx, inbox, kind, why):
    state, scheduler, notifier, _, owner, *_ = ctx
    _, root = inbox
    template_id = scheduled(ctx, min_rows=2 if kind == "rows" else 1)
    row = f"{FIRST},测试成员,1,2\n"
    table = HEADER + {
        "roster": row.replace("测试成员", "姓名不匹配"),
        "duplicate": row * 2, "required": row.replace(",1,", ",,"),
    }.get(kind, row)
    if kind != "missing":
        filename = put_local(root, csv_bytes(table), at=NOW - timedelta(hours=25 if kind == "stale" else 1)).name
    for seconds in (0, 30, 60):
        scheduler.run_once(NOW + timedelta(seconds=seconds))
    assert not notifier.sent_messages
    assert not state.conn.execute("SELECT 1 FROM task_instance WHERE template_id=?", (template_id,)).fetchone()
    record = owner.get(f"{TEMPLATES}/{template_id}").json()["data_inbox_latest"]
    assert record["status"] == "failed" and why in record["error_msg"]
    assert state.conn.execute("SELECT COUNT(*) AS n FROM data_inbox_log").fetchone()["n"] == 1
    if kind != "missing":
        assert (root / SOURCE / "failed" / "2026-11-17" / filename).read_bytes() == csv_bytes(table)
