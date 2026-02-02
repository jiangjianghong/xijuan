"""向量化服务：将分块内容向量化并提交到 Milvus。"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from utils.config import get_config


async def embed_chunks(chunks: List[Dict]) -> List[List[float]]:
    """批量将分块内容向量化。

    Args:
        chunks: 分块列表，每项包含 chunk_content。

    Returns:
        与 chunks 等长的向量列表。
    """
    # TODO: 调用 embedding 模型批量向量化
    logger.info("开始向量化，共 {} 个分块", len(chunks))
    return []


async def submit_to_milvus(chunks: List[Dict], embeddings: List[List[float]]) -> None:
    """将分块及向量批量提交到 Milvus。

    Args:
        chunks: 分块列表。
        embeddings: 对应的向量列表。
    """
    # TODO: 实现批量提交 Milvus
    logger.info("提交到 Milvus 完成")
