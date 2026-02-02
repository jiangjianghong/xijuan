"""分块服务：将文档内容按规则分块。"""

from __future__ import annotations

from typing import Dict, List

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import get_config
from utils.file_utils import generate_chunk_id


async def chunk_content(
    file_id: str,
    content: str,
    tables: List[Dict],
    session: AsyncSession,
) -> List[Dict]:
    """将文档内容分块并存入数据库。

    分块策略：
    1. 识别所有 <table>...</table> 位置
    2. 非表格文本按 chunk_size/overlap 分块
    3. 表格作为独立块，保留完整标签
    4. 表格块前拼接 table_name 作为上下文

    Args:
        file_id: 文件 ID。
        content: 文档 Markdown 全文。
        tables: 表格信息列表（含 table_name）。
        session: 数据库会话。

    Returns:
        分块列表，每项包含 file_id, chunk_id, chunk_index, total_chunks, chunk_content。
    """
    # TODO: 实现分块逻辑
    logger.info("开始分块: {}", file_id)
    return []


async def save_chunks(chunks: List[Dict], session: AsyncSession) -> None:
    """将分块信息批量存入 file_chunk 表。

    Args:
        chunks: 分块列表。
        session: 数据库会话。
    """
    # TODO: 实现批量存储
    pass
