"""并发历史采样与降采样。不依赖后台循环，直接驱动 service 层纯函数。"""

from __future__ import annotations

import asyncio

import pytest

from service import runtime_monitor_service as svc


# fixture 必须是 async 的：历史按 event loop 隔离，同步 fixture 里没有 running loop，
# 会清到 loop_id=0 的桶而测试写的是真实 loop 的桶，导致用例互相串味。
@pytest.fixture(autouse=True)
async def _clean_history():
    svc.clear_history()
    yield
    svc.clear_history()


def _fake_snapshot(active: int, capacity: int, llm_active: int, llm_limit: int) -> dict:
    return {
        "summary": {"active": active, "capacity": capacity},
        "pools": [
            {"id": "global_llm", "scope": "global", "active": llm_active, "limit": llm_limit},
            {"id": "task_extraction", "scope": "task", "busiest_active": 2, "per_instance_limit": 4},
        ],
    }


@pytest.mark.asyncio
async def test_record_sample_converts_snapshot_to_percentages():
    point = svc.record_sample(_fake_snapshot(active=5, capacity=20, llm_active=4, llm_limit=16))
    assert point["overall"] == 25
    assert point["pools"]["global_llm"] == 25
    # task 池按「最忙实例 / 每实例上限」算利用率
    assert point["pools"]["task_extraction"] == 50


@pytest.mark.asyncio
async def test_history_payload_always_returns_fixed_bucket_count():
    payload = svc.history_payload("60s")
    assert payload["window"] == "60s"
    assert payload["bucket_seconds"] == 1
    assert len(payload["points"]) == svc.HISTORY_POINTS
    # 无采样时全为 None，前端据此左侧留白
    assert all(point is None for point in payload["points"])


@pytest.mark.asyncio
async def test_new_samples_land_on_the_right_edge():
    svc.record_sample(_fake_snapshot(active=2, capacity=20, llm_active=0, llm_limit=16))
    payload = svc.history_payload("60s")
    assert payload["points"][-1]["overall"] == 10
    assert payload["points"][-2] is None


@pytest.mark.asyncio
async def test_long_window_downsamples_by_peak():
    # 30m 窗口 = 1800 点 / 60 桶 = 每桶 30 点，桶内取峰值而非均值，避免抹平尖峰
    for index in range(30):
        active = 20 if index == 7 else 0
        svc.record_sample(_fake_snapshot(active=active, capacity=20, llm_active=0, llm_limit=16))
    payload = svc.history_payload("30m")
    assert payload["bucket_seconds"] == 30
    assert len(payload["points"]) == svc.HISTORY_POINTS
    assert payload["points"][-1]["overall"] == 100
    assert payload["points"][-2] is None


@pytest.mark.asyncio
async def test_unknown_window_falls_back_to_default():
    payload = svc.history_payload("nonsense")
    assert payload["window"] == svc.DEFAULT_WINDOW
    assert payload["window_seconds"] == svc.WINDOWS[svc.DEFAULT_WINDOW]


@pytest.mark.asyncio
async def test_history_is_capped_at_retention_size():
    for _ in range(svc.HISTORY_MAXLEN + 50):
        svc.record_sample(_fake_snapshot(active=1, capacity=20, llm_active=0, llm_limit=16))
    assert len(svc.history_points()) == svc.HISTORY_MAXLEN


@pytest.mark.asyncio
async def test_sampler_loop_propagates_cancellation():
    """lifespan 关闭时靠 cancel + await 收尾，吞掉 CancelledError 会让进程退出时挂住。"""
    task = asyncio.create_task(svc.sampler_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_sampler_loop_survives_snapshot_failure(monkeypatch):
    """单轮采样失败只记日志：监控出错不该拖垮后台任务。"""
    calls = {"count": 0}

    def _boom():
        calls["count"] += 1
        raise RuntimeError("快照构建炸了")

    monkeypatch.setattr(svc, "build_snapshot", _boom)
    monkeypatch.setattr(svc, "SAMPLE_INTERVAL_S", 0.01)
    task = asyncio.create_task(svc.sampler_loop())
    await asyncio.sleep(0.08)
    still_running = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert still_running
    assert calls["count"] >= 2
