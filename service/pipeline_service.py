"""文件处理管线编排：串联 parse → chunk → embed → extract → analyze。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


async def run_pipeline(file_id: str, file_path: str, session: AsyncSession) -> None:
    """完整文件处理管线。

    按顺序执行：
    1. MinerU 解析 + 存 md + 表格提取
    2. 分块 + 存数据库
    3. 向量化 + 提交 Milvus
    4. 字段提取
    5. 逻辑分析

    Args:
        file_id: 文件 ID。
        file_path: 文件路径。
        session: 数据库会话。
    """
    # TODO: 串联各阶段服务
    logger.info("开始处理管线: {}", file_id)


async def run_from_stage(
    file_id: str, stage: str, session: AsyncSession
) -> None:
    """从指定阶段重新开始处理。

    Args:
        file_id: 文件 ID。
        stage: 起始阶段 (parsing/chunking/embedding/extracting/analyzing)。
        session: 数据库会话。
    """
    # TODO: 实现从指定阶段恢复
    logger.info("从 {} 阶段重新开始: {}", stage, file_id)
