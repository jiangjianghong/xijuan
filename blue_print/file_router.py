"""文件相关路由：/file/*"""

from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    AnalysisResultItem,
    ExtractionResultItem,
    FileChunkItem,
    FileStatusResponse,
    FileTableItem,
    ResponseWrapper,
)
from model.tables import (
    AnalysisResult,
    ExtractionResult,
    File as FileModel,
    FileChunk,
    FileContent,
    FileTable,
)
from service.pipeline_service import run_from_stage, run_from_stage_stream, run_pipeline, run_pipeline_stream
from utils.config import get_config
from utils.file_utils import generate_file_id
from utils.milvus_client import MilvusClient

router = APIRouter(prefix="/file", tags=["file"])


async def _run_pipeline_background(file_id: str, file_name: str, file_content_bytes: bytes):
    """后台运行 pipeline。"""
    from model.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            await run_pipeline(file_id, file_name, file_content_bytes, session)
        except Exception as e:
            logger.error("Pipeline 后台执行失败: {}", e)


async def _stream_pipeline_generator(file_id: str, file_name: str, file_content_bytes: bytes):
    """流式 pipeline 生成器，内部管理数据库会话。"""
    from model.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        async for event in run_pipeline_stream(file_id, file_name, file_content_bytes, session):
            yield event


@router.post("/parse")
async def parse_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = "async",
    db: AsyncSession = Depends(get_db),
):
    """提交文件解析（支持 sync/async/stream）。"""
    cfg = get_config().mineru

    # 检查文件大小
    file_content_bytes = await file.read()
    if len(file_content_bytes) > cfg.max_file_size:
        return ResponseWrapper(
            code=400,
            message=f"文件大小超过限制 ({cfg.max_file_size // 1024 // 1024}MB)",
        )

    file_name = file.filename or "unknown.pdf"
    file_id = generate_file_id(file_name)

    # 检查文件是否已存在
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    existing_file = result.scalar_one_or_none()

    if existing_file:
        progress = existing_file.progress

        # *ing 状态 → 拒绝重复提交
        if progress in ("parsing", "chunking", "embedding", "extracting", "analyzing"):
            raise HTTPException(
                status_code=409,
                detail=f"文件正在处理中，当前状态: {progress}",
            )

        # complete → 返回已完成
        if progress == "complete":
            return ResponseWrapper(
                message="文件已处理完成",
                data={"file_id": file_id, "progress": progress},
            )

        # *_failed → 清理并从对应阶段重试
        stage_mapping = {
            "parsing_failed": "parsing",
            "chunking_failed": "chunking",
            "embedding_failed": "embedding",
            "extracting_failed": "extracting",
            "analyzing_failed": "analyzing",
        }

        if progress in stage_mapping:
            retry_stage = stage_mapping[progress]

            # 对于 parsing_failed，需要重新解析，所以删除记录重新创建
            if progress == "parsing_failed":
                # 删除旧记录及关联数据
                await db.execute(delete(FileContent).where(FileContent.file_id == file_id))
                await db.execute(delete(FileTable).where(FileTable.file_id == file_id))
                await db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
                await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
                await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
                await db.execute(delete(FileModel).where(FileModel.file_id == file_id))
                await db.commit()

                try:
                    milvus_client = MilvusClient()
                    milvus_client.connect()
                    milvus_client.delete_by_file_id(file_id)
                except Exception as e:
                    logger.warning("Milvus 删除失败: {}", e)

                # 重新创建文件记录
                new_file = FileModel(
                    file_id=file_id,
                    file_name=file_name,
                    file_size=len(file_content_bytes),
                    progress="parsing",
                )
                db.add(new_file)
                await db.commit()

                if mode == "async":
                    background_tasks.add_task(
                        _run_pipeline_background, file_id, file_name, file_content_bytes
                    )
                    return ResponseWrapper(
                        message="文件已提交处理（异步）",
                        data={"file_id": file_id},
                    )
                elif mode == "stream":
                    return StreamingResponse(
                        _stream_pipeline_generator(file_id, file_name, file_content_bytes),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )
                else:
                    await run_pipeline(file_id, file_name, file_content_bytes, db)
                    return ResponseWrapper(
                        message="文件处理完成",
                        data={"file_id": file_id},
                    )
            else:
                # 其他失败状态：从对应阶段重试
                async def _retry_from_stage_background():
                    from model.database import get_session_factory

                    session_factory = get_session_factory()
                    async with session_factory() as session:
                        try:
                            await run_from_stage(file_id, retry_stage, session)
                        except Exception as e:
                            logger.error("从 {} 阶段重试失败: {}", retry_stage, e)

                async def _retry_from_stage_stream_generator():
                    """流式重试生成器，内部管理数据库会话。"""
                    from model.database import get_session_factory

                    session_factory = get_session_factory()
                    async with session_factory() as session:
                        async for event in run_from_stage_stream(file_id, retry_stage, session):
                            yield event

                if mode == "async":
                    background_tasks.add_task(_retry_from_stage_background)
                    return ResponseWrapper(
                        message=f"文件已从 {retry_stage} 阶段重新提交处理（异步）",
                        data={"file_id": file_id},
                    )
                elif mode == "stream":
                    return StreamingResponse(
                        _retry_from_stage_stream_generator(),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )
                else:
                    await run_from_stage(file_id, retry_stage, db)
                    return ResponseWrapper(
                        message="文件处理完成",
                        data={"file_id": file_id},
                    )
    else:
        # 创建新文件记录
        new_file = FileModel(
            file_id=file_id,
            file_name=file_name,
            file_size=len(file_content_bytes),
            progress="parsing",
        )
        db.add(new_file)
        await db.commit()

    if mode == "async":
        background_tasks.add_task(
            _run_pipeline_background, file_id, file_name, file_content_bytes
        )
        return ResponseWrapper(
            message="文件已提交处理（异步）",
            data={"file_id": file_id},
        )
    elif mode == "stream":
        return StreamingResponse(
            _stream_pipeline_generator(file_id, file_name, file_content_bytes),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        await run_pipeline(file_id, file_name, file_content_bytes, db)
        return ResponseWrapper(
            message="文件处理完成",
            data={"file_id": file_id},
        )


@router.get("/{file_id}/status", response_model=ResponseWrapper)
async def get_file_status(file_id: str, db: AsyncSession = Depends(get_db)):
    """查询文件处理进度。"""
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    return ResponseWrapper(
        data=FileStatusResponse(
            file_id=file_record.file_id,
            file_name=file_record.file_name,
            file_size=file_record.file_size,
            progress=file_record.progress,
            error=file_record.error,
            create_time=file_record.create_time,
            updated_at=file_record.updated_at,
        ).model_dump()
    )


@router.delete("/{file_id}", response_model=ResponseWrapper)
async def delete_file(file_id: str, db: AsyncSession = Depends(get_db)):
    """删除文件及所有关联数据。"""
    # 检查文件是否存在
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 删除所有关联数据
    await db.execute(delete(FileContent).where(FileContent.file_id == file_id))
    await db.execute(delete(FileTable).where(FileTable.file_id == file_id))
    await db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
    await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
    await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
    await db.execute(delete(FileModel).where(FileModel.file_id == file_id))
    await db.commit()

    # 删除 Milvus 数据
    try:
        milvus_client = MilvusClient()
        milvus_client.connect()
        milvus_client.delete_by_file_id(file_id)
    except Exception as e:
        logger.warning("Milvus 删除失败: {}", e)

    return ResponseWrapper(message="文件已删除")


@router.post("/{file_id}/retry/{stage}")
async def retry_file(
    file_id: str,
    stage: str,
    background_tasks: BackgroundTasks,
    mode: str = "async",
    db: AsyncSession = Depends(get_db),
):
    """从指定阶段重试（支持 async/stream/sync）。"""
    # 检查文件是否存在
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    valid_stages = ("chunking", "embedding", "extracting", "analyzing")
    if stage not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"无效的阶段: {stage}，有效值: {valid_stages}",
        )

    if mode == "stream":
        async def _retry_stream_generator():
            from model.database import get_session_factory

            session_factory = get_session_factory()
            async with session_factory() as session:
                async for event in run_from_stage_stream(file_id, stage, session):
                    yield event

        return StreamingResponse(
            _retry_stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    elif mode == "sync":
        await run_from_stage(file_id, stage, db)
        return ResponseWrapper(message=f"已从 {stage} 阶段重试完成")
    else:
        # async 模式（默认）
        async def _run_from_stage_background():
            from model.database import get_session_factory

            session_factory = get_session_factory()
            async with session_factory() as session:
                try:
                    await run_from_stage(file_id, stage, session)
                except Exception as e:
                    logger.error("从 {} 阶段重试失败: {}", stage, e)

        background_tasks.add_task(_run_from_stage_background)
        return ResponseWrapper(message=f"已从 {stage} 阶段开始重试")


@router.post("/{file_id}/retry/extracting")
async def retry_extracting(
    file_id: str,
    background_tasks: BackgroundTasks,
    mode: str = "async",
    db: AsyncSession = Depends(get_db),
):
    """重试字段提取（支持 async/stream/sync）。"""
    return await retry_file(file_id, "extracting", background_tasks, mode, db)


@router.post("/{file_id}/retry/analyzing")
async def retry_analyzing(
    file_id: str,
    background_tasks: BackgroundTasks,
    mode: str = "async",
    db: AsyncSession = Depends(get_db),
):
    """重试逻辑分析（支持 async/stream/sync）。"""
    return await retry_file(file_id, "analyzing", background_tasks, mode, db)


@router.get("/{file_id}/tables", response_model=ResponseWrapper)
async def get_file_tables(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件表格列表。"""
    stmt = select(FileTable).where(FileTable.file_id == file_id).order_by(FileTable.table_index)
    result = await db.execute(stmt)
    tables = result.scalars().all()

    return ResponseWrapper(
        data=[
            FileTableItem(
                file_id=t.file_id,
                table_index=t.table_index,
                total_table=t.total_table,
                table_name=t.table_name,
                table_content=t.table_content,
            ).model_dump()
            for t in tables
        ]
    )


@router.get("/{file_id}/chunks", response_model=ResponseWrapper)
async def get_file_chunks(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件分块列表。"""
    stmt = select(FileChunk).where(FileChunk.file_id == file_id).order_by(FileChunk.chunk_index)
    result = await db.execute(stmt)
    chunks = result.scalars().all()

    return ResponseWrapper(
        data=[
            FileChunkItem(
                file_id=c.file_id,
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                total_chunks=c.total_chunks,
                chunk_content=c.chunk_content,
            ).model_dump()
            for c in chunks
        ]
    )


@router.get("/{file_id}/extraction", response_model=ResponseWrapper)
async def get_extraction_results(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件字段提取结果。"""
    stmt = select(ExtractionResult).where(ExtractionResult.file_id == file_id)
    result = await db.execute(stmt)
    extraction_results = result.scalars().all()

    return ResponseWrapper(
        data=[
            ExtractionResultItem(
                file_id=r.file_id,
                field_id=r.field_id,
                extracted_value=r.extracted_value,
                reason=r.reason,
            ).model_dump()
            for r in extraction_results
        ]
    )


@router.get("/{file_id}/analysis", response_model=ResponseWrapper)
async def get_analysis_results(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件逻辑分析结果。"""
    stmt = select(AnalysisResult).where(AnalysisResult.file_id == file_id)
    result = await db.execute(stmt)
    analysis_results = result.scalars().all()

    return ResponseWrapper(
        data=[
            AnalysisResultItem(
                file_id=r.file_id,
                rule_id=r.rule_id,
                result_value=r.result_value,
                input_values=r.input_values,
                reason=r.reason,
            ).model_dump()
            for r in analysis_results
        ]
    )
