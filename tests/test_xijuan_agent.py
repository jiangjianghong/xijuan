import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from service.extraction_snapshot import FileExtractionSnapshot, TableRow
from utils.config import AgentConfig
from xijuan_agent import agent
from xijuan_agent.context import compact_messages, estimate_tokens, sanitize_history
from xijuan_agent.tools import (
    AgentFileContext,
    AgentToolContext,
    TOOL_DEFINITIONS,
    ToolOutput,
    _parse_page_selection,
    execute_tool,
    limit_tool_output,
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
    for name in ("search_context", "list_sections", "search_rule", "search_chunk_db", "search_bm25", "search_vector_db", "search_page", "list_tables"):
        assert "file_ids" in tools[name]["parameters"]["required"]
    assert "file_id" in tools["read_section"]["parameters"]["required"]
    assert "file_id" in tools["read_table"]["parameters"]["required"]
    assert "file_id" in tools["pdf_reader"]["parameters"]["required"]
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
    assert AgentConfig().context_max_tokens == 24000
    assert AgentConfig().context_compress_threshold == 18000
    assert AgentConfig().context_recent_tool_rounds == 4
    assert AgentConfig().tool_result_max_chars == 20000
    with pytest.raises(ValueError):
        AgentConfig(max_rounds=101)
    with pytest.raises(ValueError):
        AgentConfig(context_max_tokens=4000, context_compress_threshold=5000)
    with pytest.raises(ValueError):
        AgentConfig(tool_result_max_chars=999)
    with pytest.raises(ValueError):
        AgentConfig(tool_result_max_chars=200001)


def test_agent_extra_params_are_merged_and_cannot_replace_request_structure():
    config = AgentConfig(extra_params={"enable_thinking": True, "thinking_budget": 4096})
    payload = agent._request_payload([{"role": "user", "content": "分析"}], config, True)

    assert payload["enable_thinking"] is True
    assert payload["thinking_budget"] == 4096
    assert payload["messages"] == [{"role": "user", "content": "分析"}]
    assert payload["stream"] is True
    with pytest.raises(ValueError):
        AgentConfig(extra_params={"messages": []})


def test_agent_stream_route_is_registered():
    from scripts.gen_openapi import build_app

    schema = build_app().openapi()
    operation = schema["paths"]["/agent/stream"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    assert "/agent/files" in schema["paths"]


def test_agent_request_accepts_multiple_references_and_legacy_file_id():
    from xijuan_agent.router import AgentRunRequest

    assert AgentRunRequest(file_ids=["a", "b", "a"], question="问题").file_ids == ["a", "b"]
    assert AgentRunRequest(file_id="legacy", question="问题").file_ids == ["legacy"]
    with pytest.raises(ValueError):
        AgentRunRequest(question="缺文件")


@pytest.mark.asyncio
async def test_section_and_table_tools_list_then_read_by_index():
    snapshot = _snapshot(
        "# 1 总则\n正文\n## 1.1 范围\n适用范围\n# 2 附则\n结束",
        tables=(TableRow(0, "费用表", "<table><tr><td>100</td></tr></table>", 0, 20, "2"),),
    )
    context = AgentToolContext(file_id="file-1", snapshot=snapshot)

    sections = json.loads((await execute_tool("list_sections", {"file_ids": ["file-1"]}, context)).content)
    assert [item["title"] for item in sections] == ["总则", "范围", "附则"]
    assert sections[1]["level"] > sections[0]["level"]

    section = json.loads((await execute_tool("read_section", {"file_id": "file-1", "section_index": 0}, context)).content)
    assert "适用范围" in section["content"]

    tables = json.loads((await execute_tool("list_tables", {"file_ids": ["file-1"]}, context)).content)
    assert tables == [{"table_index": 0, "table_name": "费用表", "page_num": "2"}]
    table = json.loads((await execute_tool("read_table", {"file_id": "file-1", "table_index": 0}, context)).content)
    assert "100" in table["table_content"]


@pytest.mark.asyncio
async def test_multi_file_tools_merge_sources_and_keep_file_boundaries():
    from xijuan_agent.tools import AgentFileContext

    first = _snapshot("# 1 付款\n合同一约定付款期限为三十天。")
    second = FileExtractionSnapshot(
        file_id="file-2", type_id="default", content="# 1 付款\n合同二约定付款期限为四十五天。",
        page_mapping=[], page_contents={}, tables=(), chunks=(),
    )
    context = AgentToolContext(files={
        "file-1": AgentFileContext("file-1", "合同一.pdf", first),
        "file-2": AgentFileContext("file-2", "合同二.pdf", second),
    })

    found = json.loads((await execute_tool("search_context", {"file_ids": ["file-1", "file-2"], "keywords": ["付款期限"]}, context)).content)
    assert {row["file_id"] for row in found} == {"file-1", "file-2"}
    assert {row["file_name"] for row in found} == {"合同一.pdf", "合同二.pdf"}
    selected = json.loads((await execute_tool("search_context", {"file_ids": ["file-2"], "keywords": ["付款期限"]}, context)).content)
    assert {row["file_id"] for row in selected} == {"file-2"}

    sections = json.loads((await execute_tool("list_sections", {"file_ids": ["file-1", "file-2"]}, context)).content)
    assert {row["file_id"] for row in sections} == {"file-1", "file-2"}
    section = json.loads((await execute_tool("read_section", {"file_id": "file-2", "section_index": 0}, context)).content)
    assert section["file_name"] == "合同二.pdf"
    assert "四十五天" in section["content"]

    with pytest.raises(ValueError, match="不在当前消息引用范围"):
        await execute_tool("read_section", {"file_id": "outside", "section_index": 0}, context)

    with pytest.raises(ValueError, match="不在当前消息引用范围"):
        await execute_tool("list_sections", {"file_ids": ["outside"]}, context)
    with pytest.raises(ValueError, match="必须在参数中提供非空 file_ids"):
        await execute_tool("list_tables", {}, context)


@pytest.mark.asyncio
async def test_context_window_summarizes_old_tool_groups_and_keeps_recent_group():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "old", "function": {"name": "search_context", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old", "content": "OLD_EVIDENCE " + "x" * 500},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "new", "function": {"name": "search_rule", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "new", "content": "NEW_EVIDENCE " + "y" * 500},
        {"role": "assistant", "content": "新回答"},
        {"role": "user", "content": "当前问题"},
    ]
    summaries = []

    async def summarize(text):
        summaries.append(text)
        return "已记录早期付款证据"

    result, changed, tokens = await compact_messages(
        messages, threshold_tokens=100, max_tokens=4000, recent_tool_rounds=1, summarize=summarize,
    )

    assert changed
    assert tokens <= 4000
    assert summaries and "OLD_EVIDENCE" in summaries[0]
    assert "OLD_EVIDENCE" not in json.dumps(result, ensure_ascii=False)
    assert any("NEW_EVIDENCE" in str(item.get("content")) for item in result)
    assert any(str(item.get("content", "")).startswith("[xijuan_agent_memory]") for item in result)


def test_history_trimming_preserves_complete_turn_boundaries_and_estimates_images_without_base64():
    history = [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "第二轮"},
        {"role": "assistant", "content": "答复"},
    ]
    trimmed = sanitize_history(history, max_messages=4)
    assert [item["role"] for item in trimmed] == ["user", "assistant"]
    assert trimmed[0]["content"] == "第二轮"

    with_image = [{"role": "user", "content": [
        {"type": "text", "text": "图片"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 100_000}},
    ]}]
    assert estimate_tokens(with_image) < 2000

    long_tool_history = [{"role": "tool", "tool_call_id": "large", "content": "字" * 90000}]
    assert sanitize_history(long_tool_history) == long_tool_history


@pytest.mark.asyncio
async def test_pdf_reader_rejects_more_than_eight_pages_before_file_access():
    context = AgentToolContext(file_id="missing", snapshot=_snapshot())

    with pytest.raises(ValueError, match="最多读取 8 页"):
        await execute_tool("pdf_reader", {"file_id": "missing", "start_page": 1, "end_page": 9}, context)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [4000, 20000, 35000])
async def test_tool_text_limit_is_configurable_and_preserves_json_and_sources(limit):
    snapshot = _snapshot(
        "# 1 长章节\n" + '正文含"引号"和换行\n' * 6000,
        tables=(TableRow(0, "长表格", '<table><td title="金额">100</td></table>' * 4000, 0, 20, "2"),),
    )
    context = AgentToolContext(
        files={"file-1": AgentFileContext("file-1", "文档.pdf", snapshot)},
        tool_result_max_chars=limit,
    )
    for tool, args, key in (
        ("read_section", {"file_id": "file-1", "section_index": 0}, "content"),
        ("read_table", {"file_id": "file-1", "table_index": 0}, "table_content"),
    ):
        output = (await execute_tool(tool, args, context)).content
        result = json.loads(output)
        assert len(output) <= limit
        assert len(output) > limit - 30
        assert result["truncated"] is True
        assert result["file_id"] == "file-1" and result["file_name"] == "文档.pdf"
        assert len(result[key]) > 1000
        assert "preview" not in result


@pytest.mark.asyncio
async def test_page_text_defaults_to_settings_instead_of_twelve_thousand_chars():
    snapshot = _snapshot(pages={1: "正文" * 7400 + "页尾标记"})
    context = AgentToolContext(file_id="file-1", snapshot=snapshot)
    output = (await execute_tool("search_page", {"file_ids": ["file-1"], "page_range": "1"}, context)).content
    assert "页尾标记" in output and len(output) < 20000
    assert json.loads(output)["truncated"] is False
    context.tool_result_max_chars = 35000
    context.snapshot.page_contents[1] = "正文" * 13000 + "较大预算页尾"
    output = (await execute_tool("search_page", {"file_ids": ["file-1"], "page_range": "1"}, context)).content
    assert "较大预算页尾" in output and 20000 < len(output) < 35000


def test_request_budget_accounts_for_tool_schemas_and_output_overrides():
    config = AgentConfig(max_tokens=4096, extra_params={"max_completion_tokens": 6000})
    assert agent._request_overhead(config, False) == 6000
    assert agent._request_overhead(config, True) > 6000


def test_error_and_pdf_text_outputs_obey_the_same_final_character_limit():
    error = ToolOutput(json.dumps({"error": "\n" * 1000}, ensure_ascii=False))
    limited = limit_tool_output(error, 1000)
    assert len(limited.content) <= 1000
    assert json.loads(limited.content)["error"]
    assert json.loads(limited.content)["truncated"] is True
    images = ({"page": 1, "data_url": "data:image/jpeg;base64,AA"},)
    pdf = limit_tool_output(ToolOutput("PDF 页图说明" * 1000, images), 1000)
    assert len(pdf.content) <= 1000
    assert pdf.images == images


@pytest.mark.parametrize("text", ['"' * 15000, '\\\n' * 10000], ids=["quotes", "backslashes-and-newlines"])
def test_summary_input_budget_counts_json_escaping(text):
    config = AgentConfig(max_tokens=1600, context_max_tokens=8000, context_compress_threshold=6000)
    messages = [{"role": "system", "content": "摘要指令"}, {"role": "user", "content": text}]
    agent._fit_summary_input(messages, config)
    assert estimate_tokens(messages) + agent._request_overhead(config, False) <= config.context_max_tokens
    assert "因摘要输入预算省略" in messages[-1]["content"]


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

    await execute_tool("search_vector_db", {"file_ids": ["file-1"], "query_text": "付款"}, context)
    await execute_tool("search_vector_db", {"file_ids": ["file-1"], "query_text": "期限"}, context)

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
        {"id": "call_pdf", "type": "function", "function": {"name": "pdf_reader", "arguments": "{\"file_id\":\"file-1\",\"start_page\":1,\"end_page\":1}"}},
        {"id": "call_search", "type": "function", "function": {"name": "search_context", "arguments": "{\"file_ids\":[\"file-1\"],\"keywords\":[\"付款\"]}"}},
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
async def test_final_synthesis_waits_for_summary_and_respects_budget(monkeypatch):
    import model.database
    from service import extraction_snapshot

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
                file_id="file-1", file_name="合同.pdf", type_id="default", input_params={},
            ))

    async def load_snapshot(*_args, **_kwargs):
        return _snapshot()

    config = AgentConfig(max_rounds=1, max_tokens=200, context_max_tokens=8000, context_compress_threshold=6000)
    monkeypatch.setattr(model.database, "get_session_factory", lambda: lambda: FakeSession())
    monkeypatch.setattr(extraction_snapshot, "load_extraction_snapshot", load_snapshot)
    monkeypatch.setattr(agent, "get_config", lambda: SimpleNamespace(agent=config))
    requested = []

    async def fake_turn(messages, turn_config, *, round_number, use_tools):
        assert estimate_tokens(messages) + agent._request_overhead(turn_config, use_tools) <= config.context_max_tokens
        requested.append((round_number, use_tools))
        if round_number == 0:
            yield {"type": "token", "text": "已记录关键金额与来源"}
            return
        if use_tools:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "long_result", "function": {"name": "read_table", "arguments": '{"file_id":"file-1","table_index":0}'}},
            ]}
        else:
            assert any(str(message.get("content", "")).startswith("[xijuan_agent_memory]") for message in messages)
            message = {"role": "assistant", "content": "最终回答"}
        yield {"type": "turn_complete", "message": message}

    async def execute(_name, _args, context):
        assert context.tool_result_max_chars == 20000
        return ToolOutput("证据" * 9900)

    monkeypatch.setattr(agent, "_stream_turn", fake_turn)
    monkeypatch.setattr(agent, "execute_tool", execute)
    events = [json.loads(item.split("\n", 1)[0][6:]) async for item in agent.stream_agent("file-1", "金额是什么？")]
    assert events[-1]["type"] == "done"
    assert any(event.get("state") == "waiting" for event in events)
    assert requested == [(1, True), (0, False), (2, False)]


@pytest.mark.asyncio
async def test_stream_turn_emits_text_and_reconstructs_tool_calls(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "先查找付款依据。"}}]},
        {"choices": [{"delta": {"content": "正在检索"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "search_context", "arguments": "{\"file_ids\":[\"file-1\"],\"keywords\":[\"付款\"]}"}}]}}]},
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
    assert [event["text"] for event in events if event["type"] == "thinking"] == ["先查找付款依据。"]
    complete = events[-1]
    assert complete["type"] == "turn_complete"
    assert complete["message"]["content"] == "正在检索"
    assert complete["message"]["reasoning_content"] == "先查找付款依据。"
    call = complete["message"]["tool_calls"][0]
    assert call["function"]["name"] == "search_context"
    assert json.loads(call["function"]["arguments"]) == {"file_ids": ["file-1"], "keywords": ["付款"]}
    assert complete["usage"]["total_tokens"] == 12
