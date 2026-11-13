# -*- coding: utf-8 -*-
"""通讯录手机号查询短期内存缓存（2026-11-13 用户需求一 ·1）。

背景：2600 余人 CSV 导入时，同一手机号会在「严格核验（新）」与「导入即激活（2026-11-10
方案A 旧链路）」两条路径上被反查；批量导入前端还会有 dry_run → 正式导入的两轮提交。
每次反查都是一次完整网络往返（官方 user/search 解密信封 / 企微 getuserid），是导入耗时的
绝对大头。缓存把重复查询压成一次，TTL 短期（默认 600s）——通讯录数据本就稳定，10 分钟
内重查一次没有业务代价。

口径（与 2026-11-13 用例 test_ttl_cache_unit_semantics 对齐）：
  · 命中与「查无」（确定性空结果）都进缓存；
  · 查询失败（网络/权限/凭证错）**不缓存**——那不是事实，只是当下不可知，
    必须留给下一次重试的机会（fail-open 语义下区分 `("ok", None)` 与 `("error", None)`）；
  · 纯内存、无锁竞争热点（读写同一把轻量锁）；
  · 超过 max_entries 先淘汰过期项、再按插入序淘汰最旧的（OrderedDict）；
  · ttl<=0 或 IMT_CONTACTS_CACHE_TTL<=0 时整体禁用（每次都实查）。

安全：缓存键为手机号原文（内存中，不落盘）；本模块只存查询结果，绝不写任何业务表。
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable, Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def default_cache_ttl_seconds() -> float:
    """缓存 TTL（秒），IMT_CONTACTS_CACHE_TTL 可调；<=0 表示禁用。"""
    return _env_float("IMT_CONTACTS_CACHE_TTL", 600.0)


def default_cache_max_entries() -> int:
    """缓存条数上限；IMT_CONTACTS_CACHE_MAX_ENTRIES 可调（下限 1000）。"""
    return max(1000, _env_int("IMT_CONTACTS_CACHE_MAX_ENTRIES", 50000))


class TtlCache:
    """极简 TTL 缓存。值可为 None——区分「未查过」与「查过但结果为空」。

    ⚠️ 值为 None 与未命中同返回 None（get 无法区分二者）——调用方需要区分时
    请把结果包成元组存入（如 `(("ok", None),)` 形态），查无/失败语义在更上层表达。
    """

    def __init__(self, ttl: Optional[float] = None, max_entries: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = default_cache_ttl_seconds() if ttl is None else float(ttl)
        self._max_entries = default_cache_max_entries() if max_entries is None else max(1, int(max_entries))
        self._clock = clock
        self._data: "OrderedDict[Hashable, Any]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def ttl(self) -> float:
        return self._ttl

    def get(self, key: Hashable) -> Any:
        if self._ttl <= 0:
            return None
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if now >= expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: Hashable, value: Any) -> None:
        """写入并按需淘汰。值为 None 也会写入（表达「查过但为空」）。"""
        if self._ttl <= 0:
            return
        now = self._clock()
        with self._lock:
            self._data[key] = (now + self._ttl, value)
            if len(self._data) > self._max_entries:
                expired = [k for k, (exp, _v) in self._data.items() if exp <= now]
                for k in expired:
                    del self._data[k]
                while len(self._data) > self._max_entries:
                    self._data.popitem(last=False)  # FIFO：淘汰最旧插入的键

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
