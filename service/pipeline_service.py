"""文件处理管线编排：串联 parse → chunk → embed → extract → analyze。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

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
from service.analysis_service import run_analysis, run_analysis_stream
from service.chunk_service import chunk_content, save_chunks
from service.embedding_service import embed_chunks, submit_to_milvus
from service.extraction_service import run_extraction, run_extraction_stream
from service.parse_service import parse_file, save_file_content
from service.table_service import parse_tables, save_tables
from service.type_config_service import get_file_type_runtime_config
from utils.callback import notify_callback
from utils.errors import format_exception as _format_exception
from utils.milvus_client import MilvusClient
from utils.page_mapping import build_page_mapping

EMBEDDING_DISABLED_MESSAGE = "配置已关闭此文件的向量化，直接进行下一步"


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_pipeline_stream(
    file_id: str, file_path: str, file_content_bytes: bytes, session: AsyncSession
) -> AsyncGenerator[str, None]:
    """流式文件处理管线，每个阶段完成后 yield SSE 事件。

    事件类型：
    - parsing: MinerU 解析完成
    - content_saved: MD 存储完成
    - md_content: MD 文档内容（包含完整 MD 文本）
    - tableing_start: AI 校验表格名称开始
    - tableing: AI 校验表格名称完成
    - chunking: 分块完成
    - chunks_saved: 分块存储完成
    - embedding: 向量化完成
    - milvus_submitted: Milvus 提交完成
    - tasks_loaded: 获取提取/分析任务完成
    - field_extracted: 单个字段提取完成（每个字段独立发送）
    - extraction: 全部关键词提取完成
    - rule_analyzed: 单条规则分析完成（每条规则独立发送）
    - analysis: 全部逻辑分析完成
    - complete: 全部完成
    - error: 发生错误
    """
    logger.info("开始流式处理管线: {}", file_id)

    try:
        # ── 阶段 1: MinerU 解析 ──────────────────────────────────────
        yield _sse_event("parsing_start", {
            "file_id": file_id,
            "stage": "parsing_start",
            "message": "开始 MinerU 解析",
        })

        content, middle_json_str = await parse_file(file_path, file_content_bytes, file_id, session)
        page_mapping = build_page_mapping(content, middle_json_str) if middle_json_str else []
        yield _sse_event("parsing", {
            "file_id": file_id,
            "stage": "parsing",
            "message": "MinerU 解析完成",
            "content_length": len(content),
        })

        # 存储 MD 到数据库
        await save_file_content(file_id, content, session, middle_json=middle_json_str, page_mapping=page_mapping)
        yield _sse_event("content_saved", {
            "file_id": file_id,
            "stage": "content_saved",
            "message": "MD 内容已存储",
        })

        # 发送 MD 文档内容事件
        yield _sse_event("md_content", {
            "file_id": file_id,
            "stage": "md_content",
            "message": "MD 文档内容",
            "content": content,
        })

        # ── 阶段 2: AI 校验表格名称 ───────────────────────────────
        yield _sse_event("tableing_start", {
            "file_id": file_id,
            "stage": "tableing_start",
            "message": "开始 AI 校验表格名称",
        })

        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="tableing", start_tableing_time=datetime.now(), error=None)
        )
        await session.execute(stmt)
        await session.commit()

        try:
            tables = await parse_tables(content, file_id, page_mapping=page_mapping)
            await save_tables(tables, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_tableing_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()

            yield _sse_event("tableing", {
                "file_id": file_id,
                "stage": "tableing",
                "message": "AI 校验表格名称完成",
                "table_count": len(tables),
            })
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="tableing_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 3: 分块 ──────────────────────────────────────────
        yield _sse_event("chunking_start", {
            "file_id": file_id,
            "stage": "chunking_start",
            "message": "开始分块",
        })

        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="chunking", start_chunking_time=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()

        try:
            chunks = await chunk_content(file_id, content, tables, session, page_mapping=page_mapping)
            yield _sse_event("chunking", {
                "file_id": file_id,
                "stage": "chunking",
                "message": "分块完成",
                "chunk_count": len(chunks),
            })

            yield _sse_event("chunks_saving", {
                "file_id": file_id,
                "stage": "chunks_saving",
                "message": "开始存储分块到数据库",
            })

            await save_chunks(chunks, session)
            yield _sse_event("chunks_saved", {
                "file_id": file_id,
                "stage": "chunks_saved",
                "message": "分块已存储到数据库",
            })

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
                .values(progress="chunking_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 4: 向量化 ────────────────────────────────────────
        yield _sse_event("embedding_start", {
            "file_id": file_id,
            "stage": "embedding_start",
            "message": "开始向量化",
        })

        type_cfg = await get_file_type_runtime_config(file_id, session)
        embedding_started_at = datetime.now()
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(
                progress="embedding",
                start_embedding_time=embedding_started_at,
                error=None,
            )
        )
        await session.execute(stmt)
        await session.commit()

        if not type_cfg.enable_embedding:
            logger.info(
                "{}: file_id={}, type_id={}",
                EMBEDDING_DISABLED_MESSAGE,
                file_id,
                type_cfg.type_id,
            )
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_embedding_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
            yield _sse_event("embedding_skipped", {
                "file_id": file_id,
                "stage": "embedding_skipped",
                "message": EMBEDDING_DISABLED_MESSAGE,
            })
        else:
            try:
                embeddings = await embed_chunks(chunks)
                yield _sse_event("embedding", {
                    "file_id": file_id,
                    "stage": "embedding",
                    "message": "向量化完成",
                    "embedding_count": len(embeddings),
                })

                yield _sse_event("milvus_submitting", {
                    "file_id": file_id,
                    "stage": "milvus_submitting",
                    "message": "开始提交向量到 Milvus",
                })

                await submit_to_milvus(chunks, embeddings)
                yield _sse_event("milvus_submitted", {
                    "file_id": file_id,
                    "stage": "milvus_submitted",
                    "message": "向量已提交到 Milvus",
                })

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
                    .values(progress="embedding_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

        # ── 阶段 5: 字段提取 ──────────────────────────────────────
        yield _sse_event("tasks_loading", {
            "file_id": file_id,
            "stage": "tasks_loading",
            "message": "开始获取关键词提取及逻辑分析任务",
        })

        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="extracting")
        )
        await session.execute(stmt)
        await session.commit()

        # 获取提取/分析任务信息
        yield _sse_event("tasks_loaded", {
            "file_id": file_id,
            "stage": "tasks_loaded",
            "message": "已获取关键词提取及逻辑分析任务",
        })

        yield _sse_event("extraction_start", {
            "file_id": file_id,
            "stage": "extraction_start",
            "message": "开始关键词提取",
        })

        try:
            # 使用流式版本，每提取完一个字段发送一个事件
            async for field_result in run_extraction_stream(file_id, session):
                yield _sse_event("field_extracted", {
                    "file_id": file_id,
                    "stage": "field_extracted",
                    "message": f"字段提取完成: {field_result.get('field_name', '')}",
                    "field_id": field_result.get("field_id"),
                    "field_name": field_result.get("field_name"),
                    "extracted_value": field_result.get("extracted_value"),
                    "reason": field_result.get("reason"),
                    "success": field_result.get("success"),
                    "current": field_result.get("current"),
                    "total": field_result.get("total"),
                })

            yield _sse_event("extraction", {
                "file_id": file_id,
                "stage": "extraction",
                "message": "关键词提取完成",
            })

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
                .values(progress="extracting_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 6: 逻辑分析 ──────────────────────────────────────
        yield _sse_event("analysis_start", {
            "file_id": file_id,
            "stage": "analysis_start",
            "message": "开始逻辑分析",
        })

        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="analyzing")
        )
        await session.execute(stmt)
        await session.commit()

        try:
            # 使用流式版本，每分析完一条规则发送一个事件
            async for rule_result in run_analysis_stream(file_id, session):
                yield _sse_event("rule_analyzed", {
                    "file_id": file_id,
                    "stage": "rule_analyzed",
                    "message": f"规则分析完成: {rule_result.get('rule_name', '')}",
                    "rule_id": rule_result.get("rule_id"),
                    "rule_name": rule_result.get("rule_name"),
                    "rule_type": rule_result.get("rule_type"),
                    "result_value": rule_result.get("result_value"),
                    "input_values": rule_result.get("input_values"),
                    "reason": rule_result.get("reason"),
                    "success": rule_result.get("success"),
                    "current": rule_result.get("current"),
                    "total": rule_result.get("total"),
                })

            yield _sse_event("analysis", {
                "file_id": file_id,
                "stage": "analysis",
                "message": "逻辑分析完成",
            })

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="complete", end_analyzing_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="analyzing_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # 完成
        yield _sse_event("complete", {
            "file_id": file_id,
            "stage": "complete",
            "message": "文件处理完成",
        })

        logger.info("流式处理管线完成: {}", file_id)

    except Exception as e:
        logger.error("流式处理管线失败: {}, error={}", file_id, _format_exception(e))
        yield _sse_event("error", {
            "file_id": file_id,
            "stage": "error",
            "message": _format_exception(e),
        })


async def run_pipeline(
    file_id: str,
    file_path: str,
    file_content_bytes: bytes,
    session: AsyncSession,
    callback_url: str | None = None,
) -> None:
    """完整文件处理管线。

    按顺序执行：
    1. MinerU 解析 + 存 md
    2. AI 校验表格名称
    3. 分块 + 存数据库
    4. 向量化 + 提交 Milvus
    5. 字段提取
    6. 逻辑分析

    Args:
        file_id: 文件 ID。
        file_path: 文件路径/文件名。
        file_content_bytes: 文件二进制内容。
        session: 数据库会话。
        callback_url: 可选回调地址，每个阶段完成后 POST 状态通知。
    """
    logger.info("开始处理管线: {}", file_id)

    # 失败回调归因用的阶段跟踪,新增阶段时同步更新
    current_stage = "parsing"

    try:
        # ── 阶段 1: 解析 ──────────────────────────────────────────
        await notify_callback(callback_url, file_id, "parsing")
        content, middle_json_str = await parse_file(file_path, file_content_bytes, file_id, session)
        page_mapping = build_page_mapping(content, middle_json_str) if middle_json_str else []
        await save_file_content(file_id, content, session, middle_json=middle_json_str, page_mapping=page_mapping)

        await notify_callback(
            callback_url,
            file_id,
            "parsing",
            event="stage_done",
            data={
                "content": content,
                "middle_json": middle_json_str,
                "page_mapping": page_mapping,
            },
        )

        # ── 阶段 2: AI 校验表格名称 ───────────────────────────────
        current_stage = "tableing"
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="tableing", start_tableing_time=datetime.now(), error=None)
        )
        await session.execute(stmt)
        await session.commit()

        await notify_callback(callback_url, file_id, "tableing")
        try:
            tables = await parse_tables(content, file_id, page_mapping=page_mapping)
            await save_tables(tables, session)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_tableing_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="tableing_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        await notify_callback(
            callback_url,
            file_id,
            "tableing",
            event="stage_done",
            data={"total": len(tables), "tables": tables},
        )

        # ── 阶段 3: 分块 ──────────────────────────────────────────
        current_stage = "chunking"
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="chunking", start_chunking_time=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()

        await notify_callback(callback_url, file_id, "chunking")
        try:
            chunks = await chunk_content(file_id, content, tables, session, page_mapping=page_mapping)
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
                .values(progress="chunking_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        await notify_callback(
            callback_url,
            file_id,
            "chunking",
            event="stage_done",
            data={"total": len(chunks), "chunks": chunks},
        )

        # ── 阶段 4: 向量化 ────────────────────────────────────────
        current_stage = "embedding"
        type_cfg = await get_file_type_runtime_config(file_id, session)
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="embedding", start_embedding_time=datetime.now(), error=None)
        )
        await session.execute(stmt)
        await session.commit()

        await notify_callback(callback_url, file_id, "embedding")
        if not type_cfg.enable_embedding:
            logger.info(
                "{}: file_id={}, type_id={}",
                EMBEDDING_DISABLED_MESSAGE,
                file_id,
                type_cfg.type_id,
            )
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(end_embedding_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        else:
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
                    .values(progress="embedding_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

        # embedding stage_done 不携带数据，仅作完成信号
        await notify_callback(callback_url, file_id, "embedding", event="stage_done")

        # ── 阶段 5: 字段提取 ──────────────────────────────────────
        current_stage = "extracting"
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="extracting")
        )
        await session.execute(stmt)
        await session.commit()

        await notify_callback(callback_url, file_id, "extracting")
        try:
            await run_extraction(file_id, session, callback_url=callback_url)

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
                .values(progress="extracting_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        # ── 阶段 6: 逻辑分析 ──────────────────────────────────────
        current_stage = "analyzing"
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="analyzing")
        )
        await session.execute(stmt)
        await session.commit()

        await notify_callback(callback_url, file_id, "analyzing")
        try:
            await run_analysis(file_id, session, callback_url=callback_url)

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="complete", end_analyzing_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="analyzing_failed", error=_format_exception(e))
            )
            await session.execute(stmt)
            await session.commit()
            raise

        await notify_callback(callback_url, file_id, "complete")
        logger.info("处理管线完成: {}", file_id)

    except Exception as e:
        logger.error("处理管线失败: {}, error={}", file_id, _format_exception(e))
        await notify_callback(
            callback_url,
            file_id,
            f"{current_stage}_failed",
            event="stage_failed",
            data={"stage": current_stage, "error": _format_exception(e)},
        )
        raise


async def run_from_stage_stream(
    file_id: str, stage: str, session: AsyncSession
) -> AsyncGenerator[str, None]:
    """从指定阶段重新开始处理（流式版本）。

    Args:
        file_id: 文件 ID。
        stage: 起始阶段 (tableing/chunking/embedding/extracting/analyzing)。
        session: 数据库会话。

    Yields:
        SSE 事件字符串。
    """
    logger.info("从 {} 阶段流式重新开始: {}", stage, file_id)

    if stage == "table_name_validating":
        stage = "tableing"

    # 阶段顺序和名称映射
    stage_order = ["parsing", "tableing", "chunking", "embedding", "extracting", "analyzing"]
    stage_names = {
        "parsing": "MinerU 解析",
        "tableing": "AI 校验表格名称",
        "chunking": "分块",
        "embedding": "向量化",
        "extracting": "字段提取",
        "analyzing": "逻辑分析",
    }

    # 计算已完成的阶段
    stage_index = stage_order.index(stage)
    completed_stages = stage_order[:stage_index]
    completed_names = [stage_names[s] for s in completed_stages]

    # 发送恢复提示事件
    if completed_names:
        completed_msg = "、".join(completed_names)
        yield _sse_event("resume", {
            "file_id": file_id,
            "stage": "resume",
            "message": f"已完成: {completed_msg}，从「{stage_names[stage]}」阶段继续",
            "completed_stages": completed_stages,
            "resume_stage": stage,
        })
    else:
        yield _sse_event("resume", {
            "file_id": file_id,
            "stage": "resume",
            "message": f"从「{stage_names[stage]}」阶段开始",
            "completed_stages": [],
            "resume_stage": stage,
        })

    milvus_client = MilvusClient()
    milvus_client.connect()

    try:
        # 根据阶段清理数据
        if stage == "tableing":
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
            await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
            await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
            await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
            try:
                milvus_client.delete_by_file_id(file_id)
            except Exception as e:
                logger.warning("Milvus 删除失败: {}", e)
            await session.commit()

        elif stage == "embedding":
            await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
            await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
            try:
                milvus_client.delete_by_file_id(file_id)
            except Exception as e:
                logger.warning("Milvus 删除失败: {}", e)
            await session.commit()

        elif stage == "extracting":
            await session.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
            await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
            await session.commit()

        elif stage == "analyzing":
            await session.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
            await session.commit()

        else:
            raise ValueError(f"无效的阶段: {stage}，流式重试不支持 parsing 阶段")

        # 获取文件内容
        stmt = select(FileContent).where(FileContent.file_id == file_id)
        result = await session.execute(stmt)
        file_content = result.scalar_one_or_none()
        page_mapping = file_content.page_mapping if file_content else []

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
                "page_num": t.page_num or "",
            }
            for t in tables_orm
        ]

        current_stage = stage

        # ── 阶段: AI 校验表格名称 ───────────────────────────────
        if current_stage == "tableing":
            if not file_content:
                raise ValueError("缺少文件内容，无法从 tableing 阶段开始")

            content = file_content.file_content

            yield _sse_event("tableing_start", {
                "file_id": file_id,
                "stage": "tableing_start",
                "message": "开始 AI 校验表格名称",
            })

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="tableing", start_tableing_time=datetime.now(), error=None)
            )
            await session.execute(stmt)
            await session.commit()

            try:
                tables = await parse_tables(content, file_id, page_mapping=page_mapping)
                await save_tables(tables, session)

                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(end_tableing_time=datetime.now())
                )
                await session.execute(stmt)
                await session.commit()

                yield _sse_event("tableing", {
                    "file_id": file_id,
                    "stage": "tableing",
                    "message": "AI 校验表格名称完成",
                    "table_count": len(tables),
                })
            except Exception as e:
                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress="tableing_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

            current_stage = "chunking"

        # ── 阶段: 分块 ──────────────────────────────────────────
        if current_stage == "chunking":
            if not file_content:
                raise ValueError("缺少文件内容，无法从 chunking 阶段开始")

            content = file_content.file_content

            yield _sse_event("chunking_start", {
                "file_id": file_id,
                "stage": "chunking_start",
                "message": "开始分块",
            })

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="chunking", start_chunking_time=datetime.now(), error=None)
            )
            await session.execute(stmt)
            await session.commit()

            try:
                chunks = await chunk_content(file_id, content, tables, session, page_mapping=page_mapping)
                yield _sse_event("chunking", {
                    "file_id": file_id,
                    "stage": "chunking",
                    "message": "分块完成",
                    "chunk_count": len(chunks),
                })

                yield _sse_event("chunks_saving", {
                    "file_id": file_id,
                    "stage": "chunks_saving",
                    "message": "开始存储分块到数据库",
                })

                await save_chunks(chunks, session)
                yield _sse_event("chunks_saved", {
                    "file_id": file_id,
                    "stage": "chunks_saved",
                    "message": "分块已存储到数据库",
                })

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
                    .values(progress="chunking_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

            current_stage = "embedding"

        # ── 阶段: 向量化 ────────────────────────────────────────
        if current_stage == "embedding":
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

            yield _sse_event("embedding_start", {
                "file_id": file_id,
                "stage": "embedding_start",
                "message": "开始向量化",
            })

            type_cfg = await get_file_type_runtime_config(file_id, session)
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="embedding", start_embedding_time=datetime.now(), error=None)
            )
            await session.execute(stmt)
            await session.commit()

            if not type_cfg.enable_embedding:
                logger.info(
                    "{}: file_id={}, type_id={}",
                    EMBEDDING_DISABLED_MESSAGE,
                    file_id,
                    type_cfg.type_id,
                )
                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(end_embedding_time=datetime.now())
                )
                await session.execute(stmt)
                await session.commit()
                yield _sse_event("embedding_skipped", {
                    "file_id": file_id,
                    "stage": "embedding_skipped",
                    "message": EMBEDDING_DISABLED_MESSAGE,
                })
            else:
                try:
                    embeddings = await embed_chunks(chunks)
                    yield _sse_event("embedding", {
                        "file_id": file_id,
                        "stage": "embedding",
                        "message": "向量化完成",
                        "embedding_count": len(embeddings),
                    })

                    yield _sse_event("milvus_submitting", {
                        "file_id": file_id,
                        "stage": "milvus_submitting",
                        "message": "开始提交向量到 Milvus",
                    })

                    await submit_to_milvus(chunks, embeddings)
                    yield _sse_event("milvus_submitted", {
                        "file_id": file_id,
                        "stage": "milvus_submitted",
                        "message": "向量已提交到 Milvus",
                    })

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
                        .values(progress="embedding_failed", error=_format_exception(e))
                    )
                    await session.execute(stmt)
                    await session.commit()
                    raise

            current_stage = "extracting"

        # ── 阶段: 字段提取 ──────────────────────────────────────
        if current_stage == "extracting":
            yield _sse_event("tasks_loading", {
                "file_id": file_id,
                "stage": "tasks_loading",
                "message": "开始获取关键词提取及逻辑分析任务",
            })

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="extracting", error=None)
            )
            await session.execute(stmt)
            await session.commit()

            yield _sse_event("tasks_loaded", {
                "file_id": file_id,
                "stage": "tasks_loaded",
                "message": "已获取关键词提取及逻辑分析任务",
            })

            yield _sse_event("extraction_start", {
                "file_id": file_id,
                "stage": "extraction_start",
                "message": "开始关键词提取",
            })

            try:
                async for field_result in run_extraction_stream(file_id, session):
                    yield _sse_event("field_extracted", {
                        "file_id": file_id,
                        "stage": "field_extracted",
                        "message": f"字段提取完成: {field_result.get('field_name', '')}",
                        "field_id": field_result.get("field_id"),
                        "field_name": field_result.get("field_name"),
                        "extracted_value": field_result.get("extracted_value"),
                        "reason": field_result.get("reason"),
                        "success": field_result.get("success"),
                        "current": field_result.get("current"),
                        "total": field_result.get("total"),
                    })

                yield _sse_event("extraction", {
                    "file_id": file_id,
                    "stage": "extraction",
                    "message": "关键词提取完成",
                })

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
                    .values(progress="extracting_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

            current_stage = "analyzing"

        # ── 阶段: 逻辑分析 ──────────────────────────────────────
        if current_stage == "analyzing":
            yield _sse_event("analysis_start", {
                "file_id": file_id,
                "stage": "analysis_start",
                "message": "开始逻辑分析",
            })

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="analyzing", error=None)
            )
            await session.execute(stmt)
            await session.commit()

            try:
                async for rule_result in run_analysis_stream(file_id, session):
                    yield _sse_event("rule_analyzed", {
                        "file_id": file_id,
                        "stage": "rule_analyzed",
                        "message": f"规则分析完成: {rule_result.get('rule_name', '')}",
                        "rule_id": rule_result.get("rule_id"),
                        "rule_name": rule_result.get("rule_name"),
                        "rule_type": rule_result.get("rule_type"),
                        "result_value": rule_result.get("result_value"),
                        "input_values": rule_result.get("input_values"),
                        "reason": rule_result.get("reason"),
                        "success": rule_result.get("success"),
                        "current": rule_result.get("current"),
                        "total": rule_result.get("total"),
                    })

                yield _sse_event("analysis", {
                    "file_id": file_id,
                    "stage": "analysis",
                    "message": "逻辑分析完成",
                })

                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress="complete", end_analyzing_time=datetime.now())
                )
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress="analyzing_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

        # 完成
        yield _sse_event("complete", {
            "file_id": file_id,
            "stage": "complete",
            "message": "文件处理完成",
        })

        logger.info("从 {} 阶段流式重新开始完成: {}", stage, file_id)

    except Exception as e:
        logger.error("从 {} 阶段流式重新开始失败: {}, error={}", stage, file_id, _format_exception(e))
        yield _sse_event("error", {
            "file_id": file_id,
            "stage": "error",
            "message": _format_exception(e),
        })


async def run_from_stage(
    file_id: str, stage: str, session: AsyncSession, callback_url: str | None = None,
) -> None:
    """从指定阶段重新开始处理。

    Args:
        file_id: 文件 ID。
        stage: 起始阶段 (parsing/tableing/chunking/embedding/extracting/analyzing)。
        session: 数据库会话。
        callback_url: 可选回调地址，每个阶段完成后 POST 状态通知。
    """
    logger.info("从 {} 阶段重新开始: {}", stage, file_id)

    if stage == "table_name_validating":
        stage = "tableing"

    milvus_client = MilvusClient()
    milvus_client.connect()

    # parsing 阶段需要原始文件，无法在此重试
    if stage == "parsing":
        raise ValueError("parsing 阶段需要原始文件内容，请使用 /file/parse 重新提交文件")

    if stage not in ("tableing", "chunking", "embedding", "extracting", "analyzing"):
        raise ValueError(f"无效的阶段: {stage}")

    # 失败回调归因用的阶段跟踪,新增阶段时同步更新
    current_stage = stage
    try:
        # 根据阶段清理数据
        if stage == "tableing":
            # 清理表格及后续数据
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

        # 获取文件内容（用于后续阶段）
        stmt = select(FileContent).where(FileContent.file_id == file_id)
        result = await session.execute(stmt)
        file_content = result.scalar_one_or_none()
        page_mapping = file_content.page_mapping if file_content else []

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
                "page_num": t.page_num or "",
            }
            for t in tables_orm
        ]

        # 从指定阶段开始执行
        if stage in ("tableing",):
            current_stage = "tableing"
            if not file_content:
                raise ValueError("缺少文件内容，无法从 tableing 阶段开始")

            content = file_content.file_content

            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="tableing", start_tableing_time=datetime.now(), error=None)
            )
            await session.execute(stmt)
            await session.commit()

            await notify_callback(callback_url, file_id, "tableing")
            try:
                tables = await parse_tables(content, file_id, page_mapping=page_mapping)
                await save_tables(tables, session)

                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(end_tableing_time=datetime.now())
                )
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress="tableing_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

            await notify_callback(
                callback_url,
                file_id,
                "tableing",
                event="stage_done",
                data={"total": len(tables), "tables": tables},
            )

            stage = "chunking"

        if stage in ("chunking",):
            current_stage = "chunking"
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

            await notify_callback(callback_url, file_id, "chunking")
            try:
                chunks = await chunk_content(file_id, content, tables, session, page_mapping=page_mapping)
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
                    .values(progress="chunking_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

            await notify_callback(
                callback_url,
                file_id,
                "chunking",
                event="stage_done",
                data={"total": len(chunks), "chunks": chunks},
            )

            stage = "embedding"

        if stage in ("embedding",):
            current_stage = "embedding"
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

            type_cfg = await get_file_type_runtime_config(file_id, session)
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="embedding", start_embedding_time=datetime.now(), error=None)
            )
            await session.execute(stmt)
            await session.commit()

            await notify_callback(callback_url, file_id, "embedding")
            if not type_cfg.enable_embedding:
                logger.info(
                    "{}: file_id={}, type_id={}",
                    EMBEDDING_DISABLED_MESSAGE,
                    file_id,
                    type_cfg.type_id,
                )
                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(end_embedding_time=datetime.now())
                )
                await session.execute(stmt)
                await session.commit()
            else:
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
                        .values(progress="embedding_failed", error=_format_exception(e))
                    )
                    await session.execute(stmt)
                    await session.commit()
                    raise

            # embedding stage_done 不携带数据，仅作完成信号
            await notify_callback(callback_url, file_id, "embedding", event="stage_done")

            stage = "extracting"

        if stage in ("extracting",):
            current_stage = "extracting"
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="extracting", error=None)
            )
            await session.execute(stmt)
            await session.commit()

            await notify_callback(callback_url, file_id, "extracting")
            try:
                await run_extraction(file_id, session, callback_url=callback_url)

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
                    .values(progress="extracting_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

            stage = "analyzing"

        if stage in ("analyzing",):
            current_stage = "analyzing"
            stmt = (
                update(File)
                .where(File.file_id == file_id)
                .values(progress="analyzing", error=None)
            )
            await session.execute(stmt)
            await session.commit()

            await notify_callback(callback_url, file_id, "analyzing")
            try:
                await run_analysis(file_id, session, callback_url=callback_url)

                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress="complete", end_analyzing_time=datetime.now())
                )
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                stmt = (
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress="analyzing_failed", error=_format_exception(e))
                )
                await session.execute(stmt)
                await session.commit()
                raise

        await notify_callback(callback_url, file_id, "complete")
        logger.info("从 {} 阶段重新开始完成: {}", stage, file_id)
    except Exception as e:
        logger.error(
            "从阶段重试失败: {}, stage={}, error={}",
            file_id, current_stage, _format_exception(e),
        )
        await notify_callback(
            callback_url,
            file_id,
            f"{current_stage}_failed",
            event="stage_failed",
            data={"stage": current_stage, "error": _format_exception(e)},
        )
        raise
