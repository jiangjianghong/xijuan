"""向量化服务：将分块切成子块、向量化并提交到 Milvus。

**向量化的是子块，不是父块**——父块（512 字）留在 file_chunk 表当 LLM 的
返回单元，子块（128 字）只进 Milvus 当匹配单元。父块不 embed。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from loguru import logger

from service.subchunk_service import split_into_subchunks
from utils.config import get_config
from utils.llm_client import get_embeddings
from utils.milvus_client import get_milvus_client


async def embed_chunks(chunks: List[Dict]) -> Tuple[List[Dict], List[List[float]]]:
    """把父块切成子块并批量向量化。

    Args:
        chunks: 父块列表，每项含 chunk_content。

    Returns:
        (子块列表, 向量列表) 二元组，两者等长且顺序一致。
        返回子块而非只返回向量，是因为子块数 != 父块数，调用方必须拿到
        子块才能正确写 Milvus。
    """
    if not chunks:
        return [], []

    sub_chunks = split_into_subchunks(chunks)
    if not sub_chunks:
        logger.warning("父块 {} 个但切不出任何子块（内容全空）", len(chunks))
        return [], []

    logger.info("开始向量化：{} 个父块 -> {} 个子块", len(chunks), len(sub_chunks))

    texts = [sub["chunk_content"] for sub in sub_chunks]

    cfg = get_config().embedding
    embeddings = await get_embeddings(
        texts=texts,
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        batch_size=cfg.batch_size,
        timeout=cfg.timeout,
        max_retries=cfg.retry_count,
        task_id=sub_chunks[0].get("file_id"),
    )

    logger.info("向量化完成，共 {} 个向量", len(embeddings))
    return sub_chunks, embeddings


async def submit_to_milvus(
    sub_chunks: List[Dict], embeddings: List[List[float]]
) -> None:
    """将子块及向量批量提交到 Milvus。

    Args:
        sub_chunks: 子块列表（embed_chunks 的第一个返回值）。
        embeddings: 对应的向量列表。
    """
    if not sub_chunks or not embeddings:
        return

    if len(sub_chunks) != len(embeddings):
        raise ValueError(
            f"子块数量 ({len(sub_chunks)}) 与 embeddings 数量 ({len(embeddings)}) 不匹配"
        )

    data: List[Dict[str, Any]] = []
    for sub, embedding in zip(sub_chunks, embeddings):
        data.append({
            "chunk_id": sub["chunk_id"],
            "parent_chunk_id": sub["parent_chunk_id"],
            "file_id": sub["file_id"],
            "chunk_index": sub["chunk_index"],
            "total_chunks": sub["total_chunks"],
            "chunk_content": sub["chunk_content"],
            "start_pos": sub.get("start_pos", 0),
            "end_pos": sub.get("end_pos", 0),
            "page_num": sub.get("page_num", ""),
            "embedding": embedding,
        })

    # 复用进程级单例：原先每次 MilvusClient()+connect()+ensure_collection()
    # 会重建连接并重新 load()（backlog H6）
    get_milvus_client().insert(data)

    logger.info("提交到 Milvus 完成，共 {} 条记录", len(data))
