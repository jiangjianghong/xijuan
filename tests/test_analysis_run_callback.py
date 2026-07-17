"""独立逻辑分析任务回调测试。"""

from __future__ import annotations

import pytest

from utils import callback


def test_build_analysis_task_payload_shapes():
    assert callback.build_analysis_task_payload("task-1", "analyzing") == {
        "task_id": "task-1",
        "status": "analyzing",
    }
    assert callback.build_analysis_task_payload(
        "task-1",
        "analyzing",
        event="rule_done",
        data={"rule_id": "r1"},
    ) == {
        "task_id": "task-1",
        "status": "analyzing",
        "event": "rule_done",
        "data": {"rule_id": "r1"},
    }


@pytest.mark.anyio
async def test_notify_analysis_task_callback_posts_task_envelope(monkeypatch):
    calls = []

    async def fake_post(url, payload, *, timeout):
        calls.append((url, payload, timeout))

    monkeypatch.setattr(callback, "_post_callback_payload", fake_post)
    await callback.notify_analysis_task_callback(
        "http://callback.local/result",
        "task-1",
        "complete",
        event="task_done",
        data={"total_items": 1, "items": []},
    )
    assert calls[0][1]["event"] == "task_done"
    assert calls[0][1]["task_id"] == "task-1"


@pytest.mark.anyio
async def test_file_callback_payload_remains_compatible(monkeypatch):
    calls = []

    async def fake_post(url, payload, *, timeout):
        calls.append(payload)

    monkeypatch.setattr(callback, "_post_callback_payload", fake_post)
    await callback.notify_callback(
        "http://callback.local/file",
        "file-1",
        "analyzing",
        event="rule_done",
        data={"rule_id": "r1"},
    )
    assert calls == [{
        "file_id": "file-1",
        "status": "analyzing",
        "event": "rule_done",
        "data": {"rule_id": "r1"},
    }]


@pytest.mark.anyio
async def test_callback_network_error_is_swallowed(monkeypatch):
    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        callback.httpx,
        "AsyncClient",
        lambda timeout: FailingClient(),
    )
    await callback.notify_analysis_task_callback(
        "http://callback.local/result",
        "task-1",
        "complete",
        event="task_done",
        data={"total_items": 0, "items": []},
    )
