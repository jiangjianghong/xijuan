import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from service.extraction_snapshot import FileExtractionSnapshot, TableRow
from utils.config import AgentConfig
from xijuan_agent import agent
from xijuan_agent.tools import (
    AgentToolContext,
    TOOL_DEFINITIONS,
    ToolOutput,
    _parse_page_selection,
    execute_tool,
)


def _snapshot(content="", *, tables=(), pages=None):
    return FileExtractionSnapshot(
        file_id="file-1",
        type_id="default",
        content=content,
        page_mapping=[],
        page_contents=pages or {},
        tables=tuple(tables),
        chunks=(),
    )


def test_tool_catalog_has_split_section_and_table_workflows():
    tools = {item["function"]["name"]: item["function"] for item in TOOL_DEFINITIONS}

    assert {"list_sections", "read_section", "list_tables", "read_table", "pdf_reader"} <= tools.keys()
    assert "section_index" in tools["read_section"]["parameters"]["required"]
    assert "table_index" in tools["read_table"]["parameters"]["required"]
    assert "最多 8 页" in tools["pdf_reader"]["description"]
    assert "不执行 exact/fuzzy/contains/LLM" in tools["read_table"]["description"]


def test_parse_page_selection_validates_and_expands_ranges():
    assert _parse_page_selection("2-4", [1, 2, 3, 4, 6]) == [2, 3, 4]
    assert _parse_page_selection("all", [1, 3, 7]) == [1, 3, 7]
    with pytest.raises(ValueError):
        _parse_page_selection("4-2", [1, 2, 3, 4])
    with pytest.raises(ValueError):
        _parse_page_selection("2,4", [1, 2, 3, 4])


def test_agent_config_defaults_to_twenty_five_rounds_and_caps_at_one_hundred():
    assert AgentConfig().max_rounds == 25
    with pytest.raises(ValueError):
        AgentConfig(max_rounds=101)


def test_agent_stream_route_is_registered():
    from scripts.gen_openapi import build_app

    operation = build_app().openapi()["paths"]["/agent/stream"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]


@pytest.mark.asyncio
async def test_section_and_table_tools_list_then_read_by_index():
    snapshot = _snapshot(
        "# 1 总则\n正文\n## 1.1 范围\n适用范围\n# 2 附则\n结束",
        tables=(TableRow(0, "费用表", "<table><tr><td>100</td></tr></table>", 0, 20, "2"),),
    )
    context = AgentToolContext(file_id="file-1", snapshot=snapshot)

    sections = json.loads((await execute_tool("list_sections", {}, context)).content)
    assert [item["title"] for item in sections] == ["总则", "范围", "附则"]
    assert sections[1]["level"] > sections[0]["level"]

    section = json.loads((await execute_tool("read_section", {"section_index": 0}, context)).content)
    assert "适用范围" in section["content"]

    tables = json.loads((await execute_tool("list_tables", {}, context)).content)
    assert tables == [{"table_index": 0, "table_name": "费用表", "page_num": "2"}]
    table = json.loads((await execute_tool("read_table", {"table_index": 0}, context)).content)
    assert "100" in table["table_content"]


@pytest.mark.asyncio
async def test_pdf_reader_rejects_more_than_eight_pages_before_file_access():
    context = AgentToolContext(file_id="missing", snapshot=_snapshot())

    with pytest.raises(ValueError, match="最多读取 8 页"):
        await execute_tool("pdf_reader", {"start_page": 1, "end_page": 9}, context)


@pytest.mark.asyncio
async def test_vector_index_loads_only_on_first_vector_tool_call(monkeypatch):
    from xijuan_agent import tools

    calls = 0

    async def load_index(_file_id, _chunks):
        nonlocal calls
        calls += 1
        return object()

    async def search_vector(_file_id, _config, index):
        assert index is not None
        return [{"chunk_id": "chunk-1"}]

    monkeypatch.setattr(tools, "load_file_vector_index", load_index)
    monkeypatch.setattr(tools, "search_vector_db", search_vector)
    context = AgentToolContext(file_id="file-1", snapshot=_snapshot())

    await execute_tool("search_vector_db", {"query_text": "付款"}, context)
    await execute_tool("search_vector_db", {"query_text": "期限"}, context)

    assert calls == 1


@pytest.mark.asyncio
async def test_stream_agent_places_pdf_images_after_all_tool_replies(monkeypatch):
    import model.database
    from service import extraction_snapshot

    file_row = SimpleNamespace(
        file_id="file-1", file_name="合同.pdf", type_id="default", input_params={},
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: file_row)

    monkeypatch.setattr(model.database, "get_session_factory", lambda: lambda: FakeSession())

    async def load_snapshot(*_args, **_kwargs):
        return _snapshot()

    monkeypatch.setattr(extraction_snapshot, "load_extraction_snapshot", load_snapshot)
    monkeypatch.setattr(agent, "get_config", lambda: SimpleNamespace(agent=AgentConfig(max_rounds=2)))

    tool_calls = [
        {"id": "call_pdf", "type": "function", "function": {"name": "pdf_reader", "arguments": "{\"start_page\":1,\"end_page\":1}"}},
        {"id": "call_search", "type": "function", "function": {"name": "search_context", "arguments": "{\"keywords\":[\"付款\"]}"}},
    ]
    captured_messages = []
    first_turn = True

    async def fake_turn(messages, _config, *, round_number, use_tools):
        nonlocal first_turn
        if first_turn:
            first_turn = False
            yield {"type": "turn_complete", "round": round_number, "message": {"role": "assistant", "content": None, "tool_calls": tool_calls}}
        else:
            captured_messages.extend(messages)
            yield {"type": "turn_complete", "round": round_number, "message": {"role": "assistant", "content": "回答"}}

    async def fake_execute(name, _args, _context):
        if name == "pdf_reader":
            return ToolOutput("page attached", ({"page": 1, "mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,AA"},))
        return ToolOutput("search result")

    monkeypatch.setattr(agent, "_stream_turn", fake_turn)
    monkeypatch.setattr(agent, "execute_tool", fake_execute)
    events = [json.loads(item.split("\n", 1)[0][6:]) async for item in agent.stream_agent("file-1", "金额是什么？")]

    roles = [message["role"] for message in captured_messages]
    assert roles == ["system", "user", "assistant", "tool", "tool", "user"]
    assert [message["tool_call_id"] for message in captured_messages if message["role"] == "tool"] == ["call_pdf", "call_search"]
    assert any(event["type"] == "tool_result" and event["images"] for event in events)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_stream_turn_emits_text_and_reconstructs_tool_calls(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "正在检索"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "search_context", "arguments": "{\"keywords\":[\"付款\"]}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"total_tokens": 12}},
    ]

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for chunk in chunks:
                yield "data: " + json.dumps(chunk)
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return FakeResponse()

    class DummyLimiter:
        @asynccontextmanager
        async def context(self, _metadata):
            yield

    monkeypatch.setattr(agent.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(agent, "get_limiter", lambda *_args, **_kwargs: DummyLimiter())
    config = AgentConfig(base_url="https://model.example/v1", model="agent", retry_count=1)
    events = [event async for event in agent._stream_turn([], config, round_number=1, use_tools=True)]

    assert [event["type"] for event in events if event["type"] == "token"] == ["token"]
    complete = events[-1]
    assert complete["type"] == "turn_complete"
    assert complete["message"]["content"] == "正在检索"
    call = complete["message"]["tool_calls"][0]
    assert call["function"]["name"] == "search_context"
    assert json.loads(call["function"]["arguments"]) == {"keywords": ["付款"]}
    assert complete["usage"]["total_tokens"] == 12
