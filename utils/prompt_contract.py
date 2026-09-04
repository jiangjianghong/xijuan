"""LLM 固定输出提示词契约。"""

from __future__ import annotations


REASON_FIRST_VALUE_CONSTRAINT = (
    "请务必先输出 reason（分步、可核验的分析和判定依据），"
    "再输出 value（最终要求输出的结果）。请将 reason 写成一步步的分析和判定过程；"
    "必须先完成分析，再给出结论；"
    "不得在分析尚未完成时提前猜测最终结果。"
)

REASON_FIRST_RESULT_CONSTRAINT = (
    "请务必先输出 reason（分步、可核验的分析和判定依据），"
    "再输出 result（最终要求输出的 true/false 判定结果）。请将 reason 写成一步步的分析和判定过程；"
    "必须先完成分析，再给出结论；"
    "不得在分析尚未完成时提前猜测最终结果。"
)


def build_reason_first_value_suffix(*, include_pages: bool = False) -> str:
    """构造附加在用户提取提示词后的统一 value 输出约束。"""
    pages = ', "pages": [3,5]' if include_pages else ""
    return (
        "【固定输出约束】"
        f"{REASON_FIRST_VALUE_CONSTRAINT}"
        f'只返回一个 JSON 对象，字段顺序必须严格为 reason、value{", pages" if include_pages else ""}：'
        f'{{"reason":"分步、可核验的分析和判定依据","value":"最终要求输出的结果"{pages}}}'
    )
