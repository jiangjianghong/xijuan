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

    算法：遍历 middle_json 中每页的每个 para_block。文本块提取文本前缀在
    md_content 中前向扫描定位；表格块（type=table）改以 <table 字面量定位，
    挂整表 bbox。记录 (start_pos, page_num)。

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

            # 表格块：无 lines/spans 文本（提不出前缀），改在 markdown 中前向找
            # <table 字面量作锚点，挂整表 bbox；找不到或无 bbox 时不产锚点（容错）
            if block.get("type") == "table":
                if bbox:
                    pos = md_content.find("<table", cursor)
                    if pos != -1:
                        mapping.append(_make_entry(pos, len("<table")))
                        cursor = pos + 1
                continue

            block_text = _extract_block_text(block)
            if not block_text or len(block_text.strip()) < 3:
                continue

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


def _parse_content_list(content_list_raw: Union[str, list]) -> list:
    """安全解析 content_list，兼容 JSON 字符串和 list；解析失败返回空列表。"""
    if isinstance(content_list_raw, list):
        return content_list_raw
    if isinstance(content_list_raw, str) and content_list_raw.strip():
        try:
            parsed = json.loads(content_list_raw)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _page_sizes_from_middle(middle_json_raw: Union[str, dict, None]) -> Dict[int, list]:
    """从 middle_json 提取 {page_idx: [w, h]}，供 bbox 反归一化。"""
    if not middle_json_raw:
        return {}
    try:
        middle = _parse_middle_json(middle_json_raw)
    except (ValueError, TypeError):
        return {}
    sizes: Dict[int, list] = {}
    for page in middle.get("pdf_info", []):
        ps = page.get("page_size")
        if ps and len(ps) == 2 and ps[0] and ps[1]:
            sizes[page.get("page_idx", -1)] = ps
    return sizes


def _probe_from_item(item: dict) -> str:
    """从 content_list 项中提取用于 md 定位的探针文本。

    page_number 是页码水印，md 不渲染，返回空串跳过。
    """
    item_type = item.get("type")
    if item_type == "page_number":
        return ""
    if item_type == "table":
        raw = item.get("table_body") or ""
    elif item_type == "list":
        items = item.get("list_items") or []
        raw = items[0] if items and isinstance(items[0], str) else ""
    else:
        raw = item.get("text") or ""
    return raw.strip()


def build_page_mapping_from_content_list(
    md_content: str,
    content_list_raw: Union[str, list],
    middle_json_raw: Union[str, dict, None] = None,
) -> List[Dict[str, Any]]:
    """用 content_list 顺序重放构建 markdown 位置 → 页码映射。

    MinerU 的 md 按 content_list 顺序渲染，逐项取探针（text / table_body /
    list_items[0] 前 50 字）cursor 前向定位，单调性天然成立；单项 miss 只丢
    该项锚点，不推进 cursor，无连锁错位。

    content_list 的 bbox 是 1000×1000 归一化坐标，乘 page_size/1000 反归一
    化后落库（page_size 取自 middle_json 同页；取不到则该锚不挂 bbox）。

    Returns:
        与 build_page_mapping 同 schema 的映射列表。
    """
    if not md_content:
        return []
    items = _parse_content_list(content_list_raw)
    if not items:
        return []

    page_sizes = _page_sizes_from_middle(middle_json_raw)
    mapping: List[Dict[str, Any]] = []
    cursor = 0

    for item in items:
        probe_full = _probe_from_item(item)
        if len(probe_full) < 2:
            continue

        pos = -1
        used_len = 0
        for probe in (probe_full[:50], probe_full[:20]):
            pos = md_content.find(probe, cursor)
            if pos != -1:
                used_len = len(probe)
                break
            if len(probe_full) <= 20:
                break  # 两个候选相同，不重复找

        if pos == -1:
            continue  # 单项 miss：跳过，不推进 cursor

        page_idx = item.get("page_idx", 0)
        entry: Dict[str, Any] = {
            "start_pos": pos,
            "end_pos": pos + used_len,
            "page_num": page_idx + 1,
        }
        bbox = item.get("bbox")
        page_size = page_sizes.get(page_idx)
        if bbox and len(bbox) == 4 and page_size:
            w, h = page_size
            entry["bbox"] = [
                bbox[0] * w / 1000, bbox[1] * h / 1000,
                bbox[2] * w / 1000, bbox[3] * h / 1000,
            ]
            entry["page_size"] = page_size
        mapping.append(entry)
        cursor = pos + used_len

    mapping.sort(key=lambda x: x["start_pos"])
    return mapping


def build_page_mapping_auto(
    md_content: str,
    middle_json_raw: Union[str, dict, None],
    content_list_raw: Union[str, list, None] = None,
) -> List[Dict[str, Any]]:
    """构建页码映射的统一入口：优先 content_list 顺序重放，缺失或产出为空时
    降级 middle_json 前缀匹配（老算法，兼容存量/异常场景）。"""
    if content_list_raw:
        mapping = build_page_mapping_from_content_list(
            md_content, content_list_raw, middle_json_raw
        )
        if mapping:
            return mapping
    if middle_json_raw:
        return build_page_mapping(md_content, middle_json_raw)
    return []
