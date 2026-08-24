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
        "independent_analysis",
        "global_pipeline",
    }

    global_llm = next(pool for pool in data["pools"] if pool["id"] == "global_llm")
    assert global_llm["scope"] == "global"
    assert global_llm["active"] == 0
    assert global_llm["limit"] >= 1

    independent = next(
        pool for pool in data["pools"] if pool["id"] == "independent_analysis"
    )
    assert independent["scope"] == "global"
    assert independent["group"] == "独立接口"
    assert independent["constraints"] == ["global_analysis"]
    assert all(pool["id"] != "task_analysis" for pool in data["pools"])

    # global_pipeline 已由 pipeline_gate 真实接入，不再是恒 offline 的占位记录
    pipeline = next(pool for pool in data["pools"] if pool["id"] == "global_pipeline")
    assert pipeline["connected"] is True
    assert pipeline["status"] != "offline"
    assert pipeline["limit"] >= 1


@pytest.mark.asyncio
async def test_runtime_snapshot_hides_task_pools(client):
    """单文件池仍在 utils/concurrency.py 真实限流，但不出现在运行台快照里。

    它们的每实例上限与对应全局池同量级时会常年显示饱和，与全局池的真实
    水位相反；被吸收的排队改由各全局池的 total_wait_p95_ms 暴露。
    """
    data = (await client.get("/runtime/concurrency")).json()["data"]

    hidden = {"task_table_validation", "task_extraction", "task_file_analysis"}
    assert {pool["id"] for pool in data["pools"]}.isdisjoint(hidden)
    # 顶层不再有 task 作用域的记录，前端因此不需要 isTask 分支
    assert {pool["scope"] for pool in data["pools"]} == {"global"}
    # 事件流也要滤掉，否则会冒出没有对应卡片的 pool_id
    assert all(event.get("pool_id") not in hidden for event in data["events"])
    # 约束路径不应再指向被隐藏的池
    for pool in data["pools"]:
        assert hidden.isdisjoint(pool.get("constraints", []))


@pytest.mark.asyncio
async def test_runtime_snapshot_splits_gate_and_total_wait(client):
    """两个等待指标口径不同，必须都在且恒存在。"""
    data = (await client.get("/runtime/concurrency")).json()["data"]

    for pool in data["pools"]:
        assert "gate_wait_p95_ms" in pool, pool["id"]
        assert "total_wait_p95_ms" in pool, pool["id"]
        # 端到端含上游排队，不可能小于本闸等待
        assert pool["total_wait_p95_ms"] >= pool["gate_wait_p95_ms"], pool["id"]
    assert "wait_p95_ms" not in data["pools"][0]


@pytest.mark.asyncio
async def test_runtime_concurrency_snapshot_includes_runtime_events(client):
    response = await client.get("/runtime/concurrency")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["events"], list)
    assert "active" in data["summary"]
    assert "total_wait_p95_ms" in data["summary"]
    # 摘要取端到端口径：本闸等待会被上游闸削平，不能代表最坏等待
    assert "wait_p95_ms" not in data["summary"]


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
