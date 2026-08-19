"""字段提取并发执行测试。

配置项 task_extraction / global_extraction 长期声明了却没生效——run_extraction
是个 for 循环，limiter 包在单次调用外，实际并发恒为 1。这组测试锁住三件事：
并发真的发生、进阶字段严格晚于普通字段、单字段失败不连坐同批。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import service.extraction_service as es
from utils.concurrency import clear_limiters


@pytest.fixture(autouse=True)
def reset_limiters():
    clear_limiters()
    yield
    clear_limiters()


def _field(field_id: str, *, is_advanced: int = 0, priority: int = 0):
    """构造够用的字段替身：并发执行器只读这几个属性。"""
    return SimpleNamespace(
        field_id=field_id,
        field_name=f"字段{field_id}",
        priority=priority,
        is_advanced=is_advanced,
        source_type="text",
        search_type="context",
        search_config={},
        use_llm=1,
    )


async def _noop_callback(*args, **kwargs):
    """回调在并发测试里无关紧要，静默吞掉。"""
    return None


@pytest.mark.asyncio
async def test_field_group_runs_concurrently(monkeypatch):
    """4 个字段各睡 0.1s，并发上限 4 时总耗时应接近 0.1s 而非 0.4s。"""
    running = 0
    peak = 0

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0.1)
            return f"值-{field.field_id}", "理由", {"label": [{"text": "x"}]}, [1]
        finally:
            running -= 1

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)

    fields = [_field(f"f{i}") for i in range(4)]
    loop = asyncio.get_running_loop()
    started = loop.time()
    results = await es._run_field_group(
        "file1", fields, snapshot=None, field_values={}, field_source_pages={},
        field_pages_from={}, task_limit=4, global_limit=8, start_index=0,
    )
    elapsed = loop.time() - started

    assert len(results) == 4
    assert peak == 4, f"并发峰值应为 4，实际 {peak}"
    assert elapsed < 0.3, f"并发执行耗时应远小于串行的 0.4s，实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_field_group_honors_task_limit(monkeypatch):
    """task_extraction=2 时并发峰值不得超过 2。"""
    running = 0
    peak = 0

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0.05)
            return "值", "理由", {"label": [{"text": "x"}]}, []
        finally:
            running -= 1

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)

    fields = [_field(f"f{i}") for i in range(6)]
    await es._run_field_group(
        "file1", fields, snapshot=None, field_values={}, field_source_pages={},
        field_pages_from={}, task_limit=2, global_limit=8, start_index=0,
    )

    assert peak <= 2, f"并发峰值超过 task_extraction 限制：{peak}"


@pytest.mark.asyncio
async def test_field_failure_is_isolated(monkeypatch):
    """单字段抛异常时，同批其余字段照常完成，失败项以 success=False 返回。"""

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        if field.field_id == "bad":
            raise RuntimeError("模型超时")
        return "好值", "理由", {"label": [{"text": "x"}]}, []

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)

    fields = [_field("ok1"), _field("bad"), _field("ok2")]
    results = await es._run_field_group(
        "file1", fields, snapshot=None, field_values={}, field_source_pages={},
        field_pages_from={}, task_limit=4, global_limit=8, start_index=0,
    )

    by_id = {r.field.field_id: r for r in results}
    assert by_id["ok1"].success is True
    assert by_id["ok2"].success is True
    assert by_id["bad"].success is False
    assert "模型超时" in by_id["bad"].reason
    assert by_id["bad"].value == ""


@pytest.mark.asyncio
async def test_field_group_preserves_config_index(monkeypatch):
    """返回的 index 是配置序号（1-based），与完成顺序无关。"""

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        # 后面的字段先完成，制造完成序与配置序不一致
        await asyncio.sleep(0.05 if field.field_id == "f0" else 0.0)
        return "值", "理由", {"label": [{"text": "x"}]}, []

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)

    fields = [_field("f0"), _field("f1"), _field("f2")]
    results = await es._run_field_group(
        "file1", fields, snapshot=None, field_values={}, field_source_pages={},
        field_pages_from={}, task_limit=4, global_limit=8, start_index=0,
    )

    assert {r.field.field_id: r.index for r in results} == {"f0": 1, "f1": 2, "f2": 3}


@pytest.mark.asyncio
async def test_cancelled_error_propagates(monkeypatch):
    """CancelledError 必须外抛，不能被当成普通字段失败吞掉。"""

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)

    with pytest.raises(asyncio.CancelledError):
        await es._run_field_group(
            "file1", [_field("f0")], snapshot=None, field_values={},
            field_source_pages={}, field_pages_from={}, task_limit=4,
            global_limit=8, start_index=0,
        )
