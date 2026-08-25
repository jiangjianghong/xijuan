"""字段提取并发执行测试。

配置项 task_extraction / global_extraction 长期声明了却没生效——run_extraction
是个 for 循环，limiter 包在单次调用外，实际并发恒为 1。这组测试锁住三件事：
并发真的发生、进阶字段严格晚于普通字段、单字段失败不连坐同批。
"""

from __future__ import annotations

import asyncio
import time
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


class _FakeExecuteResult:
    def scalar_one_or_none(self):
        return None


class _RecordingSession:
    """记录写库调用的会话替身。"""

    def __init__(self):
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return _FakeExecuteResult()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_advanced_fields_start_after_all_basic_finish(monkeypatch):
    """屏障硬保证：min(进阶开始时刻) > max(普通结束时刻)。"""
    timeline = []

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        # loop.time() 在 Windows 上分辨率约 15.6ms，屏障两侧会落进同一 tick，
        # 故用 perf_counter（~100ns）测量，避免把精度不足误判成屏障失效
        timeline.append((field.field_id, "start", time.perf_counter()))
        # 普通字段故意慢，进阶字段快：若无屏障，进阶必然抢先完成
        await asyncio.sleep(0.1 if not field.is_advanced else 0.01)
        timeline.append((field.field_id, "end", time.perf_counter()))
        return f"值-{field.field_id}", "理由", {"label": [{"text": "x"}]}, [2]

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)
    monkeypatch.setattr(es, "notify_callback", _noop_callback)

    fields = [
        _field("b1"), _field("b2"), _field("b3"),
        _field("a1", is_advanced=1), _field("a2", is_advanced=1),
    ]
    session = _RecordingSession()
    items = [
        item async for item in es._iter_extraction_results(
            "file1", session, snapshot=None, ordered_fields=fields,
            basic_count=3, task_limit=4, global_limit=8,
        )
    ]

    assert len(items) == 5
    basic_end = max(t for fid, kind, t in timeline if kind == "end" and fid.startswith("b"))
    adv_start = min(t for fid, kind, t in timeline if kind == "start" and fid.startswith("a"))
    assert adv_start > basic_end, "进阶字段早于普通字段完成就启动了，屏障失效"


@pytest.mark.asyncio
async def test_advanced_fields_see_basic_values(monkeypatch):
    """屏障处聚合的 field_values / source_pages 对进阶字段可见。"""
    seen = {}

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        if field.is_advanced:
            seen["values"] = dict(values)
            seen["pages"] = dict(pages)
            seen["from"] = dict(pages_from or {})
        return f"值-{field.field_id}", "理由", {"label": [{"page_num": 3, "text": "x"}]}, [3]

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)
    monkeypatch.setattr(es, "notify_callback", _noop_callback)

    fields = [_field("b1"), _field("adv", is_advanced=1)]
    session = _RecordingSession()
    _ = [item async for item in es._iter_extraction_results(
        "file1", session, snapshot=None, ordered_fields=fields,
        basic_count=1, task_limit=4, global_limit=8,
    )]

    assert seen["values"] == {"b1": "值-b1"}
    assert seen["pages"]["b1"] == [3]
    assert seen["from"]["b1"] == "model"
    # 进阶字段自身不进入引用映射
    assert "adv" not in seen["values"]


@pytest.mark.asyncio
async def test_items_carry_config_index_and_persist(monkeypatch):
    """产出的 item 带配置序号；每个字段都落一次库。"""

    async def fake_extract(file_id, field, snapshot, values, pages, pages_from=None):
        await asyncio.sleep(0.03 if field.field_id == "f0" else 0.0)
        return "值", "理由", {"label": [{"text": "x"}]}, []

    monkeypatch.setattr(es, "_extract_field_result", fake_extract)
    monkeypatch.setattr(es, "notify_callback", _noop_callback)

    fields = [_field("f0"), _field("f1"), _field("f2")]
    session = _RecordingSession()
    items = [item async for item in es._iter_extraction_results(
        "file1", session, snapshot=None, ordered_fields=fields,
        basic_count=3, task_limit=4, global_limit=8,
    )]

    # 完成顺序：f1、f2 先于 f0
    assert [i["field_id"] for i in items][-1] == "f0"
    # 但 index 恒为配置序号
    assert {i["field_id"]: i["index"] for i in items} == {"f0": 1, "f1": 2, "f2": 3}
    assert session.commits == 3
    assert len(session.added) == 3


async def _fake_load_snapshot(file_id, session, type_id="default", *, need_vectors=False):
    return None


class _StubExtractionSession(_RecordingSession):
    """run_extraction 会查 files 与 extraction_field，两次都返回空集合。"""

    async def execute(self, stmt):
        class _R:
            def scalar_one_or_none(self):
                return None

            def scalars(self):
                return self

            def all(self):
                return []

        return _R()


@pytest.mark.asyncio
async def test_run_extraction_stage_done_sorted_by_config_index(monkeypatch):
    """field_done 按完成序推送，stage_done.results 仍按配置序排列。"""
    callbacks = []

    async def record_callback(url, file_id, status, *, event=None, data=None, timeout=2.5):
        callbacks.append((event, data))

    async def fake_iter(*args, **kwargs):
        for item in (
            {"field_id": "f2", "index": 2, "success": True},
            {"field_id": "f1", "index": 1, "success": True},
            {"field_id": "f3", "index": 3, "success": False},
        ):
            yield item

    monkeypatch.setattr(es, "notify_callback", record_callback)
    monkeypatch.setattr(es, "_iter_extraction_results", fake_iter)
    monkeypatch.setattr(es, "load_extraction_snapshot", _fake_load_snapshot)

    session = _StubExtractionSession()
    await es.run_extraction("file1", session, callback_url="http://cb")

    field_dones = [d for e, d in callbacks if e == "field_done"]
    stage_done = next(d for e, d in callbacks if e == "stage_done")

    # 推送顺序 = 完成顺序
    assert [d["field_id"] for d in field_dones] == ["f2", "f1", "f3"]
    # 聚合结果 = 配置顺序
    assert [r["index"] for r in stage_done["results"]] == [1, 2, 3]
    assert stage_done["succeeded"] == 2
    assert stage_done["failed"] == 1


@pytest.mark.asyncio
async def test_run_extraction_stream_key_mapping(monkeypatch):
    """流式对外键名保持 extracted_value / current，不因内部统一而变更契约。"""

    async def fake_iter(*args, **kwargs):
        yield {
            "field_id": "f1", "field_name": "字段1", "value": "V", "reason": "R",
            "pages": [1], "source_pages": [1], "source_refs": {"l": []},
            "success": True, "index": 1, "total": 1,
        }

    monkeypatch.setattr(es, "_iter_extraction_results", fake_iter)
    monkeypatch.setattr(es, "load_extraction_snapshot", _fake_load_snapshot)

    session = _StubExtractionSession()
    items = [item async for item in es.run_extraction_stream("file1", session)]

    assert items[0]["extracted_value"] == "V"
    assert items[0]["current"] == 1
    assert "value" not in items[0]
    assert "index" not in items[0]
