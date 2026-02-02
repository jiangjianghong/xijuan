"""分块服务：将文档内容按规则分块。"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import FileChunk
from utils.config import get_config
from utils.file_utils import generate_chunk_id


def split_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: List[str],
) -> List[str]:
    """递归字符分割器：按分隔符优先级分块文本。

    Args:
        text: 待分块文本。
        chunk_size: 目标分块大小。
        chunk_overlap: 分块重叠大小。
        separators: 分隔符优先级列表。

    Returns:
        分块后的文本列表。
    """
    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    # 尝试按当前分隔符分割
    for i, sep in enumerate(separators):
        if sep in text:
            splits = text.split(sep)
            chunks = []
            current_chunk = ""

            for split in splits:
                candidate = current_chunk + (sep if current_chunk else "") + split
                if len(candidate) <= chunk_size:
                    current_chunk = candidate
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    # 如果单个 split 超过 chunk_size，递归处理
                    if len(split) > chunk_size:
                        sub_chunks = split_text(split, chunk_size, chunk_overlap, separators[i + 1:] if i + 1 < len(separators) else [])
                        chunks.extend(sub_chunks)
                        current_chunk = ""
                    else:
                        current_chunk = split

            if current_chunk:
                chunks.append(current_chunk)

            # 添加重叠
            if chunk_overlap > 0 and len(chunks) > 1:
                overlapped_chunks = []
                for j, chunk in enumerate(chunks):
                    if j > 0:
                        prev_chunk = chunks[j - 1]
                        overlap_text = prev_chunk[-chunk_overlap:] if len(prev_chunk) > chunk_overlap else prev_chunk
                        chunk = overlap_text + sep + chunk
                    overlapped_chunks.append(chunk)
                return overlapped_chunks

            return chunks

    # 没有分隔符可用，强制按字符分割
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start = end - chunk_overlap if chunk_overlap > 0 else end

    return chunks


def _find_table_positions(content: str) -> List[Tuple[int, int, str]]:
    """找出所有表格的位置。

    Returns:
        列表，每项为 (start, end, table_content)。
    """
    table_pattern = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
    positions = []
    for match in table_pattern.finditer(content):
        positions.append((match.start(), match.end(), match.group(0)))
    return positions


async def chunk_content(
    file_id: str,
    content: str,
    tables: List[Dict],
    session: AsyncSession,
) -> List[Dict]:
    """将文档内容分块并存入数据库。

    分块策略：
    1. 识别所有 <table>...</table> 位置
    2. 非表格文本按 chunk_size/overlap 分块
    3. 表格作为独立块，保留完整标签
    4. 表格块前拼接 table_name 作为上下文

    Args:
        file_id: 文件 ID。
        content: 文档 Markdown 全文。
        tables: 表格信息列表（含 table_name）。
        session: 数据库会话。

    Returns:
        分块列表，每项包含 file_id, chunk_id, chunk_index, total_chunks, chunk_content。
    """
    logger.info("开始分块: {}", file_id)

    cfg = get_config().chunking
    chunk_size = cfg.chunk_size
    chunk_overlap = cfg.chunk_overlap
    separators = cfg.separators

    # 建立 table_content -> table_name 映射
    table_name_map = {t["table_content"]: t["table_name"] for t in tables}

    # 找出所有表格位置
    table_positions = _find_table_positions(content)

    all_chunks = []
    last_end = 0

    for start, end, table_content in table_positions:
        # 处理表格前的文本
        if start > last_end:
            text_before = content[last_end:start].strip()
            if text_before:
                text_chunks = split_text(text_before, chunk_size, chunk_overlap, separators)
                all_chunks.extend(text_chunks)

        # 处理表格（作为独立块，前面加上 table_name）
        table_name = table_name_map.get(table_content, "")
        if table_name:
            table_chunk = f"{table_name}\n{table_content}"
        else:
            table_chunk = table_content
        all_chunks.append(table_chunk)

        last_end = end

    # 处理最后一个表格后的文本
    if last_end < len(content):
        text_after = content[last_end:].strip()
        if text_after:
            text_chunks = split_text(text_after, chunk_size, chunk_overlap, separators)
            all_chunks.extend(text_chunks)

    # 如果没有表格，整个内容按规则分块
    if not table_positions and content.strip():
        all_chunks = split_text(content.strip(), chunk_size, chunk_overlap, separators)

    # 构建返回结构
    total_chunks = len(all_chunks)
    result = []
    for chunk_index, chunk_text in enumerate(all_chunks):
        chunk_id = generate_chunk_id(file_id, chunk_index)
        result.append({
            "file_id": file_id,
            "chunk_id": chunk_id,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_content": chunk_text,
        })

    logger.info("分块完成: {}, 共 {} 个分块", file_id, total_chunks)
    return result


async def save_chunks(chunks: List[Dict], session: AsyncSession) -> None:
    """将分块信息批量存入 file_chunk 表。

    Args:
        chunks: 分块列表。
        session: 数据库会话。
    """
    if not chunks:
        return

    for chunk_data in chunks:
        file_chunk = FileChunk(
            file_id=chunk_data["file_id"],
            chunk_id=chunk_data["chunk_id"],
            chunk_index=chunk_data["chunk_index"],
            total_chunks=chunk_data["total_chunks"],
            chunk_content=chunk_data["chunk_content"],
        )
        session.add(file_chunk)

    await session.commit()
    logger.info("分块已保存: {} 个", len(chunks))
