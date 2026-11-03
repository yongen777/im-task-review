# -*- coding: utf-8 -*-
"""2026-11-03 上午三项修复的回归：

1. 回调回复必须走**来源平台**的通道（`main._channel_notifier`）——
   实锤：密信回调回复被发给企微 bot（errcode=93006 invalid chatid），用户侧“完全不回话”；
2. 移出角色按「绑定平台」回收到对应平台的待归纳池（人在企微角色、退出密信角色时，
   密信绑定回密信池、企微绑定不动）——旧逻辑按“全局是否还在任意角色”判断，人在面板里凭空消失；
3. 量子密信永远不发原生图片（10-29 实测口径：手机端点不开），直接走 H5 链接，
   不再靠 send_image 签名不匹配的 TypeError 兜底（08:15 日志实锤）。
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


# ── 2. 移出角色按平台回池 ─────────────────────────────────────────
def test_remove_from_role_recycles_binding_per_platform() -> None:
    store = _store()
    try:
        z_role = store.create_role("密信组", actor="t", platform="zdxlz")["role_id"]
        w_role = store.create_role("企微组", actor="t", platform="wecom")["role_id"]
        store.add_role_member(z_role, PHONE, NAME, DEPT, actor="t")
        store.add_role_member(w_role, PHONE, NAME, DEPT, actor="t")
        store.upsert_delivery_target(PHONE, "zd_uid_1", platform="zdxlz")
        store.upsert_delivery_target(PHONE, "wx_uid_1", platform="wecom")

        # 只退出密信角色：密信绑定回密信待归纳池（带姓名/部门），企微绑定不动
        store.delete_role_member(z_role, PHONE, actor="t")
        p = store.get_pending_member("zd_uid_1")
        assert p is not None, "退出最后一个密信角色后，密信绑定应回待归纳池"
        assert (p["platform"], p["phone"], p["name"], p["dept"]) == ("zdxlz", PHONE, NAME, DEPT)
        assert store.get_pending_member("wx_uid_1") is None, "还在企微角色，企微绑定不应回池"

        zt = store.get_delivery_target(PHONE, platform="zdxlz", include_unusable=True)
        assert zt is not None and zt["usable"] == 0
        wt = store.get_delivery_target(PHONE, platform="wecom")
        assert wt is not None and wt["usable"] == 1, "企微绑定必须保持可用"

        # 再退出企微角色：企微绑定也回池
        store.delete_role_member(w_role, PHONE, actor="t")
        p2 = store.get_pending_member("wx_uid_1")
        assert p2 is not None and p2["platform"] == "wecom"
        assert store.get_delivery_target(PHONE, platform="wecom", include_unusable=True)["usable"] == 0
    finally:
        store.close()


def test_remove_one_of_two_roles_same_platform_keeps_binding() -> None:
    """同平台还有别的角色时（一人多角色），移出一个不回收绑定。"""
    store = _store()
    try:
        ra = store.create_role("密信A组", actor="t", platform="zdxlz")["role_id"]
        rb = store.create_role("密信B组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(ra, PHONE, NAME, DEPT, actor="t")
        store.add_role_member(rb, PHONE, NAME, DEPT, actor="t")
        store.upsert_delivery_target(PHONE, "zd_uid_2", platform="zdxlz")

        store.delete_role_member(ra, PHONE, actor="t")
        assert store.get_pending_member("zd_uid_2") is None
        assert store.get_delivery_target(PHONE, platform="zdxlz")["usable"] == 1
    finally:
        store.close()


# ── 3. 密信附图永远走链接 ──────────────────────────────────────────
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