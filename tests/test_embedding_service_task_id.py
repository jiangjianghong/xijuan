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
        {"chunk_content": "a", "file_id": "file-xyz"},
        {"chunk_content": "b", "file_id": "file-xyz"},
    ]
    result = await embedding_service.embed_chunks(chunks)

    assert captured["task_id"] == "file-xyz"
    assert captured["texts"] == ["a", "b"]
    assert len(result) == 2


@pytest.mark.asyncio
async def test_embed_chunks_without_file_id_passes_none(monkeypatch):
    captured = {}

    async def fake_get_embeddings(texts, **kwargs):
        captured.update(kwargs)
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embedding_service, "get_embeddings", fake_get_embeddings)

    await embedding_service.embed_chunks([{"chunk_content": "a"}])

    assert captured["task_id"] is None
