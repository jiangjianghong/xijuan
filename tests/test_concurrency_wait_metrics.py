"""等待指标分层：gate_wait（本闸）与 total_wait（端到端）的口径回归。

背景：单文件限流（task_table_validation 等）不在运行台展示后，第一道闸吸收的
排队在界面上完全不可见——只有各全局池的 total_wait_p95_ms 能把它捞回来。
本文件锁定这条链路，防止哪天有人把 work_item() 挪位置或删掉而无人察觉。
"""

from __future__ import annotations

import asyncio

import pytest

from utils.concurrency import (
    clear_limiters,
    get_limiter,
    limiter_context,
    runtime_snapshot,
    work_item,
)

WORK_S = 0.05


@pytest.fixture(autouse=True)
def reset_limiters():
    clear_limiters()
    yield
    clear_limiters()


async def _drain(gate1, gate2, count: int) -> None:
    """count 个工作项过两道串联闸，模拟 task 池 → global 池。"""

    async def worker(index: int) -> None:
        context = {"stage": "tableing", "index": index}
        with work_item():
            async with gate1.context(context):
                async with gate2.context(context):
                    await asyncio.sleep(WORK_S)

    await asyncio.gather(*(asyncio.create_task(worker(i)) for i in range(count)))


@pytest.mark.asyncio
async def test_downstream_gate_wait_is_flattened_but_total_wait_is_not():
    """第一道闸吃掉背压后，第二道闸的 gate_wait 接近 0，total_wait 仍完整。

    这正是运行台上「表名校验 p95 = 34s，但单张表实际等了 18 分钟」的成因。
    """
    upstream = get_limiter("fake_task_pool", 1)
    downstream = get_limiter("fake_global_pool", 1)

    await _drain(upstream, downstream, count=4)

    pools = runtime_snapshot()["pools"]
    down = pools["fake_global_pool"]
    up = pools["fake_task_pool"]

    # 上游把并发压成 1，下游永远有空位，故本闸几乎不排队
    assert down["gate_wait_p95_ms"] < WORK_S * 1000
    # 但端到端等待要体现出前面几个工作项的排队
    assert down["total_wait_p95_ms"] > WORK_S * 1000 * 2
    # 第一道闸是链首，两个口径应当一致（允许调度抖动）
    assert up["total_wait_p95_ms"] >= up["gate_wait_p95_ms"]


@pytest.mark.asyncio
async def test_total_wait_falls_back_to_gate_wait_without_work_item():
    """没有 work_item() 标记起点时，两个口径必须相等而不是 0。"""
    limiter = get_limiter("fake_solo", 1)

    async def worker() -> None:
        async with limiter.context({"stage": "x"}):
            await asyncio.sleep(WORK_S)

    await asyncio.gather(*(asyncio.create_task(worker()) for _ in range(3)))

    snapshot = runtime_snapshot()["pools"]["fake_solo"]
    assert snapshot["gate_wait_p95_ms"] > 0
    assert snapshot["total_wait_p95_ms"] == snapshot["gate_wait_p95_ms"]


@pytest.mark.asyncio
async def test_ambient_context_reaches_holders_across_created_tasks():
    """limiter_context 绑定一次，子任务的 holders 自动带上 file_name。

    pipeline_slot 靠这条性质覆盖 tableing/extracting/analyzing 三个阶段，
    无需逐层给 parse_tables 之类加 file_name 参数。
    """
    limiter = get_limiter("fake_pool", 2)
    started = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        async with limiter.context({"stage": "tableing", "index": 7}):
            started.set()
            await release.wait()

    with limiter_context(file_id="abc123", file_name="季度报告.pdf"):
        task = asyncio.create_task(worker())
        await started.wait()
        holders = runtime_snapshot()["pools"]["fake_pool"]["holders"]

    assert holders == [
        {
            "file_id": "abc123",
            "file_name": "季度报告.pdf",
            "stage": "tableing",
            "index": 7,
        }
    ]
    release.set()
    await task


@pytest.mark.asyncio
async def test_explicit_context_wins_over_ambient():
    """显式 context 覆盖 ambient，避免上游绑定污染下游更精确的标注。"""
    limiter = get_limiter("fake_pool", 1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        async with limiter.context({"file_id": "explicit", "stage": "extracting"}):
            started.set()
            await release.wait()

    with limiter_context(file_id="ambient", file_name="a.pdf"):
        task = asyncio.create_task(worker())
        await started.wait()
        holder = runtime_snapshot()["pools"]["fake_pool"]["holders"][0]

    assert holder["file_id"] == "explicit"
    assert holder["file_name"] == "a.pdf"
    release.set()
    await task


@pytest.mark.asyncio
async def test_ambient_context_does_not_leak_after_exit():
    """退出 limiter_context 后 ambient 必须还原，否则会污染同进程后续任务。"""
    limiter = get_limiter("fake_pool", 1)

    with limiter_context(file_name="inside.pdf"):
        pass

    started = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        async with limiter.context({"stage": "analyzing"}):
            started.set()
            await release.wait()

    task = asyncio.create_task(worker())
    await started.wait()
    holder = runtime_snapshot()["pools"]["fake_pool"]["holders"][0]
    assert "file_name" not in holder
    release.set()
    await task
