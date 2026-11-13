# -*- coding: utf-8 -*-
"""2026-11-13 下午 3 点 2600 人推送前 · 三大底层加固专项（先红后修）。

修复一 push_now 防卡死（2600 人 × 25QPS ≈ 108 秒不再冻结事件循环）：
  1. push_now_prepare 只建当期实例不发送；push_now_deliver 用 prep 时间基线投递；
  2. 同一 prep 重放 → 幂等 0 新增（后台重试/快速双击绝不二次发送——幂等红线）；
  3. 拆分后与一体化 push_now 行为等价；
  4. API 层 POST push-now 立即返回 queued（200 + instance_count），
     后台投递在响应周期外完成（TestClient 同步周期内即可见结果）。

修复二 MySQL 连接代理线程隔离契约（防 Packet sequence number wrong / 长锁串行）：
  5. 并发两线程事务**各借各的连接**（threading.local），嵌套复用同线程连接；
  6. 事务提交/回滚后归还连接池；自动提交路径「借-执-交-还」一次完成；
  7. 事务中不提前提交，异常整体回滚后归还。

修复三 月度任务周期归属（每月 1 号执行 + 本月月底 17:00 截止）：
  8. 11-13（非锚点日）15:00 手动推送：生成当期 2600 形态实例，
     截止 = 2026-11-30 17:00:00，消息文案显示「11月30日 17:00」；
  9. H5 Token 有效期 = min(截止+24h, 720h) = 434h（覆盖到 12-01 17:00，月底前随时可交）；
  10. 11-13 ~ 11-30 任意时刻的 30s tick（非锚点日）严格跳过：零生成零发送；
  11. 12-01 15:00 锚点 tick：自动生成 12 月新周期实例（截止 2026-12-31 17:00）并各发 1 条；
      连跑多 tick 幂等；11 月已交数据（submitted）互不冲突、不再被打扰。

全部相对时刻（fake clock）+ 内存假件，绝不发起真实网络请求、不碰 data/im_task.db。
"""
from __future__ import annotations

import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from app.api.admin import create_admin_router, register_admin_guard  # noqa: E402
from app.db import MySQLConnProxy  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore, calc_h5_token_ttl_hours  # noqa: E402

DEFAULT_SUB_ADMIN_PASSWORD = StateStore.DEFAULT_SUB_ADMIN_PASSWORD

PHONE_A = "13900003001"
PHONE_B = "13900003002"

CLOCK = {"now": datetime(2026, 11, 13, 8, 0, 0)}  # 绑定时刻须早于 15:00 推送（激活切分口径）


def _store() -> StateStore:
    return StateStore(Path(tempfile.mkdtemp(prefix="imtask_fix3_")) / "im.db", now_fn=lambda: CLOCK["now"])


def _app(store: StateStore, scheduler: Scheduler) -> FastAPI:
    app = FastAPI()
    register_admin_guard(app, store)
    app.include_router(create_admin_router(store, notifier=None, public_base_url="http://t.example.com"))
    app.state.scheduler = scheduler
    return app


def _login(app: FastAPI, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return c


def _new_sub_admin(app: FastAPI, super_admin: TestClient, username: str) -> TestClient:
    r = super_admin.post("/api/admin/accounts", json={"username": username, "display_name": username})
    assert r.status_code == 200, r.text
    c = _login(app, username, DEFAULT_SUB_ADMIN_PASSWORD)
    r = c.post("/api/admin/change-password",
                json={"old_password": DEFAULT_SUB_ADMIN_PASSWORD, "new_password": "NewPass@2026"})
    assert r.status_code == 200, r.text
    return c


# ═══════════════════ 修复一：push_now 防卡死 ═══════════════════

def _push_setup():
    """通用脚手架：超管 + 属主分级 sa + 双成员（已激活）+ upload 模板（每月 1 号 / 月底 17:00 截止）。
    同时充当修复三的模板底座（exec_dom_days=[1] + deadline_type=month_end）。"""
    CLOCK["now"] = datetime(2026, 11, 13, 8, 0, 0)  # 重置共享时钟（绑定早于当日推送）
    store = _store()
    store.upsert_admin_user("admin", "Admin@2026", must_change=False)
    ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
    sched = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                      now_fn=lambda: CLOCK["now"])
    app = _app(store, sched)
    sa = _login(app, "admin", "Admin@2026")
    sub = _new_sub_admin(app, sa, "sa")
    rid = store.create_role("月报组", actor="t", platform="zdxlz", owner_id=2)["role_id"]
    store.add_role_member(rid, PHONE_A, "甲一", "一部", owner_id=2, actor="sub")
    store.add_role_member(rid, PHONE_B, "乙二", "一部", owner_id=2, actor="sub")
    store.upsert_delivery_target(PHONE_A, f"uid_{PHONE_A[-4:]}", platform="zdxlz", external_type="union_id")
    store.upsert_delivery_target(PHONE_B, f"uid_{PHONE_B[-4:]}", platform="zdxlz", external_type="union_id")
    r = sub.post("/api/admin/templates", json={
        "name": "月度数据上报", "kind": "upload", "role_ids": [rid], "period": "day",
        "remind_at": "15:00", "has_deadline": True, "deadline_type": "month_end",
        "deadline_time": "17:00", "text_tpl": "请提交本月数据", "enabled": True,
        "need_review": False, "exec_dom_days": [1],
        "spec": {"upload_modules": ["image"],
                 "fields": [{"key": "n", "label": "数量", "type": "number", "required": False}]},
    })
    assert r.status_code == 200, r.text
    return store, app, sa, sub, sched, ntf, r.json()["template_id"]


def test_fix1_prepare_creates_instances_without_sending() -> None:
    """prepare 段只建当期实例，绝不发送任何消息；时间基线/周期/截止计算正确。"""
    store, app, sa, sub, sched, ntf, tpl_id = _push_setup()
    try:
        CLOCK["now"] = datetime(2026, 11, 13, 15, 0, 0)
        prep = sched.push_now_prepare(tpl_id, actor="sub")
        assert prep["instance_count"] == 2, prep
        assert prep["period_key"] == "2026-11-13" and prep["pushed_at"] == "2026-11-13 15:00:00", prep
        assert prep["deadline_at"] == "2026-11-30 17:00:00", prep
        assert ntf.sent_messages == [], "prepare 不得发送消息"
        for inst in store.list_instances_for_period(tpl_id, "2026-11-13"):
            assert inst["status"] == "pending", inst
            assert inst["deadline_at"] == "2026-11-30 17:00:00", inst
        with store.lock:
            assert store.conn.execute(
                "SELECT COUNT(*) FROM reminder_log WHERE template_id = ?",
                (tpl_id,)).fetchone()[0] == 0, "prepare 不得留发送/占位记录"
    finally:
        store.close()


def test_fix1_split_equivalent_and_prep_replay_idempotent() -> None:
    """拆分后 prepare+deliver 与一体化 push_now 行为等价；同一 prep 重放幂等 0 新增。"""
    store, app, sa, sub, sched, ntf, tpl_id = _push_setup()
    try:
        # 一体化路径（调度层直调，历史行为）
        CLOCK["now"] = datetime(2026, 11, 13, 15, 0, 0)
        stats1 = sched.push_now(tpl_id, actor="sub")
        assert stats1["sent"] == 2 and stats1["pushed_instances"] == 2, stats1

        # 拆分路径等价（新 store 重新走一遍同场景）
        store2, _app2, _sa2, sub2, sched2, ntf2, tpl2 = _push_setup()
        try:
            CLOCK["now"] = datetime(2026, 11, 13, 15, 0, 0)
            prep = sched2.push_now_prepare(tpl2, actor="sub")
            stats2 = sched2.push_now_deliver(tpl2, prep, actor="sub")
            assert stats2 == stats1, (stats1, stats2)
            assert len(ntf2.sent_messages) == 2

            # 同一 prep 重放（后台重试/双击）：幂等 0 新增——幂等红线
            stats3 = sched2.push_now_deliver(tpl2, prep, actor="sub")
            assert stats3["sent"] == 0 and stats3["pushed_instances"] == 0, stats3
            assert len(ntf2.sent_messages) == 2, "重放不得产生第二条"
        finally:
            store2.close()
    finally:
        store.close()


def test_fix1_api_returns_queued_and_background_completes() -> None:
    """API 层：POST push-now 立即返回 queued（200 + instance_count）；
    后台投递在响应周期内完成（真实环境=后台线程，事件循环零阻塞）。"""
    store, app, sa, sub, sched, ntf, tpl_id = _push_setup()
    try:
        CLOCK["now"] = datetime(2026, 11, 13, 15, 0, 0)
        r = sub.post(f"/api/admin/templates/{tpl_id}/push-now")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("queued") is True, body
        assert body.get("instance_count") == 2, body
        assert body.get("pushed_at") == "2026-11-13 15:00:00", body

        # 后台投递完成（TestClient 等待完整响应周期，含 BackgroundTasks）
        assert len(ntf.sent_messages) == 2, ntf.sent_messages
        for m in ntf.sent_messages:
            assert m.text.startswith("【任务提醒】"), m.text[:30]
            assert "11月30日 17:00" in m.text, m.text  # 修复三：截止显示为具体日期
            assert "/h5/task#t=" in m.text
        # 留痕：manual_push = 2 实例行 + 1 marker；审计 dispatch + completion ≥ 2 条
        with store.lock:
            kinds = dict((k, c) for k, c in store.conn.execute(
                "SELECT kind, COUNT(*) FROM reminder_log WHERE template_id = ? GROUP BY kind", (tpl_id,)))
        assert kinds.get("manual_push") == 3, kinds
        with store.lock:
            audit = store.conn.execute(
                "SELECT COUNT(1) FROM admin_audit WHERE action = 'template.push_now' AND target_id = ?",
                (tpl_id,)).fetchone()[0]
        assert audit >= 2, audit
    finally:
        store.close()


# ═══════════════════ 修复二：MySQL 连接代理线程隔离契约 ═══════════════════

class _FakeCursor:
    def __init__(self, conn: "_FakeConn", rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self._conn = conn
        self._rows = list(rows or [{"ok": 1}])
        self._pos = 0
        self.lastrowid = 101
        self.rowcount = len(self._rows)
        self.description = ()

    def execute(self, sql, params=()):
        self._conn.ops.append(("execute", sql))
        self._pos = 0
        return self

    def executemany(self, sql, seq):
        self._conn.ops.append(("executemany", sql))
        self.rowcount = len(list(seq))
        return self

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(list(self._rows))

    def close(self):
        self._conn.ops.append(("cursor_close",))


class _FakeConn:
    def __init__(self, cid: str) -> None:
        self.cid = cid
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.ops: List[Any] = []

    def cursor(self):
        self.ops.append(("cursor",))
        cur = _FakeCursor(self)
        self.ops.append(("cursor_made",))  # 只为计数稳定；真实断言看 commits/closes
        self.ops.pop()
        return cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class _FakePool:
    """round-robin 借出——任何人改成「单例句柄」共享都会被断言抓红。"""

    def __init__(self, n: int = 4) -> None:
        self.conns = [_FakeConn(f"c{i}") for i in range(n)]
        self.i = 0

    def connection(self) -> "_FakeConn":
        conn = self.conns[self.i % len(self.conns)]
        self.i += 1
        return conn


def _proxy(pool: Optional[_FakePool] = None) -> MySQLConnProxy:
    """绕过 __init__（避免真装 pymysql/PooledDB）手工装配：生产等价形态。"""
    p = MySQLConnProxy.__new__(MySQLConnProxy)
    p._cfg = {}  # type: ignore[attr-defined]
    p._tls = threading.local()  # type: ignore[attr-defined]
    p._pool = pool or _FakePool()  # type: ignore[attr-defined]
    return p


def test_fix2_concurrent_threads_borrow_isolated_connections() -> None:
    """两线程并发事务：各借各的连接；嵌套复用同线程连接；提交后各自归还一次。"""
    pool = _FakePool()
    proxy = _proxy(pool)
    out: Dict[str, Any] = {}
    a_in = threading.Event()
    b_in = threading.Event()

    def worker_a() -> None:
        with proxy:
            out["a"] = proxy._tls.stack[-1]
            with proxy:  # 嵌套：复用同线程外层连接，不新借
                out["a_nested"] = proxy._tls.stack[-1]
            a_in.set()
            b_in.wait(5)  # B 在 A 持事务期间并发进入
        out["a_done"] = True

    def worker_b() -> None:
        a_in.wait(5)
        with proxy:
            out["b"] = proxy._tls.stack[-1]
            b_in.set()

    ta = threading.Thread(target=worker_a)
    tb = threading.Thread(target=worker_b)
    ta.start()
    tb.start()
    ta.join(10)
    tb.join(10)
    assert out.get("a_done") is True, out
    assert out["a"] is not out["b"], "⛔ 并发线程必须借到不同连接（线程隔离回归）"
    assert out["a_nested"] is out["a"], "嵌套事务必须复用同线程连接"
    a: _FakeConn = out["a"]
    b: _FakeConn = out["b"]
    assert a.commits == 1 and a.closes == 1, (a.cid, a.commits, a.closes)
    assert b.commits == 1 and b.closes == 1, (b.cid, b.commits, b.closes)
    assert a.rollbacks == 0 and b.rollbacks == 0


def test_fix2_in_txn_execute_does_not_commit_early() -> None:
    """事务段内 execute 不提前 commit；正常退出统一提交归还。"""
    pool = _FakePool()
    proxy = _proxy(pool)
    with proxy:
        conn = proxy._tls.stack[-1]
        cur = proxy.execute("INSERT INTO t(a) VALUES (?)", (1,))
        assert conn.commits == 0, "事务中不得中途提交"
        assert cur.fetchone() is None or cur.fetchone() in (None, {"ok": 1})  # cursor 可用即可
    assert conn.commits == 1 and conn.closes == 1, "退出事务必须提交并归还"


def test_fix2_autocommit_path_borrow_execute_commit_release() -> None:
    """无事务段 execute：借→执→交→还一次完成，返回的立即可用。"""
    pool = _FakePool()
    proxy = _proxy(pool)
    cur = proxy.execute("SELECT 1 AS ok")
    row = cur.fetchone()
    assert row and row["ok"] == 1, row
    conn: _FakeConn = pool.conns[0]
    assert conn.commits == 1 and conn.closes == 1, (conn.commits, conn.closes)
    assert not getattr(proxy._tls, "stack", None), "自动提交路径不得残留线程事务栈"


def test_fix2_exception_rolls_back_and_releases() -> None:
    """事务中抛异常 → 整体回滚 + 归还；绝不泄漏连接。"""
    pool = _FakePool()
    proxy = _proxy(pool)
    try:
        with proxy:
            conn = proxy._tls.stack[-1]
            proxy.execute("INSERT INTO t(a) VALUES (?)", (1,))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert conn.rollbacks == 1 and conn.closes == 1, (conn.rollbacks, conn.closes)
    assert conn.commits == 0, "回滚路径不得提交"


def test_fix2_sequential_txns_return_to_pool_between() -> None:
    """同线程顺序两个事务：各自借还（池两次借出），不残留线程栈。"""
    pool = _FakePool()
    proxy = _proxy(pool)
    with proxy:
        first = proxy._tls.stack[-1]
    with proxy:
        second = proxy._tls.stack[-1]
    assert first.closes == 1 and second.closes == 1
    assert not getattr(proxy._tls, "stack", None)


def test_fix2_real_init_uses_thread_local_storage() -> None:
    """源码级守卫：真实 MySQLConnProxy.__init__ 必须用 threading.local() 存线程连接栈，
    且不得存在共享单例活动连接句柄（self._conn/self._active 之类）——
    防止将来重构回「单例句柄」形态（跨线程共享 pymysql 连接 → Packet sequence 错乱）。"""
    import inspect
    src = inspect.getsource(MySQLConnProxy)
    assert "threading.local()" in src, "必须使用 threading.local() 做线程本地连接栈"
    for forbidden in ("self._conn =", "self._active_conn =", "self._current ="):
        assert forbidden not in src, f"不得存在共享单例连接句柄: {forbidden}"
    # 借还必须走池：__enter__ 借、__exit__ 归还（源码形态）
    assert "_borrow()" in src and "_release(" in src


# ═══════════════════ 修复三：月度任务周期归属 ═══════════════════

def test_fix3_monthly_full_scenario() -> None:
    """「每月 1 号执行 + 月底 17:00 截止」全链路：
    11-13 手推（截止 11-30 17:00、Token 覆盖至 12-01 17:00）→
    11 月非锚点日 tick 全跳过 → 12-01 锚点自动新周期（截止 12-31 17:00）→
    已交数据互不冲突。"""
    store, app, sa, sub, sched, ntf, tpl_id = _push_setup()
    try:
        # ── 11-13 15:00 管理员手动推送（round 1）──
        CLOCK["now"] = datetime(2026, 11, 13, 15, 0, 0)
        prep = sched.push_now_prepare(tpl_id, actor="sub")
        sched.push_now_deliver(tpl_id, prep, actor="sub")
        nov = store.list_instances_for_period(tpl_id, "2026-11-13")
        assert len(nov) == 2 and all(i["status"] == "pending" for i in nov)
        assert all(i["deadline_at"] == "2026-11-30 17:00:00" for i in nov)
        assert all("11月30日 17:00" in m.text for m in ntf.sent_messages)

        # H5 Token：min(截止+24h, 720h) —— 11-13 15:00 签发 → 434h（到 12-01 17:00）
        for inst in nov:
            ttl = calc_h5_token_ttl_hours(inst, datetime(2026, 11, 13, 15, 0, 0))
            assert abs(ttl - 434.0) < 1e-6, (inst["deadline_at"], ttl)
            assert calc_h5_token_ttl_hours(inst, datetime(2026, 11, 30, 16, 59, 0)) > 1.0, "月底最后一刻仍可提交"

        # ── 11 月其余时间（非锚点日 13~30 号）：30s tick 逐个跑，严格零触发 ──
        for t in (datetime(2026, 11, 13, 15, 0, 30), datetime(2026, 11, 13, 15, 1, 0),
                  datetime(2026, 11, 20, 15, 0, 30), datetime(2026, 11, 25, 9, 0, 30)):
            CLOCK["now"] = t
            st = sched.run_once()
            assert st["generated_instances"] == 0, (t, st)
            assert st["sent"] == 0, (t, st)
        assert len(ntf.sent_messages) == 2, ntf.sent_messages

        # 员工月底前提交（11 月已交）；H5 Token 到 12-01 17:00 仍覆盖窗口
        with store.lock, store.conn:
            store.conn.execute(
                "UPDATE task_instance SET status = 'submitted' "
                "WHERE template_id = ? AND period_key = '2026-11-13'", (tpl_id,))

        # ── 12-01 15:00:30 锚点：自动新周期 ──
        CLOCK["now"] = datetime(2026, 12, 1, 15, 0, 30)
        st = sched.run_once()
        assert st["generated_instances"] == 2, st
        assert st["sent"] == 2, st
        dec = store.list_instances_for_period(tpl_id, "2026-12-01")
        assert len(dec) == 2, dec
        assert all(i["deadline_at"] == "2026-12-31 17:00:00" for i in dec), dec
        assert all(i["status"] == "pending" for i in dec)
        assert len(ntf.sent_messages) == 4, ntf.sent_messages
        assert all("12月31日 17:00" in m.text for m in ntf.sent_messages[2:])

        # 同日再跑 2 个 30s tick：幂等，无新增
        for _ in range(2):
            CLOCK["now"] = CLOCK["now"] + timedelta(seconds=30)
            st2 = sched.run_once()
            assert st2["sent"] == 0 and st2["generated_instances"] == 0, st2
        assert len(ntf.sent_messages) == 4

        # 11 月已交数据互不冲突：仍是 submitted，不被 12 月链路复活/打扰
        nov_after = store.list_instances_for_period(tpl_id, "2026-11-13")
        assert all(i["status"] == "submitted" for i in nov_after), nov_after
        assert st["missed"] == 0, st  # 11 月已交，未触发逾期补发噪音
    finally:
        store.close()


def test_fix3_frontend_save_and_echo_roundtrip() -> None:
    """前端保存/回显链路：API 建的「每月 1 号 + 月底 17:00」模板，get_template 回显
    exec_dom_days=[1]、deadline_type=month_end、deadline_time=17:00（前端回显取同源字段）。"""
    store, app, sa, sub, sched, ntf, tpl_id = _push_setup()
    try:
        tpl = store.get_template(tpl_id)
        assert tpl is not None
        assert tpl["exec_dom_days"] == [1], tpl
        assert tpl["deadline_type"] == "month_end", tpl
        assert tpl["deadline_time"] == "17:00", tpl
        assert bool(tpl["has_deadline"]) is True, tpl
        # 编辑弹窗回显走 GET /templates；此处守 DB 源头（前端字段映射 realApi 已对齐 c022ff0）
        r = sub.get("/api/admin/templates?size=50")
        assert r.status_code == 200, r.text
        found = next((t for t in r.json()["items"] if t["template_id"] == tpl_id), None)
        assert found is not None
    finally:
        store.close()
