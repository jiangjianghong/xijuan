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
        "task_file_analysis",
        "independent_analysis",
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

    independent = next(
        pool for pool in data["pools"] if pool["id"] == "independent_analysis"
    )
    assert independent["scope"] == "global"
    assert independent["group"] == "独立接口"
    assert independent["constraints"] == ["global_analysis"]

    file_analysis = next(
        pool for pool in data["pools"] if pool["id"] == "task_file_analysis"
    )
    assert file_analysis["scope"] == "task"
    assert file_analysis["group"] == "文件内任务"
    assert file_analysis["constraints"] == ["global_analysis"]
    assert all(pool["id"] != "task_analysis" for pool in data["pools"])

    # global_pipeline 已由 pipeline_gate 真实接入，不再是恒 offline 的占位记录
    pipeline = next(pool for pool in data["pools"] if pool["id"] == "global_pipeline")
    assert pipeline["connected"] is True
    assert pipeline["status"] != "offline"
    assert pipeline["limit"] >= 1


@pytest.mark.asyncio
async def test_runtime_concurrency_snapshot_includes_runtime_events(client):
    response = await client.get("/runtime/concurrency")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["events"], list)
    assert "active" in data["summary"]
    assert "wait_p95_ms" in data["summary"]


@pytest.mark.asyncio
async def test_runtime_concurrency_snapshot_includes_history_window(client):
    response = await client.get("/runtime/concurrency")
    assert response.status_code == 200
    history = response.json()["data"]["history"]
    assert history["window"] == "60s"
    assert history["bucket_seconds"] == 1
    assert history["retention_seconds"] == 1800
    assert history["windows"] == ["60s", "5m", "30m"]
    # 定长 60 桶：响应体积与窗口长度无关，前端图表点数恒定
    assert len(history["points"]) == 60


@pytest.mark.asyncio
async def test_runtime_concurrency_history_window_param(client):
    response = await client.get("/runtime/concurrency", params={"window": "30m"})
    assert response.status_code == 200
    history = response.json()["data"]["history"]
    assert history["window"] == "30m"
    assert history["window_seconds"] == 1800
    assert history["bucket_seconds"] == 30
    assert len(history["points"]) == 60


@pytest.mark.asyncio
async def test_runtime_concurrency_history_bad_window_falls_back(client):
    response = await client.get("/runtime/concurrency", params={"window": "nonsense"})
    assert response.status_code == 200
    assert response.json()["data"]["history"]["window"] == "60s"
