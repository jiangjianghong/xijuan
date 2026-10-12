"""MinerU 队列宽度的实际限流、令牌释放与运行台回归。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from service import parse_service, runtime_monitor_service
from service.pipeline_gate import pipeline_slot
from utils.concurrency import clear_limiters, limiter_context, runtime_snapshot
from utils.config import AppConfig


@pytest.fixture(autouse=True)
async def mineru_config(monkeypatch):
    clear_limiters()
    cfg = AppConfig(mineru={"queue_width": 2}, concurrency={"global_pipeline": 10})
    monkeypatch.setattr(parse_service, "get_config", lambda: cfg)
    monkeypatch.setattr(runtime_monitor_service, "get_config", lambda: cfg)
    monkeypatch.setattr(
        parse_service, "get_file_type_runtime_config",
        AsyncMock(return_value=SimpleNamespace(type_id="default", max_parse_pages=3)),
    )
    yield cfg
    clear_limiters()


def _session():
    return SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())


@pytest.mark.asyncio
async def test_parse_file_caps_mineru_concurrency(monkeypatch):
    """首次调用直接按配置建池；无需测试预先创建 limiter。"""
    peak = active = 0

    async def parse_pdf(**kwargs):
        nonlocal peak, active
        active += 1
        peak = max(peak, active)
        assert kwargs["max_parse_pages"] == 3
        await asyncio.sleep(0.01)
        active -= 1
        return {"md_content": "内容", "middle_json": "{}"}

    monkeypatch.setattr(parse_service, "parse_pdf", parse_pdf)

    async def run(index):
        async with pipeline_slot(f"id{index}", limit=10):
            return await parse_service.parse_file(f"f{index}.pdf", b"pdf", f"id{index}", _session())

    results = await asyncio.gather(*[
        run(i) for i in range(10)
    ])
    assert peak == 2
    assert results == [("内容", "{}")] * 10
    pool = runtime_snapshot()["pools"]["mineru_parse"]
    assert (pool["active"], pool["queued"], pool["completed"]) == (0, 0, 10)


@pytest.mark.asyncio
async def test_parse_slot_is_released_while_pipeline_continues(monkeypatch, mineru_config):
    """首文件仍占管线令牌时，后一个文件已能开始解析。"""
    mineru_config.mineru.queue_width = 1
    monkeypatch.setattr(
        parse_service, "parse_pdf", AsyncMock(return_value={"md_content": "", "middle_json": ""}),
    )
    downstream, release = asyncio.Event(), asyncio.Event()

    async def first():
        async with pipeline_slot("first", limit=10):
            await parse_service.parse_file("first.pdf", b"pdf", "first", _session())
            downstream.set()
            await release.wait()

    task = asyncio.create_task(first())
    try:
        await asyncio.wait_for(downstream.wait(), 1)
        async with pipeline_slot("second", limit=10):
            assert runtime_snapshot()["pools"]["global_pipeline"]["active"] == 2
            await asyncio.wait_for(parse_service.parse_file("second.pdf", b"pdf", "second", _session()), 1)
            assert not task.done()
        assert runtime_snapshot()["pools"]["mineru_parse"]["completed"] == 2
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_parse_failure_releases_token(monkeypatch, mineru_config):
    mineru_config.mineru.queue_width = 1
    monkeypatch.setattr(parse_service, "parse_pdf", AsyncMock(side_effect=RuntimeError("解析失败")))
    failed = AsyncMock()
    monkeypatch.setattr(parse_service, "mark_file_failed", failed)
    session = _session()
    with pytest.raises(RuntimeError, match="解析失败"):
        await parse_service.parse_file("f.pdf", b"pdf", "id", session)
    failed.assert_awaited_once_with(session, "id", "parsing_failed", ANY)
    assert runtime_snapshot()["pools"]["mineru_parse"]["active"] == 0
    monkeypatch.setattr(
        parse_service, "parse_pdf", AsyncMock(return_value={"md_content": "", "middle_json": ""}),
    )
    await asyncio.wait_for(parse_service.parse_file("f2.pdf", b"pdf", "id2", _session()), 1)


@pytest.mark.asyncio
async def test_snapshot_and_cancellation_track_real_parse_tasks(monkeypatch, mineru_config):
    mineru_config.mineru.queue_width = 1
    initial = runtime_monitor_service.build_snapshot()
    pool = next(p for p in initial["pools"] if p["id"] == "mineru_parse")
    assert (pool["limit"], pool["active"], pool["queued"]) == (1, 0, 0)
    assert pool["group"] == "管线调度"
    entered = asyncio.Event()

    async def parse_pdf(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(parse_service, "parse_pdf", parse_pdf)

    async def run(file_id, session):
        with limiter_context(file_name=f"{file_id}.pdf"):
            return await parse_service.parse_file(f"{file_id}.pdf", b"pdf", file_id, session)

    holder_session, waiter_session = _session(), _session()
    holder = asyncio.create_task(run("holder", holder_session))
    waiter = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        waiter = asyncio.create_task(run("waiter", waiter_session))
        await asyncio.sleep(0)
        snapshot = runtime_monitor_service.build_snapshot()
        pool = next(p for p in snapshot["pools"] if p["id"] == "mineru_parse")
        assert (pool["active"], pool["queued"], pool["status"]) == (1, 1, "saturated")
        assert pool["tasks"] == [{"file_name": "holder.pdf", "file_id": "holder", "stage": "parsing"}]
        assert any(e["type"] == "queued" and e["context"]["file_id"] == "waiter"
                   for e in snapshot["events"])
        waiter_session.rollback.assert_awaited_once()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert runtime_snapshot()["pools"]["mineru_parse"]["queued"] == 0
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        assert runtime_snapshot()["pools"]["mineru_parse"]["active"] == 0
    finally:
        for task in [holder, waiter]:
            if task is not None:
                task.cancel()
        await asyncio.gather(*[t for t in [holder, waiter] if t is not None], return_exceptions=True)
