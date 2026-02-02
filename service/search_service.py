"""向量检索服务。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


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
    # TODO: 实现向量检索
    logger.info("执行向量检索: query={}, top_k={}", query, top_k)
    return []
