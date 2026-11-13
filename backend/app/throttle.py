# -*- coding: utf-8 -*-
"""2026-11-12 晚 · 冲刺模块二：受控并发投递的地基组件（令牌桶 + 熔断器）。

设计约束：
  · 2665 人整点高峰：单线程串行发送约 10~15 分钟 → 8 工作线程 + 20~25 QPS 全局限流
    可将总投递时间稳定压进 2 分钟（2665 / 25 QPS ≈ 107s），且流量平滑不触发平台 429 频控；
  · 令牌桶：线程安全，支持注入单调时钟（压测用例零真实等待）；acquire 阻塞语义带上限保护；
  · 熔断器：只对「网络超时 / 5xx 不可用」计数，连续 N 次失败即熔断短路剩余批次，
    防止个别慢连接击穿 30 分钟补偿窗口、耗尽线程拖垮后台与 H5 服务；
  · 幂等红线（发送前查重 / 周期唯一）由调度器侧 reminder_log 唯一键 + 前置查重保证，
    本模块只管「节奏」不管「查重」。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

# ── 环境变量默认值（生产可调）────────────────────────────────────────
DEFAULT_DELIVERY_QPS = 25.0        # IMT_DELIVERY_QPS：全局放行速率（规范 20~25，默认取上界保 120s 闸门）
DEFAULT_DELIVERY_THREADS = 8       # IMT_DELIVERY_THREADS：工作线程数（规范 8~10）
DEFAULT_DELIVERY_BREAKER_N = 10    # IMT_DELIVERY_BREAKER_N：连续失败熔断阈值


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def delivery_qps() -> float:
    """全局令牌桶速率（每秒放行条数），环境变量 IMT_DELIVERY_QPS 可调。"""
    return max(1.0, _env_float("IMT_DELIVERY_QPS", DEFAULT_DELIVERY_QPS))


def delivery_threads() -> int:
    """发送工作线程数，环境变量 IMT_DELIVERY_THREADS 可调。"""
    return max(1, min(32, int(_env_float("IMT_DELIVERY_THREADS", DEFAULT_DELIVERY_THREADS))))


def breaker_threshold() -> int:
    """连续失败熔断阈值，环境变量 IMT_DELIVERY_BREAKER_N 可调。"""
    return max(1, int(_env_float("IMT_DELIVERY_BREAKER_N", DEFAULT_DELIVERY_BREAKER_N)))


class TokenBucket:
    """线程安全令牌桶（支持注入虚拟时钟用于零等待单测）。

    2026-11-13 修正（压测实锤）：早期「睡眠即补充 + 预支未来 _last」写法在多线程下
    互相污染（elapsed 恒 0 → 自动补充失效、锁自旋放大耗时，实测 2665 人 302s > 阈值）。
    现行实现：
      · 真实路径（默认）：单锁 + Condition.wait(timeout)——等待期间释放锁，醒来统一
        `_refill(单调时钟)` 精确补 token，数学正确、无预支污染；
      · 虚拟时钟路径（clock/sleep 均被注入，测试用）：sleep 直接推进虚拟时钟，
        醒来按速率折算入账——不依赖真实等待，单测毫秒级；
      · 速率上限两种路径同权：每次放行都对应真实/虚拟的 rate 时长等待。
    """

    def __init__(self, rate_per_sec: float, burst: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self.rate = float(rate_per_sec)
        self.capacity = float(burst if burst and burst > 0 else rate_per_sec)
        self._clock = clock
        self._sleep = sleep
        self._virtual = sleep is not None
        self._tokens = self.capacity
        self._last = self._clock()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def _refill_locked(self, now: float) -> None:
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    def acquire(self, n: float = 1.0) -> float:
        """阻塞获取 n 个令牌，返回累计等待秒数（压测断言用）。"""
        need = max(1.0, float(n))
        waited = 0.0
        if self._virtual:
            # 虚拟时钟路径（单测）：缺多少睡多少（推进虚拟时钟），醒来折算入账并放行
            with self._lock:
                self._refill_locked(self._clock())
                if self._tokens >= need:
                    self._tokens -= need
                    return waited
                wait_s = (need - self._tokens) / self.rate
                self._tokens = 0.0
                self._last = self._clock() + wait_s  # 预支到醒来时刻（单线程语义安全）
            assert self._sleep is not None
            self._sleep(wait_s)
            waited += wait_s
            with self._lock:
                self._tokens = min(self.capacity, self._tokens + wait_s * self.rate)
                self._tokens = max(0.0, self._tokens - need)
            return waited
        # 真实路径：Condition 等待（等待释放锁，醒来由统一 refill 精确补足）
        while True:
            with self._cond:
                self._refill_locked(self._clock())
                if self._tokens >= need:
                    self._tokens -= need
                    return waited
                wait_s = (need - self._tokens) / self.rate
            t0 = time.monotonic()
            with self._cond:
                self._cond.wait(timeout=max(wait_s, 0.001))
            waited += time.monotonic() - t0

    def try_acquire(self, n: float = 1.0, timeout: float = 0.0) -> bool:
        """带超时让位的获取：熔断开启时批次收尾线程用它退出，不无限挂桶。"""
        need = max(1.0, float(n))
        deadline = time.monotonic() + max(0.0, timeout)
        if self._virtual:
            with self._lock:
                self._refill_locked(self._clock())
                if self._tokens >= need:
                    self._tokens -= need
                    return True
                if timeout <= 0:
                    return False
                wait_s = min((need - self._tokens) / self.rate, timeout)
                self._last = self._clock() + wait_s
            assert self._sleep is not None
            self._sleep(wait_s)
            with self._lock:
                self._tokens = min(self.capacity, self._tokens + wait_s * self.rate)
                if self._tokens >= need:
                    self._tokens -= need
                    return True
            return False
        while True:
            with self._cond:
                self._refill_locked(self._clock())
                if self._tokens >= need:
                    self._tokens -= need
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_s = min((need - self._tokens) / self.rate, remaining, 0.25)
            with self._cond:
                self._cond.wait(timeout=max(wait_s, 0.001))
            if time.monotonic() >= deadline:
                with self._cond:
                    self._refill_locked(self._clock())
                    if self._tokens >= need:
                        self._tokens -= need
                        return True
                return False

    def tokens(self) -> float:
        with self._lock:
            self._refill_locked(self._clock())
            return self._tokens


class CircuitBreaker:
    """连续失败熔断器：同一批次内连续 fail_threshold 次「超时/5xx」→ open（短路剩余投递）。

    · record_success()：成功即清零连续计数；
    · record_failure()：只由调用方在**网络超时 / 5xx 不可用**时记（业务跳过/幂等拦截不算失败）；
    · is_open：达到阈值后保持 open，直到 reset()（新批次开始时调用）。
    """

    def __init__(self, fail_threshold: int = DEFAULT_DELIVERY_BREAKER_N) -> None:
        self.threshold = max(1, int(fail_threshold))
        self._lock = threading.Lock()
        self._consecutive = 0

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive += 1

    def is_open(self) -> bool:
        with self._lock:
            return self._consecutive >= self.threshold

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive

    def reset(self) -> None:
        with self._lock:
            self._consecutive = 0
