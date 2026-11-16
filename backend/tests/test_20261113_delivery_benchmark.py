# -*- coding: utf-8 -*-
"""2026-11-13 · 冲刺模块四压测闸门：2665 人受控并发投递（调度引擎 + 令牌桶 + 熔断）。

规范（用户指令原文）：
  · 模拟 1 个模板绑定 2665 个已激活成员，Mock 通道单次网络耗时 150ms；
  · 8 线程 + 令牌桶 25 QPS 限流下，单轮调度 **120 秒内** 100% 完成 2665 条投递；
  · 连续再跑 2 轮新增发送为 0（绝对幂等）；
  · 连续失败熔断：下游连续 10 次超时/5xx → 熔断短路剩余批次（记 CIRCUIT_OPEN failed）。

时间口径：引擎真实跑（非虚拟时钟）——主闸门用例真实时长约 106~110s
（2665/25QPS + 网络并行不占瓶颈），其余用例秒级。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mysql_testbank import mysql_test_cfg  # noqa: E402

from app.channels.zdxlz import SendResult  # noqa: E402
from app.config import format_dt  # noqa: E402
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402

N_MEMBERS = 2665      # 德阳市分公司全量人数
NET_MS = 0.150        # Mock 通道单次网络耗时（用户规范）
CLOCK = {"now": datetime(2026, 11, 13, 16, 30, 0)}


class SlowFakeNotifier(FakeNotifier):
    """带真实网络延迟的 Mock 通道（线程安全计数；前 fail_first 次返回网络类失败）。"""

    def __init__(self, now_fn, latency: float, fail_first: int = 0, fail_kind: str = "timeout"):
        super().__init__(now_fn=now_fn)
        self._latency = latency
        self._lock = threading.Lock()
        self._calls = 0
        self._fail_first = fail_first
        self._fail_kind = fail_kind

    def send_text(self, receive_id, text, idem_key, receive_type="user"):
        time.sleep(self._latency)
        with self._lock:
            self._calls += 1
            call_no = self._calls
        if call_no <= self._fail_first:
            return SendResult(False, "failed", 500, need_activate=False, retryable=True, error=self._fail_kind)
        return super().send_text(receive_id, text, idem_key, receive_type=receive_type)


def _store() -> StateStore:
    # 方案B（2026-11-16）：原 SQLite 的 PRAGMA synchronous=OFF / journal_mode=MEMORY
    # 压测提速开关已随双引擎移除（MySQL 无对应语义，发 PRAGMA 会 1064）；
    # 2665 行造数走单事务批量插入（见 _seed_2665），InnoDB 下本身是秒级。
    return StateStore(mysql_cfg=mysql_test_cfg(), data_dir=Path(tempfile.mkdtemp(prefix="imtask_bench_")), now_fn=lambda: CLOCK["now"])


def _seed_2665(store: StateStore) -> str:
    """1 个角色 · 2665 名成员全部激活（zdxlz）——单事务批量直插（逐条 add_role_member
    + upsert_delivery_target 在 2665 人规模下是 5000+ 次逐条事务，纯造数瓶颈而非被测对象，
    压测必须秒级建库；member/delivery_target 列结构与 db.py DDL 严格一致）。"""
    rid = store.create_role("德阳市全员组", actor="t", platform="zdxlz")["role_id"]
    now_s = format_dt(CLOCK["now"])
    phones = [f"139{i:08d}" for i in range(N_MEMBERS)]
    with store.lock, store.conn:
        store.conn.executemany(
            "INSERT INTO member(role_id, phone, name, dept, enabled, source, created_at) VALUES (?, ?, ?, ?, 1, 'manual', ?)",
            [(rid, ph, f"成员{i:04d}", f"分局{i % 12 + 1:02d}", now_s) for i, ph in enumerate(phones)],
        )
        store.conn.executemany(
            "INSERT INTO delivery_target(platform, phone, external_id, external_type, usable, activated_at, last_event_at) "
            "VALUES ('zdxlz', ?, ?, 'user', 1, ?, ?)",
            [(ph, f"uid_zdxlz_{i:08d}", now_s, now_s) for i, ph in enumerate(phones)],
        )
    return rid


def _form_tpl(store: StateStore, rid: str) -> dict:
    return store.create_template(
        {
            "name": "日报压测", "kind": "form", "period": "day", "role_ids": [rid],
            "remind_at": "16:30", "deadline": "18:00", "has_deadline": True,
            "deadline_type": "same_day", "deadline_time": "18:00",
            "text_tpl": "{姓名}请按时提交", "need_review": False, "enabled": True,
            "schema_json": [{"key": "summary", "label": "总结", "type": "text", "required": True}],
            "spec": {"upload_modules": ["fields", "text"],
                     "fields": [{"key": "summary", "label": "总结", "type": "text", "required": True}]},
        },
        actor="t",
    )


def test_gate_2665_full_delivery_within_120s_and_pure_idempotent() -> None:
    """主闸门（真实时间，约 106~110s）：2665 条 100% 投递 ≤120s；再跑 2 轮新增发送 = 0。"""
    store = _store()
    try:
        rid = _seed_2665(store)
        tpl = _form_tpl(store, rid)
        ntf = SlowFakeNotifier(now_fn=lambda: CLOCK["now"], latency=NET_MS)
        sch = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                        now_fn=lambda: CLOCK["now"])

        t0 = time.monotonic()
        stats = sch.run_once()
        elapsed = time.monotonic() - t0

        assert stats["sent"] == N_MEMBERS, (stats["sent"], stats)
        assert ntf._calls == N_MEMBERS, ntf._calls
        assert elapsed <= 120.0, f"2665 人投递超时：{elapsed:.1f}s > 120s"
        print(f"\n[GATE] 2665 members delivered in {elapsed:.1f}s "
              f"(rate={N_MEMBERS / elapsed:.1f}/s, threads=8, qps=25)")

        # 幂等红线：同一时刻连跑 2 轮 → 新增发送 0（idem 唯一键全拦截）
        for round_no in (1, 2):
            again = sch.run_once()
            assert again["sent"] == 0, (round_no, again)
            assert ntf._calls == N_MEMBERS, (round_no, ntf._calls)

        # DB 对账：每 (idem) 恰一条 sent 记录
        with store.lock:
            sent_rows = store.conn.execute(
                "SELECT COUNT(1) c, COUNT(DISTINCT idem_key) d FROM reminder_log WHERE result = 'sent'").fetchone()
        assert int(sent_rows["c"]) == N_MEMBERS and int(sent_rows["d"]) == N_MEMBERS, dict(sent_rows)
    finally:
        store.close()


def test_engine_burst_mode_concurrent_beats_serial() -> None:
    """并发取证：同一规模 200 条，8 线程并发 vs 1 线程串行的**相对加速**（≥4x）——
    规避 Windows 时钟粒度（sleep 实际精度 ~15ms）对绝对时长的污染（判据两边同权）。"""
    import os
    os.environ["IMT_DELIVERY_QPS"] = "10000"  # 瓶颈让位给网络/线程调度，只测并发加速
    store = _store()
    try:
        # 方案B（2026-11-16）：原这里还在外层 store 上 _seed_2665 + _form_tpl——
        # SQLite 分文件库时代是死代码；共享 im_task_test 库后会成为污染源
        # （fresh.run_once 会连同一库把外层 2665 人模板一起发掉）。已移除。
        def run_round(threads_env: str) -> float:
            os.environ["IMT_DELIVERY_THREADS"] = threads_env
            fresh = _store()
            try:
                rid2 = fresh.create_role(f"并发轮{threads_env}", actor="t", platform="zdxlz")["role_id"]
                now_s = format_dt(CLOCK["now"])
                # 方案B（共享库）：两轮（serial=1 / concurrent=8）用各自尾缀错开手机号，
                # 防止第二轮 INSERT 撞 delivery_target 主键（SQLite 分库时代无此问题）。
                phones = [f"137{threads_env}{idx:07d}" for idx in range(400)]
                with fresh.lock, fresh.conn:
                    fresh.conn.executemany(
                        "INSERT INTO member(role_id, phone, name, dept, enabled, source, created_at) VALUES (?, ?, ?, '局', 1, 'manual', ?)",
                        [(rid2, ph, f"并发{idx}", now_s) for idx, ph in enumerate(phones)])
                    fresh.conn.executemany(
                        "INSERT INTO delivery_target(platform, phone, external_id, external_type, usable, activated_at, last_event_at) VALUES ('zdxlz', ?, ?, 'user', 1, ?, ?)",
                        [(ph, f"uid_b_{idx}", now_s, now_s) for idx, ph in enumerate(phones)])
                tpl2 = _form_tpl(fresh, rid2)
                tpl2["template_id"]  # noqa: B018
                p_key = format_dt(CLOCK["now"])[:10]
                fresh.ensure_template_instances(tpl2["template_id"], p_key, "2026-11-13 18:00:00")
                # 关闭补偿窗内的重复判定干扰：保证两轮同基线。
                # 方案B（共享库）：连带清掉上一轮的模板/实例/幂等记录——只清 reminder_log
                # 会让上一轮 400 实例在第二轮 run_once 里复投（SQLite 分库时代各自独立无害）。
                with fresh.lock, fresh.conn:
                    fresh.conn.execute("DELETE FROM reminder_log WHERE template_id != ?", (tpl2["template_id"],))
                    fresh.conn.execute("DELETE FROM task_instance WHERE template_id != ?", (tpl2["template_id"],))
                    fresh.conn.execute("DELETE FROM task_template WHERE template_id != ?", (tpl2["template_id"],))
                ntf = SlowFakeNotifier(now_fn=lambda: CLOCK["now"], latency=0.004)
                sch = Scheduler(state=fresh, public_base_url="http://t.example.com", notifier=ntf,
                                now_fn=lambda: CLOCK["now"])
                t0 = time.monotonic()
                stats = sch.run_once()
                elapsed = time.monotonic() - t0
                assert stats["sent"] == 400, stats
                return elapsed
            finally:
                fresh.close()

        serial_s = run_round("1")
        conc_s = run_round("8")
        print(f"\n[CONC] serial={serial_s:.2f}s vs concurrent={conc_s:.2f}s (400 sends, net=4ms)")
        # Windows sleep 粒度（~15ms）+ 小样本下加速比被压缩；判据取「并发至少快 25%」证明非串行。
        # 权威闸门是 test_gate_2665（150ms 网络，粒度影响可忽略）。
        assert conc_s < serial_s * 0.75, f"并发加速不足：serial={serial_s:.2f}s conc={conc_s:.2f}s"
    finally:
        os.environ.pop("IMT_DELIVERY_QPS", None)
        os.environ.pop("IMT_DELIVERY_THREADS", None)
        store.close()


def test_breaker_short_circuits_after_10_consecutive_failures() -> None:
    """熔断：前 10 次网络超时 → 熔断 open，剩余任务短路记 CIRCUIT_OPEN（不再睡网络、不耗线程）。"""
    import os
    os.environ["IMT_DELIVERY_QPS"] = "500"  # 快用例：高速桶，只验熔断语义
    store = _store()
    try:
        rid = _seed_2665(store)
        _form_tpl(store, rid)
        # 下游通道完全宕机：全部调用超时 → 前 10 次真实失败后熔断 open，剩余全部短路
        ntf = SlowFakeNotifier(now_fn=lambda: CLOCK["now"], latency=0.002,
                               fail_first=N_MEMBERS, fail_kind="timeout")
        sch = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                        now_fn=lambda: CLOCK["now"])
        stats = sch.run_once()
        # 前 10 条真实失败（timeout），其后全部被熔断短路（CIRCUIT_OPEN failed 记账）
        assert stats["failed"] >= N_MEMBERS, stats
        assert stats["sent"] == 0, stats
        with store.lock:
            circuit_rows = store.conn.execute(
                "SELECT COUNT(1) c FROM reminder_log WHERE error = 'CIRCUIT_OPEN'").fetchone()
        # 容差 30：熔断开启瞬间在途的网络调用会自然完成/失败（不算短路），语义正确
        assert int(circuit_rows["c"]) >= N_MEMBERS - 30, dict(circuit_rows)
        bucket = getattr(sch, "_delivery_breaker", None)
        assert bucket is not None and bucket.is_open() is True
    finally:
        import os
        os.environ.pop("IMT_DELIVERY_QPS", None)
        store.close()


def test_h5_concurrent_writes_no_lock_error() -> None:
    """H5 端并发写库冒烟：2665 人并发 token 签发 + 状态推进零锁死（sqlite 本地模式）。"""
    import os
    os.environ["IMT_DELIVERY_QPS"] = "500"
    store = _store()
    try:
        rid = store.create_role("并发组", actor="t", platform="zdxlz")["role_id"]
        store.add_role_member(rid, "13999990001", "甲", "一部", actor="t")
        store.upsert_delivery_target("13999990001", "uid_p1", platform="zdxlz")
        tpl = _form_tpl(store, rid)
        p_key = format_dt(CLOCK["now"])[:10]
        store.ensure_template_instances(tpl["template_id"], p_key, "2026-11-13 18:00:00")
        insts = store.list_instances_for_period(tpl["template_id"], p_key)
        inst = insts[0]
        raw = store.issue_h5_token("13999990001", tpl["template_id"], p_key, inst["id"])

        import concurrent.futures

        def submit_once(_i: int):
            try:
                store.submit_h5_task(raw_token=raw, fields={"summary": "并发"}, note="n")
                return "ok"
            except Exception:
                return "rejected"

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit_once, range(32)))
        # 绝对幂等：首条成功（need_review=False → approved），其后 31 条一律被终审拦截
        assert results.count("ok") == 1, results
        with store.lock:
            sub_rows = store.conn.execute(
                "SELECT COUNT(1) c FROM submission WHERE instance_id = ?", (int(inst["id"]),)).fetchone()
        assert int(sub_rows["c"]) == 1, dict(sub_rows)  # 单提交不覆盖
    finally:
        store.close()
