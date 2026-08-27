"""提取阶段的只读快照。

AsyncSession 非并发安全，而字段提取要并发跑，所以并发开始前把该文件的
只读数据（正文 / 表格 / 分块）一次性取出冻结，并发段只读快照不碰 session。
写库仍回到主协程串行执行。此模式与 analysis_run_service.FileFieldSnapshot 一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loguru import logger

from model.tables import FileChunk, FileContent, FileTable
from service.file_vector_index import FileVectorIndex, load_file_vector_index
from utils.page_mapping import build_page_projection


@dataclass(frozen=True)
class TableRow:
    """file_table 的只读投影，只保留提取链路真正用到的列。

    不直接持有 ORM 实例：会话关闭后 ORM 属性访问会触发 lazy load，
    在并发段等价于隐式 session 访问。
    """

    table_index: int
    table_name: str | None
    table_content: str | None
    start_pos: int | None
    end_pos: int | None
    page_num: Any


@dataclass(frozen=True)
class ChunkRow:
    """file_chunk 的只读投影。"""

    chunk_id: str
    chunk_index: int
    chunk_content: str
    start_pos: int | None
    end_pos: int | None
    page_num: Any


@dataclass(frozen=True)
class FileExtractionSnapshot:
    """一次字段提取所需的全部只读数据。"""

    file_id: str
    type_id: str
    content: str
    page_mapping: List[Dict[str, Any]]
    page_contents: Dict[Any, str]
    tables: Tuple[TableRow, ...]
    chunks: Tuple[ChunkRow, ...]
    # 单文件全部子块向量。仅当该类型确实有 vector_db 字段时加载
    # （拉取可达上百 MB，无谓加载代价太大）；未加载或加载失败为 None。
    vector_index: FileVectorIndex | None = None
    # middle_json 的页级文本投影。None 表示存量记录缺失或结构数据损坏；
    # 空 tuple 表示结构有效但所请求页面没有可提取文本。
    page_projection: Tuple[Dict[str, Any], ...] | None = None


async def load_extraction_snapshot(
    file_id: str,
    session: AsyncSession,
    type_id: str = "default",
    *,
    need_vectors: bool = False,
) -> FileExtractionSnapshot:
    """一次性读出字段提取所需的全部只读数据（3 次查询）。

    必须在并发开始之前调用。

    Args:
        file_id: 文件 ID。
        session: 数据库会话（仅在此函数内使用）。
        type_id: 文件归属类型，随快照透传，避免并发段回查 files 表。
        need_vectors: True 时额外拉取该文件的全部子块向量（供 vector_db
            检索的单文件全量打分）。调用方应按「该类型是否存在 vector_db
            字段」决定，避免无谓的上百 MB 传输。

    Returns:
        冻结的 FileExtractionSnapshot。文件未解析时 content 为空串。
    """
    # 延迟导入避免与 extraction_service 循环依赖
    from service.extraction_service import split_md_by_pages

    fc_row = (
        await session.execute(select(FileContent).where(FileContent.file_id == file_id))
    ).scalar_one_or_none()
    content = (fc_row.file_content if fc_row else "") or ""
    page_mapping = (fc_row.page_mapping if fc_row else None) or []
    projected_pages = build_page_projection(
        getattr(fc_row, "middle_json", None) if fc_row else None
    )
    page_projection = (
        tuple(projected_pages) if projected_pages is not None else None
    )

    table_rows = (
        await session.execute(select(FileTable).where(FileTable.file_id == file_id))
    ).scalars().all()
    chunk_rows = (
        await session.execute(select(FileChunk).where(FileChunk.file_id == file_id))
    ).scalars().all()

    page_contents: Dict[Any, str] = {}
    if content:
        page_contents = {
            p["page_num"]: p["content"] for p in split_md_by_pages(content, page_mapping)
        }

    chunks = tuple(
        ChunkRow(
            chunk_id=c.chunk_id,
            chunk_index=c.chunk_index,
            chunk_content=c.chunk_content,
            start_pos=c.start_pos,
            end_pos=c.end_pos,
            page_num=c.page_num,
        )
        for c in chunk_rows
    )

    vector_index = None
    if need_vectors:
        try:
            vector_index = await load_file_vector_index(file_id, chunks)
        except Exception as e:
            # Milvus 不可用不该拖垮整个提取阶段：vector_db 字段各自失败即可，
            # 其他 search_type 的字段照常跑。
            logger.error("file_id={} 载入子块向量失败，vector_db 字段将无命中: {}", file_id, e)

    return FileExtractionSnapshot(
        file_id=file_id,
        type_id=type_id,
        content=content,
        page_mapping=page_mapping,
        page_contents=page_contents,
        tables=tuple(
            TableRow(
                table_index=t.table_index,
                table_name=t.table_name,
                table_content=t.table_content,
                start_pos=t.start_pos,
                end_pos=t.end_pos,
                page_num=t.page_num,
            )
            for t in table_rows
        ),
        chunks=chunks,
        vector_index=vector_index,
        page_projection=page_projection,
    )
