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
    DocType,
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

        # 自动补充新列（仅当列不存在时添加）
        migrations = [
            ("extraction_field", "table_match_keywords", "JSON"),
            ("extraction_field", "table_match_max_results", "INT"),
            ("files", "type_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("extraction_field", "type_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("analysis_rule", "type_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("extraction_field", "vl_method", "VARCHAR(32) NULL"),
            ("extraction_field", "vl_config", "JSON NULL"),
            ("extraction_field", "vl_system_prompt", "TEXT NULL"),
            ("extraction_field", "vl_extract_prompt", "TEXT NULL"),
            ("extraction_field", "use_llm", "TINYINT NOT NULL DEFAULT 1"),
            ("doc_type", "is_template", "TINYINT NOT NULL DEFAULT 0"),
            ("doc_type", "parent_type_id", "VARCHAR(64) NULL"),
            ("doc_type", "project_id", "VARCHAR(64) NULL"),
            ("doc_type", "max_parse_pages", "INT NULL"),
            ("doc_type", "enable_embedding", "TINYINT NOT NULL DEFAULT 1"),
            ("analysis_rule", "web_search", "JSON NULL"),
            ("files", "start_extracting_time", "DATETIME NULL"),
            ("files", "start_analyzing_time", "DATETIME NULL"),
            ("analysis_rule", "is_formatted", "TINYINT NOT NULL DEFAULT 0"),
            ("analysis_rule", "output_schema", "JSON NULL"),
        ]
        for table_name, column_name, column_type in migrations:
            result = await conn.execute(
                text(f"SELECT COUNT(*) FROM information_schema.COLUMNS "
                     f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table_name}' "
                     f"AND COLUMN_NAME = '{column_name}'")
            )
            if result.scalar() == 0:
                await conn.execute(
                    text(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_type}")
                )
                logger.info("已为 {} 表添加 {} 列", table_name, column_name)

        # source_type enum 扩展：旧值 ('table','text') → 新值 ('table','text','vl')
        result = await conn.execute(
            text(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'extraction_field' "
                "AND COLUMN_NAME = 'source_type'"
            )
        )
        col_type = (result.scalar() or "").lower()
        if col_type and "'vl'" not in col_type:
            await conn.execute(
                text(
                    "ALTER TABLE `extraction_field` "
                    "MODIFY COLUMN `source_type` ENUM('table','text','vl') NOT NULL"
                )
            )
            logger.info("已扩展 extraction_field.source_type 枚举：加入 'vl'")

        # search_type enum 扩展：旧值 (context,section,rule,chunk_db,vector_db) → 新增 'page'
        result = await conn.execute(
            text(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'extraction_field' "
                "AND COLUMN_NAME = 'search_type'"
            )
        )
        col_type = (result.scalar() or "").lower()
        if col_type and "'page'" not in col_type:
            await conn.execute(
                text(
                    "ALTER TABLE `extraction_field` "
                    "MODIFY COLUMN `search_type` "
                    "ENUM('context','section','rule','chunk_db','vector_db','page') NULL"
                )
            )
            logger.info("已扩展 extraction_field.search_type 枚举：加入 'page'")

        # rule_type enum 扩展：('judge','calc') → 加 'custom'
        result = await conn.execute(
            text(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'analysis_rule' "
                "AND COLUMN_NAME = 'rule_type'"
            )
        )
        col_type = (result.scalar() or "").lower()
        if col_type and "'custom'" not in col_type:
            await conn.execute(
                text(
                    "ALTER TABLE `analysis_rule` "
                    "MODIFY COLUMN `rule_type` ENUM('judge','calc','custom') NOT NULL"
                )
            )
            logger.info("已扩展 analysis_rule.rule_type 枚举：加入 'custom'")

        # result_value 扩容：VARCHAR(500) → TEXT（格式化 JSON 可能超长）
        result = await conn.execute(
            text(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'analysis_result' "
                "AND COLUMN_NAME = 'result_value'"
            )
        )
        data_type = (result.scalar() or "").lower()
        if data_type and data_type != "text":
            await conn.execute(
                text("ALTER TABLE `analysis_result` MODIFY COLUMN `result_value` TEXT")
            )
            logger.info("已将 analysis_result.result_value 扩容为 TEXT")

        # 索引补充：type_id 索引（IF NOT EXISTS 兼容方式）
        index_migrations = [
            ("files", "ix_files_type_id", "type_id"),
            ("extraction_field", "ix_extraction_field_type_id", "type_id"),
            ("analysis_rule", "ix_analysis_rule_type_id", "type_id"),
            ("doc_type", "ix_doc_type_parent_type_id", "parent_type_id"),
            ("doc_type", "ix_doc_type_project_id", "project_id"),
        ]
        for table_name, index_name, columns in index_migrations:
            result = await conn.execute(
                text(f"SELECT COUNT(*) FROM information_schema.STATISTICS "
                     f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table_name}' "
                     f"AND INDEX_NAME = '{index_name}'")
            )
            if result.scalar() == 0:
                await conn.execute(
                    text(f"CREATE INDEX `{index_name}` ON `{table_name}` ({columns})")
                )
                logger.info("已为 {} 表添加索引 {}", table_name, index_name)

        # 回填 default 类型（兼容老数据：旧列可能仍为 NULL/空串）
        await conn.execute(
            text("UPDATE files SET type_id = 'default' WHERE type_id IS NULL OR type_id = ''")
        )
        await conn.execute(
            text("UPDATE extraction_field SET type_id = 'default' WHERE type_id IS NULL OR type_id = ''")
        )
        await conn.execute(
            text("UPDATE analysis_rule SET type_id = 'default' WHERE type_id IS NULL OR type_id = ''")
        )
        await conn.execute(
            text("UPDATE doc_type SET enable_embedding = 1 WHERE enable_embedding IS NULL")
        )

        # 确保默认类型记录存在
        await conn.execute(
            text(
                "INSERT IGNORE INTO doc_type "
                "(type_id, type_name, description, is_default, enabled, enable_embedding) "
                "VALUES ('default', '默认类型', '系统默认文档类型，不可删除', 1, 1, 1)"
            )
        )

    logger.info("数据库表检查完成")


async def recover_abnormal_status(session: AsyncSession) -> None:
    """将所有处理中（*ing）状态恢复为对应的失败状态。

    - parsing → parsing_failed
    - tableing → tableing_failed
    - chunking → chunking_failed
    - embedding → embedding_failed
    - extracting → extracting_failed
    - analyzing → analyzing_failed
    """
    # 先归一化历史状态名，确保后续统一按 tableing/tableing_failed 处理
    legacy_status_mapping = {
        "table_name_validating": "tableing",
        "table_name_validating_failed": "tableing_failed",
    }
    for old_status, new_status in legacy_status_mapping.items():
        stmt = (
            update(File)
            .where(File.progress == old_status)
            .values(progress=new_status)
        )
        result = await session.execute(stmt)
        if result.rowcount > 0:
            logger.info("归一化历史状态 {} -> {}: {} 条记录", old_status, new_status, result.rowcount)

    status_mapping = {
        "parsing": "parsing_failed",
        "tableing": "tableing_failed",
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
    - tableing_failed → 清理 file_table, file_chunk, extraction_result, analysis_result, Milvus
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

    # tableing_failed: 清理 file_table, file_chunk, extraction_result, analysis_result, Milvus
    stmt = select(File.file_id).where(File.progress == "tableing_failed")
    result = await session.execute(stmt)
    tableing_failed_ids = [row[0] for row in result.fetchall()]
    for file_id in tableing_failed_ids:
        await session.execute(delete(FileTable).where(FileTable.file_id == file_id))
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
        await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除 file_id={} 失败: {}", file_id, e)
    if tableing_failed_ids:
        logger.info("清理 tableing_failed 数据: {} 个文件", len(tableing_failed_ids))

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


async def cleanup_orphan_pdfs(session: AsyncSession) -> None:
    """清理 uploads/ 下不在 files 表中的孤儿 PDF。"""
    from utils import vl_client

    storage_dir = vl_client._get_pdf_storage_dir()
    if not storage_dir.exists():
        return

    stmt = select(File.file_id)
    result = await session.execute(stmt)
    valid_ids = {row[0] for row in result.fetchall()}

    removed = 0
    for pdf_file in storage_dir.glob("*.pdf"):
        file_id = pdf_file.stem
        if file_id not in valid_ids:
            try:
                pdf_file.unlink()
                removed += 1
            except OSError as e:
                logger.warning("删除孤儿 PDF 失败 {}: {}", pdf_file, e)
    if removed > 0:
        logger.info("清理孤儿 PDF: {} 个", removed)


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
        await cleanup_orphan_pdfs(session)
        from service.retention_service import enforce_pdf_retention
        await enforce_pdf_retention(session)

    logger.info("服务初始化完成")
