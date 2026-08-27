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


# 行首列表 / 章节序号：1、 一、 2. 3.2 （一） (2)
# 括号形式自成序号，不要求后面再跟标点——「（一）本企业的母公司」这种常见格式
# 否则抓不到。已知限制：「1.5 万吨/日规模」会被误判为序号，与章节号「3.2 财务测算」
# 结构相同、纯规则无法区分；实测这类占比极低。
_LIST_LEAD_RE = re.compile(
    r"^\s*(?:[（(][0-9一二三四五六七八九十]{1,3}[)）]"
    r"|(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[、.．)）])"
)
# 正文句长度阈值：超过它且不含表类型词，视为正文句而非表题
_BODY_TEXT_MIN_LEN = 20
# 表类型词。刻意不含单字「式」「单」——「方式」「单位」会误命中；
# 长词在前只是可读性，判断用 in 与顺序无关。
_TABLE_TYPE_WORDS = (
    "计算公式", "明细表", "统计表", "花名册", "一览表", "样表",
    "公式", "账单", "附表", "底册", "台账", "清单",
    "记录", "目录", "说明", "表", "图",
)


def _contains_table_type_word(name: str) -> bool:
    """名称中是否出现表类型词（表 / 图 / 台账 / 明细表 …）。"""
    return any(w in (name or "") for w in _TABLE_TYPE_WORDS)


def _looks_like_non_caption(name: str) -> bool:
    """判断候选「不像表题」：纯日期 / 比例尺 / 公司名 / 列表序号 / 正文句。

    刻意不按顿号「、」判废：工程文档表名大量用顿号并列（「构筑物、设备一览表」），
    按顿号判废会误杀标准表题。全库 4295 条含顿号候选里，垃圾的真实特征是
    「行首列表序号」（3622 条，如「1、货币资金」），顿号只是巧合同现。
    """
    s = (name or "").strip()
    if not s:
        return True
    if re.fullmatch(r"\d{4}\s*年\s*\d{1,2}\s*月(\s*\d{1,2}\s*日)?", s):
        return True
    if re.fullmatch(r"\d+\s*[:：]\s*\d+", s):
        return True
    if s.endswith("公司"):
        return True
    if _LIST_LEAD_RE.match(s):
        return True
    if re.search(r"[。，；]", s):
        return True
    if len(s) >= _BODY_TEXT_MIN_LEN and not _contains_table_type_word(s):
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
    """判断模型是否返回了"无法识别/未知"类占位结果。

    除占位词表外，还叠加格式与语义两层硬过滤：公式 / 图片 / HTML 是结构性垃圾，
    日期 / 比例尺 / 公司名 / 行首序号 / 正文句是语义垃圾，两类都触发回退。
    """
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
    if _looks_like_invalid_candidate(_clean_text_line(name)):
        return True
    # 语义硬过滤：日期 / 比例尺 / 公司名 / 行首序号 / 正文句
    return _looks_like_non_caption(cleaned)
