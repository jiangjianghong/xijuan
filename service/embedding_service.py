"""向量化服务：将分块内容向量化并提交到 Milvus。"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from utils.config import get_config
from utils.llm_client import get_embeddings
from utils.milvus_client import MilvusClient


async def embed_chunks(chunks: List[Dict]) -> List[List[float]]:
    """批量将分块内容向量化。

    Args:
        chunks: 分块列表，每项包含 chunk_content。

    Returns:
        与 chunks 等长的向量列表。
    """
    if not chunks:
        return []

    logger.info("开始向量化，共 {} 个分块", len(chunks))

    texts = [chunk["chunk_content"] for chunk in chunks]

    cfg = get_config().embedding
    embeddings = await get_embeddings(
        texts=texts,
        base_url=cfg.base_url,
        model=cfg.model_name,
        api_key=cfg.api_key,
        batch_size=cfg.batch_size,
        timeout=cfg.timeout,
        max_retries=cfg.retry_count,
    )

    logger.info("向量化完成，共 {} 个向量", len(embeddings))
    return embeddings


async def submit_to_milvus(chunks: List[Dict], embeddings: List[List[float]]) -> None:
    """将分块及向量批量提交到 Milvus。

    Args:
        chunks: 分块列表。
        embeddings: 对应的向量列表。
    """
    if not chunks or not embeddings:
        return

    if len(chunks) != len(embeddings):
        raise ValueError(f"chunks 数量 ({len(chunks)}) 与 embeddings 数量 ({len(embeddings)}) 不匹配")

    # 合并 chunks + embeddings
    data: List[Dict[str, Any]] = []
    for chunk, embedding in zip(chunks, embeddings):
        data.append({
            "chunk_id": chunk["chunk_id"],
            "file_id": chunk["file_id"],
            "chunk_index": chunk["chunk_index"],
            "total_chunks": chunk["total_chunks"],
            "chunk_content": chunk["chunk_content"],
            "embedding": embedding,
        })

    # 批量插入 Milvus
    milvus_client = MilvusClient()
    milvus_client.connect()
    milvus_client.ensure_collection()
    milvus_client.insert(data)

    logger.info("提交到 Milvus 完成，共 {} 条记录", len(data))
