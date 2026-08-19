"""Pipeline 闸门测试。

concurrency.global_pipeline 长期只在 /runtime/concurrency 里展示，没有任何地方
acquire——上传多少文件就并发跑多少。这组测试锁住：超限文件进入 queued、
拿到令牌后才开始、异常路径不泄漏令牌。
"""

from __future__ import annotations

import asyncio

import pytest

from service.pipeline_gate import pipeline_slot
from utils.concurrency import clear_limiters, get_limiter, runtime_snapshot


@pytest.fixture(autouse=True)
def reset_limiters():
    clear_limiters()
    yield
    clear_limiters()


class _MarkRecorder:
    """记录 queued 状态写入的桩。"""

    def __init__(self):
        self.marked = []

    async def __call__(self, file_id: str) -> None:
        self.marked.append(file_id)


@pytest.mark.asyncio
async def test_slot_available_does_not_mark_queued():
    """有空位时直接进入，不写 queued（避免状态闪烁）。"""
    recorder = _MarkRecorder()

    async with pipeline_slot("f1", limit=2, mark_queued=recorder):
        assert runtime_snapshot()["pools"]["global_pipeline"]["active"] == 1

    assert recorder.marked == []
    assert runtime_snapshot()["pools"]["global_pipeline"]["active"] == 0


@pytest.mark.asyncio
async def test_second_file_queues_when_limit_reached():
    """limit=1 时第二个文件先被标 queued，再等待令牌。"""
    recorder = _MarkRecorder()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        async with pipeline_slot("f1", limit=1, mark_queued=recorder):
            first_entered.set()
            await release_first.wait()

    async def second():
        async with pipeline_slot("f2", limit=1, mark_queued=recorder):
            second_entered.set()

    task1 = asyncio.create_task(first())
    await first_entered.wait()

    task2 = asyncio.create_task(second())
    await asyncio.sleep(0.05)

    assert recorder.marked == ["f2"], "第二个文件应被标记为排队中"
    assert not second_entered.is_set(), "令牌未释放前不应进入管线"

    release_first.set()
    await asyncio.wait_for(asyncio.gather(task1, task2), timeout=2)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_token_released_on_exception():
    """管线抛异常时令牌必须释放，否则后续文件永久卡住。"""
    recorder = _MarkRecorder()

    with pytest.raises(RuntimeError):
        async with pipeline_slot("f1", limit=1, mark_queued=recorder):
            raise RuntimeError("管线炸了")

    assert runtime_snapshot()["pools"]["global_pipeline"]["active"] == 0

    # 令牌确实回收了：下一个文件能立刻进入
    async with pipeline_slot("f2", limit=1, mark_queued=recorder):
        pass
    assert recorder.marked == []


@pytest.mark.asyncio
async def test_mark_queued_failure_does_not_block_pipeline():
    """标记 queued 失败（例如 DB 抖动）不能拖垮管线本身。"""

    async def broken(file_id: str) -> None:
        raise RuntimeError("DB 挂了")

    limiter = get_limiter("global_pipeline", 1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with limiter:
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()

    async def waiter():
        async with pipeline_slot("f2", limit=1, mark_queued=broken):
            return "进来了"

    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    release.set()
    result = await asyncio.wait_for(waiter_task, timeout=2)
    await task

    assert result == "进来了"


def test_queued_is_recovered_as_parsing_failed():
    """queued 属于「重启后不会自己跑起来」的状态，必须归为 parsing_failed。"""
    import inspect

    import service.init_service as init_service

    source = inspect.getsource(init_service)
    assert '"queued": "parsing_failed"' in source, (
        "崩溃恢复未覆盖 queued，排队中的文件会成为永久幽灵"
    )
