from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_runtime_concurrency_snapshot_shape(client):
    response = await client.get("/runtime/concurrency")
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["scope"] == "single-process"
    assert {pool["id"] for pool in data["pools"]} == {
        "global_llm",
        "global_embedding",
        "global_vl",
        "global_table_validation",
        "global_extraction",
        "global_analysis",
        "task_table_validation",
        "task_extraction",
        "task_analysis",
        "global_pipeline",
    }

    global_llm = next(pool for pool in data["pools"] if pool["id"] == "global_llm")
    assert global_llm["scope"] == "global"
    assert global_llm["active"] == 0
    assert global_llm["limit"] >= 1

    task_extraction = next(
        pool for pool in data["pools"] if pool["id"] == "task_extraction"
    )
    assert task_extraction["scope"] == "task"
    assert task_extraction["instance_count"] == 0
    assert task_extraction["busiest_active"] == 0

    pipeline = next(pool for pool in data["pools"] if pool["id"] == "global_pipeline")
    assert pipeline["status"] == "offline"
    assert pipeline["connected"] is False


@pytest.mark.asyncio
async def test_runtime_concurrency_snapshot_includes_runtime_events(client):
    response = await client.get("/runtime/concurrency")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["events"], list)
    assert "active" in data["summary"]
    assert "wait_p95_ms" in data["summary"]
