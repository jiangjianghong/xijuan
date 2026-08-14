"""进程内并发限制器注册表。

每个事件循环拥有自己的 semaphore 实例，避免测试或生命周期重建时复用
绑定到旧事件循环的 asyncio 原语。生产环境通常只有一个事件循环，因此
同一名称仍然是整个进程共享的限制。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping


_limiters: dict[tuple[int, str], asyncio.Semaphore] = {}


def _key(name: str) -> tuple[int, str]:
    loop = asyncio.get_running_loop()
    return id(loop), name


def get_limiter(name: str, limit: int) -> asyncio.Semaphore:
    """获取当前事件循环内指定名称的 limiter。"""
    if limit < 1:
        raise ValueError(f"并发上限必须大于 0: {name}={limit}")
    key = _key(name)
    limiter = _limiters.get(key)
    if limiter is None:
        limiter = asyncio.Semaphore(limit)
        _limiters[key] = limiter
    return limiter


def replace_limiters(limits: Mapping[str, int]) -> None:
    """原子替换当前事件循环中传入名称的 limiter。"""
    for name, limit in limits.items():
        if limit < 1:
            raise ValueError(f"并发上限必须大于 0: {name}={limit}")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        for key in [key for key in _limiters if key[1] in limits]:
            _limiters.pop(key, None)
        return
    replacement: dict[tuple[int, str], asyncio.Semaphore] = {
        (id(loop), name): asyncio.Semaphore(limit)
        for name, limit in limits.items()
    }
    for key in [key for key in _limiters if key[0] == id(loop) and key[1] in limits]:
        _limiters.pop(key, None)
    _limiters.update(replacement)


def clear_limiters() -> None:
    """清空当前事件循环 limiter，供测试和生命周期重建使用。"""
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        _limiters.clear()
        return
    for key in [key for key in _limiters if key[0] == loop_id]:
        _limiters.pop(key, None)
