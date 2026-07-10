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


# ── json.loads 失败时的容错抢救 ──────────────────────────────
#
# 模型偶尔在 value / reason 值里输出未转义的英文双引号，导致整段响应
# 不是合法 JSON、json.loads 直接抛错。此时标准解析拿不到任何字段，
# 下面的 salvage 用正则从原始文本里抢救 value / reason 的值并规范化引号，
# 好过直接把整段原始响应当成 value 返回。
#
# 局限：依赖 value 在前、reason 在后的字段顺序（本项目所有 JSON 模板均如此）。
# 若字段顺序反了，靠谱程度下降，属于已知边界。

_SALVAGE_STR_VALUE = re.compile(r'"value"\s*:\s*"(.*?)"\s*,\s*"reason"', re.DOTALL)
_SALVAGE_JSON_VALUE = re.compile(r'"value"\s*:\s*(\[.*\]|\{.*\})\s*,\s*"reason"', re.DOTALL)
_SALVAGE_REASON = re.compile(r'"reason"\s*:\s*"(.*)"', re.DOTALL)
# 切掉贪婪匹配 reason 时吞进来的后续字段（如 ..."， "result": false）
_SALVAGE_TAIL = re.compile(r'"\s*,\s*"(?:result|value|reason)"\s*:')


def salvage_reason(response: str) -> str:
    """从非法 JSON 响应中抢救 reason 值并规范化引号；找不到返回空串。"""
    m = _SALVAGE_REASON.search(response)
    if not m:
        return ""
    body = _SALVAGE_TAIL.split(m.group(1))[0]
    return normalize_cjk_quotes(body.strip())


def salvage_value_reason(response: str) -> tuple[str, str]:
    """从非法 JSON 响应中抢救 (value, reason)；抢救不到的字段返回空串。

    value 为字符串时规范化英文双引号；value 是 list/dict 字面量时保留其
    结构性引号原样返回（避免破坏 JSON 结构）。
    """
    value = ""
    m = _SALVAGE_STR_VALUE.search(response)
    if m:
        value = normalize_cjk_quotes(m.group(1).strip())
    else:
        mj = _SALVAGE_JSON_VALUE.search(response)
        if mj:
            value = mj.group(1).strip()
    return value, salvage_reason(response)
