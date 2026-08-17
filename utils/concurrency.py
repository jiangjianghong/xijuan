"""进程内并发限制器注册表与运行时观测。"""

from __future__ import annotations

import asyncio
import contextvars
import math
import time
from collections import deque
from collections.abc import Mapping
from typing import Any


_limiters: dict[tuple[int, str], "ObservableLimiter"] = {}


def _percentile(samples: deque[float], quantile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return round(ordered[index] * 1000, 2)


class LimiterLease:
    """带业务上下文的 limiter 异步上下文管理器。"""

    def __init__(self, limiter: "ObservableLimiter", metadata: dict[str, Any] | None):
        self._limiter = limiter
        self._metadata = metadata
        self._token: int | None = None

    async def __aenter__(self) -> "ObservableLimiter":
        self._token = await self._limiter.acquire(self._metadata)
        return self._limiter

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._limiter.release(self._token)


class ObservableLimiter:
    """不依赖 asyncio.Semaphore 私有字段的可观测并发限制器。"""

    def __init__(self, name: str, limit: int):
        if limit < 1:
            raise ValueError(f"并发上限必须大于 0: {name}={limit}")
        self.name = name
        self._limit = limit
        self._condition = asyncio.Condition()
        self._active = 0
        self._queued = 0
        self._completed = 0
        self._wait_samples: deque[float] = deque(maxlen=256)
        self._holders: dict[int, dict[str, Any]] = {}
        self._next_token = 0
        self._token_stack: contextvars.ContextVar[tuple[int, ...]] = contextvars.ContextVar(
            f"limiter_tokens_{id(self)}", default=()
        )

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def _value(self) -> int:
        """兼容旧测试/调用方的 semaphore 可用槽位属性。"""
        return max(0, self._limit - self._active)

    def set_limit(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"并发上限必须大于 0: {self.name}={limit}")
        self._limit = limit
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._wake_waiters())

    async def _wake_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def acquire(self, metadata: dict[str, Any] | None = None) -> int:
        started = time.monotonic()
        async with self._condition:
            self._queued += 1
            try:
                while self._active >= self._limit:
                    await self._condition.wait()
            except BaseException:
                self._queued -= 1
                raise

            self._queued -= 1
            self._active += 1
            self._next_token += 1
            token = self._next_token
            self._holders[token] = dict(metadata or {})
            self._wait_samples.append(time.monotonic() - started)
            stack = self._token_stack.get()
            self._token_stack.set(stack + (token,))
            return token

    def release(self, token: int | None = None) -> None:
        if token is None:
            stack = self._token_stack.get()
            if not stack:
                raise RuntimeError(f"limiter release without acquire: {self.name}")
            token = stack[-1]
            self._token_stack.set(stack[:-1])

        if token not in self._holders:
            raise RuntimeError(f"limiter release without acquire: {self.name}")
        self._holders.pop(token, None)
        self._active -= 1
        self._completed += 1
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._wake_waiters())

    def context(self, metadata: dict[str, Any] | None = None) -> LimiterLease:
        return LimiterLease(self, metadata)

    async def __aenter__(self) -> "ObservableLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()

    def snapshot(self) -> dict[str, Any]:
        return {
            "limit": self._limit,
            "active": self._active,
            "queued": self._queued,
            "completed": self._completed,
            "wait_p95_ms": _percentile(self._wait_samples, 0.95),
            "holders": [dict(value) for value in self._holders.values()],
        }


def _key(name: str) -> tuple[int, str]:
    loop = asyncio.get_running_loop()
    return id(loop), name


def get_limiter(name: str, limit: int) -> ObservableLimiter:
    """获取当前事件循环内指定名称的稳定 limiter。"""
    if limit < 1:
        raise ValueError(f"并发上限必须大于 0: {name}={limit}")
    key = _key(name)
    limiter = _limiters.get(key)
    if limiter is None:
        limiter = ObservableLimiter(name, limit)
        _limiters[key] = limiter
    return limiter


def runtime_snapshot() -> dict[str, dict[str, Any]]:
    """返回当前事件循环中的全局 limiter 快照。"""
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        return {"pools": {}}
    return {
        "pools": {
            name: limiter.snapshot()
            for (registered_loop_id, name), limiter in _limiters.items()
            if registered_loop_id == loop_id
        }
    }


def replace_limiters(limits: Mapping[str, int]) -> None:
    """更新 limiter 容量，保留已有对象以保证运行中 acquire/release 配对。"""
    for name, limit in limits.items():
        if limit < 1:
            raise ValueError(f"并发上限必须大于 0: {name}={limit}")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop_id = id(loop)
    for name, limit in limits.items():
        limiter = _limiters.get((loop_id, name))
        if limiter is None:
            _limiters[(loop_id, name)] = ObservableLimiter(name, limit)
        else:
            limiter.set_limit(limit)


def clear_limiters() -> None:
    """清空当前事件循环 limiter，供测试和生命周期重建使用。"""
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        _limiters.clear()
        return
    for key in [key for key in _limiters if key[0] == loop_id]:
        _limiters.pop(key, None)
