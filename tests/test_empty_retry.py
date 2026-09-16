"""字段空值重试及固定温差。"""
from types import SimpleNamespace

import pytest

from utils.empty_retry import retry_empty, retry_empty_stream, retry_temperature


def field(enabled=True, count=2):
    return SimpleNamespace(empty_retry_enabled=enabled, empty_retry_count=count, field_id="f")


@pytest.mark.parametrize("original,expected", [(0.0, 0.1), (0.7, 0.8), (0.95, 1.0), (1.0, 1.0), (1.2, 1.0)])
async def test_retry_temperature_increases_once_and_caps_at_one(original, expected):
    temperatures = []

    @retry_empty
    async def extract(file_id, field):
        temperatures.append(retry_temperature(original))
        return "", "", None, []

    await extract("doc", field())
    assert temperatures == [original, expected, expected]


@pytest.mark.parametrize("value", [None, "", " \n"])
async def test_empty_retries_with_fixed_temperature(value):
    temperatures = []

    @retry_empty
    async def extract(file_id, field):
        temperatures.append(retry_temperature(0.7))
        return value, "原因", {"source": [1]}, []

    result = await extract("doc", field())
    assert result[0] == value
    assert temperatures == [0.7, 0.8, 0.8]
    assert retry_temperature(0.7) == 0.7


@pytest.mark.parametrize("value", [0, False, "0", "false", "有值"])
async def test_nonempty_does_not_retry(value):
    calls = []

    @retry_empty
    async def extract(file_id, field):
        calls.append(1)
        return value, "", None, []

    await extract("doc", field())
    assert len(calls) == 1


async def test_disabled_and_early_success():
    calls = []

    @retry_empty
    async def extract(file_id, field):
        calls.append(1)
        return ("ok" if len(calls) == 2 else ""), "", None, []

    await extract("doc", field(False))
    assert len(calls) == 1
    calls.clear()
    assert (await extract("doc", field()))[0] == "ok"
    assert len(calls) == 2


async def test_errors_do_not_retry_and_context_resets():
    calls = []

    @retry_empty
    async def extract(file_id, field):
        calls.append(1)
        if len(calls) > 1:
            assert retry_temperature(0.05) == 0.15
            raise RuntimeError("网络错误")
        return "", "", None, []

    with pytest.raises(RuntimeError):
        await extract("doc", field())
    assert len(calls) == 2
    assert retry_temperature(0.05) == 0.05


async def test_nested_calls_share_budget():
    calls = []

    @retry_empty
    async def inner(file_id, field):
        calls.append(1)
        return "", "", None, []

    @retry_empty
    async def outer(file_id, field):
        return await inner(file_id, field)

    await outer("doc", field())
    assert len(calls) == 3


async def test_stream_only_emits_final_result_and_done():
    calls = []

    @retry_empty_stream
    async def extract(file_id, field):
        calls.append(retry_temperature(0.7))
        yield {"event": "prompt", "data": {}}
        yield {"event": "result", "data": {"extracted_value": ""}}
        yield {"event": "done", "data": {}}

    events = [event async for event in extract("doc", field())]
    assert calls == [0.7, 0.8, 0.8]
    assert [e["event"] for e in events].count("retry") == 2
    assert [e["event"] for e in events].count("result") == 1
    assert [e["event"] for e in events].count("done") == 1


def test_json_null_is_empty_but_zero_and_false_are_not():
    from service.extraction_service import parse_llm_json_response
    assert parse_llm_json_response('{"value": null}')[0] == ""
    assert parse_llm_json_response('{"value": 0}')[0] == "0"
    assert parse_llm_json_response('{"value": false}')[0] == "False"
    from service.vl_service._common import parse_vl_json_response
    assert parse_vl_json_response('{"value": null}')[0] == ""


@pytest.mark.parametrize("count", [0, -1, 11, 1.5])
def test_configuration_rejects_invalid_counts(count):
    from pydantic import ValidationError
    from model.schemas import ExtractionFieldCreate, ExportFieldItem
    from blue_print.extraction_router import _build_temp_field
    from fastapi import HTTPException
    data = dict(field_id="f", field_name="字段", source_type="text", use_llm=0, empty_retry_count=count)
    for schema in [ExtractionFieldCreate, ExportFieldItem]:
        with pytest.raises(ValidationError):
            schema(**data)
    with pytest.raises(HTTPException) as error:
        _build_temp_field(data)
    assert error.value.status_code == 422


async def test_real_text_extraction_retries_null(monkeypatch):
    from model.tables import ExtractionField
    from service import extraction_service as svc
    from service.extraction_snapshot import FileExtractionSnapshot
    calls = []

    async def chat(*args, **kwargs):
        calls.append(retry_temperature(0.7))
        return '{"value": null}' if len(calls) < 3 else '{"value": "找到", "pages": [1]}'

    monkeypatch.setattr(svc, "chat_completion", chat)
    config = ExtractionField(field_id="f", source_type="text", search_type="page", search_config={"page_range": "1"}, text_extract_prompt="<search_result>page_content</search_result>", empty_retry_enabled=True, empty_retry_count=2)
    snapshot = FileExtractionSnapshot(file_id="doc", type_id="default", content="正文", page_mapping=[{"page_num": 1, "start_pos": 0, "end_pos": 2}], page_contents={1: "正文"}, tables=(), chunks=())
    result = await svc.extract_text_field("doc", config, snapshot)
    assert result[0] == "找到"
    assert result[3] == [1]
    assert calls == [0.7, 0.8, 0.8]


async def test_parallel_retry_does_not_change_other_field_temperature():
    import asyncio
    reached_retry = asyncio.Event()
    other_finished = asyncio.Event()
    temperatures = []

    @retry_empty
    async def extract(file_id, config):
        if retry_temperature(0.7) == 0.7:
            return "", "", None, []
        reached_retry.set()
        await other_finished.wait()
        temperatures.append(retry_temperature(0.7))
        return "ok", "", None, []

    async def other():
        await reached_retry.wait()
        temperatures.append(retry_temperature(0.7))
        other_finished.set()

    await asyncio.gather(extract("doc", field()), other())
    assert temperatures == [0.7, 0.8]


@pytest.mark.parametrize("kind,base,expected", [("text", None, [1.0, 1.0, 1.0]), ("text", 0.7, [0.7, 0.8, 0.8]), ("vl", 0.05, [0.05, 0.15, 0.15])])
async def test_actual_model_payloads(monkeypatch, kind, base, expected):
    import copy
    import httpx
    from utils import llm_client, vl_client
    from utils.config import AppConfig
    cfg = AppConfig()
    if kind == "text" and base is not None:
        cfg.extraction.extra_body = {"temperature": base}
    if kind == "vl":
        cfg.vl_model.temperature = base
    client_module = llm_client if kind == "text" else vl_client
    monkeypatch.setattr(client_module, "get_config", lambda: cfg)
    payloads = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json, **kwargs):
            payloads.append(copy.deepcopy(json))
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": ""}}]})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", Client)

    @retry_empty
    async def extract(file_id, config):
        if kind == "text":
            await llm_client.chat_completion("prompt")
        else:
            await vl_client.vl_chat([])
        return "", "", None, []

    await extract("doc", field())
    assert [p.get("temperature") for p in payloads] == expected
    assert all("temperature" not in p.get("extra_body", {}) for p in payloads[1:])
    if kind == "text" and base is not None:
        assert cfg.extraction.extra_body["temperature"] == base


async def test_client_exception_is_not_retried_as_empty(monkeypatch):
    from service import extraction_service as svc
    from service.extraction_snapshot import FileExtractionSnapshot
    from model.tables import ExtractionField
    calls = []

    async def chat(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("请求失败")

    monkeypatch.setattr(svc, "chat_completion", chat)
    config = ExtractionField(field_id="f", source_type="text", search_type="page", search_config={"page_range": "1"}, text_extract_prompt="<search_result>page_content</search_result>", empty_retry_enabled=True, empty_retry_count=2)
    snapshot = FileExtractionSnapshot(file_id="doc", type_id="default", content="正文", page_mapping=[{"page_num": 1, "start_pos": 0, "end_pos": 2}], page_contents={}, tables=(), chunks=())
    await svc.extract_text_field("doc", config, snapshot)
    assert len(calls) == 1


@pytest.mark.parametrize("streaming", [False, True])
async def test_table_match_error_does_not_retry(monkeypatch, streaming):
    from unittest.mock import AsyncMock, MagicMock
    from model.tables import ExtractionField
    from service import extraction_service as svc
    from service.extraction_snapshot import FileExtractionSnapshot, TableRow
    calls = []

    async def chat(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("匹配请求失败")

    monkeypatch.setattr(svc, "chat_completion", chat)
    config = ExtractionField(field_id="f", source_type="table", table_match_type="llm", table_match_keywords=["资产"], use_llm=0, empty_retry_enabled=True, empty_retry_count=2)
    tables = (TableRow(0, "资产", "<table>资产</table>", 0, 20, 1),)
    if streaming:
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = tables
        db.execute.return_value = result
        events = [e async for e in svc.test_field_extraction_stream("doc", config, db)]
        assert not any(e["event"] == "retry" for e in events)
    else:
        snapshot = FileExtractionSnapshot("doc", "default", "", [], {}, tables, ())
        await svc.extract_table_field("doc", config, snapshot)
    assert len(calls) == 1


@pytest.mark.parametrize("debug", [False, True])
async def test_hybrid_handled_channel_error_does_not_suppress_retry(monkeypatch, debug):
    from service import extraction_service as svc
    from model.tables import ExtractionField
    from utils.empty_retry import mark_retry_failure
    calls = []
    config = ExtractionField(
        field_id="f", source_type="text", search_type="hybrid", use_llm=1,
        empty_retry_enabled=True, empty_retry_count=2,
        text_extract_prompt="<search_result>混合检索结果</search_result>",
        search_config={"strategy": "fallback", "items": [
            {"id": "vl", "source_type": "vl", "method": "vl_model", "config": {}},
            {"id": "text", "source_type": "text", "method": "context", "config": {}},
        ]},
    )

    async def vl(*args):
        mark_retry_failure()
        return "", "请求失败", None, []

    async def vl_stream(*args):
        yield {"event": "error", "data": {"message": "请求失败"}}

    async def search(*args):
        return "证据", "", {"_texts": {"k": "证据"}}, []

    async def chat(*args, **kwargs):
        calls.append(retry_temperature(0.7))
        return '{"value": ""}'

    monkeypatch.setattr(svc, "extract_vl_field", vl)
    monkeypatch.setattr(svc, "_vl_field_extraction_stream", vl_stream)
    monkeypatch.setattr(svc, "_hybrid_run_search_item", search)
    monkeypatch.setattr(svc, "chat_completion", chat)
    await svc.extract_hybrid_field("doc", config, SimpleNamespace(), debug_events=[] if debug else None)
    assert calls == [0.7, 0.8, 0.8]


async def test_stream_close_cleans_up_without_leaking_context():
    from contextlib import aclosing
    closed = []

    @retry_empty_stream
    async def stream(file_id, field):
        try:
            yield {"event": "prompt", "data": {}}
        finally:
            closed.append(True)

    async with aclosing(stream("doc", field())) as events:
        await anext(events)
        assert retry_temperature(0.7) == 0.7
    assert closed == [True]
