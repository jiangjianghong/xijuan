"""页码映射工具：将 markdown 文本位置映射到 PDF 页码。"""

from __future__ import annotations

import json
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Union


def _parse_middle_json(middle_json_raw: Union[str, dict]) -> dict:
    """安全解析 middle_json，兼容字符串和 dict。"""
    if isinstance(middle_json_raw, str):
        return json.loads(middle_json_raw)
    return middle_json_raw


def _extract_block_text(block: dict) -> str:
    """从 para_block 中提取纯文本内容。"""
    parts = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            content = span.get("content", "")
            if content:
                parts.append(content)
    return " ".join(parts)


def build_page_mapping(
    md_content: str,
    middle_json_raw: Union[str, dict],
) -> List[Dict[str, Any]]:
    """构建 markdown 文本位置 → 页码的映射表。

    算法：遍历 middle_json 中每页的每个 para_block，提取其文本前缀，
    在 md_content 中前向扫描定位，记录 (start_pos, page_num)。

    Args:
        md_content: MinerU 返回的 markdown 全文。
        middle_json_raw: MinerU 返回的 middle_json（字符串或 dict）。

    Returns:
        按 start_pos 排序的映射列表，每项: {"start_pos": int, "end_pos": int, "page_num": int,
        "bbox": [x0, y0, x1, y1], "page_size": [w, h]}（bbox/page_size 在 middle_json 缺失时不带）。
    """
    if not md_content or not middle_json_raw:
        return []

    middle = _parse_middle_json(middle_json_raw)
    pdf_info = middle.get("pdf_info", [])
    if not pdf_info:
        return []

    mapping: List[Dict[str, Any]] = []
    cursor = 0  # 前向扫描游标

    for page in pdf_info:
        page_num = page.get("page_idx", 0) + 1  # 转为 1-indexed
        page_size = page.get("page_size")
        blocks = page.get("para_blocks", [])

        for block in blocks:
            block_text = _extract_block_text(block)
            if not block_text or len(block_text.strip()) < 3:
                continue
            bbox = block.get("bbox")

            def _make_entry(pos: int, length: int) -> Dict[str, Any]:
                entry: Dict[str, Any] = {
                    "start_pos": pos,
                    "end_pos": pos + length,
                    "page_num": page_num,
                }
                if bbox:
                    entry["bbox"] = bbox
                if page_size:
                    entry["page_size"] = page_size
                return entry

            # 用不同长度的前缀尝试定位
            found = False
            for prefix_len in (50, 30, 20):
                prefix = block_text[:prefix_len].strip()
                if not prefix:
                    continue
                pos = md_content.find(prefix, cursor)
                if pos != -1:
                    mapping.append(_make_entry(pos, len(prefix)))
                    cursor = pos + 1
                    found = True
                    break

            if not found:
                # 尝试更短的片段
                short = block_text[:10].strip()
                if short:
                    pos = md_content.find(short, cursor)
                    if pos != -1:
                        mapping.append(_make_entry(pos, len(short)))
                        cursor = pos + 1

    # 按 start_pos 排序
    mapping.sort(key=lambda x: x["start_pos"])

    return mapping


def lookup_page_num(
    mapping: List[Dict[str, Any]],
    start_pos: int,
    end_pos: int,
) -> str:
    """根据文本位置查找对应的页码。

    Args:
        mapping: build_page_mapping 返回的映射列表。
        start_pos: 查询的起始位置。
        end_pos: 查询的结束位置。

    Returns:
        页码字符串，如 "1" 或 "1-3"。映射为空时返回空字符串。
    """
    if not mapping:
        return ""

    # 提取排序的 start_pos 列表用于二分查找
    positions = [m["start_pos"] for m in mapping]

    # 查找 start_pos 所在页
    idx = bisect_right(positions, start_pos) - 1
    if idx < 0:
        idx = 0
    page_start = mapping[idx]["page_num"]

    # 查找 end_pos 所在页
    idx_end = bisect_right(positions, end_pos) - 1
    if idx_end < 0:
        idx_end = 0
    page_end = mapping[idx_end]["page_num"]

    if page_start == page_end:
        return str(page_start)
    else:
        return f"{page_start}-{page_end}"


def lookup_bboxes(
    mapping: List[Dict[str, Any]],
    start_pos: int,
    end_pos: int,
) -> List[Dict[str, Any]]:
    """根据文本位置查找命中范围内的块级 bbox 列表。

    Args:
        mapping: build_page_mapping 返回的映射列表。
        start_pos: 查询的起始位置。
        end_pos: 查询的结束位置。

    Returns:
        [{"page_num": int, "bbox": [x0, y0, x1, y1], "page_size": [w, h]}] 列表。
        锚点块无 bbox（存量老数据）时跳过；映射为空返回空列表。
    """
    if not mapping:
        return []

    positions = [m["start_pos"] for m in mapping]

    # 包含 start_pos 所在块（其锚点可能在 start_pos 之前）
    idx = bisect_right(positions, start_pos) - 1
    if idx < 0:
        idx = 0

    results: List[Dict[str, Any]] = []
    for m in mapping[idx:]:
        if m["start_pos"] > end_pos:
            break
        bbox = m.get("bbox")
        if not bbox:
            continue
        item: Dict[str, Any] = {"page_num": m["page_num"], "bbox": bbox}
        if m.get("page_size"):
            item["page_size"] = m["page_size"]
        results.append(item)
    return results
