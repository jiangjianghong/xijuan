from types import SimpleNamespace

import pytest

from service.bm25_search import search_bm25


def _chunk(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=f"c{index}",
        chunk_index=index,
        chunk_content=text,
        start_pos=index * 100,
        end_pos=index * 100 + len(text),
        page_num=str(index + 1),
    )


def test_bm25_ranks_multi_term_chinese_match_first():
    chunks = (
        _chunk(0, "投标保证金退还期限为三十天。"),
        _chunk(1, "保证金金额按合同约定执行。"),
        _chunk(2, "工程质量应满足国家标准。"),
    )

    results = search_bm25("f1", {"query_text": "保证金退还期限"}, chunks)

    assert [item["chunk_id"] for item in results] == ["c0", "c1"]
    assert results[0]["score"] > results[1]["score"] > 0
    assert results[0]["keyword"] == "保证金退还期限"


def test_bm25_supports_multiple_queries_and_top_k():
    chunks = (
        _chunk(0, "投标保证金退还期限为三十天。"),
        _chunk(1, "保证金金额按合同约定执行。"),
        _chunk(2, "工程质量应满足国家标准。"),
    )

    results = search_bm25(
        "f1", {"query_text": ["保证金", "工程质量"], "top_k": 1}, chunks
    )

    assert len(results) == 2
    by_query = {item["keyword"]: item["chunk_id"] for item in results}
    assert by_query["保证金"] in {"c0", "c1"}
    assert by_query["工程质量"] == "c2"


def test_bm25_matches_english_case_insensitively():
    results = search_bm25(
        "f1",
        {"query_text": "Contract No. 123", "top_k": 3},
        (_chunk(0, "contract no 123"), _chunk(1, "a different contract")),
    )

    assert results[0]["chunk_id"] == "c0"


async def test_text_extraction_uses_bm25_and_preserves_source_refs():
    from model.tables import ExtractionField
    from service.extraction_service import extract_text_field
    from service.extraction_snapshot import ChunkRow, FileExtractionSnapshot

    snapshot = FileExtractionSnapshot(
        file_id="f1", type_id="default", content="投标保证金退还期限为三十天。",
        page_mapping=[], page_contents={},
        chunks=(ChunkRow("c0", 0, "投标保证金退还期限为三十天。", 0, 15, 4),),
        tables=(),
    )
    field = ExtractionField(
        field_id="guarantee", field_name="保证金期限", source_type="text",
        use_llm=0, search_type="bm25", search_config={"query_text": "保证金退还期限"},
    )

    value, _, refs, pages = await extract_text_field("f1", field, snapshot)

    assert "投标保证金退还期限" in value
    assert refs["保证金退还期限"][0]["type"] == "bm25"
    assert refs["保证金退还期限"][0]["chunk_id"] == "c0"
    assert refs["保证金退还期限"][0]["page_num"] == 4
    assert pages == []


async def test_bm25_runs_as_a_hybrid_union_channel():
    from model.tables import ExtractionField
    from service.extraction_service import extract_hybrid_field
    from service.extraction_snapshot import ChunkRow, FileExtractionSnapshot

    snapshot = FileExtractionSnapshot(
        file_id="f1", type_id="default", content="投标保证金退还期限为三十天。",
        page_mapping=[], page_contents={},
        chunks=(ChunkRow("c0", 0, "投标保证金退还期限为三十天。", 0, 15, 4),),
        tables=(),
    )
    field = ExtractionField(
        field_id="guarantee", field_name="保证金期限", source_type="text",
        use_llm=0, search_type="hybrid", search_config={"strategy": "union", "items": [
            {"id": "lexical", "source_type": "text", "method": "bm25",
             "config": {"query_text": "保证金退还期限"}},
        ]},
    )

    value, _, refs, _ = await extract_hybrid_field("f1", field, snapshot)

    assert "投标保证金退还期限" in value
    assert refs["_hybrid"]["items"][0]["status"] == "matched"


@pytest.mark.parametrize("config", [
    {"query_text": "不存在的查询"},
    {"query_text": "   "},
    {"query_text": [None, " "]},
])
def test_bm25_empty_or_unmatched_query_returns_no_hits(config):
    assert search_bm25("f1", config, (_chunk(0, "合同金额为100万元。"),)) == []


@pytest.mark.parametrize("config", [
    {"query_text": "金额", "k1": 0},
    {"query_text": "金额", "b": 1.1},
])
def test_bm25_rejects_invalid_parameters(config):
    with pytest.raises(ValueError):
        search_bm25("f1", config, (_chunk(0, "合同金额为100万元。"),))
