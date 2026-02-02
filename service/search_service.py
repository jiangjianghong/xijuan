"""向量检索服务。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from utils.llm_client import get_embeddings
from utils.milvus_client import MilvusClient


async def search(
    query: str,
    top_k: int = 10,
    file_id: Optional[str] = None,
    score_threshold: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """向量检索：将 query 向量化后在 Milvus 中检索。

    Args:
        query: 查询文本。
        top_k: 返回条数。
        file_id: 限定文件 ID。
        score_threshold: 分数阈值。

    Returns:
        检索结果列表。
    """
    logger.info("执行向量检索: query={}, top_k={}", query, top_k)

    if not query.strip():
        return []

    # 向量化查询文本
    embeddings = await get_embeddings([query])
    if not embeddings:
        logger.warning("查询文本向量化失败")
        return []

    query_vector = embeddings[0]

    # Milvus 检索
    milvus_client = MilvusClient()
    milvus_client.connect()
    milvus_client.ensure_collection()

    results = milvus_client.search(
        query_vector=query_vector,
        top_k=top_k,
        file_id=file_id,
        score_threshold=score_threshold,
    )

    logger.info("向量检索完成，返回 {} 条结果", len(results))
    return results
