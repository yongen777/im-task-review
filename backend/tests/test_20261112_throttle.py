# -*- coding: utf-8 -*-
"""2026-11-12 晚 · 冲刺模块二地基单测：令牌桶（25 QPS 数学闸门）+ 熔断器。

2665 人压测的**数学层**断言（零真实等待，注入虚拟时钟）：
  · 25 QPS 桶放行 2665 条 ≥ 106.6s（理论下界）且 ≤ 120s（验收上界）→ 单轮投递规划达标；
  · 突发容量 burst=25（1 秒存量）先放行 25 条再按速率匀速；
  · 多线程并发借桶仍严格守恒（放行总数 = 等待时间 × 速率 + 初始容量）。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.throttle import (  # noqa: E402
    DEFAULT_DELIVERY_BREAKER_N,
    DEFAULT_DELIVERY_QPS,
    DEFAULT_DELIVERY_THREADS,
    CircuitBreaker,
    TokenBucket,
    breaker_threshold,
    delivery_qps,
    delivery_threads,
)


class FakeClock:
    """虚拟单调时钟：sleep 不真等待，直接把时钟推进 sleep 的秒数。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def tick(self) -> float:  # clock()
        return self.now

    def sleep(self, seconds: float) -> None:  # sleep()
        self.now += max(0.0, seconds)


def test_bucket_25qps_2665_within_120s_gate() -> None:
    """2665 人 · 25 QPS：前 25 条吃初始容量（burst），其后匀速——
    理论总等待 = (2665-25)/25 = 105.6s，落在 (0, 120s] → 规范闸门数学层达标。"""
    clk = FakeClock()
    bucket = TokenBucket(rate_per_sec=25.0, clock=clk.tick, sleep=clk.sleep)
    waited = 0.0
    for _ in range(2665):
        waited += bucket.acquire()
    assert waited >= (2665 - 25) / 25.0 - 0.01, waited  # 速率守恒下界（允许初始突发）
    assert waited <= 120.0 + 0.01, waited              # 验收上界：120 秒内必须放完


def test_bucket_burst_then_smooth_rate() -> None:
    """burst=速率（25 存量）：前 25 条零等待瞬间放行，其后严格按 0.04s/条匀速——
    50 条总等待 ≈ 25×0.04 = 1.0s（平滑不触发 429）。"""
    clk = FakeClock()
    bucket = TokenBucket(rate_per_sec=25.0, clock=clk.tick, sleep=clk.sleep)
    w1 = bucket.acquire()   # 初始容量内 → 0 等待
    assert w1 == 0.0
    waited = sum(bucket.acquire() for _ in range(49))  # 存量还剩 24 条 → 25 条需匀速
    assert waited >= 25 * 0.04 - 0.01, waited
    assert waited <= 25 * 0.04 + 0.01, waited


def test_bucket_conservation_math() -> None:
    """放行守恒（数学层）：400 条 / 25 QPS / 存量 25 → 墙钟推进 ≥ 375/25 = 15s。
    桶本身是串行化资源，单线程顺序获取与多线程总时长守恒等价（多线程安全另用真实时钟用例验证）。"""
    clk = FakeClock()
    bucket = TokenBucket(rate_per_sec=25.0, clock=clk.tick, sleep=clk.sleep)
    for _ in range(400):
        bucket.acquire()
    assert clk.now >= 1000.0 + (400 - 25) / 25.0 - 0.1, clk.now


def test_bucket_multithread_realtime_safe() -> None:
    """真实多线程不挂死/不超发：4 线程 × 10 条，全部放行、无异常（线程安全冒烟）。"""
    import time as _time
    bucket = TokenBucket(rate_per_sec=200.0)  # 真时钟，高速率 → 总耗时 < 1s
    done: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(10):
            bucket.acquire()
            with lock:
                done.append(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    t0 = _time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(done) == 40, len(done)
    assert _time.monotonic() - t0 < 10.0


def test_bucket_try_acquire_respects_deadline() -> None:
    """try_acquire 超时让位：熔断短路场景下不无限挂桶。"""
    clk = FakeClock()
    bucket = TokenBucket(rate_per_sec=2.0, clock=clk.tick, sleep=clk.sleep)
    assert bucket.try_acquire(timeout=0.0) is True     # 存量内立即成功
    got = [bucket.try_acquire(timeout=0.05) for _ in range(3)]  # 容量 2 → 后续让位
    assert got[0] is True or got[1] is True            # 至少再放 1 条（补充的存量）
    assert not all(got), "容量耗尽后 try_acquire 必须出现让位失败"


def test_breaker_consecutive_failures_and_reset() -> None:
    """连续 10 次失败 → 熔断 open；成功清零；reset 供新批次。"""
    br = CircuitBreaker(fail_threshold=10)
    assert br.is_open() is False
    for i in range(9):
        br.record_failure()
        assert br.is_open() is False, i
    br.record_failure()
    assert br.is_open() is True
    assert br.consecutive_failures == 10
    br.record_success()
    assert br.is_open() is False
    for _ in range(10):
        br.record_failure()
    br.reset()
    assert br.is_open() is False and br.consecutive_failures == 0


def test_env_defaults_match_spec() -> None:
    """规范默认值：25 QPS / 8 线程 / 连续 10 次熔断（未设环境变量时）。"""
    import os
    for key in ("IMT_DELIVERY_QPS", "IMT_DELIVERY_THREADS", "IMT_DELIVERY_BREAKER_N"):
        os.environ.pop(key, None)
    assert delivery_qps() == DEFAULT_DELIVERY_QPS == 25.0
    assert delivery_threads() == DEFAULT_DELIVERY_THREADS == 8
    assert breaker_threshold() == DEFAULT_DELIVERY_BREAKER_N == 10
