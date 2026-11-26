"""工作台更正引导仅随截止前的已完成任务展示。"""

from pathlib import Path

import pytest

from test_20261117_manual_mark_submitted import PHONE, ctx  # noqa: F401
from test_20261119_manual_repush_batches import SECOND, add_member, advance, token_of


IM_GUIDANCE = "如需修改提交内容，请前往「个人任务工作台 → 提交记录」修改提交。"
H5_GUIDANCE = "如需修改记录，请前往「个人任务工作台 → 提交记录」修改提交。"


@pytest.mark.parametrize("cutoff", ["open", "no_deadline", "closed"])
def test_completed_repush_guidance_and_h5_review(ctx, cutoff):
    state, scheduler, notifier, _, owner, _, template, instance = ctx
    tid = template["template_id"]
    state.update_template(tid, {"has_deadline": cutoff != "no_deadline",
                                "spec": {"upload_modules": ["fields"]}})
    add_member(state, template["role_ids"][0], SECOND, "未提交成员", template["owner_id"])

    raw = state.issue_h5_token(PHONE, tid, instance["period_key"], instance["id"])
    saved = state.submit_h5_task(raw_token=raw, fields={"qty": "2", "summary": "原提交"}, refs=[], note=None)
    assert saved["ok"] and state.get_instance(instance["id"])["status"] == "approved"

    advance(state, scheduler, minutes=9 * 60 if cutoff == "closed" else 3)
    prep = scheduler.push_now_prepare(tid, reprocess=True)
    assert prep["new_batch"]
    assert scheduler.push_now_deliver(tid, prep)["sent"] == 2

    message = next(m.text for m in notifier.sent_messages if m.receive_id == "test_recipient")
    assert "你已在本周期完成该任务" in message
    assert (IM_GUIDANCE + "\n点击链接可查看已提交内容：" in message) is (cutoff != "closed")
    if cutoff == "closed":
        assert IM_GUIDANCE not in message
    response = owner.post("/api/h5/task/info", json={"token": token_of(notifier, "test_recipient")})
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["instance"]["period_key"] == prep["period_key"]
    assert review["instance"]["status"] == "approved" and not review["window_open"]
    assert review["submission"]["fields"]["summary"] == "原提交"
    assert review["can_edit"] is False
    assert review["show_edit_guidance"] is (cutoff != "closed")

    other = next(m.text for m in notifier.sent_messages if m.receive_id == f"union-{SECOND}")
    assert IM_GUIDANCE not in other
    if cutoff == "closed":
        assert "任务已截止" in other
    response = owner.post("/api/h5/task/info", json={"token": token_of(notifier, f"union-{SECOND}")})
    assert response.status_code == 200, response.text
    assert response.json()["show_edit_guidance"] is False


def test_task_html_uses_server_guidance_flag():
    html = (Path(__file__).resolve().parents[1] / "app" / "h5" / "task.html").read_text(encoding="utf-8")
    assert 'if (tipText || data.show_edit_guidance)' in html
    assert 'if (data.show_edit_guidance)' in html
    assert html.count(H5_GUIDANCE) == 1
    assert "<title>IMTask</title>" in html
    assert "crypto.randomUUID(" not in html
    assert "<script src=" not in html
