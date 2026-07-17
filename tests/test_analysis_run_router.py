"""POST /analysis/run 三模式测试。"""

from __future__ import annotations

import importlib

import pytest
from httpx import AsyncClient

analysis_router = importlib.import_module("blue_print.analysis_router")


REQUEST_ITEM = {
    "type_id": "contract",
    "biz_id": "order-889",
    "field_values": {"amount": "1200000"},
}


@pytest.mark.anyio
async def test_analysis_run_sync_returns_batch_result(
    client: AsyncClient,
    monkeypatch,
):
    expected = {
        "total_items": 1,
        "items": [{
            "item_index": 0,
            "biz_id": "order-889",
            "type_id": "contract",
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
        }],
    }

    async def fake_run(items, *, on_rule_done=None):
        assert items == [REQUEST_ITEM]
        assert on_rule_done is None
        return expected

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    response = await client.post(
        "/analysis/run",
        json={"mode": "sync", "items": [REQUEST_ITEM]},
    )
    assert response.status_code == 200
    assert response.json()["data"] == expected


@pytest.mark.anyio
async def test_analysis_run_async_requires_callback(client: AsyncClient):
    response = await client.post(
        "/analysis/run",
        json={"mode": "async", "items": [REQUEST_ITEM]},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_analysis_run_async_returns_task_id(
    client: AsyncClient,
    monkeypatch,
):
    calls = []

    async def fake_background(task_id, items, callback_url):
        calls.append((task_id, items, callback_url))

    monkeypatch.setattr(
        analysis_router,
        "_run_analysis_task_background",
        fake_background,
    )
    monkeypatch.setattr(
        analysis_router,
        "_new_analysis_task_id",
        lambda: "task-fixed",
    )
    response = await client.post(
        "/analysis/run",
        json={
            "mode": "async",
            "callback_url": "http://callback.local/result",
            "items": [REQUEST_ITEM],
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"task_id": "task-fixed"}
    assert calls == [("task-fixed", [REQUEST_ITEM], "http://callback.local/result")]


@pytest.mark.anyio
async def test_analysis_run_stream_uses_task_event_envelope(
    client: AsyncClient,
    monkeypatch,
):
    async def fake_stream(task_id, items):
        yield analysis_router._sse_event(
            "analyzing",
            {"task_id": task_id, "status": "analyzing"},
        )
        yield analysis_router._sse_event(
            "task_done",
            {
                "task_id": task_id,
                "status": "complete",
                "event": "task_done",
                "data": {"total_items": 1, "items": []},
            },
        )

    monkeypatch.setattr(analysis_router, "_analysis_run_stream", fake_stream)
    monkeypatch.setattr(
        analysis_router,
        "_new_analysis_task_id",
        lambda: "task-stream",
    )
    response = await client.post(
        "/analysis/run",
        json={"mode": "stream", "items": [REQUEST_ITEM]},
    )
    assert response.status_code == 200
    assert "event: analyzing" in response.text
    assert "event: task_done" in response.text
    assert '"task_id": "task-stream"' in response.text


@pytest.mark.anyio
async def test_analysis_run_background_failure_pushes_task_failed(monkeypatch):
    calls = []

    async def fake_notify(
        callback_url,
        task_id,
        status,
        *,
        event=None,
        data=None,
        timeout=2.5,
    ):
        calls.append({"status": status, "event": event, "data": data})

    async def boom(items, *, on_rule_done=None):
        raise RuntimeError("规则加载失败")

    monkeypatch.setattr(
        analysis_router,
        "notify_analysis_task_callback",
        fake_notify,
    )
    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", boom)

    await analysis_router._run_analysis_task_background(
        "task-1",
        [REQUEST_ITEM],
        "http://callback.local/result",
    )

    assert [(call["status"], call["event"]) for call in calls] == [
        ("analyzing", None),
        ("analysis_failed", "task_failed"),
    ]
    assert calls[-1]["data"] == {"error": "RuntimeError: 规则加载失败"}
