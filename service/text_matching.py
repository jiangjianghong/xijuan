"""文本检索共用正则匹配与原文坐标投影。"""

from array import array
from bisect import bisect_left
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import re


def _is_horizontal_space(char: str) -> bool:
    return char.isspace() and char not in "\r\n\v\f\u0085\u2028\u2029"


def strip_horizontal_space(text: str) -> str:
    """只忽略横向排版空白，保留换行、段落及页边界。"""
    return "".join(c for c in text if not _is_horizontal_space(c))


def _compact_pattern(keyword: str) -> str:
    """只忽略模式里的普通行内空格，保护转义、字符组和扩展模式注释。"""
    if re.search(r"\(\?[aiLmsux-]*x", keyword):
        return keyword
    output = []
    escaped = False
    in_class = False
    for char in keyword:
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        if in_class or not _is_horizontal_space(char):
            output.append(char)
    return "".join(output)


@lru_cache(maxsize=512)
def compile_keyword(keyword: str, case_sensitive: bool = True, ignore_whitespace: bool = False) -> re.Pattern:
    """关键词直接使用 Python 正则；错误表达式返回中文错误。"""
    try:
        if ignore_whitespace:
            keyword = _compact_pattern(keyword)
        return re.compile(keyword, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"关键词正则无效「{keyword}」：{exc}") from exc


@dataclass(frozen=True)
class TextProjection:
    text: str
    offsets: array
    ignore_whitespace: bool = False
    original: str = ""

    @classmethod
    def build(cls, text: str, ignore_whitespace: bool = False):
        if not ignore_whitespace:
            return cls(text, array("Q"), False, text)
        offsets = array("Q", (i for i, c in enumerate(text) if not _is_horizontal_space(c)))
        return cls("".join(text[i] for i in offsets), offsets, True, text)

    def spans(self, keyword: str, case_sensitive: bool = True):
        """投影上匹配后恢复原文半开区间；忽略零长度命中。"""
        if not keyword:
            return
        spans = set()
        for match in compile_keyword(keyword, case_sensitive, self.ignore_whitespace).finditer(self.text):
            start, end = match.span()
            if start == end:
                continue
            if self.ignore_whitespace:
                spans.add((self.offsets[start], self.offsets[end - 1] + 1))
            else:
                yield start, end
        if self.ignore_whitespace:
            # 原文匹配与投影匹配取并集：显式要求空格的正则仍能命中。
            for match in compile_keyword(keyword, case_sensitive).finditer(self.original):
                if match.start() != match.end():
                    spans.add(match.span())
            yield from sorted(spans)

    def literal_spans(self, literal: str, start: int = 0, end: int | None = None,
                      require_end: bool = True, from_end: bool = False):
        """按字面量查找指定原文窗口，并在启用空白容忍时忽略横向空白。

        ``require_end=False`` 用于前向边界：停止词起点落入窗口即可截断，
        即使停止词末端越过窗口也不能漏掉。``from_end=True`` 只返回窗口内
        最靠近末端的一次命中，适合反向边界判断。
        """
        use_projection = self.ignore_whitespace
        if use_projection:
            compact_literal = strip_horizontal_space(literal)
            if compact_literal:
                literal = compact_literal
            else:
                # 全是横向空白时保留停止词的字面量语义，避免配置项静默失效。
                use_projection = False
        if end is None:
            end = len(self.original)
        if end < start:
            return
        if not literal:
            boundary = end if from_end else start
            yield boundary, boundary
            return
        if end == start:
            return

        if use_projection:
            search_start = bisect_left(self.offsets, start)
            projected_end = bisect_left(self.offsets, end)
            search_text = self.text
        else:
            search_start = start
            projected_end = end
            search_text = self.original

        while search_start < projected_end:
            if from_end:
                match_start = search_text.rfind(literal, search_start, projected_end)
            else:
                # require_end=False 允许停止词末端越出窗口，但起点必须落在窗口内，
                # 因此把 find 上界放宽一个字面量长度而不是扫描到全文末尾。
                find_end = projected_end if require_end else projected_end + len(literal) - 1
                match_start = search_text.find(literal, search_start, find_end)
            if match_start == -1 or match_start >= projected_end:
                return
            match_end = match_start + len(literal)
            if not require_end or match_end <= projected_end:
                if use_projection:
                    yield self.offsets[match_start], self.offsets[match_end - 1] + 1
                else:
                    yield match_start, match_end
            if from_end:
                return
            # 停止词需要保留重叠命中，才能复现 rfind 的最近字面量边界。
            search_start = match_start + 1


def matches_text(text: str, keyword: str, *, match_type: str = "contains",
                 ignore_whitespace: bool = False, threshold: float = 0.8,
                 case_sensitive: bool = False) -> bool:
    """精确模式全串匹配；模糊模式普通词算相似度、正则按搜索执行。"""
    if not keyword:
        return False
    pattern = compile_keyword(keyword, case_sensitive if match_type != "exact" else True, ignore_whitespace)
    original = text or ""
    text = strip_horizontal_space(original) if ignore_whitespace else original
    if match_type == "exact":
        match = pattern.fullmatch(text)
        original_match = compile_keyword(keyword, True).fullmatch(original) if ignore_whitespace else None
        return bool((match and match.start() != match.end()) or
                    (original_match and original_match.start() != original_match.end()))
    if match_type == "fuzzy" and not any(c in keyword for c in r"\.^$*+?{}[]|()"):
        needle = strip_horizontal_space(keyword) if ignore_whitespace else keyword
        return SequenceMatcher(None, text, needle).ratio() >= threshold
    return next(TextProjection.build(original, ignore_whitespace).spans(keyword, case_sensitive), None) is not None
