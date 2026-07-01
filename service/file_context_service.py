"""文件片段上下文查询服务。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.schemas import FileContextQueryRequest
from model.tables import FileChunk, FileContent
from utils.page_mapping import lookup_bboxes, lookup_page_num


def _lookup_page_for_half_open_range(
    page_mapping: List[Dict[str, Any]],
    start_pos: int,
    end_pos: int,
) -> str:
    """按 Python 半开区间 [start, end) 查页码，避免命中刚好止于下一页起点。"""
    if end_pos <= start_pos:
        return lookup_page_num(page_mapping, start_pos, end_pos)
    return lookup_page_num(page_mapping, start_pos, end_pos - 1)


def _lookup_bboxes_for_half_open_range(
    page_mapping: List[Dict[str, Any]],
    start_pos: int,
    end_pos: int,
) -> List[Dict[str, Any]]:
    if end_pos <= start_pos:
        return lookup_bboxes(page_mapping, start_pos, end_pos)
    return lookup_bboxes(page_mapping, start_pos, end_pos - 1)


def find_context_matches(
    content: str,
    query: str,
    context_before: int,
    context_after: int,
    case_sensitive: bool,
    page_mapping: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """在 MinerU Markdown 全文中查找 query，并返回所有命中的上下文。"""
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)

    matches: List[Dict[str, Any]] = []
    for index, match in enumerate(pattern.finditer(content), 1):
        match_start = match.start()
        match_end = match.end()
        context_start = max(0, match_start - context_before)
        context_end = min(len(content), match_end + context_after)
        bboxes = _lookup_bboxes_for_half_open_range(page_mapping, match_start, match_end)

        matches.append(
            {
                "match_index": index,
                "keyword": query,
                "position": match_start,
                "match_start_pos": match_start,
                "match_end_pos": match_end,
                "context_start_pos": context_start,
                "context_end_pos": context_end,
                "context": content[context_start:context_end],
                "page_num": _lookup_page_for_half_open_range(
                    page_mapping, match_start, match_end
                ),
                "bboxes": bboxes,
            }
        )

    return matches


def _count_chunk_hits(chunk: Any, matches: List[Dict[str, Any]]) -> int:
    count = 0
    for match in matches:
        if (
            match["match_start_pos"] < chunk.end_pos
            and match["match_end_pos"] > chunk.start_pos
        ):
            count += 1
    return count


def build_file_context_response(
    request: FileContextQueryRequest,
    content: str,
    page_mapping: List[Dict[str, Any]],
    chunks: Sequence[Any],
) -> Dict[str, Any]:
    """根据全文、页码映射和分块对象组装上下文查询响应。"""
    matches = find_context_matches(
        content=content,
        query=request.query,
        context_before=request.context_before,
        context_after=request.context_after,
        case_sensitive=request.case_sensitive,
        page_mapping=page_mapping,
    )

    chunk_items: List[Dict[str, Any]] = []
    if request.include_all_chunks:
        for chunk in chunks:
            hit_count = _count_chunk_hits(chunk, matches)
            chunk_items.append(
                {
                    "file_id": chunk.file_id,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "chunk_content": chunk.chunk_content,
                    "start_pos": chunk.start_pos,
                    "end_pos": chunk.end_pos,
                    "page_num": chunk.page_num or "",
                    "hit": hit_count > 0,
                    "hit_count": hit_count,
                }
            )

    return {
        "file_id": request.file_id,
        "query": request.query,
        "query_type": request.query_type,
        "matched": bool(matches),
        "match_count": len(matches),
        "matches": matches,
        "chunks": chunk_items,
    }


async def query_file_context(
    request: FileContextQueryRequest,
    session: AsyncSession,
) -> Dict[str, Any]:
    """按 file_id + 文本片段查询上下文、页码，并返回该文件全部分块。"""
    stmt = select(FileContent).where(FileContent.file_id == request.file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()
    if not file_content or not file_content.file_content:
        raise HTTPException(status_code=404, detail="文件内容不存在或尚未解析完成")

    content = file_content.file_content
    page_mapping = file_content.page_mapping or []

    chunks: Sequence[FileChunk] = []
    if request.include_all_chunks:
        chunk_stmt = (
            select(FileChunk)
            .where(FileChunk.file_id == request.file_id)
            .order_by(FileChunk.chunk_index)
        )
        chunk_result = await session.execute(chunk_stmt)
        chunks = chunk_result.scalars().all()

    return build_file_context_response(request, content, page_mapping, chunks)
