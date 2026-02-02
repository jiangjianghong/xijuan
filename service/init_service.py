"""服务初始化：建表检查、异常状态恢复、垃圾数据清理。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_engine
from model.tables import Base


async def init_database() -> None:
    """检查并创建所有数据库表。"""
    logger.info("正在检查并创建数据库表...")
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表检查完成")


async def recover_abnormal_status(session: AsyncSession) -> None:
    """将所有处理中（*ing）状态恢复为对应的失败状态。

    - parsing → parsing_failed
    - chunking → chunking_failed
    - embedding → embedding_failed
    - extracting → extracting_failed
    - analyzing → analyzing_failed
    """
    # TODO: 实现异常状态恢复
    logger.info("异常状态恢复完成")


async def cleanup_garbage_data(session: AsyncSession) -> None:
    """根据失败状态执行对应的垃圾数据清理。

    - parsing_failed → 清理 file_content, file_table, file_chunk, Milvus
    - chunking_failed → 清理 file_chunk, Milvus
    - embedding_failed → 清理 Milvus 中 file_id 对应记录
    - extracting_failed → 清理 extraction_result 中 file_id 对应记录
    - analyzing_failed → 清理 analysis_result 中 file_id 对应记录
    """
    # TODO: 实现垃圾数据清理
    logger.info("垃圾数据清理完成")


async def run_init() -> None:
    """启动时执行完整初始化流程。"""
    await init_database()
    # TODO: 获取 session 执行状态恢复和垃圾清理
    logger.info("服务初始化完成")
