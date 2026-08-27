"""页码映射工具：将 markdown 文本位置映射到 PDF 页码。"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


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


def _block_probes_and_bbox(block: dict):
    """从任意块递归收集探针候选(span content / 表格 html),返回 (probes, bbox)。

    text/title/list 块取 span content;table 块取 caption 的 content 与 table_body
    span 的 html。probes[0] 是所有片段单空格拼接的整体探针(历史口径),其后依次是
    各片段自身。

    之所以要逐片段兜底:MinerU 渲染 markdown 时片段之间未必是单空格——表格上方的
    若干 table_caption 在 md 里由硬换行 `  \\n` 分隔,拼接探针一旦跨过片段边界就与
    md 逐字不符,count 恒为 0,整块产不出锚点。首片段短于 _PROBE_LENS 时必然跨界,
    单页表格 PDF 因而 page_mapping 全空。
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
    frags = [t for t in texts if t]
    probes = [" ".join(frags).strip()]
    if len(frags) > 1:
        probes.extend(f.strip() for f in frags)
    return probes, block.get("bbox")


def _unique_find(md_content: str, probes: List[str]):
    """在整篇 md 里找候选探针的全局唯一出现;返回 (pos, used_len),都不唯一返回 (-1, 0)。

    按 probes 顺序逐个尝试,每个再按 _PROBE_LENS 先长后短——整体探针命中时 pos 即块
    起始,最精确;失败才退到片段。片段列表保持块内原序,故首片段命中时 pos 同样是块
    起始。
    """
    for probe in probes:
        for length in _PROBE_LENS:
            candidate = probe[:length].strip()
            if len(candidate) < 8:
                continue
            if md_content.count(candidate) == 1:
                return md_content.find(candidate), len(candidate)
    return -1, 0


# 与 table_service 的表格枚举口径保持一致——两处必须数出同样多、同样顺序的表格，
# 否则「第 i 个表格组 ↔ md 中第 i 张表」的配对会错位。
_TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)


def _is_continuation_table_block(block: dict) -> bool:
    """判定 table 块是否为 MinerU 跨页表格的「后续页空壳」。

    MinerU 把跨页表格合并成一个 <table> 写进 md，middle_json 里只有首页块携带
    完整 html，后续页退化成 {"lines": [], "lines_deleted": true} 的 table_body
    空壳——提不出任何探针文本，因而产不出锚点。

    要求「有 table_body 子块」而不仅是「提不出文本」：扫描件里未转成 HTML 的表格
    块只有 type/bbox、连 blocks 都没有，那种块与跨页无关，误并入表格组会凭空撑大
    末页页码。
    """
    if block.get("type") != "table":
        return False
    if _block_probes_and_bbox(block)[0][0].strip():
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "table_body"
        for b in block.get("blocks", [])
    )


def _collect_table_groups(pdf_info: List[dict]) -> List[Dict[str, int]]:
    """按 middle_json 顺序收集表格组，返回 [{"first_page", "last_page"}, ...]。

    一个组 = 一个携带 html 的首页块 + 其后紧邻页的连续空壳块。空壳块只在页码恰好
    衔接（`last_page + 1`）时并入，避免相隔数页的异常空壳块把末页拨过头；出现在
    任何有内容块之前的空壳块无组可归，直接忽略。
    """
    groups: List[Dict[str, int]] = []
    for page in pdf_info:
        page_num = page.get("page_idx", 0) + 1
        for block in page.get("para_blocks", []):
            if block.get("type") != "table":
                continue
            if _is_continuation_table_block(block):
                if groups and page_num == groups[-1]["last_page"] + 1:
                    groups[-1]["last_page"] = page_num
                continue
            groups.append({"first_page": page_num, "last_page": page_num})
    return groups


def _collect_table_block_groups(pdf_info: List[dict]) -> List[Dict[str, Any]]:
    """收集跨页表格组，并保留首页块与每页 bbox 的直接引用。

    仅供页码取文使用：页级投影不需要把 middle_json 再反向匹配到整篇 Markdown，
    因而必须保留表格首次出现的块和所有续页块。分组规则与
    ``_collect_table_groups`` 保持一致。
    """
    groups: List[Dict[str, Any]] = []
    for page in pdf_info:
        page_num = page.get("page_idx", 0) + 1
        page_size = page.get("page_size")
        for block_index, block in enumerate(page.get("para_blocks", [])):
            if block.get("type") != "table":
                continue
            if _is_continuation_table_block(block):
                if groups and page_num == groups[-1]["last_page"] + 1:
                    groups[-1]["last_page"] = page_num
                    groups[-1]["blocks"].append((page_num, block, page_size))
                continue
            groups.append(
                {
                    "first_page": page_num,
                    "last_page": page_num,
                    "owner_page": page_num,
                    "owner_block_index": block_index,
                    "owner_block": block,
                    "blocks": [(page_num, block, page_size)],
                }
            )
    return groups


def _projection_bbox(page_num: int, block: dict, page_size: Any) -> List[Dict[str, Any]]:
    """把 middle_json 的块坐标转换为页码取文 ref 可直接使用的 bbox。"""
    bbox = block.get("bbox")
    if not bbox:
        return []
    item: Dict[str, Any] = {"page_num": page_num, "bbox": bbox}
    if page_size:
        item["page_size"] = page_size
    return [item]


def _has_valid_page_projection_structure(pdf_info: List[Any]) -> bool:
    """校验页级投影会直接遍历的 middle_json 基本结构。"""
    for page in pdf_info:
        if not isinstance(page, dict):
            return False
        page_idx = page.get("page_idx", 0)
        if isinstance(page_idx, bool) or not isinstance(page_idx, int) or page_idx < 0:
            return False
        para_blocks = page.get("para_blocks", [])
        if not isinstance(para_blocks, list):
            return False
        for block in para_blocks:
            if not isinstance(block, dict):
                return False
            child_blocks = block.get("blocks")
            if child_blocks is not None and not isinstance(child_blocks, list):
                return False
    return True


def build_page_projection(
    middle_json_raw: Union[str, dict],
) -> Optional[List[Dict[str, Any]]]:
    """将 MinerU 的页级结构投影为可供 ``search_type=page`` 使用的有序文本段。

    返回 ``None`` 表示 middle_json 缺失或格式非法，应走历史 page_mapping 兼容路径；
    返回空列表表示结构数据有效，但页面没有可提取文本（例如纯图片或空白 PDF）。
    """
    if not middle_json_raw:
        return None
    try:
        middle = _parse_middle_json(middle_json_raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(middle, dict):
        return None
    pdf_info = middle.get("pdf_info")
    if not isinstance(pdf_info, list):
        return None
    if not _has_valid_page_projection_structure(pdf_info):
        return None

    table_groups = _collect_table_block_groups(pdf_info)
    owner_groups = {
        (group["owner_page"], group["owner_block_index"]): group
        for group in table_groups
    }
    projection: List[Dict[str, Any]] = []

    for page in pdf_info:
        page_num = page.get("page_idx", 0) + 1
        page_size = page.get("page_size")
        pending_text: List[str] = []
        pending_bboxes: List[Dict[str, Any]] = []

        def flush_pending() -> None:
            if not pending_text:
                return
            projection.append(
                {
                    "page_num": page_num,
                    "source_pages": [page_num],
                    "content": "\n".join(pending_text),
                    "bboxes": list(pending_bboxes),
                    "mapping_quality": "middle_json",
                }
            )
            pending_text.clear()
            pending_bboxes.clear()

        for block_index, block in enumerate(page.get("para_blocks", [])):
            if block.get("type") == "table":
                if _is_continuation_table_block(block):
                    continue
                flush_pending()
                group = owner_groups.get((page_num, block_index))
                if not group:
                    continue
                content = _block_probes_and_bbox(group["owner_block"])[0][0].strip()
                if not content:
                    continue
                source_pages = list(range(group["first_page"], group["last_page"] + 1))
                page_label: Union[int, str] = (
                    source_pages[0]
                    if len(source_pages) == 1
                    else f"{source_pages[0]}-{source_pages[-1]}"
                )
                bboxes: List[Dict[str, Any]] = []
                for block_page_num, group_block, group_page_size in group["blocks"]:
                    bboxes.extend(_projection_bbox(block_page_num, group_block, group_page_size))
                projection.append(
                    {
                        "page_num": page_label,
                        "source_pages": source_pages,
                        "content": content,
                        "bboxes": bboxes,
                        "mapping_quality": "middle_json",
                    }
                )
                continue

            content = _block_probes_and_bbox(block)[0][0].strip()
            if not content:
                continue
            pending_text.append(content)
            pending_bboxes.extend(_projection_bbox(page_num, block, page_size))
        flush_pending()

    return projection


def select_page_projection(
    projection: Optional[Sequence[Dict[str, Any]]], start_page: int, end_page: int
) -> List[Dict[str, Any]]:
    """选择与请求范围相交的页级文本段；跨页表格会完整保留一次。"""
    if not projection:
        return []
    return [
        item
        for item in projection
        if item.get("source_pages")
        and min(item["source_pages"]) <= end_page
        and max(item["source_pages"]) >= start_page
    ]


def _cross_page_table_anchors(
    md_content: str,
    pdf_info: List[dict],
) -> List[Tuple[int, int, int, None, None]]:
    """为跨页表格在 </table> 之后补一个「末页」锚点。

    跨页表格覆盖的第 2..N 页在 middle_json 里全是空壳块、产不出锚点，而
    lookup_page_num 的语义是「取 start_pos 之前最近的锚点页码」——于是表格之后、
    下一个真实锚点之前的正文会继承表格**之前**的页码（实测 22-23 页的表，其后第
    23 页正文被标成第 21 页）。这里按「第 i 个表格组 ↔ md 中第 i 张表」配对，给
    跨页组补一个零宽锚点把页码拨到末页。

    锚点不带 bbox/page_size：它落在表格之外，只作页码分界，挂整表框会让前端在正文
    位置画出表格高亮。组数与 md 中表格数对不上时整体放弃——宁可不补，也不错位污染。
    """
    groups = _collect_table_groups(pdf_info)
    spans = [m.end() for m in _TABLE_RE.finditer(md_content)]
    if not groups or len(groups) != len(spans):
        return []
    return [
        (end, 0, g["last_page"], None, None)
        for end, g in zip(spans, groups)
        if g["last_page"] > g["first_page"]
    ]


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
    (count==1)得到可信锚 (pos, page_num, bbox, page_size)；跨页表格额外补一个
    末页锚(见 _cross_page_table_anchors)；锚点按 pos 排序后用 LIS 保留 page_num
    非降的最长子序列，剔除极少数破坏单调的假唯一匹配。
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
            probes, bbox = _block_probes_and_bbox(block)
            pos, used_len = _unique_find(md_content, probes)
            if pos < 0:
                continue
            candidates.append((pos, used_len, page_num, bbox, page_size))

    if not candidates:
        return []

    # 1.5) 跨页表格补末页锚（空壳块产不出锚，表格后的正文否则会继承表格之前的页码）
    candidates.extend(_cross_page_table_anchors(md_content, pdf_info))

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


# 假锚防御：一页 markdown 至少占的字符数（保守下界，实测每页 400~500 字）。
# 页码跳变 >= 2 页时，若被跳过的页在 md 里连这个下界都装不下，判定为假唯一锚。
_MIN_CHARS_PER_PAGE = 100


def to_int_page(raw: Any) -> Optional[int]:
    """把 page_num 归一成 int；无法解析返回 None。

    生产数据是 int，历史数据与测试 fixture 可能是 str。
    公开（无下划线前缀）命名：extraction_service 需要跨模块调用它做页码归一。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def split_span_by_pages(
    mapping: List[Dict[str, Any]],
    start_pos: int,
    end_pos: int,
) -> List[Dict[str, Any]]:
    """把全文坐标区间 [start_pos, end_pos) 按页边界切成逐页子段。

    与 lookup_page_num 返回 "2-4" 这类范围串不同，本函数保留区间内每一页的
    真实边界，供调用方给每段各标注单页页码（模型才能知道内容的页分布）。

    Args:
        mapping: build_page_mapping 返回的映射（按 start_pos 升序、page_num 非降）。
        start_pos: 区间起始位置。
        end_pos: 区间结束位置（不含）。

    Returns:
        [{"page_num": int, "start_pos": int, "end_pos": int}]，按位置升序，
        首尾相接无缝覆盖 [start_pos, end_pos)。mapping 为空或区间为空返回 []。

    页码跳变 >= 2 页且字符数装不下被跳过的页时，判定为 build_page_mapping 遗留的
    假唯一锚并丢弃该切点（详见 _MIN_CHARS_PER_PAGE）。
    """
    if not mapping or end_pos <= start_pos:
        return []

    positions = [m["start_pos"] for m in mapping]
    idx = bisect_right(positions, start_pos) - 1
    if idx < 0:
        idx = 0

    cur_page = to_int_page(mapping[idx]["page_num"])
    if cur_page is None:
        return []

    segments: List[Dict[str, Any]] = []
    seg_start = start_pos

    for m in mapping[idx + 1:]:
        cut = m["start_pos"]
        if cut >= end_pos:
            break
        if cut <= seg_start:
            continue
        page = to_int_page(m["page_num"])
        if page is None or page == cur_page:
            continue
        # 假锚防御：mapping 从 cur_page 直接跳到 page，被跳过的 (jump-1) 页内容
        # 只可能落在 [seg_start, cut) 里；装不下就说明 page 是假唯一锚。
        # 拒绝时 seg_start / cur_page 都不推进，后续锚点会被同一判据持续拒绝，
        # 整段因而保持一致的可信页码。
        jump = page - cur_page
        if jump >= 2 and (cut - seg_start) < (jump - 1) * _MIN_CHARS_PER_PAGE:
            continue
        segments.append({"page_num": cur_page, "start_pos": seg_start, "end_pos": cut})
        seg_start = cut
        cur_page = page

    segments.append({"page_num": cur_page, "start_pos": seg_start, "end_pos": end_pos})
    return segments
