"""search_vector_db 测试：走内存全量打分路径，不连 Milvus。"""

import numpy as np

from service import extraction_service
from service.file_vector_index import FileVectorIndex, l2_normalize


class Parent:
    def __init__(self, chunk_id, content, page_num="1", index=0):
        self.chunk_id = chunk_id
        self.chunk_content = content
        self.chunk_index = index
        self.start_pos = index * 100
        self.end_pos = index * 100 + len(content)
        self.page_num = page_num


def _index(rows, parents):
    """rows: [(sub_id, parent_id, vector)]"""
    matrix = l2_normalize(np.array([r[2] for r in rows], dtype=np.float32))
    return FileVectorIndex(
        file_id="f1",
        sub_ids=tuple(r[0] for r in rows),
        parent_ids=tuple(r[1] for r in rows),
        matrix=matrix,
        parents={p.chunk_id: p for p in parents},
        degraded=False,
    )


def _stub_embeddings(monkeypatch, mapping):
    """按 query 文本返回指定向量。"""
    async def fake(texts, **kwargs):
        return [mapping[t] for t in texts]

    monkeypatch.setattr(extraction_service, "get_embeddings", fake)


async def test_returns_parent_content_not_subchunk(monkeypatch):
    """命中子块后返回的是**父块**文本——检索单元与返回单元解耦的核心。"""
    _stub_embeddings(monkeypatch, {"项目名称": [1.0, 0.0]})
    index = _index(
        [("p1_s0", "p1", [1.0, 0.0])],
        [Parent("p1", "本项目名称为XX污水处理厂，位于城东")],
    )

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": "项目名称"}, index
    )

    assert len(results) == 1
    assert results[0]["chunk_content"] == "本项目名称为XX污水处理厂，位于城东"
    assert results[0]["chunk_id"] == "p1"


async def test_single_string_query_stays_backward_compatible(monkeypatch):
    """存量单串配置归一为单元素列表，占位符标签不变，老 prompt 照常生效。"""
    _stub_embeddings(monkeypatch, {"合同总金额": [1.0, 0.0]})
    index = _index([("p1_s0", "p1", [1.0, 0.0])], [Parent("p1", "总金额壹万元")])

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": " 合同总金额 "}, index
    )

    assert all(r["keyword"] == "合同总金额" for r in results)


async def test_multi_query_each_gets_own_label(monkeypatch):
    """数组多路检索，每路结果挂自己的 keyword 作为独立占位符标签。"""
    _stub_embeddings(monkeypatch, {"项目名称": [1.0, 0.0], "工程名称": [0.0, 1.0]})
    index = _index(
        [("pa_s0", "pa", [1.0, 0.0]), ("pb_s0", "pb", [0.0, 1.0])],
        [Parent("pa", "项目名称段落", index=0), Parent("pb", "工程名称段落", index=1)],
    )

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": ["项目名称", "工程名称"]}, index
    )

    labels = {r["keyword"] for r in results}
    assert labels == {"项目名称", "工程名称"}


async def test_same_parent_hit_by_two_queries_appears_under_each_label(monkeypatch):
    """两路都命中同一父块时，各标签下各出现一次——它们要注入不同占位符。"""
    _stub_embeddings(monkeypatch, {"甲": [1.0, 0.0], "乙": [0.9, 0.436]})
    index = _index([("p1_s0", "p1", [1.0, 0.0])], [Parent("p1", "共同命中的段落")])

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": ["甲", "乙"]}, index
    )

    assert sorted(r["keyword"] for r in results) == ["乙", "甲"]


async def test_empty_query_returns_empty(monkeypatch):
    """空 query 不调 embedding 接口。"""
    async def fail(texts, **kwargs):
        raise AssertionError("空 query 不应调用 embedding")

    monkeypatch.setattr(extraction_service, "get_embeddings", fail)
    index = _index([("p1_s0", "p1", [1.0, 0.0])], [Parent("p1", "内容")])

    assert await extraction_service.search_vector_db("f1", {"query_text": ""}, index) == []
    assert await extraction_service.search_vector_db("f1", {"query_text": []}, index) == []
    assert await extraction_service.search_vector_db("f1", {}, index) == []


async def test_explicit_top_k_is_respected(monkeypatch):
    """显式配了 top_k 就按它截断，尊重存量配置。"""
    _stub_embeddings(monkeypatch, {"查询": [1.0, 0.0]})
    index = _index(
        [(f"p{i}_s0", f"p{i}", [1.0, 0.01 * i]) for i in range(6)],
        [Parent(f"p{i}", f"段落{i}", index=i) for i in range(6)],
    )

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": "查询", "top_k": 2}, index
    )

    assert len(results) == 2


async def test_missing_parent_is_skipped(monkeypatch):
    """Milvus 有向量但父块已从 MySQL 删除时跳过，不产出空文本 ref。"""
    _stub_embeddings(monkeypatch, {"查询": [1.0, 0.0]})
    index = _index([("ghost_s0", "ghost", [1.0, 0.0])], [])

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": "查询"}, index
    )

    assert results == []


async def test_no_index_returns_empty(monkeypatch):
    """快照没装向量（存量文件 / Milvus 挂了）时返回空，不抛异常。"""
    _stub_embeddings(monkeypatch, {"查询": [1.0, 0.0]})

    assert await extraction_service.search_vector_db("f1", {"query_text": "查询"}, None) == []


async def test_result_shape_feeds_build_text_source_refs(monkeypatch):
    """结果形态必须与 _build_text_source_refs 对 vector_db 的期望一致。"""
    _stub_embeddings(monkeypatch, {"查询": [1.0, 0.0]})
    index = _index([("p1_s0", "p1", [1.0, 0.0])], [Parent("p1", "段落文本", page_num="7")])

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": "查询"}, index
    )
    refs, texts = extraction_service._build_text_source_refs("vector_db", results, [])

    assert texts["查询"] == "【第7页】\n段落文本"
    assert refs["查询"][0]["chunk_id"] == "p1"


def test_normalize_query_texts():
    """归一化：单串 / 数组 / None / 含空项 / 重复项。"""
    from service.extraction_service import normalize_query_texts

    assert normalize_query_texts("项目名称") == ["项目名称"]
    assert normalize_query_texts(" 项目名称 ") == ["项目名称"]
    assert normalize_query_texts(None) == []
    assert normalize_query_texts("") == []
    assert normalize_query_texts(["项目名称", "  ", "工程名称"]) == ["项目名称", "工程名称"]
    assert normalize_query_texts(["甲", "甲"]) == ["甲"]
    assert normalize_query_texts([None, 3, "甲"]) == ["甲"]
