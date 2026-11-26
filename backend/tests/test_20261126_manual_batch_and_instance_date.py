"""手动上传批次的并发投递和实例页的发生日期筛选。"""

from threading import Barrier, Lock

import pytest

from app.channels.zdxlz import SendResult
from test_20261117_manual_mark_submitted import PHONE, ctx  # noqa: F401
from test_20261119_manual_repush_batches import PERIOD, add_member, advance, make_periodic_form


@pytest.mark.parametrize("reprocess", [False, True], ids=["first_push", "repush"])
def test_manual_upload_batch_overlaps_sends_and_isolates_failures(ctx, monkeypatch, reprocess):
    store, scheduler, notifier, _, owner, _, existing, _ = ctx
    role_id = existing["role_ids"][0]
    for idx in range(2, 5):
        add_member(store, role_id, f"1390000940{idx}", f"并发成员{idx}", existing["owner_id"], ext=f"parallel-{idx}")
    tid = make_periodic_form(owner, role_id)
    if reprocess:
        scheduler.push_now_deliver(tid, scheduler.push_now_prepare(tid))
        advance(store, scheduler)

    prep = scheduler.push_now_prepare(tid, reprocess=reprocess)
    assert prep["new_batch"] is reprocess
    monkeypatch.setenv("IMT_DELIVERY_THREADS", "4")
    barrier = Barrier(4, timeout=3)
    lock = Lock()
    calls = []

    def send_text(receive_id, text, idem_key, *, receive_type):
        with lock:
            calls.append(receive_id)
        barrier.wait()
        if receive_id == "parallel-2":
            raise RuntimeError("模拟网络异常")
        if receive_id == "parallel-3":
            return SendResult(ok=False, result="skipped", error="FAKE_SKIP")
        return SendResult(ok=True, result="sent")

    monkeypatch.setattr(notifier, "send_text", send_text)
    assert scheduler.push_now_deliver(tid, prep) == {
        "sent": 2, "skipped": 1, "failed": 1, "pushed_instances": 2,
    }
    for _ in range(2):
        assert scheduler.push_now_deliver(tid, prep) == {
            "sent": 0, "skipped": 0, "failed": 0, "pushed_instances": 0,
        }
    assert sorted(calls) == ["parallel-2", "parallel-3", "parallel-4", "test_recipient"]
    failures = store.conn.execute(
        "SELECT r.error FROM reminder_log r JOIN task_instance i ON i.id=r.instance_id "
        "WHERE r.template_id=? AND i.period_key=? AND r.kind='manual_push' AND r.result='failed'",
        (tid, prep["period_key"]),
    ).fetchall()
    assert [row["error"] for row in failures] == ["EXC:模拟网络异常"]


def test_instance_list_date_includes_activity_and_preserves_combined_filters(ctx):
    store, _, _, _, owner, sub, daily, daily_instance = ctx
    owner_id = daily["owner_id"]
    role_id = daily["role_ids"][0]
    before = "2026-11-16 23:59:59"
    start = f"{PERIOD} 00:00:00"
    last_second = f"{PERIOD} 23:59:59"
    next_start = "2026-11-18 00:00:00"
    deadline = "2026-11-30 18:00:00"

    def instance(period, key, created, *, first_sent=None, status="pending", template_owner=owner_id, role=role_id):
        tpl = store.create_template({
            "name": f"实例筛选 {key}", "kind": "form", "period": period, "role_ids": [role],
            "is_manual": key == "once", "manual_deadline_at": deadline if key == "once" else None,
            "remind_at": "08:30", "deadline": "18:00", "text_tpl": "请提交",
        }, owner_id=template_owner)
        row = store.ensure_instance(tpl["template_id"], PHONE, "测试成员", None, key, deadline)
        store.conn.execute(
            "UPDATE task_instance SET created_at=?, first_sent_at=?, status=? WHERE id=?",
            (created, first_sent, status, row["id"]),
        )
        return tpl["template_id"], row["id"]

    store.conn.execute("UPDATE task_instance SET created_at=? WHERE id=?", (before, daily_instance["id"]))
    _, monthly_id = instance("month", "2026-11", start)
    weekly_template, weekly_id = instance("week", "2026-W47", before, first_sent=last_second, status="overdue")
    _, once_id = instance("day", "once", last_second)
    _, other_day_id = instance("month", "2026-11", before, first_sent=next_start)
    _, next_day_id = instance("week", "2026-W47", next_start)
    other_owner_id, _ = sub("other_instance_owner")
    other_role_id = store.create_role("其他管理员组", owner_id=other_owner_id)["role_id"]
    instance("month", "2026-11", start, template_owner=other_owner_id, role=other_role_id)

    def ids(**params):
        response = owner.get("/api/admin/instances", params=params)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert response.json()["total"] == len(items)
        return {row["id"] for row in items}

    expected = {daily_instance["id"], monthly_id, weekly_id, once_id}
    # 面板使用 period_key，API 也接受 date。
    assert ids(period_key=PERIOD) == expected
    assert ids(date=PERIOD) == expected
    assert ids(date=PERIOD, template_id=weekly_template, status="overdue") == {weekly_id}
    assert ids(date=PERIOD, template_id=weekly_template, status="approved") == set()
    assert ids(period_key="2026-W47") == {weekly_id, next_day_id}
    assert ids(date="", period_key="") == ids() == expected | {other_day_id, next_day_id}
    stats = owner.get("/api/admin/stats", params={"date": PERIOD})
    assert stats.status_code == 200, stats.text
    assert stats.json()["stats"]["total"] == len(expected)
