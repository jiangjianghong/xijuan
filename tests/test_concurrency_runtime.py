from __future__ import annotations

import asyncio

import pytest

from utils.concurrency import (
    clear_limiters,
    get_limiter,
    register_task_limiter,
    replace_limiters,
    runtime_snapshot,
    unregister_task_limiter,
)


@pytest.fixture(autouse=True)
def reset_limiters():
    clear_limiters()
    yield
    clear_limiters()


@pytest.mark.asyncio
async def test_global_limiter_tracks_active_and_completed():
    limiter = get_limiter("global_llm", 2)

    assert get_limiter("global_llm", 2) is limiter
    assert runtime_snapshot()["pools"]["global_llm"]["active"] == 0

    async with limiter:
        snapshot = runtime_snapshot()["pools"]["global_llm"]
        assert snapshot["active"] == 1
        assert snapshot["queued"] == 0

    snapshot = runtime_snapshot()["pools"]["global_llm"]
    assert snapshot["active"] == 0
    assert snapshot["completed"] == 1


@pytest.mark.asyncio
async def test_limiter_reports_waiting_acquire_and_wait_p95():
    limiter = get_limiter("global_llm", 1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with limiter:
            entered.set()
            await release.wait()

    async def waiter():
        async with limiter:
            return True

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    assert runtime_snapshot()["pools"]["global_llm"]["queued"] == 1
    release.set()
    assert await waiter_task
    await holder_task

    snapshot = runtime_snapshot()["pools"]["global_llm"]
    assert snapshot["queued"] == 0
    assert snapshot["completed"] == 2
    assert snapshot["wait_p95_ms"] >= 0


@pytest.mark.asyncio
async def test_replace_limiters_preserves_existing_holder():
    limiter = get_limiter("global_llm", 2)
    await limiter.acquire()

    replace_limiters({"global_llm": 4})

    assert get_limiter("global_llm", 4) is limiter
    assert runtime_snapshot()["pools"]["global_llm"]["active"] == 1
    assert limiter.limit == 4
    limiter.release()
    assert runtime_snapshot()["pools"]["global_llm"]["active"] == 0


@pytest.mark.asyncio
async def test_task_pool_reports_instances_without_fake_global_capacity():
    first = register_task_limiter("task_table_validation", "file-a", 4)
    second = register_task_limiter("task_table_validation", "file-b", 4)

    await first.acquire()
    await second.acquire()

    pool = runtime_snapshot()["task_pools"]["task_table_validation"]
    assert pool["per_instance_limit"] == 4
    assert pool["busiest_active"] == 1
    assert pool["aggregate_active"] == 2
    assert pool["instance_count"] == 2

    first.release()
    second.release()
    unregister_task_limiter("task_table_validation", "file-a")
    unregister_task_limiter("task_table_validation", "file-b")
    assert "task_table_validation" not in runtime_snapshot()["task_pools"]
