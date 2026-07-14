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


# 唯一锚前缀尝试长度：先长(更易唯一)后短(容忍 md 渲染在中段与块文本分叉)
_PROBE_LENS = (40, 25)


def _block_probe_and_bbox(block: dict):
    """从任意块递归收集探针文本(span content / 表格 html),返回 (probe, bbox)。

    text/title/list 块取 span content;table 块取 table_body span 的 html。
    统一用 " " 连接(与 _extract_block_text 口径一致,30~40 字前缀极少跨 span)。
    """
    texts: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            content = node.get("content")
            if isinstance(content, str) and content:
                texts.append(content)
            html = node.get("html")
            if isinstance(html, str) and html:
                texts.append(html)
            for key in ("blocks", "lines", "spans"):
                if key in node:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(block)
    probe = " ".join(t for t in texts if t).strip()
    return probe, block.get("bbox")


def _unique_find(md_content: str, probe: str):
    """在整篇 md 里找 probe 的全局唯一出现;返回 (pos, used_len),不唯一返回 (-1, 0)。"""
    for length in _PROBE_LENS:
        candidate = probe[:length].strip()
        if len(candidate) < 8:
            continue
        if md_content.count(candidate) == 1:
            return md_content.find(candidate), len(candidate)
    return -1, 0


def _longest_nondecreasing_keep(pages: List[int]) -> List[int]:
    """返回要保留的下标(page_num 的最长非降子序列),剔除破坏单调的假唯一锚。"""
    if not pages:
        return []
    from bisect import bisect_right

    tails_val: List[int] = []   # tails_val[k] = 长度 k+1 的非降子序列的最小结尾值
    tails_idx: List[int] = []   # 对应 pages 下标
    prev = [-1] * len(pages)
    for i, v in enumerate(pages):
        j = bisect_right(tails_val, v)
        if j == len(tails_val):
            tails_val.append(v)
            tails_idx.append(i)
        else:
            tails_val[j] = v
            tails_idx[j] = i
        prev[i] = tails_idx[j - 1] if j > 0 else -1
    keep: List[int] = []
    k = tails_idx[-1]
    while k != -1:
        keep.append(k)
        k = prev[k]
    keep.reverse()
    return keep


def build_page_mapping(
    md_content: str,
    middle_json_raw: Union[str, dict],
) -> List[Dict[str, Any]]:
    """构建 markdown 文本位置 → 页码的映射表(全局唯一锚 + LIS 单调清洗)。

    算法：遍历 middle_json 每页每块，取足够长前缀在整篇 md 做全局唯一匹配
    (count==1)得到可信锚 (pos, page_num, bbox, page_size)；锚点按 pos 排序后
    用 LIS 保留 page_num 非降的最长子序列，剔除极少数破坏单调的假唯一匹配。
    产出 schema 与历史版本一致，lookup_page_num/lookup_bboxes 无需改动。

    Args:
        md_content: MinerU 返回的 markdown 全文。
        middle_json_raw: MinerU 返回的 middle_json（字符串或 dict）。

    Returns:
        按 start_pos 排序的映射列表，每项: {"start_pos", "end_pos", "page_num",
        "bbox"(可选), "page_size"(可选)}。
    """
    if not md_content or not middle_json_raw:
        return []
    middle = _parse_middle_json(middle_json_raw)
    pdf_info = middle.get("pdf_info", [])
    if not pdf_info:
        return []

    # 1) 候选：每块取全局唯一锚
    candidates = []  # (pos, used_len, page_num, bbox, page_size)
    for page in pdf_info:
        page_num = page.get("page_idx", 0) + 1
        page_size = page.get("page_size")
        for block in page.get("para_blocks", []):
            probe, bbox = _block_probe_and_bbox(block)
            if len(probe) < 8:
                continue
            pos, used_len = _unique_find(md_content, probe)
            if pos < 0:
                continue
            candidates.append((pos, used_len, page_num, bbox, page_size))

    if not candidates:
        return []

    # 2) 按位置排序
    candidates.sort(key=lambda c: c[0])

    # 3) LIS 单调清洗（page_num 非降）
    keep = _longest_nondecreasing_keep([c[2] for c in candidates])
    candidates = [candidates[i] for i in keep]

    # 4) 组装（schema 不变）
    mapping: List[Dict[str, Any]] = []
    for pos, used_len, page_num, bbox, page_size in candidates:
        entry: Dict[str, Any] = {
            "start_pos": pos,
            "end_pos": pos + used_len,
            "page_num": page_num,
        }
        if bbox:
            entry["bbox"] = bbox
        if page_size:
            entry["page_size"] = page_size
        mapping.append(entry)
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
