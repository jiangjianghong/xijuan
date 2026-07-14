"""文件相关路由：/file/*"""

from __future__ import annotations

import asyncio
import re
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from logs import log_context
from model.database import get_db
from model.schemas import (
    AnalysisResultItem,
    BatchDeleteRequest,
    BatchDeleteResponse,
    ExtractionResultItem,
    FileChunkItem,
    FileContextQueryRequest,
    FileContextQueryResponse,
    FileDetailResponse,
    FileListItem,
    FileListResponse,
    FileStatusResponse,
    FileTableItem,
    ResponseWrapper,
)
from model.tables import (
    AnalysisResult,
    AnalysisRule,
    ExtractionField,
    ExtractionResult,
    File as FileModel,
    FileChunk,
    FileContent,
    FileTable,
)
from service.file_context_service import query_file_context
from service.pipeline_service import run_from_stage, run_from_stage_stream, run_pipeline, run_pipeline_stream
from service.extraction_service import parse_sections, split_md_by_pages
from utils.config import get_config
from utils.file_utils import generate_file_id
from utils.milvus_client import get_milvus_client

router = APIRouter(prefix="/file", tags=["file"])


# ─────────────────────────────────────────────────────────────
# 静态路由（必须放在 {file_id} 动态路由之前）
# ─────────────────────────────────────────────────────────────

@router.get("/list", response_model=ResponseWrapper)
async def list_files(
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    type_id: str = "",
    db: AsyncSession = Depends(get_db),
):
    """分页查询文件列表。可选按 type_id 过滤。"""
    # 构建基础查询
    base_query = select(FileModel)
    count_query = select(func.count(FileModel.file_id))

    # 状态筛选
    if status:
        base_query = base_query.where(FileModel.progress == status)
        count_query = count_query.where(FileModel.progress == status)

    # 类型筛选
    if type_id:
        base_query = base_query.where(FileModel.type_id == type_id)
        count_query = count_query.where(FileModel.type_id == type_id)

    # 查询总数
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页查询
    offset = (page - 1) * page_size
    stmt = base_query.order_by(FileModel.create_time.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    files = result.scalars().all()

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return ResponseWrapper(
        data=FileListResponse(
            items=[
                FileListItem(
                    file_id=f.file_id,
                    file_name=f.file_name,
                    file_size=f.file_size,
                    progress=f.progress,
                    type_id=f.type_id or "default",
                    error=f.error,
                    create_time=f.create_time,
                )
                for f in files
            ],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        ).model_dump()
    )


@router.post("/context_query", response_model=ResponseWrapper)
async def query_context(
    request: FileContextQueryRequest,
    db: AsyncSession = Depends(get_db),
):
    """按请求体 file_id + 文本片段查询上下文、页码和全部分块。"""
    data = await query_file_context(request, db)
    return ResponseWrapper(data=FileContextQueryResponse(**data).model_dump())


@router.delete("/batch", response_model=ResponseWrapper)
async def batch_delete_files(
    request: BatchDeleteRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """批量删除文件及关联数据。

    MySQL 关联表同步删完并一次性提交;每个被成功删除的 file_id 都
    调度一次后台 `_cleanup_file_artifacts` 处理 Milvus + PDF。
    """
    deleted_count = 0
    failed_ids: list[str] = []
    cleanup_items: list[tuple[str, str]] = []

    for file_id in request.file_ids:
        try:
            stmt = select(FileModel).where(FileModel.file_id == file_id)
            result = await db.execute(stmt)
            file_record = result.scalar_one_or_none()

            if not file_record:
                failed_ids.append(file_id)
                continue

            await db.execute(delete(FileContent).where(FileContent.file_id == file_id))
            await db.execute(delete(FileTable).where(FileTable.file_id == file_id))
            await db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
            await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
            await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
            await db.execute(delete(FileModel).where(FileModel.file_id == file_id))

            cleanup_items.append((file_id, file_record.type_id or "default"))
            deleted_count += 1
        except Exception as e:
            with log_context(file_id=file_id):
                logger.error("删除文件失败 (file_id={}): {}", file_id, e)
            failed_ids.append(file_id)

    await db.commit()

    for fid, item_type_id in cleanup_items:
        background_tasks.add_task(_cleanup_file_artifacts, fid, item_type_id)

    return ResponseWrapper(
        data=BatchDeleteResponse(
            deleted_count=deleted_count,
            failed_ids=failed_ids,
        ).model_dump()
    )


# ─────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────


async def _run_pipeline_background(
    file_id: str,
    file_name: str,
    file_content_bytes: bytes,
    callback_url: str | None = None,
    type_id: str = "default",
):
    """后台运行 pipeline。"""
    from model.database import get_session_factory

    with log_context(file_id=file_id, type_id=type_id):
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                await run_pipeline(file_id, file_name, file_content_bytes, session, callback_url=callback_url)
            except Exception as e:
                logger.exception("Pipeline 后台执行失败: type={}, repr={}", type(e).__name__, repr(e))


async def _stream_pipeline_generator(
    file_id: str,
    file_name: str,
    file_content_bytes: bytes,
    type_id: str = "default",
):
    """流式 pipeline 生成器，内部管理数据库会话。"""
    from model.database import get_session_factory

    with log_context(file_id=file_id, type_id=type_id):
        session_factory = get_session_factory()
        async with session_factory() as session:
            async for event in run_pipeline_stream(file_id, file_name, file_content_bytes, session):
                yield event


@router.post("/parse")
async def parse_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = "async",
    type_id: str = "default",
    callback_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """提交文件解析（支持 sync/async/stream）。

    - type_id：归属的文档类型（默认 'default'，通过 query 参数传入）
    - 当 mode=async 时，可传入 callback_url 参数，管线每完成一个阶段会向该地址 POST：
      {"file_id": "...", "status": "parsing/tableing/chunking/embedding/extracting/analyzing/complete"}
    """
    cfg = get_config().mineru

    # 检查文件大小
    file_content_bytes = await file.read()
    if len(file_content_bytes) > cfg.max_file_size:
        return ResponseWrapper(
            code=400,
            message=f"文件大小超过限制 ({cfg.max_file_size // 1024 // 1024}MB)",
        )

    file_name = file.filename or "unknown.pdf"
    type_id = (type_id or "default").strip() or "default"
    file_id = generate_file_id(type_id, file_name)

    # 持久化原始 PDF 字节，VL 抽取依赖（写盘失败不阻断主流程）
    with log_context(file_id=file_id, type_id=type_id):
        try:
            from utils import vl_client as _vl_client_for_storage
            pdf_target = _vl_client_for_storage.pdf_path(file_id)
            pdf_target.parent.mkdir(parents=True, exist_ok=True)
            pdf_target.write_bytes(file_content_bytes)
        except Exception as e:
            logger.warning("写盘 PDF 失败（不阻断 pipeline）: file_id={} error={}", file_id, e)

    # file_id 已带纳秒时间戳，每次上传都是全新记录，直接建档
    new_file = FileModel(
        file_id=file_id,
        type_id=type_id,
        file_name=file_name,
        file_size=len(file_content_bytes),
        progress="parsing",
    )
    db.add(new_file)
    await db.commit()

    if mode == "async":
        background_tasks.add_task(
            _run_pipeline_background, file_id, file_name, file_content_bytes, callback_url, type_id
        )
        return ResponseWrapper(
            message="文件已提交处理（异步）",
            data={"file_id": file_id},
        )
    elif mode == "stream":
        return StreamingResponse(
            _stream_pipeline_generator(file_id, file_name, file_content_bytes, type_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        with log_context(file_id=file_id, type_id=type_id):
            await run_pipeline(file_id, file_name, file_content_bytes, db, callback_url=callback_url)
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
            type_id=file_record.type_id or "default",
            error=file_record.error,
            create_time=file_record.create_time,
            updated_at=file_record.updated_at,
        ).model_dump()
    )


@router.get("/{file_id}/pdf")
async def get_file_pdf(file_id: str):
    """下发原始 PDF（uploads/{file_id}.pdf 原始字节），供前端定位预览。

    历史文件可能未持久化 PDF（vl 持久化机制上线前上传），此时 404。
    """
    from utils import vl_client

    # file_id 为 SHA256 hex；白名单校验防止路径穿越（Windows 下 ..%5C 可逃出存储目录）
    if not re.fullmatch(r"[\w-]+", file_id):
        raise HTTPException(status_code=404, detail="原始 PDF 不存在")

    pdf = vl_client.pdf_path(file_id)
    if not pdf.is_file():
        raise HTTPException(status_code=404, detail="原始 PDF 不存在")
    return FileResponse(
        pdf,
        media_type="application/pdf",
        filename=f"{file_id}.pdf",
        content_disposition_type="inline",
    )


def _cleanup_file_artifacts(file_id: str, type_id: str = "default") -> None:
    """删除接口的后台清理:Milvus 向量 + 持久化 PDF。

    设计为同步函数,通过 BackgroundTasks 调度时 Starlette 会用
    anyio.run_in_threadpool 执行,所以不会阻塞 asyncio 事件循环。
    任何异常都吞掉只记日志——清理失败不影响用户已收到的删除成功响应。
    """
    with log_context(file_id=file_id, type_id=type_id):
        try:
            get_milvus_client().delete_by_file_id(file_id)
        except Exception as e:
            logger.warning("Milvus 删除失败 file_id={}: {}", file_id, e)

        try:
            from utils import vl_client as _vl_client_for_storage
            _vl_client_for_storage.pdf_path(file_id).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("清理 PDF 失败 file_id={}: {}", file_id, e)


@router.delete("/{file_id}", response_model=ResponseWrapper)
async def delete_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """删除文件及所有关联数据。

    MySQL 关联表立刻删完并提交;Milvus 向量与持久化 PDF 走后台
    清理,接口立即返回——前端列表轮询不会被 Milvus 调用阻塞。
    """
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    await db.execute(delete(FileContent).where(FileContent.file_id == file_id))
    await db.execute(delete(FileTable).where(FileTable.file_id == file_id))
    await db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
    await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
    await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
    await db.execute(delete(FileModel).where(FileModel.file_id == file_id))
    await db.commit()

    background_tasks.add_task(_cleanup_file_artifacts, file_id, file_record.type_id or "default")

    return ResponseWrapper(message="文件已删除")


@router.post("/{file_id}/retry/{stage}")
async def retry_file(
    file_id: str,
    stage: str,
    background_tasks: BackgroundTasks,
    mode: str = "async",
    callback_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """从指定阶段重试（支持 async/stream/sync）。

    当 mode=async 时，可传入 callback_url 参数，管线每完成一个阶段会向该地址 POST：
    {"file_id": "...", "status": "parsing/tableing/chunking/embedding/extracting/analyzing/complete"}
    """
    # 检查文件是否存在
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    type_id = file_record.type_id or "default"

    stage_aliases = {
        "table_name_validating": "tableing",
    }
    stage = stage_aliases.get(stage, stage)

    valid_stages = ("tableing", "chunking", "embedding", "extracting", "analyzing")
    if stage not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"无效的阶段: {stage}，有效值: {valid_stages}",
        )

    if mode == "stream":
        async def _retry_stream_generator():
            from model.database import get_session_factory

            with log_context(file_id=file_id, type_id=type_id):
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
        with log_context(file_id=file_id, type_id=type_id):
            await run_from_stage(file_id, stage, db, callback_url=callback_url)
        return ResponseWrapper(message=f"已从 {stage} 阶段重试完成")
    else:
        # async 模式（默认）
        async def _run_from_stage_background():
            from model.database import get_session_factory

            with log_context(file_id=file_id, type_id=type_id):
                session_factory = get_session_factory()
                async with session_factory() as session:
                    try:
                        await run_from_stage(file_id, stage, session, callback_url=callback_url)
                    except Exception as e:
                        logger.exception(
                            "从 {} 阶段重试失败: type={}, repr={}",
                            stage,
                            type(e).__name__,
                            repr(e),
                        )

        background_tasks.add_task(_run_from_stage_background)
        return ResponseWrapper(message=f"已从 {stage} 阶段开始重试")


@router.post("/{file_id}/retry/extracting")
async def retry_extracting(
    file_id: str,
    background_tasks: BackgroundTasks,
    mode: str = "async",
    callback_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """重试字段提取（支持 async/stream/sync）。"""
    return await retry_file(file_id, "extracting", background_tasks, mode, callback_url, db)


@router.post("/{file_id}/retry/analyzing")
async def retry_analyzing(
    file_id: str,
    background_tasks: BackgroundTasks,
    mode: str = "async",
    callback_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """重试逻辑分析（支持 async/stream/sync）。"""
    return await retry_file(file_id, "analyzing", background_tasks, mode, callback_url, db)


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
                page_num=t.page_num or "",
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
                page_num=c.page_num or "",
            ).model_dump()
            for c in chunks
        ]
    )


@router.get("/{file_id}/outline", response_model=ResponseWrapper)
async def get_file_outline(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件大纲(基于 parse_sections 正则解析的章节列表,含切片后的正文)。

    与抽取阶段 search_section 使用的章节口径完全一致 —
    前端看到什么 = 抽取时能匹配到什么。文件不存在或内容为空 → 返回空列表(非 404),
    与 /tables、/chunks 行为一致。
    """
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await db.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content or not file_content.file_content:
        return ResponseWrapper(data=[])

    content = file_content.file_content
    sections = parse_sections(content)

    return ResponseWrapper(
        data=[
            {
                "index": s.index,
                "number": s.number,
                "title": s.title,
                "level": s.level,
                "numbered": s.numbered,
                "content": content[s.start_pos:s.end_pos],          # 自身正文（平铺）
                "tree_content": content[s.start_pos:s.tree_end_pos],  # 含子树
                "start_pos": s.start_pos,
                "end_pos": s.end_pos,
                "tree_end_pos": s.tree_end_pos,
            }
            for s in sections
        ]
    )


@router.get("/{file_id}/content", response_model=ResponseWrapper)
async def get_file_content_by_page(file_id: str, db: AsyncSession = Depends(get_db)):
    """按页返回文件 markdown 内容：[{"page_num": 1, "content": "..."}, ...]。

    基于 parsing 阶段落库的 page_mapping 逐页切分整篇 markdown，页码升序。
    文件不存在/内容为空/无 page_mapping（存量老文件重解析前无 bbox 亦无逐页锚点）
    → 返回空列表（非 404），与 /tables、/chunks、/outline 行为一致。
    """
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await db.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content or not file_content.file_content:
        return ResponseWrapper(data=[])

    pages = split_md_by_pages(file_content.file_content, file_content.page_mapping or [])

    return ResponseWrapper(data=pages)


@router.get("/{file_id}/extraction", response_model=ResponseWrapper)
async def get_extraction_results(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件字段提取结果（含字段名称）。"""
    stmt = (
        select(ExtractionResult, ExtractionField.field_name)
        .outerjoin(
            ExtractionField,
            ExtractionResult.field_id == ExtractionField.field_id,
        )
        .where(ExtractionResult.file_id == file_id)
        .order_by(ExtractionField.priority)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return ResponseWrapper(
        data=[
            ExtractionResultItem(
                file_id=r.file_id,
                field_id=r.field_id,
                field_name=field_name,
                extracted_value=r.extracted_value,
                reason=r.reason,
                source_refs=r.source_refs,
            ).model_dump()
            for r, field_name in rows
        ]
    )


@router.get("/{file_id}/analysis", response_model=ResponseWrapper)
async def get_analysis_results(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件逻辑分析结果（含规则名称）。"""
    stmt = (
        select(AnalysisResult, AnalysisRule.rule_name)
        .outerjoin(
            AnalysisRule,
            AnalysisResult.rule_id == AnalysisRule.rule_id,
        )
        .where(AnalysisResult.file_id == file_id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return ResponseWrapper(
        data=[
            AnalysisResultItem(
                file_id=r.file_id,
                rule_id=r.rule_id,
                rule_name=rule_name,
                result_value=r.result_value,
                input_values=r.input_values,
                reason=r.reason,
                source_refs=r.source_refs,
            ).model_dump()
            for r, rule_name in rows
        ]
    )


@router.get("/{file_id}/detail", response_model=ResponseWrapper)
async def get_file_detail(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件完整详情（含所有时间字段）。"""
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    return ResponseWrapper(
        data=FileDetailResponse(
            file_id=file_record.file_id,
            file_name=file_record.file_name,
            file_size=file_record.file_size,
            progress=file_record.progress,
            type_id=file_record.type_id or "default",
            error=file_record.error,
            create_time=file_record.create_time,
            updated_at=file_record.updated_at,
            start_parsing_time=file_record.start_parsing_time,
            end_parsing_time=file_record.end_parsing_time,
            start_tableing_time=file_record.start_tableing_time,
            end_tableing_time=file_record.end_tableing_time,
            start_chunking_time=file_record.start_chunking_time,
            end_chunking_time=file_record.end_chunking_time,
            start_embedding_time=file_record.start_embedding_time,
            end_embedding_time=file_record.end_embedding_time,
            start_extracting_time=file_record.start_extracting_time,
            end_extracting_time=file_record.end_extracting_time,
            start_analyzing_time=file_record.start_analyzing_time,
            end_analyzing_time=file_record.end_analyzing_time,
        ).model_dump()
    )
