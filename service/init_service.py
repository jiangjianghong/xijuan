"""服务初始化：建表检查、异常状态恢复、垃圾数据清理。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from model.database import get_engine, get_session_factory
from utils.config import get_config
from model.tables import (
    AnalysisResult,
    Base,
    ExtractionResult,
    File,
    FileChunk,
    FileContent,
    FileTable,
)
from utils.milvus_client import MilvusClient


async def ensure_database_exists() -> None:
    """确保数据库存在，如果不存在则创建。"""
    cfg = get_config().mysql
    # 连接到 MySQL 服务器（不指定数据库）
    server_url = f"mysql+aiomysql://{cfg.username}:{cfg.password}@{cfg.host}:{cfg.port}/?charset=utf8mb4"
    temp_engine = create_async_engine(server_url, echo=False)

    async with temp_engine.begin() as conn:
        # 检查数据库是否存在
        result = await conn.execute(text(f"SHOW DATABASES LIKE '{cfg.database}'"))
        exists = result.fetchone() is not None

        if not exists:
            logger.info("数据库 {} 不存在，正在创建...", cfg.database)
            await conn.execute(text(f"CREATE DATABASE `{cfg.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            logger.info("数据库 {} 创建成功", cfg.database)
        else:
            logger.info("数据库 {} 已存在", cfg.database)

    await temp_engine.dispose()


async def init_database() -> None:
    """检查并创建所有数据库表。"""
    # 先确保数据库存在
    await ensure_database_exists()

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
    status_mapping = {
        "parsing": "parsing_failed",
        "chunking": "chunking_failed",
        "embedding": "embedding_failed",
        "extracting": "extracting_failed",
        "analyzing": "analyzing_failed",
    }

    for ing_status, failed_status in status_mapping.items():
        stmt = (
            update(File)
            .where(File.progress == ing_status)
            .values(progress=failed_status, error=f"服务重启时状态恢复：{ing_status} -> {failed_status}")
        )
        result = await session.execute(stmt)
        if result.rowcount > 0:
            logger.info("恢复 {} 状态为 {}: {} 条记录", ing_status, failed_status, result.rowcount)

    await session.commit()
    logger.info("异常状态恢复完成")


async def cleanup_garbage_data(session: AsyncSession) -> None:
    """根据失败状态执行对应的垃圾数据清理。

    - parsing_failed → 清理 file_content, file_table, file_chunk, Milvus
    - chunking_failed → 清理 file_chunk, Milvus
    - embedding_failed → 清理 Milvus 中 file_id 对应记录
    - extracting_failed → 清理 extraction_result 中 file_id 对应记录
    - analyzing_failed → 清理 analysis_result 中 file_id 对应记录
    """
    milvus_client = MilvusClient()
    milvus_client.connect()

    # parsing_failed: 清理 file_content, file_table, file_chunk, Milvus
    stmt = select(File.file_id).where(File.progress == "parsing_failed")
    result = await session.execute(stmt)
    parsing_failed_ids = [row[0] for row in result.fetchall()]
    for file_id in parsing_failed_ids:
        await session.execute(delete(FileContent).where(FileContent.file_id == file_id))
        await session.execute(delete(FileTable).where(FileTable.file_id == file_id))
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除 file_id={} 失败: {}", file_id, e)
    if parsing_failed_ids:
        logger.info("清理 parsing_failed 数据: {} 个文件", len(parsing_failed_ids))

    # chunking_failed: 清理 file_chunk, Milvus
    stmt = select(File.file_id).where(File.progress == "chunking_failed")
    result = await session.execute(stmt)
    chunking_failed_ids = [row[0] for row in result.fetchall()]
    for file_id in chunking_failed_ids:
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除 file_id={} 失败: {}", file_id, e)
    if chunking_failed_ids:
        logger.info("清理 chunking_failed 数据: {} 个文件", len(chunking_failed_ids))

    # embedding_failed: 清理 Milvus
    stmt = select(File.file_id).where(File.progress == "embedding_failed")
    result = await session.execute(stmt)
    embedding_failed_ids = [row[0] for row in result.fetchall()]
    for file_id in embedding_failed_ids:
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除 file_id={} 失败: {}", file_id, e)
    if embedding_failed_ids:
        logger.info("清理 embedding_failed 数据: {} 个文件", len(embedding_failed_ids))

    # extracting_failed: 清理 extraction_result
    stmt = select(File.file_id).where(File.progress == "extracting_failed")
    result = await session.execute(stmt)
    extracting_failed_ids = [row[0] for row in result.fetchall()]
    for file_id in extracting_failed_ids:
        await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
    if extracting_failed_ids:
        logger.info("清理 extracting_failed 数据: {} 个文件", len(extracting_failed_ids))

    # analyzing_failed: 清理 analysis_result
    stmt = select(File.file_id).where(File.progress == "analyzing_failed")
    result = await session.execute(stmt)
    analyzing_failed_ids = [row[0] for row in result.fetchall()]
    for file_id in analyzing_failed_ids:
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
    if analyzing_failed_ids:
        logger.info("清理 analyzing_failed 数据: {} 个文件", len(analyzing_failed_ids))

    await session.commit()
    logger.info("垃圾数据清理完成")


async def run_init() -> None:
    """启动时执行完整初始化流程。"""
    await init_database()

    # 确保 Milvus Collection 存在
    try:
        milvus_client = MilvusClient()
        milvus_client.connect()
        milvus_client.ensure_collection()
        logger.info("Milvus collection 检查完成")
    except Exception as e:
        logger.error("Milvus 初始化失败: {}", e)

    # 执行状态恢复和垃圾清理
    session_factory = get_session_factory()
    async with session_factory() as session:
        await recover_abnormal_status(session)
        await cleanup_garbage_data(session)

    logger.info("服务初始化完成")
