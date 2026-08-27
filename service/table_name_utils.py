"""表格名称的纯文本/规则工具函数（无 async / LLM / DB）。

从 table_service.py 抽离，便于单测。table_service 通过 re-import 同时充当
re-export，保证 scripts/debug_table_name_extraction.py 里
parse_service._clean_text_line / _extract_table_name 等旧引用继续可用。
"""

from __future__ import annotations

import re


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


def _extract_last_line(preceding_text: str) -> str:
    """提取表格前最后一行（模型失败时唯一回退）。"""
    text = preceding_text.rstrip()
    if not text:
        return ""

    lines = text.splitlines()
    if not lines:
        return ""

    return _clean_text_line(lines[-1])


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
    """规则回退：只取表格前最后一行。"""
    name = _extract_last_line(preceding_text) or "未知"
    return name[:30]


def _is_unknown_table_name(name: str) -> bool:
    """判断模型是否返回了"无法识别/未知"类占位结果。"""
    cleaned = _clean_text_line(name).strip("`\"'[](){}（）【】")
    if not cleaned:
        return True

    lowered = cleaned.lower()
    if lowered in _UNKNOWN_TABLE_NAME_VALUES:
        return True

    for hint in _UNKNOWN_TABLE_NAME_HINTS:
        if hint in cleaned or hint in lowered:
            return True

    return False
