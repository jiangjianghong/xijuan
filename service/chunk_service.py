"""分块服务：将文档内容按规则分块。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import FileChunk
from utils.config import get_config
from utils.file_utils import generate_chunk_id
from utils.page_mapping import lookup_page_num


def _normalize_chunk_overlap(chunk_size: int, chunk_overlap: int) -> int:
    """归一化 overlap，避免 start 指针不前进导致死循环。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")

    if chunk_overlap <= 0:
        return 0

    # overlap 不能大于等于 chunk_size，否则窗口可能不前进
    return min(chunk_overlap, chunk_size - 1)


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
    safe_overlap = _normalize_chunk_overlap(chunk_size, chunk_overlap)

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
            if safe_overlap > 0 and len(chunks) > 1:
                overlapped_chunks = []
                for j, chunk in enumerate(chunks):
                    if j > 0:
                        prev_chunk = chunks[j - 1]
                        overlap_text = prev_chunk[-safe_overlap:] if len(prev_chunk) > safe_overlap else prev_chunk
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

        # 到达尾部后立即结束，避免 end 固定在 len(text) 时死循环
        if end >= len(text):
            break

        if safe_overlap > 0:
            next_start = end - safe_overlap
            # 理论兜底：确保游标单调前进
            start = next_start if next_start > start else end
        else:
            start = end

    return chunks


def split_text_with_positions(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: List[str],
    base_offset: int = 0,
) -> List[Tuple[str, int, int]]:
    """递归分割文本，返回带位置信息的分块。

    Args:
        text: 待分块文本。
        chunk_size: 目标分块大小。
        chunk_overlap: 分块重叠大小。
        separators: 分隔符优先级列表。
        base_offset: 当前文本在原文中的起始偏移量。

    Returns:
        列表，每项为 (chunk_text, start_pos, end_pos)。
        位置是相对于原始完整文档的绝对位置。
    """
    safe_overlap = _normalize_chunk_overlap(chunk_size, chunk_overlap)

    if not text:
        return []

    if len(text) <= chunk_size:
        return [(text, base_offset, base_offset + len(text))]

    # 尝试按当前分隔符分割
    for i, sep in enumerate(separators):
        if sep in text:
            # 追踪每个片段的位置
            chunks_with_pos: List[Tuple[str, int, int]] = []
            current_chunk = ""
            current_start = 0  # 当前 chunk 在 text 中的起始位置
            pos = 0  # 当前扫描位置

            parts = text.split(sep)
            for j, part in enumerate(parts):
                # 计算这个 part 在 text 中的位置
                part_start = pos
                part_end = pos + len(part)

                candidate = current_chunk + (sep if current_chunk else "") + part
                if len(candidate) <= chunk_size:
                    if not current_chunk:
                        current_start = part_start
                    current_chunk = candidate
                else:
                    if current_chunk:
                        # 保存当前 chunk 的结束位置（不含分隔符）
                        chunk_end = pos - len(sep) if j > 0 else part_start
                        chunks_with_pos.append((
                            current_chunk,
                            base_offset + current_start,
                            base_offset + chunk_end
                        ))

                    # 处理超长的 part
                    if len(part) > chunk_size:
                        sub_chunks = split_text_with_positions(
                            part, chunk_size, chunk_overlap,
                            separators[i + 1:] if i + 1 < len(separators) else [],
                            base_offset + part_start
                        )
                        chunks_with_pos.extend(sub_chunks)
                        current_chunk = ""
                        current_start = part_end
                    else:
                        current_chunk = part
                        current_start = part_start

                pos = part_end + len(sep)  # 跳过分隔符

            if current_chunk:
                chunks_with_pos.append((
                    current_chunk,
                    base_offset + current_start,
                    base_offset + len(text)
                ))

            # 处理 overlap
            if safe_overlap > 0 and len(chunks_with_pos) > 1:
                overlapped: List[Tuple[str, int, int]] = []
                for j, (chunk_text, start, end) in enumerate(chunks_with_pos):
                    if j > 0:
                        prev_chunk, prev_start, prev_end = chunks_with_pos[j - 1]
                        overlap_len = min(safe_overlap, len(prev_chunk))
                        overlap_text = prev_chunk[-overlap_len:]
                        new_chunk = overlap_text + sep + chunk_text
                        new_start = prev_end - overlap_len
                        overlapped.append((new_chunk, new_start, end))
                    else:
                        overlapped.append((chunk_text, start, end))
                return overlapped

            return chunks_with_pos

    # 强制字符分割
    chunks_with_pos: List[Tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_text = text[start:end]
        chunks_with_pos.append((
            chunk_text,
            base_offset + start,
            base_offset + end
        ))

        # 到达尾部后立即结束，避免 end 固定在 len(text) 时死循环
        if end >= len(text):
            break

        if safe_overlap > 0:
            next_start = end - safe_overlap
            # 理论兜底：确保游标单调前进
            start = next_start if next_start > start else end
        else:
            start = end

    return chunks_with_pos


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
    page_mapping: Optional[List] = None,
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
        page_mapping: 页码映射列表（可选）。

    Returns:
        分块列表，每项包含 file_id, chunk_id, chunk_index, total_chunks, chunk_content, start_pos, end_pos。
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

    # 存储 (chunk_text, start_pos, end_pos)
    all_chunks: List[Tuple[str, int, int]] = []
    last_end = 0

    for start, end, table_content in table_positions:
        # 处理表格前的文本
        if start > last_end:
            text_before_raw = content[last_end:start]
            text_before = text_before_raw.strip()
            if text_before:
                # 计算 strip 后的实际起始位置
                strip_left = len(text_before_raw) - len(text_before_raw.lstrip())
                actual_start = last_end + strip_left
                text_chunks = split_text_with_positions(
                    text_before, chunk_size, chunk_overlap, separators,
                    base_offset=actual_start
                )
                all_chunks.extend(text_chunks)

        # 处理表格（作为独立块，前面加上 table_name）
        # chunk_content 包含 table_name 前缀，但 start_pos/end_pos 是原始表格位置
        table_name = table_name_map.get(table_content, "")
        if table_name:
            table_chunk = f"{table_name}\n{table_content}"
        else:
            table_chunk = table_content

        # 超长表格需要分块，避免超出 embedding 模型输入长度限制
        max_embedding_len = 8192
        if len(table_chunk) > max_embedding_len:
            table_sub_chunks = split_text_with_positions(
                table_chunk, chunk_size, chunk_overlap,
                ["</tr>", "</td>", "\n"],
                base_offset=start
            )
            all_chunks.extend(table_sub_chunks)
        else:
            all_chunks.append((table_chunk, start, end))

        last_end = end

    # 处理最后一个表格后的文本
    if last_end < len(content):
        text_after_raw = content[last_end:]
        text_after = text_after_raw.strip()
        if text_after:
            strip_left = len(text_after_raw) - len(text_after_raw.lstrip())
            actual_start = last_end + strip_left
            text_chunks = split_text_with_positions(
                text_after, chunk_size, chunk_overlap, separators,
                base_offset=actual_start
            )
            all_chunks.extend(text_chunks)

    # 如果没有表格，整个内容按规则分块
    if not table_positions and content.strip():
        text_stripped = content.strip()
        strip_left = len(content) - len(content.lstrip())
        all_chunks = split_text_with_positions(
            text_stripped, chunk_size, chunk_overlap, separators,
            base_offset=strip_left
        )

    # 构建返回结构
    total_chunks = len(all_chunks)
    result = []
    for chunk_index, (chunk_text, start_pos, end_pos) in enumerate(all_chunks):
        chunk_id = generate_chunk_id(file_id, chunk_index)
        result.append({
            "file_id": file_id,
            "chunk_id": chunk_id,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_content": chunk_text,
            "start_pos": start_pos,
            "end_pos": end_pos,
            "page_num": lookup_page_num(page_mapping or [], start_pos, end_pos),
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
            start_pos=chunk_data.get("start_pos", 0),
            end_pos=chunk_data.get("end_pos", 0),
            page_num=chunk_data.get("page_num", ""),
        )
        session.add(file_chunk)

    await session.commit()
    logger.info("分块已保存: {} 个", len(chunks))
