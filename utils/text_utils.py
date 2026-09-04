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
# salvage 按字段名定位，不依赖 value/reason 的先后顺序；这样新旧提示词都能兜底。

_SALVAGE_JSON_VALUE = re.compile(
    r'"value"\s*:\s*(\[.*\]|\{.*\})(?=\s*(?:,\s*"(?:reason|pages|result)"\s*:|\}))',
    re.DOTALL,
)
# 切掉贪婪匹配 reason 时吞进来的后续字段（如 ..."， "result": false）
_SALVAGE_TAIL = re.compile(r'"\s*,\s*"(?:result|value|reason)"\s*:')


def _string_field_matches(response: str, field: str) -> list[re.Match[str]]:
    return list(re.finditer(rf'"{re.escape(field)}"\s*:\s*"', response, re.DOTALL))


def _salvage_string_field(
    response: str,
    field: str,
    following: tuple[str, ...],
    *,
    field_match: re.Match[str] | None = None,
    boundary_pos: int | None = None,
) -> str:
    """按指定字段边界切出字符串，兼容值中出现未转义英文引号。"""
    field_match = field_match or re.search(
        rf'"{re.escape(field)}"\s*:\s*"', response, re.DOTALL
    )
    if not field_match:
        return ""

    body = response[field_match.end() :]
    if boundary_pos is not None:
        body = response[field_match.end() : boundary_pos]
        body = re.sub(r'\s*,\s*$', "", body, flags=re.DOTALL).rstrip()
    else:
        names = "|".join(re.escape(name) for name in following)
        boundary = re.search(rf'\s*,\s*"(?:{names})"\s*:', body, re.DOTALL)
        if boundary:
            body = body[: boundary.start()].rstrip()
        else:
            # 合法 JSON 的末尾字段通常以 } 结束；没有边界时也保留旧的宽松行为。
            body = re.split(r'\s*}\s*$', body, maxsplit=1, flags=re.DOTALL)[0].rstrip()

    # 边界前的一个英文引号是合法 JSON 的结构引号，应移除；
    # 偶数个尾部引号则视为非法 JSON 中的一对内嵌引号，需完整保留。
    if body.endswith('"') and body.count('"') % 2 == 1:
        body = body[:-1]
    return normalize_cjk_quotes(body.strip())


def salvage_reason(response: str) -> str:
    """从非法 JSON 响应中抢救 reason 值并规范化引号；找不到返回空串。"""
    reason_matches = _string_field_matches(response, "reason")
    result_matches = _string_field_matches(response, "result")
    if reason_matches and result_matches:
        reason_first = reason_matches[0].start() < result_matches[0].start()
        reason_match = reason_matches[0] if reason_first else reason_matches[-1]
        result_match = result_matches[-1] if reason_first else reason_matches[0]
        boundary = result_match.start() if reason_first else None
        reason = _salvage_string_field(
            response,
            "reason",
            ("value", "pages", "result", "reason"),
            field_match=reason_match,
            boundary_pos=boundary,
        )
    else:
        reason = _salvage_string_field(response, "reason", ("value", "pages", "result", "reason"))
    if reason:
        return reason
    # 兼容字段后仍有未闭合内容的旧响应。
    m = re.search(r'"reason"\s*:\s*"(.*)"', response, re.DOTALL)
    if not m:
        return ""
    body = _SALVAGE_TAIL.split(m.group(1))[0]
    return normalize_cjk_quotes(body.strip())


def salvage_value_reason(response: str) -> tuple[str, str]:
    """从非法 JSON 响应中抢救 (value, reason)；抢救不到的字段返回空串。

    value 为字符串时规范化英文双引号；value 是 list/dict 字面量时保留其
    结构性引号原样返回（避免破坏 JSON 结构）。
    """
    value_matches = _string_field_matches(response, "value")
    reason_matches = _string_field_matches(response, "reason")
    value_match = value_matches[0] if value_matches else None
    reason_match = reason_matches[0] if reason_matches else None
    value_boundary = None
    reason_boundary = None
    if value_matches and reason_matches:
        reason_first = reason_matches[0].start() < value_matches[0].start()
        value_match = value_matches[-1] if reason_first else value_matches[0]
        reason_match = reason_matches[0] if reason_first else reason_matches[-1]
        if reason_first:
            reason_boundary = value_match.start()
        else:
            value_boundary = reason_match.start()

    value = _salvage_string_field(
        response,
        "value",
        ("reason", "pages", "result", "value"),
        field_match=value_match,
        boundary_pos=value_boundary,
    )
    if not value:
        mj = _SALVAGE_JSON_VALUE.search(response)
        if mj:
            value = mj.group(1).strip()

    if reason_match is not None:
        reason = _salvage_string_field(
            response,
            "reason",
            ("value", "pages", "result", "reason"),
            field_match=reason_match,
            boundary_pos=reason_boundary,
        )
    else:
        reason = salvage_reason(response)
    return value, reason
