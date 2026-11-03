"""表格名称的纯文本/规则工具函数（无 async / LLM / DB）。

从 table_service.py 抽离，便于单测。table_service 通过 re-import 同时充当
re-export，保证 scripts/debug_table_name_extraction.py 里
parse_service._clean_text_line / _extract_table_name 等旧引用继续可用。
"""

from __future__ import annotations

import re
from html import unescape
from typing import List, Sequence


_UNKNOWN_TABLE_NAME_VALUES = {
    "未知",
    "unknown",
    "none",
    "null",
    "n/a",
    "na",
    "无",
}
_UNKNOWN_TABLE_NAME_HINTS = (
    "未找到",
    "无法提取",
    "无法确定",
    "无法识别",
    "不明确",
    "不确定",
    "not found",
)


def _clean_text_line(line: str) -> str:
    """清洗单行文本，去掉 markdown 标记与多余空白。"""
    line = re.sub(r"^#+\s*", "", line.strip())
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _looks_like_invalid_candidate(name: str) -> bool:
    """判断候选是否为结构性垃圾：图片语法 / HTML 标签 / LaTeX 公式。

    刻意不拦「含 $ 或反斜杠」——那会误杀 "工程款支付表($)" 这类合法表名，
    真正的公式靠 LaTeX 命令特征识别就够了。
    """
    if not name:
        return True
    if "![" in name:
        return True
    if re.search(r"</?[a-zA-Z][^>]*>", name):
        return True
    if re.search(r"\\begin\{|\\end\{|\\frac|\\sum|\\sqrt|\\int|\\lim|\\cdot|\\times", name):
        return True
    return False


# 表题关键词只用于候选优先级，不再据此或按标点、编号、长度判废。
# 不含单字「式」「单」，避免「方式」「单位」误命中。
_TABLE_TYPE_WORDS = (
    "计算公式", "明细表", "统计表", "花名册", "一览表", "样表",
    "公式", "账单", "附表", "底册", "台账", "清单",
    "记录", "目录", "说明", "表", "图",
)

# 明确以表类名称结尾，可带人数、单位等括注；不放宽「说明」「表明」等正文词。
_EXPLICIT_CAPTION_END_RE = re.compile(
    r"(?:表|清单|台账|花名册|目录)(?:\s*[（(][^（）()]*[）)])*\s*$"
)


def _contains_table_type_word(name: str) -> bool:
    """名称中是否出现表类型词（表 / 图 / 台账 / 明细表 …）。"""
    return any(w in (name or "") for w in _TABLE_TYPE_WORDS)


def _extract_last_line(preceding_text: str) -> str:
    """优先取最近含表题关键词的文本行，否则取最近非空文本行。"""
    text = (preceding_text or "").rstrip()
    if not text:
        return ""

    nearest_line = ""
    for line in reversed(text.splitlines()):
        candidate = _clean_text_line(line)
        if not candidate:
            continue
        if _looks_like_invalid_candidate(candidate):
            continue
        if not nearest_line:
            nearest_line = candidate
        if _contains_table_type_word(candidate):
            return candidate
    return nearest_line


def _build_llm_context_text(preceding_text: str, max_lines: int = 3) -> str:
    """构造 LLM 上下文。

    规则：
    1. 默认只取当前表格前最多 3 行。
    2. 若这 3 行内包含上一个 </table>，改为取该 </table> 到当前表格开始之间的文本。
    """
    text = preceding_text.rstrip()
    if not text:
        return ""

    lines_with_breaks = text.splitlines(keepends=True)
    recent_text = "".join(lines_with_breaks[-max_lines:])
    recent_start = len(text) - len(recent_text)

    prev_table_end = text.lower().rfind("</table>")
    if prev_table_end != -1 and prev_table_end >= recent_start:
        segment = text[prev_table_end + len("</table>"):]
        return segment.strip()

    return recent_text.strip()


def _extract_table_name(preceding_text: str) -> str:
    """规则回退表名；一条合法行都没有时返回空串。

    返回空串而非「未知」：下游 extraction_service 已有
    `table.table_name or f'表格{table.table_index}'` 兜底，空串会显示成「表格3」；
    「未知」会绕过这个兜底，还让多张无名表在 exact 模式下命中同一个名字。
    """
    return _extract_last_line(preceding_text)[:200]


def _extract_table_header_name(table_content: str) -> str:
    """只认首行唯一单元格中的明确表题，不从列名或数据行推断。"""
    row = re.search(r"<tr\b[^>]*>(.*?)</tr\s*>", table_content or "", re.I | re.S)
    if not row:
        return ""
    cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row.group(1), re.I | re.S)
    if len(cells) != 1:
        return ""
    name = _clean_text_line(unescape(re.sub(r"<[^>]+>", "", cells[0])))
    if not _EXPLICIT_CAPTION_END_RE.search(name) or _is_unknown_table_name(name):
        return ""
    return name[:200]


def _is_unknown_table_name(name: str) -> bool:
    """仅拦截未知占位结果和非文本结构，不按标点、编号或长度否决模型表名。"""
    cleaned = _clean_text_line(name).strip("`\"'[](){}（）【】")
    if not cleaned:
        return True

    lowered = cleaned.lower()
    if lowered in _UNKNOWN_TABLE_NAME_VALUES:
        return True

    for hint in _UNKNOWN_TABLE_NAME_HINTS:
        if hint in cleaned or hint in lowered:
            return True

    # 格式硬过滤：对未剥括号的原始候选判断，避免 "![...]" 的括号被剥后漏判
    return _looks_like_invalid_candidate(_clean_text_line(name))


def _gap_has_title(gap_text: str) -> bool:
    """相邻两表之间的文本是否含标题行。

    空行、图片 / 公式 / HTML 残留、纯数字或页码（「51」「第 51 页」「-----」）都不算。
    """
    for line in (gap_text or "").splitlines():
        candidate = _clean_text_line(line)
        if not candidate:
            continue
        if _looks_like_invalid_candidate(candidate):
            continue
        if re.fullmatch(r"[\d\s\-—–]+", candidate):
            continue
        if re.fullmatch(r"第\s*\d+\s*页", candidate):
            continue
        return True
    return False


def _is_same_page(prev_page: str, cur_page: str) -> bool:
    """两张表是否确定在同一页。

    page_num 形如 "30" 或跨页的 "31-32"（见 utils.page_mapping.lookup_page_num）。
    只有两边都是纯数字且相等才判定同页；空值或跨页串一律返回 False——
    「无法确定」不该否决续表。
    """
    a = (prev_page or "").strip()
    b = (cur_page or "").strip()
    if not a or not b:
        return False
    if not a.isdigit() or not b.isdigit():
        return False
    return a == b


def _resolve_continuation_names(
    base_names: Sequence[str],
    content: str,
    matches: Sequence,
    page_nums: Sequence[str] = (),
) -> List[str]:
    """回填跨页续表名：判定为续表时沿用上一张表的最终名。

    base_names 与 matches 同序（文档顺序），返回等长列表。

    不加 (k) 后缀：table_name 是下游表格匹配的键，「投资估算表(1)」在 exact 模式下
    匹配不到「投资估算表」，fuzzy 算出 0.769 也卡在 0.8 阈值下。要给人看第几段
    应在 UI 层拼，不动落库的表名。
    """
    final: List[str] = []
    for i, m in enumerate(matches):
        if i == 0:
            is_continuation = False
        else:
            gap = content[matches[i - 1].end():m.start()]
            is_continuation = not _gap_has_title(gap)
            if is_continuation and i < len(page_nums):
                # 同页紧邻的两张独立表不是跨页续表
                if _is_same_page(page_nums[i - 1], page_nums[i]):
                    is_continuation = False

        if is_continuation and final and final[-1]:
            final.append(final[-1])
        else:
            final.append(base_names[i])
    return final
