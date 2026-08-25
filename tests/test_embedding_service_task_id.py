"""向量化服务的单文件闸门接线测试。"""

from __future__ import annotations

import pytest

from service import embedding_service


@pytest.mark.asyncio
async def test_embed_chunks_passes_file_id_as_task_id(monkeypatch):
    captured = {}

    async def fake_get_embeddings(texts, **kwargs):
        captured.update(kwargs)
        captured["texts"] = texts
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embedding_service, "get_embeddings", fake_get_embeddings)

    chunks = [
        {"chunk_content": "a", "file_id": "file-xyz", "chunk_id": "p1"},
        {"chunk_content": "b", "file_id": "file-xyz", "chunk_id": "p2"},
    ]
    sub_chunks, embeddings = await embedding_service.embed_chunks(chunks)

    assert captured["task_id"] == "file-xyz"
    assert captured["texts"] == ["a", "b"]
    # 两个短父块各自成一个子块，向量数 = 子块数
    assert len(sub_chunks) == 2
    assert len(embeddings) == 2


@pytest.mark.asyncio
async def test_embed_chunks_without_file_id_passes_none(monkeypatch):
    captured = {}

    async def fake_get_embeddings(texts, **kwargs):
        captured.update(kwargs)
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embedding_service, "get_embeddings", fake_get_embeddings)

    await embedding_service.embed_chunks([{"chunk_content": "a", "chunk_id": "p1"}])

    assert captured["task_id"] is None
