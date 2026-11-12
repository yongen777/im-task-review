# -*- coding: utf-8 -*-
"""2026-11-03 上午三项修复的回归：

1. 回调回复必须走**来源平台**的通道（`main._channel_notifier`）——
   实锤：密信回调回复被发给企微 bot（errcode=93006 invalid chatid），用户侧“完全不回话”；
2. 量子密信永远不发原生图片（10-29 实测口径：手机端点不开），直接走 H5 链接，
   不再靠 send_image 签名不匹配的 TypeError 兜底（08:15 日志实锤）；
3. 移出分组 → 未完成任务实例一并清掉（带"另一角色保留/已完成历史不删"边界）。
   （绑定名册比对与"移除=彻底删绑定"2026-11-03 下午已迁至 test_activation_model.py）
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import SHANGHAI_TZ  # noqa: E402
from app.main import _channel_notifier  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402


def bind_activated_at(store, phone: str, external_id: str, activated_at: str = "2000-01-01 00:00:00", platform: str = "zdxlz") -> None:
    """建立平台绑定并**指定首次激活时刻**。

    2026-11-09 新口径「当天激活的只创建激活时刻之后的任务，第二天才是刷新完整一天」：
    实例生成会拿 delivery_target.activated_at 与 period_key 比 ——
      · 激活时刻早于 period_key 那天 → 建实例
      · 激活时刻晚于 period_key 那天 → 不建（那天他还没激活）

    历史 period_key 的测试场景必须把激活时刻设在那天之前，
    否则语义上「那天他还没激活」→ 不该有实例，断言就会对不上。
    """
    store.upsert_delivery_target(phone, external_id, platform=platform, activated_at=activated_at)
PHONE = "13800000222"
NAME = "测试员"
DEPT = "云中台"


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_fix_")) / "im.db")


# ── 1. 回复通道按平台 ─────────────────────────────────────────────
def test_channel_notifier_picks_source_platform() -> None:
    z, w, fb = object(), object(), object()
    # 多通道装配：密信回调的回复必须拿密信通道，绝不能跟 active（企微）走
    assert _channel_notifier({"zdxlz": z, "wecom": w}, "zdxlz", fb) is z
    assert _channel_notifier({"zdxlz": z, "wecom": w}, "wecom", fb) is w
    # 单通道/测试：注册表为空时退回 fallback
    assert _channel_notifier({}, "zdxlz", fb) is fb
    assert _channel_notifier(None, "wecom", fb) is fb


# ── 2. 密信附图永远走链接 ──────────────────────────────────────────
class _ZdxlzLikeNotifier(FakeNotifier):
    """模拟密信通道：有 send_image 但签名与调度器不兼容——一旦被调用立即报错。"""

    def send_image(self, *args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("量子密信通道不允许尝试原生图片（应直接走 H5 链接）")


def test_zdxlz_attach_image_goes_link_not_native() -> None:
    store = _store()
    try:
        role = store.create_role("密信图组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(role, PHONE, NAME, DEPT, actor="t")
        store.upsert_delivery_target(PHONE, "zd_uid_img", platform="zdxlz")

        data_dir = Path(store.db.db_path).parent
        (data_dir / "notice_attach").mkdir(parents=True, exist_ok=True)
        img_file = data_dir / "notice_attach" / "x.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        store.create_template(
            {
                "name": "带图通知", "kind": "notice", "role_ids": [role],
                "text_tpl": "今日数据", "notice_times": ["08:00"], "period": "day",
                "attach_image_path": "notice_attach/x.png", "attach_image_name": "统计图",
                "enabled": True,
            },
            actor="t",
        )

        zdx = _ZdxlzLikeNotifier(now_fn=lambda: datetime(2026, 11, 3, 8, 5, 0, tzinfo=SHANGHAI_TZ))
        sched = Scheduler(
            state=store, public_base_url="http://127.0.0.1:18090", notifier=zdx,
            now_fn=lambda: datetime(2026, 11, 3, 8, 5, 0, tzinfo=SHANGHAI_TZ),
            notifiers={"zdxlz": zdx},
        )
        sched.run_once()

        texts = [m.text for m in zdx.sent_messages]
        assert any(t == "今日数据" for t in texts), texts
        assert any(t.startswith("统计图（点击查看）：") for t in texts), texts
        # 两条消息（正文 + 链接），绝无图片消息、无异常路径
        assert len(texts) == 2, texts
    finally:
        store.close()


# ── 3. 移出分组 → 未完成任务实例一并清掉 ───────────────────────────
def _notice_tpl(store: StateStore, role_ids: list, name: str) -> str:
    tpl = store.create_template(
        {
            "name": name, "kind": "notice", "role_ids": role_ids,
            "text_tpl": "提醒正文", "notice_times": ["08:00"], "period": "day", "enabled": True,
        },
        actor="t",
    )
    return tpl["template_id"]


def test_remove_member_purges_open_instances_of_that_role() -> None:
    store = _store()
    try:
        ra = store.create_role("密信A组", actor="t", platform="zdxlz")["role_id"]
        rb = store.create_role("密信B组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(ra, PHONE, NAME, DEPT, actor="t")
        bind_activated_at(store, PHONE, "uid_phone")
        store.add_role_member(rb, PHONE, NAME, DEPT, actor="t")
        bind_activated_at(store, PHONE, "uid_phone")

        exclusive_tpl = _notice_tpl(store, [ra], "仅A组任务")
        shared_tpl = _notice_tpl(store, [ra, rb], "A+B共用任务")
        pkey, deadline = "2026-11-03", "2026-11-03 23:59:00"
        for tid in (exclusive_tpl, shared_tpl):
            store.ensure_template_instances(tid, pkey, deadline)
        # 另一天的已完成实例：删人时必须保留历史
        store.ensure_template_instances(exclusive_tpl, "2026-11-02", "2026-11-02 23:59:00")
        done = store.get_instance_by_key(exclusive_tpl, PHONE, "2026-11-02")
        store.conn.execute("UPDATE task_instance SET status = 'approved' WHERE id = ?", (int(done["id"]),))
        store.conn.commit()

        # 只退出 A 组：仅A组模板的未完成实例转 cancelled（2026-11-12 新口径：作废留痕而非物理删除）；
        # 共用模板实例保留（还在 B 组）；已完成历史删干净（2026-11-09 口径保持）
        store.delete_role_member(ra, PHONE, actor="t")
        _open = store.get_instance_by_key(exclusive_tpl, PHONE, pkey)
        assert _open is not None and _open["status"] == "cancelled", "仅A组任务的未完成实例应转为 cancelled 作废"
        assert store.get_instance_by_key(shared_tpl, PHONE, pkey) is not None, "还在B组，共用模板实例不得删"
        assert store.get_instance(done["id"]) is None, "移除成员后本组模板下的已完成实例也应删除（2026-11-09 新口径：移除要删干净）"

        # 再退出 B 组：共用模板的未完成实例同样转 cancelled（不再是物理删除）
        store.delete_role_member(rb, PHONE, actor="t")
        _shared_open = store.get_instance_by_key(shared_tpl, PHONE, pkey)
        assert _shared_open is not None and _shared_open["status"] == "cancelled", \
            "退出共用模板最后一个角色后，未完成实例应转为 cancelled（2026-11-12 新口径）"
    finally:
        store.close()


def test_remove_member_keeps_terminal_instances() -> None:
    store = _store()
    try:
        ra = store.create_role("密信C组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(ra, PHONE, NAME, DEPT, actor="t")
        bind_activated_at(store, PHONE, "uid_phone")
        tid = _notice_tpl(store, [ra], "通知任务")
        pkey = "2026-11-03"
        store.ensure_template_instances(tid, pkey, "2026-11-03 23:59:00")
        inst = store.get_instance_by_key(tid, PHONE, pkey)
        store.conn.execute("UPDATE task_instance SET status = 'sent' WHERE id = ?", (int(inst["id"]),))
        store.conn.commit()

        store.delete_role_member(ra, PHONE, actor="t")
        assert store.get_instance(inst["id"]) is None, "移除成员后本组模板下的已发送实例也应删除（2026-11-09 新口径）"
    finally:
        store.close()