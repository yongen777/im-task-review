from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from PIL import Image

from app.config import format_dt
from app.state import BizError
from test_20261117_manual_mark_submitted import ctx
from test_20261118_portal import portal, publish


@pytest.mark.parametrize("kind", ["form", "notice"])
def test_create_and_push_reuses_manual_delivery_and_deduplicates_request(ctx, kind):
    store, scheduler, notifier, _, owner, _, original, member = ctx
    payload = {"name": "创建后立即推送", "kind": kind, "period": "day", "is_manual": True,
               "role_ids": original["role_ids"], "text_tpl": "只发送这条创建提醒",
               "push_on_create": True, "request_id": "432a97d5021c4e61a8438deadf65a46c"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: owner.post("/api/admin/templates", json=payload), range(2)))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    bodies = [response.json() for response in responses]
    assert len({body["template_id"] for body in bodies}) == 1
    [accepted] = [body["push"] for body in bodies if not body.get("replayed")]
    assert accepted["queued"] and accepted["instance_count"] == 1
    tid = bodies[0]["template_id"]
    for seconds in (0, 30, 60):
        scheduler.run_once(store.now() + timedelta(seconds=seconds))
    assert len([message for message in notifier.sent_messages if "只发送这条创建提醒" in message.text]) == 1
    assert [inst["phone"] for inst in store.list_instances_for_period(tid, "once")] == [member["phone"]]
    # 2026-11-19 需求一：已推送过 → 先回 needs_confirm（预检不占冷却 marker）；
    # 真的确认重推时才撞上「创建即推送」留下的 3 秒防抖（2026-11-20 改）。
    confirm = owner.post(f"/api/admin/templates/{tid}/push-now", json={})
    assert confirm.status_code == 200 and confirm.json()["needs_confirm"] is True, confirm.text
    blocked = owner.post(f"/api/admin/templates/{tid}/push-now?confirmed=1", json={})
    assert blocked.status_code == 429 and blocked.json()["code"] == "PUSH_COOLDOWN", blocked.text
    assert owner.delete(f"/api/admin/templates/{tid}").status_code == 200
    assert owner.post("/api/admin/templates", json=payload).status_code == 409
    assert store.get_template(tid) is None


def test_create_and_push_keeps_saved_template_if_scheduler_is_unavailable(ctx):
    store, _, notifier, _, owner, _, original, _ = ctx
    owner.app.state.scheduler = None
    response = owner.post("/api/admin/templates", json={
        "name": "调度不可用时保存", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": original["role_ids"], "text_tpl": "等待管理员推送", "push_on_create": True,
        "request_id": "652acde5021c4e61a8438deadf65a46c",
    })
    assert response.status_code == 200, response.text
    response_data = response.json()
    assert response_data["push"]["queued"] is False and response_data["push"]["code"] == "PUSH_UNAVAILABLE"
    assert store.get_template(response_data["template_id"])["enabled"]
    assert not notifier.sent_messages


@pytest.mark.parametrize("overrides", [{"is_manual": False}, {"enabled": False}, {"request_id": "bad"}])
def test_create_and_push_rejects_invalid_configuration_before_saving(ctx, overrides):
    store, _, notifier, _, owner, _, original, _ = ctx
    count = store.conn.execute("SELECT COUNT(*) FROM task_template").fetchone()[0]
    response = owner.post("/api/admin/templates", json={
        "name": "不可直接推送", "kind": "notice", "period": "day", "is_manual": True,
        "role_ids": original["role_ids"], "text_tpl": "本条不会发送", "push_on_create": True,
        "request_id": "a18fcde5021c4e61a8438deadf65a46c",
        **overrides,
    })
    assert response.status_code == 400, response.text
    assert store.conn.execute("SELECT COUNT(*) FROM task_template").fetchone()[0] == count
    assert not notifier.sent_messages


@pytest.mark.parametrize("kind", ["form", "notice"])
def test_immediate_template_only_pushes_once_per_recipient(ctx, kind):
    store, scheduler, notifier, _, owner, _, original, member = ctx
    payload = {"name": "仅立即推送", "kind": kind, "period": "day", "is_manual": True,
               "role_ids": original["role_ids"], "text_tpl": "请核对本次任务", "push_on_create": False,
               "request_id": "00a184f05c3b409b891271ba8b850ec8"}
    response = owner.post("/api/admin/templates", json=payload)
    assert response.status_code == 200, response.text
    template = response.json()["template"]
    tid = template["template_id"]
    assert template["is_manual"] and template["enabled"] and not template["remind_at"] and not template["notice_times"]
    assert "push" not in response.json()
    assert not template["exec_weekdays"] and not template["exec_dom_days"]

    now = store.now()
    for seconds in (0, 30, 60):
        scheduler.run_once(now + timedelta(seconds=seconds))
    assert store.conn.execute("SELECT COUNT(*) FROM task_instance WHERE template_id=?", (tid,)).fetchone()[0] == 0
    assert not any("请核对本次任务" in msg.text for msg in notifier.sent_messages)

    sent = owner.post(f"/api/admin/templates/{tid}/push-now", json={})
    assert sent.status_code == 200 and sent.json()["period_key"] == "once", sent.text
    for minutes in (3, 4, 5):
        tick = now + timedelta(minutes=minutes)
        scheduler.run_once(tick)
        assert scheduler.push_now(tid, now_dt=tick)["sent"] == 0
    instances = store.list_instances_for_period(tid, "once")
    assert len(instances) == 1 and instances[0]["phone"] == member["phone"]
    assert instances[0]["status"] == ("sent" if kind == "notice" else "pending")
    messages = [msg.text for msg in notifier.sent_messages if "请核对本次任务" in msg.text]
    assert len(messages) == 1
    assert ("/h5/task#t=" in messages[0]) == (kind == "form")
    listing = owner.get("/api/admin/instances", params={"template_id": tid, "period_key": now.date().isoformat()})
    assert listing.status_code == 200 and listing.json()["total"] == 1
    if kind == "form":
        tasks = owner.get("/api/admin/stats-panel/tasks").json()["items"]
        assert next(task for task in tasks if task["template_id"] == tid)["current_period_key"] == "once"
        overview = owner.get("/api/admin/stats-panel/overview", params={"template_id": tid, "period_key": "once"})
        assert overview.status_code == 200, overview.text


@pytest.mark.parametrize("manual", [False, True])
def test_notice_sends_each_uploaded_image_once(ctx, manual):
    store, scheduler, notifier, _, owner, _, original, _ = ctx
    paths = ["notice_attach/first.png", "notice_attach/second.webp"]
    (store.data_dir / "notice_attach").mkdir(exist_ok=True)
    for path in paths:
        Image.new("RGB", (4, 4), "blue").save(store.data_dir / path)
    response = owner.post("/api/admin/templates", json={
        "name": "仅图片通知", "kind": "notice", "period": "day", "is_manual": manual,
        "role_ids": original["role_ids"], "notice_times": [] if manual else ["10:00"], "text_tpl": "",
        "attach_image_path": paths[0], "attach_image_name": "第一张",
        "spec": {"notice_items": [
            {"id": "one", "format": "file", "image_path": paths[0], "file_caption": "第一张"},
            {"id": "two", "format": "file", "image_path": paths[1], "file_caption": "第二张"},
        ]},
    })
    assert response.status_code == 200, response.text
    tid = response.json()["template_id"]
    now = store.now()
    if manual:
        assert owner.post(f"/api/admin/templates/{tid}/push-now", json={}).status_code == 200
    for seconds in (0, 30, 60):
        scheduler.run_once(now + timedelta(seconds=seconds))
    if manual:
        scheduler.push_now(tid, now_dt=now + timedelta(minutes=3))
    messages = [msg.text for msg in notifier.sent_messages if "/h5/img/" in msg.text]
    assert [message.splitlines()[0] for message in messages] == ["第一张（点击查看）：", "第二张（点击查看）："]
    image_url = urlsplit(messages[-1].splitlines()[-1])
    response = owner.get(image_url.path, params={"raw": 1})
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"


def test_manual_upload_claims_before_concurrent_delivery_and_honors_deadline(ctx):
    store, scheduler, notifier, _, owner, _, template, _ = ctx
    now = store.now()
    payload = {"name": "并发手推", "kind": "form", "period": "day", "is_manual": True,
               "role_ids": template["role_ids"], "text_tpl": "仅投递一次"}
    past = owner.post("/api/admin/templates", json={**payload, "manual_deadline_at": format_dt(now - timedelta(seconds=1))})
    assert past.status_code == 400 and past.json()["code"] == "INVALID_DEADLINE"
    deadline = format_dt(now + timedelta(hours=1))
    created = owner.post("/api/admin/templates", json={**payload, "manual_deadline_at": deadline})
    assert created.status_code == 200, created.text
    tid = created.json()["template_id"]
    prep = scheduler.push_now_prepare(tid, now_dt=now)
    assert prep["deadline_at"] == deadline
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: scheduler.push_now_deliver(tid, prep), range(3)))
    assert sum(stats["sent"] for stats in results) == 1
    assert len([msg for msg in notifier.sent_messages if "仅投递一次" in msg.text]) == 1
    with pytest.raises(BizError, match="任务已到截止时间"):
        scheduler.push_now_prepare(tid, now_dt=now + timedelta(hours=1))


def test_portal_task_panel_endpoints_are_sub_admin_only(portal):
    service, identity, (_, _, _, admin, owner, *_) = portal
    task_id = publish(service, identity)["task_id"]
    assert owner.get("/api/admin/portal-tasks").status_code == 200
    assert admin.get("/api/admin/portal-tasks").status_code == 403
    assert admin.get(f"/api/admin/portal-tasks/{task_id}/attachments/placeholder").status_code == 403
