"""文件处理管线编排：串联 parse → chunk → embed → extract → analyze。"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import (
    AnalysisResult,
    ExtractionResult,
    File,
    FileChunk,
    FileContent,
    FileTable,
)
from service.analysis_service import run_analysis
from service.chunk_service import chunk_content, save_chunks
from service.embedding_service import embed_chunks, submit_to_milvus
from service.extraction_service import run_extraction
from service.parse_service import parse_file, parse_tables, save_file_content, save_tables
from utils.milvus_client import MilvusClient


async def run_pipeline(file_id: str, file_path: str, file_content_bytes: bytes, session: AsyncSession) -> None:
    """完整文件处理管线。

    按顺序执行：
    1. MinerU 解析 + 存 md + 表格提取
    2. 分块 + 存数据库
    3. 向量化 + 提交 Milvus
    4. 字段提取
    5. 逻辑分析

    Args:
        file_id: 文件 ID。
        file_path: 文件路径/文件名。
        file_content_bytes: 文件二进制内容。
        session: 数据库会话。
    """
    logger.info("开始处理管线: {}", file_id)

    try:
        # ── 阶段 1: 解析 ──────────────────────────────────────────
        content = await parse_file(file_path, file_content_bytes, file_id, session)
        await save_file_content(file_id, content, session)

        # 表格提取
        tables = parse_tables(content, file_id)
        await save_tables(tables, session)

        # ── 阶段 2: 分块 ──────────────────────────────────────────
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="chunking", start_chunking_time=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()

        try:
            chunks = await chunk_content(file_id, content, tables, session)
            await save_chunks(chunks, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_chunking_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="chunking_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 3: 向量化 ────────────────────────────────────────
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="embedding", start_embedding_time=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()

        try:
            embeddings = await embed_chunks(chunks)
            await submit_to_milvus(chunks, embeddings)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_embedding_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="embedding_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 4: 字段提取 ──────────────────────────────────────
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="extracting")
        )
        await session.execute(stmt)
        await session.commit()

        try:
            await run_extraction(file_id, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_extracting_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="extracting_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 5: 逻辑分析 ──────────────────────────────────────
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="analyzing")
        )
        await session.execute(stmt)
        await session.commit()

        try:
            await run_analysis(file_id, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_analyzing_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="analyzing_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        logger.info("处理管线完成: {}", file_id)

    except Exception as e:
        logger.error("处理管线失败: {}, error={}", file_id, e)
        raise


async def run_from_stage(
    file_id: str, stage: str, session: AsyncSession
) -> None:
    """从指定阶段重新开始处理。

    Args:
        file_id: 文件 ID。
        stage: 起始阶段 (parsing/chunking/embedding/extracting/analyzing)。
        session: 数据库会话。
    """
    logger.info("从 {} 阶段重新开始: {}", stage, file_id)

    milvus_client = MilvusClient()
    milvus_client.connect()

    # 根据阶段清理数据
    if stage == "parsing":
        # 清理所有数据
        await session.execute(delete(FileContent).where(FileContent.file_id == file_id))
        await session.execute(delete(FileTable).where(FileTable.file_id == file_id))
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
        await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除失败: {}", e)
        await session.commit()

    elif stage == "chunking":
        # 清理分块及后续数据
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
        await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除失败: {}", e)
        await session.commit()

    elif stage == "embedding":
        # 清理 Milvus 及后续数据
        await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
        try:
            milvus_client.delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除失败: {}", e)
        await session.commit()

    elif stage == "extracting":
        # 清理提取及分析结果
        await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
        await session.commit()

    elif stage == "analyzing":
        # 仅清理分析结果
        await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
        await session.commit()

    else:
        raise ValueError(f"无效的阶段: {stage}")

    # 获取文件内容（用于后续阶段）
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()

    # 获取表格信息
    stmt = select(FileTable).where(FileTable.file_id == file_id)
    result = await session.execute(stmt)
    tables_orm = result.scalars().all()
    tables = [
        {
            "file_id": t.file_id,
            "table_index": t.table_index,
            "total_table": t.total_table,
            "table_name": t.table_name,
            "table_content": t.table_content,
        }
        for t in tables_orm
    ]

    # 从指定阶段开始执行
    if stage == "parsing":
        # 需要原始文件内容，这里无法重新解析，抛出错误
        raise ValueError("parsing 阶段需要原始文件内容，请使用 /file/parse 重新提交文件")

    if stage in ("chunking",):
        if not file_content:
            raise ValueError("缺少文件内容，无法从 chunking 阶段开始")

        content = file_content.file_content

        # 分块
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="chunking", start_chunking_time=datetime.now(), error=None)
        )
        await session.execute(stmt)
        await session.commit()

        try:
            chunks = await chunk_content(file_id, content, tables, session)
            await save_chunks(chunks, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_chunking_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="chunking_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        stage = "embedding"

    if stage in ("embedding",):
        # 获取分块
        stmt = select(FileChunk).where(FileChunk.file_id == file_id).order_by(FileChunk.chunk_index)
        result = await session.execute(stmt)
        chunks_orm = result.scalars().all()
        chunks = [
            {
                "file_id": c.file_id,
                "chunk_id": c.chunk_id,
                "chunk_index": c.chunk_index,
                "total_chunks": c.total_chunks,
                "chunk_content": c.chunk_content,
            }
            for c in chunks_orm
        ]

        if not chunks:
            raise ValueError("缺少分块数据，无法从 embedding 阶段开始")

        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="embedding", start_embedding_time=datetime.now(), error=None)
        )
        await session.execute(stmt)
        await session.commit()

        try:
            embeddings = await embed_chunks(chunks)
            await submit_to_milvus(chunks, embeddings)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_embedding_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="embedding_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        stage = "extracting"

    if stage in ("extracting",):
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="extracting", error=None)
        )
        await session.execute(stmt)
        await session.commit()

        try:
            await run_extraction(file_id, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_extracting_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="extracting_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        stage = "analyzing"

    if stage in ("analyzing",):
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="analyzing", error=None)
        )
        await session.execute(stmt)
        await session.commit()

        try:
            await run_analysis(file_id, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_analyzing_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="analyzing_failed", error=str(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

    logger.info("从 {} 阶段重新开始完成: {}", stage, file_id)
