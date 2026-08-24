"""向量化服务测试：打桩 get_embeddings 与 MilvusClient，不打真实接口。"""

import pytest

from service import embedding_service


def _parent(chunk_id, content):
    return {
        "file_id": "f1",
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "total_chunks": 2,
        "chunk_content": content,
        "start_pos": 0,
        "end_pos": len(content),
        "page_num": "1",
    }


async def test_embed_chunks_returns_subchunks_and_vectors(monkeypatch):
    """embed_chunks 返回 (子块, 向量) 二元组，向量数 = 子块数而非父块数。"""
    captured = {}

    async def fake_get_embeddings(texts, **kwargs):
        captured["texts"] = texts
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(embedding_service, "get_embeddings", fake_get_embeddings)

    long_text = "。".join(f"第{i}句内容填充到足够长度用于触发子块切分" for i in range(30))
    sub_chunks, embeddings = await embedding_service.embed_chunks([
        _parent("p1", long_text),
        _parent("p2", "短块"),
    ])

    assert len(sub_chunks) == len(embeddings)
    assert len(sub_chunks) > 2  # p1 被切成多个子块
    # 送去向量化的是子块文本，不是父块文本
    assert captured["texts"] == [s["chunk_content"] for s in sub_chunks]


async def test_embed_chunks_empty_input(monkeypatch):
    """空输入直接返回两个空列表，不调 embedding 接口。"""
    async def fail_get_embeddings(texts, **kwargs):
        raise AssertionError("空输入不应调用 embedding 接口")

    monkeypatch.setattr(embedding_service, "get_embeddings", fail_get_embeddings)

    assert await embedding_service.embed_chunks([]) == ([], [])


async def test_submit_to_milvus_carries_parent_chunk_id(monkeypatch):
    """写入 Milvus 的每条记录都要带 parent_chunk_id。"""
    inserted = {}

    class FakeClient:
        def insert(self, data):
            inserted["data"] = data

    monkeypatch.setattr(embedding_service, "get_milvus_client", lambda: FakeClient())

    sub_chunks = [{
        "chunk_id": "p1_s0", "parent_chunk_id": "p1", "file_id": "f1",
        "chunk_index": 0, "total_chunks": 1, "chunk_content": "子块文本",
        "start_pos": 0, "end_pos": 4, "page_num": "1",
    }]
    await embedding_service.submit_to_milvus(sub_chunks, [[0.1, 0.2]])

    assert inserted["data"][0]["parent_chunk_id"] == "p1"
    assert inserted["data"][0]["chunk_id"] == "p1_s0"
    assert inserted["data"][0]["embedding"] == [0.1, 0.2]


async def test_submit_to_milvus_rejects_length_mismatch(monkeypatch):
    """数量不匹配必须抛错——静默 zip 截断会让向量与文本错位且不可察。"""
    monkeypatch.setattr(embedding_service, "get_milvus_client", lambda: None)

    with pytest.raises(ValueError, match="不匹配"):
        await embedding_service.submit_to_milvus(
            [{"chunk_id": "a"}, {"chunk_id": "b"}], [[0.1]]
        )
