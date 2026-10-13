"""按原文坐标合并关键词窗口，并保护 HTML 表格边界。"""

from bisect import bisect_left, bisect_right
import re
from typing import Any, Dict, List


_TABLE_PATTERN = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.DOTALL | re.IGNORECASE)


def merge_keyword_spans(
    content: str,
    results: List[Dict[str, Any]],
    *,
    text_key: str,
    sort_order: str,
) -> List[Dict[str, Any]]:
    """同一关键词的重叠/相接窗口取并集；有间隔的窗口保持独立。

    仅用于 context / rule 产出的原文切片：先补齐边界落入的表格，再按坐标
    合并，最后统一从原文切片，避免字符串去重误删不同位置的相同内容。
    合并发生在排序和条数限制之前，密集命中只占一个名额。坐标仍对应全文，
    后续页码标注和 bbox 定位沿用既有流程，不把跨页内容压成一个页码。
    不适用于带表名前缀的 chunk_content（其长度不等于原文坐标跨度）。
    """
    if not results:
        return []

    tables = [match.span() for match in _TABLE_PATTERN.finditer(content)]
    table_starts = [start for start, _ in tables]
    table_ends = [end for _, end in tables]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for result in results:
        row = dict(result)
        start, end = row["start_pos"], row["end_pos"]
        # 二分定位窗口相交的首尾表格，避免每个命中遍历所有表格。
        first = bisect_right(table_ends, start)
        if first < len(tables) and table_starts[first] < end:
            last = bisect_left(table_starts, end) - 1
            start = min(start, table_starts[first])
            end = max(end, table_ends[last])
        row["start_pos"], row["end_pos"] = start, end
        groups.setdefault(row["keyword"], []).append(row)

    merged: List[Dict[str, Any]] = []
    for rows in groups.values():
        rows.sort(key=lambda row: (row["start_pos"], row["end_pos"]))
        spans: List[Dict[str, Any]] = []
        for row in rows:
            if not spans or row["start_pos"] > spans[-1]["end_pos"]:
                spans.append(row)
                continue
            previous = spans[-1]
            previous["end_pos"] = max(previous["end_pos"], row["end_pos"])
            # 倒序以最晚命中为代表，其余模式以最早命中为代表。
            choose_position = max if sort_order == "desc" else min
            previous["position"] = choose_position(previous["position"], row["position"])
        for span in spans:
            span[text_key] = content[span["start_pos"]:span["end_pos"]]
        merged.extend(spans)
    return merged
