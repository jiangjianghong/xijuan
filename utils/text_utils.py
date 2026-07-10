"""文本规范化工具。"""

from __future__ import annotations

import re

# 成对英文双引号 -> 中文左右引号
_PAIRED_DQUOTE = re.compile(r'"([^"]*)"')


def normalize_cjk_quotes(text: str) -> str:
    """将英文双引号替换为中文引号。

    模型偶尔在 value / reason 文本里输出英文双引号，既不美观，也可能在
    下游把该文本再次序列化为 JSON 时破坏结构。成对的英文双引号转中文左右
    引号（“ ”），落单的兜底转中文右引号（”）。其它英文标点（逗号、句号、
    冒号等）在字符串值内部不影响 JSON 解析，按需求保留不动。

    注意：仅用于标量 value 与 reason 文本；list/dict 序列化后的 JSON
    字符串不要传入本函数，否则会破坏其结构性引号。
    """
    if not text or '"' not in text:
        return text
    text = _PAIRED_DQUOTE.sub(r"“\1”", text)
    # 处理落单的英文双引号
    return text.replace('"', "”")
