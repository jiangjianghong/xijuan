import json
from types import SimpleNamespace

import pytest

from service import extraction_service as svc


@pytest.mark.asyncio
async def test_text_extraction_reranks_overflow_with_prompt_query(monkeypatch):
    field = SimpleNamespace(
        field_id="amount",
        search_type="context",
        search_config={
            "keywords": ["合同"],
            "max_results": 3,
            "max_total_results": 1,
        },
        text_system_prompt="提取合同金额",
        text_extract_prompt="只找含税总价：<search_result>合同</search_result>",
        use_llm=1,
    )
    snapshot = SimpleNamespace(
        content="甲候选乙候选丙候选",
        page_mapping=[],
        chunks=(),
        vector_index=None,
        page_projection=None,
    )
    rerank_calls = []
    llm_calls = []

    async def search_context(content, config, chunks, *, candidate_only=False):
        assert candidate_only is True
        return [
            {"keyword": "合同", "context": "候选甲"},
            {"keyword": "合同", "context": "候选乙"},
            {"keyword": "合同", "context": "候选丙"},
        ]

    async def rerank(query, documents, top_n):
        rerank_calls.append((query, documents, top_n))
        return [1]

    async def chat_completion(prompt, *, messages=None, **kwargs):
        llm_calls.append(messages[1]["content"] if messages else prompt)
        return json.dumps({"reason": "matched", "value": "乙", "pages": []})

    monkeypatch.setattr(svc, "search_context", search_context)
    monkeypatch.setattr(svc, "rerank_indices", rerank)
    monkeypatch.setattr(svc, "chat_completion", chat_completion)

    value, _, refs, _ = await svc.extract_text_field("f1", field, snapshot)

    assert value == "乙"
    assert rerank_calls == [
        ("提取合同金额\n\n只找含税总价：", ["候选甲", "候选乙", "候选丙"], 1)
    ]
    assert "候选乙" in llm_calls[0]
    assert "候选甲" not in llm_calls[0]
    assert refs["_texts"]["合同"] == "候选乙"


@pytest.mark.asyncio
async def test_text_extraction_skips_rerank_when_candidates_fit_limit(monkeypatch):
    field = SimpleNamespace(
        field_id="amount",
        search_type="context",
        search_config={"keywords": ["合同"], "max_results": 2, "max_total_results": 2},
        text_system_prompt="",
        text_extract_prompt="<search_result>合同</search_result>",
        use_llm=1,
    )
    snapshot = SimpleNamespace(
        content="合同候选",
        page_mapping=[],
        chunks=(),
        vector_index=None,
        page_projection=None,
    )
    rerank_calls = []

    async def search_context(*args, **kwargs):
        return [{"keyword": "合同", "context": "唯一候选"}]

    async def rerank(*args):
        rerank_calls.append(args)
        return [0]

    async def chat_completion(prompt, **kwargs):
        return json.dumps({"reason": "matched", "value": "合同", "pages": []})

    monkeypatch.setattr(svc, "search_context", search_context)
    monkeypatch.setattr(svc, "rerank_indices", rerank)
    monkeypatch.setattr(svc, "chat_completion", chat_completion)

    await svc.extract_text_field("f1", field, snapshot)

    assert rerank_calls == []


@pytest.mark.asyncio
async def test_section_candidate_mode_defers_limit_and_reranks_by_target_title(monkeypatch):
    content = "# 一、付款方式\n甲\n# 二、付款期限\n乙\n# 三、付款条件\n丙\n"
    config = {
        "section_pattern": "付款",
        "section_match_type": "contains",
        "max_results": 1,
    }
    candidates = await svc.search_section(content, config, candidate_only=True)
    calls = []

    async def rerank(query, documents, top_n):
        calls.append((query, documents, top_n))
        return [2]

    monkeypatch.setattr(svc, "rerank_indices", rerank)
    field = SimpleNamespace(field_id="section", text_system_prompt="", text_extract_prompt="")
    results = await svc._rerank_text_candidates(field, "section", config, candidates)

    assert calls == [("付款", ["付款方式", "付款期限", "付款条件"], 1)]
    assert [item["section_title"] for item in results] == ["付款条件"]


@pytest.mark.asyncio
async def test_keyword_searchers_preserve_per_keyword_candidates_for_rerank():
    content = "".join(f"合同金额是{i}。" for i in range(8))
    config = {
        "keywords": ["合同"],
        "max_results": 4,
        "max_total_results": 1,
        "stop_words": ["。"],
        "max_length": 20,
    }

    contexts = await svc.search_context(
        content, {**config, "context_before": 0, "context_after": 1}, (),
        candidate_only=True,
    )
    rules = await svc.search_rule(content, config, (), candidate_only=True)
    chunks = tuple(SimpleNamespace(
        chunk_id=f"c{i}", chunk_index=i, chunk_content=f"合同金额 {i}",
        start_pos=i, end_pos=i + 1, page_num="1",
    ) for i in range(8))
    db_results = await svc.search_chunk_db(
        "f1", config, chunks, candidate_only=True
    )

    assert len(contexts) == len(rules) == len(db_results) == 4


@pytest.mark.asyncio
async def test_bm25_candidate_mode_does_not_apply_top_k_before_rerank():
    chunks = [SimpleNamespace(
        chunk_id=f"c{i}", chunk_index=i, chunk_content=f"合同 金额 {i}",
        start_pos=i, end_pos=i + 1, page_num="1",
    ) for i in range(5)]

    candidates = svc.search_bm25(
        "f1", {"query_text": "合同", "top_k": 1}, chunks, candidate_only=True
    )

    assert len(candidates) == 5


@pytest.mark.asyncio
async def test_vector_candidate_mode_applies_top_k_after_full_score_filter(monkeypatch):
    parents = {
        f"p{i}": SimpleNamespace(
            chunk_id=f"c{i}", chunk_index=i, chunk_content=f"片段{i}",
            start_pos=i, end_pos=i + 1, page_num="1",
        ) for i in range(3)
    }
    index = SimpleNamespace(size=8, parents=parents, degraded=False)
    limits = []

    async def embeddings(queries):
        return [[1.0, 0.0] for _ in queries]

    def select(index, vector, **kwargs):
        limits.append(kwargs)
        return [(parent_id, 0.9 - i * 0.1) for i, parent_id in enumerate(parents)]

    monkeypatch.setattr(svc, "get_embeddings", embeddings)
    monkeypatch.setattr(svc, "select_parent_hits", select)
    monkeypatch.setattr(
        svc, "get_config",
        lambda: SimpleNamespace(milvus=SimpleNamespace(max_results=10, score_ratio=0.0)),
    )

    candidates = await svc.search_vector_db(
        "f1", {"query_text": "合同", "top_k": 1}, index, candidate_only=True
    )

    assert len(candidates) == 3
    assert limits[0]["top_k"] is None
    assert limits[0]["max_results"] == 3


@pytest.mark.asyncio
async def test_degraded_vector_candidate_mode_uses_threshold_iterator(monkeypatch):
    parents = {
        f"p{i}": SimpleNamespace(
            chunk_id=f"c{i}", chunk_index=i, chunk_content=f"片段{i}",
            start_pos=i, end_pos=i + 1, page_num="1",
        ) for i in range(2)
    }
    index = SimpleNamespace(size=100_000, parents=parents, degraded=True)
    calls = []

    async def embeddings(queries):
        return [[1.0, 0.0] for _ in queries]

    class Client:
        def search_candidates(self, **kwargs):
            calls.append(kwargs)
            return [
                {"parent_chunk_id": "p0", "score": 0.9},
                {"parent_chunk_id": "p1", "score": 0.8},
            ]

        def search(self, **kwargs):
            raise AssertionError("candidate mode must not use a fixed top-k search")

    monkeypatch.setattr(svc, "get_embeddings", embeddings)
    monkeypatch.setattr(svc, "get_milvus_client", lambda: Client())
    monkeypatch.setattr(
        svc,
        "get_config",
        lambda: SimpleNamespace(milvus=SimpleNamespace(max_results=10, score_ratio=0.85)),
    )

    candidates = await svc.search_vector_db(
        "f1",
        {"query_text": "合同", "top_k": 1, "score_threshold": 0.6},
        index,
        candidate_only=True,
    )

    assert len(candidates) == 2
    assert calls == [{
        "query_vector": [1.0, 0.0],
        "file_id": "f1",
        "score_threshold": 0.6,
        "score_ratio": 0.85,
    }]


@pytest.mark.asyncio
async def test_table_extraction_reranks_all_matches_before_final_limit(monkeypatch):
    tables = [
        SimpleNamespace(
            table_index=index, table_name=name, table_content=f"内容{name}",
            page_num=None, start_pos=None, end_pos=None,
        )
        for index, name in enumerate(("付款方式", "付款条件", "付款记录"))
    ]
    snapshot = SimpleNamespace(tables=tables, page_mapping=[])
    field = SimpleNamespace(
        field_id="table", table_match_type="contains",
        table_match_keywords=["付款"], table_name_pattern="付款明细",
        table_match_max_results=1, search_config={}, use_llm=0,
    )
    calls = []

    async def rerank(query, documents, top_n):
        calls.append((query, documents, top_n))
        return [2]

    monkeypatch.setattr(svc, "rerank_indices", rerank)

    value, _, refs, _ = await svc.extract_table_field("f1", field, snapshot)

    assert calls == [("付款明细", ["付款方式", "付款条件", "付款记录"], 1)]
    assert [item["table_name"] for item in refs["_tables"]] == ["付款记录"]
    assert "付款记录" in value
