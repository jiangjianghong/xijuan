from types import SimpleNamespace

import pytest

from service import extraction_service as svc


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
