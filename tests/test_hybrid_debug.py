"""混合检索正式执行与同步/流式调试共用证据和模型请求。"""

import asyncio
import importlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from model.schemas import ExtractionTestRequest
from model.tables import ExtractionField
from service import extraction_service as svc
from service.extraction_snapshot import ChunkRow, FileExtractionSnapshot, TableRow

router = importlib.import_module("blue_print.extraction_router")


@pytest.fixture
def debug_env(monkeypatch):
    snapshot = FileExtractionSnapshot(
        file_id="file", type_id="default", content="amount 100", page_mapping=[],
        page_contents={}, tables=(), chunks=(ChunkRow("c", 0, "amount 100", 0, 10, 7),),
    )
    calls, loads = [], []
    async def load(*args, **kwargs):
        loads.append(kwargs)
        return snapshot
    async def params(*args):
        return {}
    async def chat(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return '{"reason":"ok","value":"100","pages":[7]}'
    monkeypatch.setattr(router, "load_extraction_snapshot", load)
    monkeypatch.setattr(router, "resolve_debug_params", params)
    monkeypatch.setattr(svc, "load_extraction_snapshot", load)
    monkeypatch.setattr(svc, "chat_completion", chat)
    return snapshot, calls, loads


def config(strategy="union", use_llm=1, method="chunk_db"):
    return {
        "field_id": "amount", "field_name": "金额", "source_type": "text",
        "search_type": "hybrid", "use_llm": use_llm,
        "text_extract_prompt": "金额：<search_result>混合检索结果</search_result>",
        "text_system_prompt": "系统规则",
        "search_config": {"strategy": strategy, "items": [
            {"id": "a", "source_type": "text", "method": method,
             "config": {"keywords": ["amount"], "query_text": "amount"}},
        ]},
    }


class FakeDB:
    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(file_content="amount 100"))


@pytest.mark.parametrize("strategy", ["union", "fallback"])
async def test_sync_debug_matches_production(debug_env, strategy):
    snapshot, calls, _ = debug_env
    cfg = config(strategy)
    production = await svc.extract_hybrid_field("file", ExtractionField(**cfg), snapshot)
    response = await router.test_extraction(ExtractionTestRequest(file_id="file", config=cfg), FakeDB())
    assert response.data["extracted_value"] == production[0]
    assert response.data["source_pages"] == [7]
    assert response.data["search_results"][0]["text"] == "amount 100"
    assert len(calls) == 2 and calls[0] == calls[1]
    assert response.data["llm_input"] == calls[1][1]["messages"][1]["content"]


async def test_sync_debug_loads_hybrid_vectors(debug_env, monkeypatch):
    _, _, loads = debug_env
    async def vector(*args):
        return []
    monkeypatch.setattr(svc, "search_vector_db", vector)
    await router.test_extraction(
        ExtractionTestRequest(file_id="file", config=config(method="vector_db")), FakeDB())
    assert loads == [{"need_vectors": True}]


@pytest.mark.parametrize("strategy", ["union", "fallback"])
async def test_stream_exposes_actual_evidence_before_llm(debug_env, strategy):
    _, calls, _ = debug_env
    stream = svc.test_field_extraction_stream("file", ExtractionField(**config(strategy)), FakeDB())
    first = await anext(stream)
    while first["event"].startswith("hybrid_"):
        first = await anext(stream)
    assert first["event"] == "search_results"
    assert calls == []
    assert first["data"]["results_by_label"] == {"混合检索结果": "【第7页】\namount 100"}
    assert len(first["data"]["results"]) == 1
    remaining = [event async for event in stream]
    assert [event["event"] for event in remaining] == ["prompt", "llm_response", "result", "done"]
    assert remaining[0]["data"]["system_prompt"] == "系统规则"
    assert remaining[0]["data"]["user_prompt"] == calls[0][1]["messages"][1]["content"]
    assert remaining[1]["data"]["raw_response"] == '{"reason":"ok","value":"100","pages":[7]}'
    assert remaining[2]["data"]["source_pages"] == [7]


async def test_disabled_llm_debug_has_no_fake_prompt(debug_env):
    _, calls, _ = debug_env
    events = [e async for e in svc.test_field_extraction_stream(
        "file", ExtractionField(**config(use_llm=0)), FakeDB())]
    events = [e for e in events if not e["event"].startswith("hybrid_")]
    assert [e["event"] for e in events] == ["search_results", "result", "done"]
    assert events[1]["data"]["extracted_value"] == "【第7页】\namount 100"
    assert calls == []


async def test_vl_debug_shows_vl_prompt_without_text_llm(debug_env, monkeypatch):
    _, calls, _ = debug_env
    cfg = config("fallback")
    cfg["search_config"]["items"] = [{
        "id": "vl", "source_type": "vl", "method": "vl_model",
        "config": {"vl_extract_prompt": "视觉指令 value reason", "vl_system_prompt": "视觉系统"},
    }]
    async def vl(file_id, field):
        yield {"event": "prompt", "data": {"system_prompt": field.vl_system_prompt, "user_prompt": "实际视觉指令"}}
        yield {"event": "result", "data": {"extracted_value": "视觉值", "reason": "理由", "source_refs": {"_vl": {"key_pages": [3]}}}}
        yield {"event": "done", "data": {}}
    monkeypatch.setattr(svc, "_vl_field_extraction_stream", vl)
    events = [e async for e in svc.test_field_extraction_stream("file", ExtractionField(**cfg), FakeDB())]
    prompt = next(e["data"] for e in events if e["event"] == "prompt")
    assert prompt == {"system_prompt": "视觉系统", "user_prompt": "实际视觉指令"}
    assert next(e["data"] for e in events if e["event"] == "result")["source_pages"] == [3]
    assert calls == []


def _channel(item_id, *, keywords=None, source="text", method="chunk_db"):
    return {"id": item_id, "source_type": source, "method": method,
            "config": {"keywords": ["amount"] if keywords is None else keywords}}


async def test_channel_start_is_sent_before_search(debug_env, monkeypatch):
    _, calls, _ = debug_env
    searches = []
    original = svc._hybrid_run_search_item
    async def search(*args):
        searches.append(args[2]["id"])
        return await original(*args)
    monkeypatch.setattr(svc, "_hybrid_run_search_item", search)
    stream = svc.test_field_extraction_stream("file", ExtractionField(**config()), FakeDB())
    try:
        first = await anext(stream)
        assert first["event"] == "hybrid_start"
        assert first["data"]["items"][0]["status"] == "pending"
        second = await anext(stream)
        assert second["event"] == "hybrid_item_start" and second["data"]["status"] == "running"
        assert searches == [] and calls == []
        remaining = [e async for e in stream]
        assert next(e for e in remaining if e["event"] == "hybrid_item_done")["data"]["adopted"]
    finally:
        await stream.aclose()


async def test_union_reports_hits_separately_from_deduplication(debug_env):
    cfg = config(use_llm=0)
    cfg["search_config"]["items"] = [_channel("a"), _channel("duplicate"), _channel("empty", keywords=["missing"])]
    events = [e async for e in svc.test_field_extraction_stream("file", ExtractionField(**cfg), FakeDB())]
    summary = next(e["data"] for e in events if e["event"] == "hybrid_done")
    assert [i["id"] for i in summary["items"]] == ["a", "duplicate", "empty"]
    assert [i["hit_count"] for i in summary["items"]] == [1, 1, 0]
    assert [i["adopted_count"] for i in summary["items"]] == [1, 0, 0]
    assert [i["status"] for i in summary["items"]] == ["matched", "matched", "empty"]
    assert all(i["elapsed_ms"] >= 0 for i in summary["items"])
    assert summary["items"][0]["source_pages"] == [7]
    assert "amount 100" in summary["items"][0]["preview"]
    assert summary["items"][1]["message"]


async def test_fallback_marks_remaining_items_skipped_after_error_and_empty(debug_env, monkeypatch):
    cfg = config("fallback", use_llm=0)
    cfg["search_config"]["items"] = [_channel("error"), _channel("empty", keywords=["missing"]), _channel("hit"), _channel("tail")]
    original = svc._hybrid_run_search_item
    invoked = []
    async def search(*args):
        item_id = args[2]["id"]
        invoked.append(item_id)
        if item_id == "error":
            raise RuntimeError("检索超时")
        return await original(*args)
    monkeypatch.setattr(svc, "_hybrid_run_search_item", search)
    events = [e async for e in svc.test_field_extraction_stream("file", ExtractionField(**cfg), FakeDB())]
    summary = next(e["data"] for e in events if e["event"] == "hybrid_done")
    assert summary["selected_item"] == "hit"
    assert invoked == ["error", "empty", "hit"]
    assert [i["status"] for i in summary["items"]] == ["error", "empty", "matched", "skipped"]
    assert summary["items"][0]["error"] == "检索超时"
    assert summary["items"][3]["elapsed_ms"] == 0
    assert sum(i["adopted"] for i in summary["items"]) == 1
    assert not any(e["event"] == "error" for e in events)


async def test_union_limit_does_not_report_unused_evidence_as_adopted(debug_env):
    snapshot, _, _ = debug_env
    cfg = config(use_llm=0)
    cfg["search_config"]["max_length"] = 5
    cfg["search_config"]["items"] = [_channel("a"), _channel("b", source="table", method="table_match")]
    # 第二路采用不同原文位置，确保是总长度限制而非去重使其未采用。
    snapshot = replace(snapshot, tables=(TableRow(1, "amount", "<table>200</table>", 20, 38, 9),))
    events = []
    await svc.extract_hybrid_field("file", ExtractionField(**cfg), snapshot, debug_events=events)
    items = next(e["data"]["items"] for e in events if e["event"] == "hybrid_done")
    assert items[0]["adopted"] is True
    assert items[1]["adopted"] is False
    assert items[1]["hit_count"] == 1 and "上限" in items[1]["message"]


async def test_sync_debug_exposes_channel_metadata_and_events(debug_env):
    response = await router.test_extraction(ExtractionTestRequest(file_id="file", config=config()), FakeDB())
    assert response.data["hybrid"]["items"][0]["adopted"] is True
    assert response.data["debug_events"][0]["event"] == "hybrid_start"


async def test_vl_progress_is_scoped_to_channel_and_not_final_result(debug_env, monkeypatch):
    cfg = config("fallback")
    cfg["search_config"]["items"] = [_channel("vl", source="vl", method="vl_locate"), _channel("tail")]
    async def vl(file_id, field):
        yield {"event": "pdf_loaded", "data": {"total_pages": 5, "vl_method": "vl_locate"}}
        yield {"event": "locate_locate", "data": {"phase": "locate", "grid_idx": 1, "found_pages": [3]}}
        yield {"event": "locate_extract", "data": {"phase": "extract", "key_pages": [3]}}
        yield {"event": "prompt", "data": {"system_prompt": "", "user_prompt": "实际视觉提示词"}}
        yield {"event": "result", "data": {"extracted_value": "100", "reason": "ok", "source_refs": {"_vl": {"key_pages": [3]}}}}
        yield {"event": "done", "data": {}}
    monkeypatch.setattr(svc, "_vl_field_extraction_stream", vl)
    events = [e async for e in svc.test_field_extraction_stream("file", ExtractionField(**cfg), FakeDB())]
    progress = [e["data"] for e in events if e["event"] == "hybrid_item_progress"]
    assert [p["event"] for p in progress] == ["pdf_loaded", "locate_locate", "locate_extract"]
    assert all(p["id"] == "vl" for p in progress)
    assert sum(e["event"] == "result" for e in events) == 1
    assert sum(e["event"] == "done" for e in events) == 1
    assert next(e["data"] for e in events if e["event"] == "hybrid_done")["selected_item"] == "vl"


@pytest.mark.parametrize("hybrid", [False, True])
async def test_closing_vl_debug_cancels_pending_model_task(tmp_path, monkeypatch, debug_env, hybrid):
    import fitz
    pdf = tmp_path / "file.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(pdf)
    monkeypatch.setattr(svc.vl_client, "pdf_path", lambda _: pdf)
    released, cancelled = asyncio.Event(), asyncio.Event()
    tasks = []
    async def progressive(*args, progress_cb, **kwargs):
        tasks.append(asyncio.current_task())
        try:
            await progress_cb({"batch_index": 0, "total_batches": 2, "has_info": True})
            await released.wait()
            return "100", "ok", {"key_pages": [1]}
        finally:
            cancelled.set()
    monkeypatch.setattr(svc.vl_service, "vl_progressive_extract", progressive)
    if hybrid:
        cfg = config("fallback")
        cfg["search_config"]["items"] = [_channel("vl", source="vl", method="vl_progressive")]
        stream = svc.test_field_extraction_stream("file", ExtractionField(**cfg), FakeDB())
    else:
        field = ExtractionField(source_type="vl", vl_method="vl_progressive", vl_config={}, vl_extract_prompt="value reason")
        stream = svc._vl_field_extraction_stream("file", field)
    try:
        while True:
            event = await asyncio.wait_for(anext(stream), 2)
            if event["event"] == "progressive_batch" or (
                event["event"] == "hybrid_item_progress" and event["data"]["event"] == "progressive_batch"
            ):
                break
        await stream.aclose()
        assert cancelled.is_set()
    finally:
        released.set()
        await stream.aclose()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
