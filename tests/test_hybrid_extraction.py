from types import SimpleNamespace

import pytest

from service import extraction_service as svc
from service.extraction_snapshot import ChunkRow, FileExtractionSnapshot, TableRow
from model.tables import ExtractionField


def _field(strategy, items, *, use_llm=0):
    return SimpleNamespace(
        field_id="f1", source_type="text", use_llm=use_llm,
        search_type="hybrid", search_config={"strategy": strategy, "items": items},
        text_extract_prompt="依据 <search_result>混合检索结果</search_result> 提取",
        table_extract_prompt=None, text_system_prompt=None, table_system_prompt=None,
    )


@pytest.mark.asyncio
async def test_union_merges_text_and_table_in_item_order(monkeypatch):
    field = _field("union", [
        {"id": "text", "source_type": "text", "method": "chunk_db", "config": {}},
        {"id": "table", "source_type": "table", "method": "table_match", "config": {}},
    ])
    async def text(*args):
        return "文本证据", "", {"_texts": {"k": [{"chunk_id": "same"}]}}, []
    async def table(*args):
        return "表格证据", "", {"_tables": [{"table_index": 1}]}, []
    monkeypatch.setattr(svc, "extract_text_field", text)
    monkeypatch.setattr(svc, "extract_table_field", table)
    value, reason, refs, pages = await svc.extract_hybrid_field("file", field, SimpleNamespace())
    assert value == "文本证据\n---\n表格证据"
    assert len(refs["_hybrid"]["items"]) == 2
    assert pages == []


@pytest.mark.asyncio
async def test_fallback_continues_from_empty_text_to_table(monkeypatch):
    field = _field("fallback", [
        {"id": "text", "source_type": "text", "method": "context", "config": {}},
        {"id": "table", "source_type": "table", "method": "table_match", "config": {}},
    ])
    calls = []
    async def text(*args):
        calls.append("text")
        return "", "", None, []
    async def table(*args):
        calls.append("table")
        return "表格证据", "", {"_tables": []}, []
    monkeypatch.setattr(svc, "extract_text_field", text)
    monkeypatch.setattr(svc, "extract_table_field", table)
    value, _, _, _ = await svc.extract_hybrid_field("file", field, SimpleNamespace())
    assert value == "表格证据"
    assert calls == ["text", "table"]


@pytest.mark.asyncio
async def test_fallback_uses_vl_as_final_direct_result(monkeypatch):
    field = _field("fallback", [
        {"id": "text", "source_type": "text", "method": "context", "config": {}},
        {"id": "vl", "source_type": "vl", "method": "vl_locate", "config": {}},
    ])
    async def text(*args):
        return "", "", None, []
    async def vl(*args):
        return "视觉值", "视觉理由", {"_vl": {"key_pages": [2]}}, [2]
    monkeypatch.setattr(svc, "extract_text_field", text)
    monkeypatch.setattr(svc, "extract_vl_field", vl)
    result = await svc.extract_hybrid_field("file", field, SimpleNamespace())
    assert result[:2] == ("视觉值", "视觉理由")
    assert result[2]["_vl"] == {"key_pages": [2]}
    assert result[2]["_hybrid"]["selected_item"] == "vl"
    assert result[3] == [2]


@pytest.mark.asyncio
async def test_union_uses_fixed_placeholder_once(monkeypatch):
    field = _field("union", [
        {"id": "a", "source_type": "text", "method": "context", "config": {}},
    ], use_llm=1)
    seen = []
    async def text(*args):
        return "证据", "", {"_texts": {"same": [{"text": "证据"}]}}, []
    async def chat(prompt):
        seen.append(prompt)
        return '{"reason":"ok","value":"值","pages":[]}'
    monkeypatch.setattr(svc, "extract_text_field", text)
    monkeypatch.setattr(svc, "chat_completion", chat)
    value, _, _, _ = await svc.extract_hybrid_field("file", field, SimpleNamespace())
    assert value == "值"
    assert "证据" in seen[0]
    assert "<search_result>" not in seen[0]


def _item(item_id="a", source="text", method="chunk_db", **config):
    return {"id": item_id, "source_type": source, "method": method, "config": config}


def _real_field(items, *, strategy="union", use_llm=0, **kwargs):
    return ExtractionField(
        field_id="amount", field_name="金额", source_type="text", use_llm=use_llm,
        search_type="hybrid", search_config={"strategy": strategy, "items": items},
        text_extract_prompt="依据 <search_result>混合检索结果</search_result> 提取",
        **kwargs,
    )


@pytest.fixture
def snapshot():
    return FileExtractionSnapshot(
        file_id="file", type_id="default", content="amount 100", page_mapping=[],
        page_contents={}, chunks=(ChunkRow("c1", 0, "amount 100", 0, 10, 7),),
        tables=(TableRow(0, "amount", "<table>100</table>", 20, 38, 9),),
    )


def test_hybrid_vector_item_requests_vector_index():
    field = _real_field([_item(method="vector_db", query_text="amount")])
    assert svc.needs_vector_index([field])
    assert not svc.needs_vector_index([_real_field([_item(keywords=["amount"])])])


@pytest.mark.parametrize("strategy", ["union", "fallback"])
async def test_empty_hybrid_result_is_not_success(snapshot, strategy):
    field = _real_field([_item(keywords=["absent"])], strategy=strategy)
    value, reason, refs, _ = await svc.extract_hybrid_field("file", field, snapshot)
    assert refs["_hybrid"]["items"][0]["matched"] is False
    with pytest.raises(svc.NoExtractionResultError):
        svc._ensure_valid_extraction_result(field, value, reason, refs)


@pytest.mark.parametrize("strategy", ["union", "fallback"])
async def test_hybrid_sends_system_prompt(snapshot, monkeypatch, strategy):
    calls = []
    async def chat(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return '{"reason":"ok","value":"100","pages":[7]}'
    monkeypatch.setattr(svc, "chat_completion", chat)
    field = _real_field([_item(keywords=["amount"])], strategy=strategy,
                        use_llm=1, text_system_prompt="系统规则")
    value, _, _, pages = await svc.extract_hybrid_field("file", field, snapshot)
    assert value == "100" and pages == [7]
    assert calls[0][1]["messages"][0] == {"role": "system", "content": "系统规则"}
    assert "【第7页】" in calls[0][1]["messages"][1]["content"]


async def test_union_preserves_table_refs_and_deduplicates_tables(snapshot):
    items = [_item(i, "table", "table_match", table_match_keywords=["amount"])
             for i in ["a", "b"]]
    value, _, refs, pages = await svc.extract_hybrid_field("file", _real_field(items), snapshot)
    assert value.count("<table>100</table>") == 1
    assert refs["_tables"][0]["table_index"] == 0
    assert len(refs["_tables"]) == 1
    assert svc.derive_source_pages(pages, refs) == [9]


async def test_union_keeps_page_markers_without_duplicate_fallback(snapshot):
    items = [_item(i, keywords=["amount"]) for i in ["a", "b"]]
    value, _, refs, _ = await svc.extract_hybrid_field("file", _real_field(items), snapshot)
    assert value == "【第7页】\namount 100"
    assert refs["_texts"]["混合检索结果"] == value


async def test_union_preserves_cross_page_markers():
    content = "page1\npage2"
    mapping = [{"page_num": 1, "start_pos": 0, "end_pos": 6},
               {"page_num": 2, "start_pos": 6, "end_pos": 11}]
    snapshot = FileExtractionSnapshot(
        file_id="file", type_id="default", content=content, page_mapping=mapping,
        page_contents={}, tables=(), chunks=(ChunkRow("c", 0, content, 0, 11, "1-2"),),
    )
    field = _real_field([_item(keywords=["page"])])
    value, _, _, _ = await svc.extract_hybrid_field("file", field, snapshot)
    assert "【第1页】\npage1" in value
    assert "【第2页】\npage2" in value


@pytest.mark.parametrize("source,method,nested,expected", [
    ("text", "page", False, "7-8"),
    ("vl", "vl_locate", False, "7,9"),
    ("vl", "vl_model", True, "7,9"),
])
def test_hybrid_resolves_page_links(source, method, nested, expected):
    config = {"page_source_field": "base", "max_pages": 2}
    if nested:
        config = {"vl_config": config}
    field = _real_field([_item(source=source, method=method, **config)],
                        strategy="fallback", is_advanced=1)
    assert svc.collect_depend_fields(field) == ["base"]
    resolved, provenance = svc.resolve_advanced_field(field, {}, {"base": [7, 9]}, {"base": "refs"})
    options = resolved.search_config["items"][0]["config"]
    assert (options.get("vl_config") or options)["page_range"] == expected
    assert "page_range" not in (field.search_config["items"][0]["config"].get("vl_config") or config)
    assert provenance["_hybrid_page_links"]["a"]["source_field"] == "base"
    with pytest.raises(ValueError, match="base"):
        svc.resolve_advanced_field(field, {}, {})


def test_hybrid_ignores_inactive_page_link_and_removes_empty_keywords():
    field = _real_field([_item(keywords=["<field_result>base</field_result>", "amount"],
                              page_source_field="unused")], is_advanced=1)
    assert svc.collect_depend_fields(field) == ["base"]
    resolved, _ = svc.resolve_advanced_field(field, {}, {})
    assert resolved.search_config["items"][0]["config"]["keywords"] == ["amount"]


async def test_union_respects_page_item_length_limit():
    snapshot = FileExtractionSnapshot(
        file_id="file", type_id="default", content="amount " * 30, page_mapping=[],
        page_contents={}, tables=(), chunks=(), page_projection=({
            "page_num": 1, "source_pages": [1], "content": "amount " * 30,
            "mapping_quality": "middle_json", "bboxes": [],
        },),
    )
    field = _real_field([_item(method="page", page_range="1", max_length=20)])
    value, _, refs, _ = await svc.extract_hybrid_field("file", field, snapshot)
    assert value == ("【第1页】\n" + "amount " * 30)[:20]
    assert refs["_texts"]["混合检索结果"] == value


async def test_short_page_item_does_not_hide_later_longer_evidence():
    content = "开头" * 20 + "仅在后续更长证据中出现的金额100"
    snapshot = FileExtractionSnapshot(
        file_id="file", type_id="default", content=content, page_mapping=[],
        page_contents={}, tables=(), chunks=(), page_projection=({
            "page_num": 1, "source_pages": [1], "content": content,
            "mapping_quality": "middle_json", "bboxes": [],
        },),
    )
    field = _real_field([_item("short", method="page", page_range="1", max_length=20),
                         _item("same", method="page", page_range="1", max_length=20),
                         _item("long", method="page", page_range="1", max_length=200)])
    value, _, refs, _ = await svc.extract_hybrid_field("file", field, snapshot)
    assert "仅在后续更长证据中出现的金额100" in value
    assert [entry["matched"] for entry in refs["_hybrid"]["items"]] == [True, True, True]
    assert [entry["adopted"] for entry in refs["_hybrid"]["items"]] == [True, False, True]


async def test_union_deduplicates_same_span_across_text_methods(snapshot):
    # 同一块被按上下文与按块命中时，稳定 ID 不同也应按原文坐标去重。
    field = _real_field([_item("chunk", keywords=["amount"]),
                         _item("context", method="context", keywords=["amount"],
                               context_before=0, context_after=100)])
    value, _, _, _ = await svc.extract_hybrid_field("file", field, snapshot)
    assert value.count("amount 100") == 1


@pytest.mark.parametrize("strategy", ["union", "fallback"])
async def test_model_error_is_not_converted_to_success(snapshot, monkeypatch, strategy):
    async def chat(*args, **kwargs):
        raise RuntimeError("模型不可用")
    monkeypatch.setattr(svc, "chat_completion", chat)
    field = _real_field([_item(keywords=["amount"])], use_llm=1, strategy=strategy)
    with pytest.raises(RuntimeError, match="模型不可用"):
        await svc.extract_hybrid_field("file", field, snapshot)
