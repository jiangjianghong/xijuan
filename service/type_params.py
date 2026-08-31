"""文档类型入参：第三类占位符 <param>key</param> 的解析与渲染。

现有两类占位符都源于文档自身——<search_result> 是文档内检索到的原文，
<field_result> 是同类型其它字段的抽取结果。本模块引入第三类：由调用方在提交
时传入的运行时上下文（当前时间、申报年度、送审批次号等文档里不会写的东西）。

与 <field_result> 那套（service/extraction_service.py）逐处同构，遍历位置刻意
保持一一对应，改动其中一边时另一边也要跟。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

PARAM_REF_PATTERN = re.compile(r"<param>(.+?)</param>")


def collect_param_refs(text: Any) -> List[str]:
    """扫出文本里引用的参数 key，按出现顺序去重。非字符串返回空列表。"""
    if not isinstance(text, str) or not text:
        return []
    seen: Dict[str, None] = {}
    for match in PARAM_REF_PATTERN.finditer(text):
        key = match.group(1).strip()
        if key:
            seen.setdefault(key, None)
    return list(seen.keys())


def resolve_param_refs(text: str, params: Mapping[str, Any]) -> str:
    """把 <param>key</param> 替换成实参。

    未定义的 key 替换成空串并打 warning——与 <field_result> 的空引用行为一致。
    单趟 re.sub，替换出来的文本不会被再次扫描，故参数值里的 <param> 字面量安全。
    """

    def _repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key not in params:
            logger.warning("配置引用了未定义的入参: param_key={}", key)
            return ""
        value = params.get(key)
        return "" if value is None else str(value)

    return PARAM_REF_PATTERN.sub(_repl, text)


def _resolve_list(items: Sequence[Any], params: Mapping[str, Any]) -> List[Any]:
    """逐项解析列表，只剔除「本来含参数引用、解析后变空」的项。

    与 extraction_service._resolve_str_list 同语义（那边判的是 <field_result>）：
    不含引用的项原样保留（含刻意留的空白项，例如 stop_words），被剔除的只有因为
    参数没取到值而变空的项——留着会让关键词退化成全文命中。

    没有复用 _resolve_str_list 而是复写一遍：那边的「是否含引用」判定写死了
    collect_field_refs，改成可注入会让 extraction_service 反过来 import 本模块，
    与本模块对 _clone_field_transient 的依赖形成循环。十行的重复比循环导入划算。
    """
    out: List[Any] = []
    for item in items:
        if not isinstance(item, str):
            out.append(item)
            continue
        had_ref = bool(collect_param_refs(item))
        value = resolve_param_refs(item, params)
        if had_ref and not value.strip():
            continue
        out.append(value)
    return out
