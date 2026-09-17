"""POST /analysis/run 三模式测试。"""

from __future__ import annotations

import importlib

import pytest
from httpx import AsyncClient

analysis_router = importlib.import_module("blue_print.analysis_router")


@pytest.fixture(autouse=True)
def task_storage(analysis_task_db):
    """路由测试跨会话读写真实任务表。"""


REQUEST_ITEM = {
    "type_id": "contract",
    "biz_id": "order-889",
    "field_values": {"amount": "1200000"},
}

# 请求体经 model_dump() 后必然补齐 rule_ids / file_id / params 默认值，服务层收到的是这一份
DUMPED_ITEM = {**REQUEST_ITEM, "rule_ids": None, "file_id": None, "params": {}}


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
            "unknown_rule_ids": [],
            "error": None,
        }],
    }

    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        assert items == [DUMPED_ITEM]
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
async def test_analysis_run_passes_rule_ids_to_service(
    client: AsyncClient,
    monkeypatch,
):
    """点名的 rule_ids 必须原样透传到服务层。"""
    captured = {}

    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        captured["items"] = items
        return {"total_items": 1, "items": []}

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    response = await client.post(
        "/analysis/run",
        json={
            "mode": "sync",
            "items": [{**REQUEST_ITEM, "rule_ids": ["amount_check"]}],
        },
    )
    assert response.status_code == 200
    assert captured["items"][0]["rule_ids"] == ["amount_check"]


@pytest.mark.anyio
async def test_analysis_run_sync_exposes_unknown_rule_ids(
    client: AsyncClient,
    monkeypatch,
):
    """坏 rule_id 不报错，经响应体 unknown_rule_ids 回传。"""
    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        return {
            "total_items": 1,
            "items": [{
                "item_index": 0,
                "biz_id": "order-889",
                "type_id": "contract",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
                "unknown_rule_ids": ["ghost"],
            }],
        }

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    response = await client.post(
        "/analysis/run",
        json={"mode": "sync", "items": [{**REQUEST_ITEM, "rule_ids": ["ghost"]}]},
    )
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["unknown_rule_ids"] == ["ghost"]


@pytest.mark.anyio
@pytest.mark.parametrize("callback_mode", ["full", "simple"])
async def test_analysis_run_async_without_callback_executes(
    client: AsyncClient, monkeypatch, callback_mode,
):
    calls = []

    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        assert on_rule_done is None
        calls.append(items)
        return {"total_items": 1, "items": []}

    async def unexpected_callback(*args, **kwargs):
        pytest.fail("未提供回调地址时不应调用回调通知")

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    monkeypatch.setattr(analysis_router, "notify_analysis_task_callback", unexpected_callback)
    response = await client.post(
        "/analysis/run",
        json={"mode": "async", "callback_mode": callback_mode, "items": [REQUEST_ITEM]},
    )
    assert response.status_code == 200
    assert response.json()["data"]["task_id"]
    assert calls == [[DUMPED_ITEM]]
    task = await client.get(f"/analysis/tasks/{response.json()['data']['task_id']}")
    assert task.status_code == 200
    assert task.json()["data"]["status"] == "complete"
    assert task.json()["data"]["result"] == {"total_items": 1, "items": []}


@pytest.mark.anyio
async def test_analysis_run_async_returns_task_id(
    client: AsyncClient,
    monkeypatch,
):
    calls = []

    async def fake_background(
        task_id, items, callback_url, source="values", persist=False,
        callback_mode="full",
    ):
        from service.analysis_task_store import get_task
        assert (await get_task(task_id))["status"] == "queued"
        calls.append((task_id, items, callback_url, callback_mode))

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
    assert calls == [("task-fixed", [DUMPED_ITEM], "http://callback.local/result", "full")]

    monkeypatch.setattr(analysis_router, "_new_analysis_task_id", lambda: "task-simple")
    response = await client.post(
        "/analysis/run",
        json={
            "mode": "async",
            "callback_url": "http://callback.local/result",
            "callback_mode": "simple",
            "items": [REQUEST_ITEM],
        },
    )
    assert response.status_code == 200
    assert calls[-1][3] == "simple"


@pytest.mark.anyio
async def test_analysis_run_stream_uses_task_event_envelope(
    client: AsyncClient,
    monkeypatch,
):
    async def fake_stream(task_id, items, source="values", persist=False):
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
    from service.analysis_task_store import create_task, get_task
    await create_task("task-1")
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

    async def boom(items, *, on_rule_done=None, source="values", persist=False):
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
    assert (await get_task("task-1"))["status"] == "analysis_failed"
    assert (await get_task("task-1"))["error"] == "RuntimeError: 规则加载失败"


@pytest.mark.anyio
async def test_analysis_run_background_simple_skips_rule_done(monkeypatch):
    from service.analysis_task_store import create_task
    await create_task("task-1")
    calls = []
    on_rule_done_seen = []

    async def fake_notify(
        callback_url,
        task_id,
        status,
        *,
        event=None,
        data=None,
        timeout=2.5,
    ):
        calls.append({"status": status, "event": event})

    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        on_rule_done_seen.append(on_rule_done)
        return {"total_items": 1, "items": []}

    monkeypatch.setattr(analysis_router, "notify_analysis_task_callback", fake_notify)
    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)

    await analysis_router._run_analysis_task_background(
        "task-1",
        [REQUEST_ITEM],
        "http://callback.local/result",
        callback_mode="simple",
    )

    assert on_rule_done_seen == [None]
    assert [(call["status"], call["event"]) for call in calls] == [
        ("analyzing", None),
        ("complete", "task_done"),
    ]


# ── source=file 模式 ────────────────────────────────────────


FILE_ITEM = {"biz_id": "order-889", "file_id": "f" * 32}


@pytest.mark.anyio
async def test_query_missing_task_returns_404(client):
    response = await client.get("/analysis/tasks/missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "独立分析任务不存在"


@pytest.mark.anyio
async def test_task_query_preserves_nested_results(client, monkeypatch):
    expected = {"total_items": 1, "items": [{
        "item_index": 0, "biz_id": "业务编号", "type_id": "contract",
        "total": 1, "succeeded": 0, "failed": 1,
        "unknown_rule_ids": ["unknown"], "error": "部分规则输入缺失",
        "results": [{
            "rule_id": "amount", "rule_name": "金额", "rule_type": "judge",
            "result": "", "reason": "缺少输入", "input_values": {"amount": "100"},
            "source_refs": {"amount": [{"page_num": "3", "text": "原始金额"}]},
            "success": False, "index": 1, "total": 1,
        }],
    }]}

    async def fake_run(*args, **kwargs):
        return expected

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    response = await client.post("/analysis/run", json={"mode": "async", "items": [REQUEST_ITEM]})
    task = (await client.get(f"/analysis/tasks/{response.json()['data']['task_id']}")).json()["data"]
    assert task["status"] == "complete"
    assert task["error"] is None
    assert task["result"] == expected


@pytest.mark.anyio
async def test_async_failure_without_callback_is_queryable(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("规则加载失败")

    async def unexpected_callback(*args, **kwargs):
        pytest.fail("未提供回调地址时不应发送失败通知")

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", boom)
    monkeypatch.setattr(analysis_router, "notify_analysis_task_callback", unexpected_callback)
    response = await client.post("/analysis/run", json={"mode": "async", "items": [REQUEST_ITEM]})
    task = (await client.get(f"/analysis/tasks/{response.json()['data']['task_id']}")).json()["data"]
    assert task["status"] == "analysis_failed"
    assert task["result"] is None
    assert task["error"] == "RuntimeError: 规则加载失败"


@pytest.mark.anyio
async def test_async_file_task_states_and_callback_result_agree(client, monkeypatch):
    """完成回调发出之前结果已可查；文件来源与 persist 仍原样传递。"""
    from service.analysis_task_store import get_task
    events = []
    expected = {"total_items": 1, "items": []}
    monkeypatch.setattr(analysis_router, "_new_analysis_task_id", lambda: "task-file")

    async def fake_run(items, *, on_rule_done, source, persist):
        assert source == "file"
        assert persist is True
        assert items[0]["file_id"] == FILE_ITEM["file_id"]
        running = (await client.get("/analysis/tasks/task-file")).json()["data"]
        assert running["status"] == "analyzing"
        assert running["result"] is None
        await on_rule_done({"rule_id": "rule-1"})
        return expected

    async def notify(url, task_id, status, *, event=None, data=None):
        assert url == "http://callback.local/result"
        assert (await get_task(task_id))["status"] == status
        if event == "task_done":
            assert (await get_task(task_id))["result"] == data == expected
        events.append(event)

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    monkeypatch.setattr(analysis_router, "notify_analysis_task_callback", notify)
    response = await client.post("/analysis/run", json={
        "mode": "async", "source": "file", "persist": True,
        "items": [FILE_ITEM], "callback_url": "http://callback.local/result",
    })
    assert response.status_code == 200
    assert events == [None, "rule_done", "task_done"]


@pytest.mark.anyio
async def test_queued_task_is_queryable_before_background_starts(client, monkeypatch):
    async def delayed_background(*args, **kwargs):
        pass

    monkeypatch.setattr(analysis_router, "_run_analysis_task_background", delayed_background)
    response = await client.post("/analysis/run", json={"mode": "async", "items": [REQUEST_ITEM]})
    task = (await client.get(f"/analysis/tasks/{response.json()['data']['task_id']}")).json()["data"]
    assert task["status"] == "queued"
    assert task["result"] is None
    assert task["error"] is None


@pytest.mark.anyio
async def test_analysis_run_passes_source_and_persist(
    client: AsyncClient,
    monkeypatch,
):
    captured = {}

    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        captured["source"] = source
        captured["persist"] = persist
        captured["items"] = items
        return {"total_items": 1, "items": []}

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    response = await client.post(
        "/analysis/run",
        json={"mode": "sync", "source": "file", "persist": True,
              "items": [FILE_ITEM]},
    )
    assert response.status_code == 200
    assert captured["source"] == "file"
    assert captured["persist"] is True
    assert captured["items"][0]["file_id"] == "f" * 32


@pytest.mark.anyio
async def test_analysis_run_rejects_persist_without_file_source(client: AsyncClient):
    response = await client.post(
        "/analysis/run",
        json={"mode": "sync", "persist": True, "items": [REQUEST_ITEM]},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_analysis_run_file_source_exposes_item_error(
    client: AsyncClient,
    monkeypatch,
):
    async def fake_run(items, *, on_rule_done=None, source="values", persist=False):
        return {
            "total_items": 1,
            "items": [{
                "item_index": 0, "biz_id": "order-889", "type_id": "contract",
                "total": 0, "succeeded": 0, "failed": 0, "results": [],
                "unknown_rule_ids": [], "error": "文件不存在: ffff",
            }],
        }

    monkeypatch.setattr(analysis_router, "_run_analysis_with_session", fake_run)
    response = await client.post(
        "/analysis/run",
        json={"mode": "sync", "source": "file", "items": [FILE_ITEM]},
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["error"] == "文件不存在: ffff"
