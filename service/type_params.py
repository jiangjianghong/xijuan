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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

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


def _resolve_list(items: Sequence[Any], resolve: Callable[[Any], Any]) -> List[Any]:
    """逐项解析列表，只剔除「本来含参数引用、解析后变空」的项。

    与 extraction_service._resolve_str_list 同语义（那边判的是 <field_result>）：
    不含引用的项原样保留（含刻意留的空白项，例如 stop_words），被剔除的只有因为
    参数没取到值而变空的项——留着会让关键词退化成全文命中。

    收 resolve 回调而非 params：调用方的 _res 除了替换还负责把用到的参数记进
    provenance，直接调 resolve_param_refs 会让列表里用到的参数不进溯源。

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
        value = resolve(item)
        if had_ref and not str(value).strip():
            continue
        out.append(value)
    return out


# ── 引用扫描：遍历位置与 extraction_service.collect_depend_fields 一一对应 ──


def collect_field_param_refs(field: Any) -> List[str]:
    """扫出字段配置里引用的全部参数 key，按出现顺序去重。

    位置清单与 collect_depend_fields 对齐，唯一区别是**不含 page_source_field /
    vl_config.page_source_field**——那两个存的是字段 ID，不是参数 key。
    """
    seen: Dict[str, None] = {}

    def _add(text: Any) -> None:
        for key in collect_param_refs(text):
            seen.setdefault(key, None)

    for attr in (
        "table_name_pattern",
        "table_extract_prompt", "table_system_prompt", "table_match_prompt",
        "text_extract_prompt", "text_system_prompt",
        "vl_extract_prompt", "vl_system_prompt",
    ):
        _add(getattr(field, attr, None))

    for keyword in (getattr(field, "table_match_keywords", None) or []):
        _add(keyword)

    search_config = getattr(field, "search_config", None) or {}
    if isinstance(search_config, dict):
        for value in search_config.values():
            if isinstance(value, str):
                _add(value)
            elif isinstance(value, list):
                for item in value:
                    _add(item)

    vl_config = getattr(field, "vl_config", None) or {}
    if isinstance(vl_config, dict):
        for key in ("field_hints", "batch_prompt_template", "locate_prompt_template"):
            _add(vl_config.get(key))

    return list(seen.keys())


def collect_rule_param_refs(rule: Any) -> List[str]:
    """扫出规则配置里引用的全部参数 key（expression / system_prompt / web_search.query）。"""
    seen: Dict[str, None] = {}

    def _add(text: Any) -> None:
        for key in collect_param_refs(text):
            seen.setdefault(key, None)

    _add(getattr(rule, "expression", None))
    _add(getattr(rule, "system_prompt", None))

    web_search = getattr(rule, "web_search", None)
    if isinstance(web_search, dict):
        _add(web_search.get("query"))

    return list(seen.keys())


# ── 渲染 ──────────────────────────────────────────────────


def render_field_params(
    field: Any, params: Mapping[str, Any]
) -> Tuple[Any, Dict[str, Any]]:
    """把字段配置里的 <param> 渲染成实参，返回 (游离副本, provenance)。

    provenance 形如 {"_params": {key: 实际填入值}}，由调用方并进 source_refs。
    字段不引用任何参数时原样返回本体、provenance 为空 dict——绝大多数字段都
    不引用参数，省掉无谓的深拷贝。

    绝不改动传入对象：那是会话内的 ORM 实例，就地改会让 commit 把渲染结果写回
    extraction_field 表。
    """
    if not collect_field_param_refs(field):
        return field, {}

    # 延迟导入避免与 extraction_service 循环依赖（同 extraction_snapshot 的做法）
    from service.extraction_service import _clone_field_transient

    used: Dict[str, str] = {}

    def _res(text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        for key in collect_param_refs(text):
            value = params.get(key)
            used[key] = "" if value is None else str(value)
        return resolve_param_refs(text, params)

    search_config = field.search_config
    if isinstance(search_config, dict):
        search_config = copy.deepcopy(search_config)
        for key, value in list(search_config.items()):
            if isinstance(value, str):
                search_config[key] = _res(value)
            elif isinstance(value, list):
                search_config[key] = _resolve_list(value, _res)

    table_match_keywords = field.table_match_keywords
    if isinstance(table_match_keywords, list):
        table_match_keywords = _resolve_list(table_match_keywords, _res)

    vl_config = field.vl_config
    if isinstance(vl_config, dict):
        vl_config = copy.deepcopy(vl_config)
        for key in ("field_hints", "batch_prompt_template", "locate_prompt_template"):
            if isinstance(vl_config.get(key), str):
                vl_config[key] = _res(vl_config[key])

    resolved = _clone_field_transient(
        field,
        search_config=search_config,
        table_match_keywords=table_match_keywords,
        vl_config=vl_config,
        # table_name_pattern 同时是表格抽取的占位符 label，必须与 prompt 一起渲染，
        # 否则 label 仍是占位符原文、prompt 已是实际值，两边对不上导致「未找到」
        table_name_pattern=_res(field.table_name_pattern),
        table_extract_prompt=_res(field.table_extract_prompt),
        table_system_prompt=_res(field.table_system_prompt),
        table_match_prompt=_res(field.table_match_prompt),
        text_extract_prompt=_res(field.text_extract_prompt),
        text_system_prompt=_res(field.text_system_prompt),
        vl_extract_prompt=_res(field.vl_extract_prompt),
        vl_system_prompt=_res(field.vl_system_prompt),
    )
    return resolved, {"_params": used}


@dataclass(frozen=True)
class RenderedRule:
    """规则配置里三个可含参数的位置的渲染结果。

    刻意不返回克隆后的规则对象：分析侧有三种规则载体（ORM AnalysisRule、
    FileRuleSnapshot、AnalysisRuleSnapshot），克隆要按类型分支；而实际被渲染的
    只有这三个位置，直接把它们交给调用方最简单也最诚实。
    """

    expression: str
    system_prompt: str
    web_search: Optional[dict]
    params_used: Dict[str, str]


def render_rule_params(rule: Any, params: Mapping[str, Any]) -> RenderedRule:
    """把规则的 expression / system_prompt / web_search.query 渲染成实参。

    calc 规则的 expression 走 numexpr 数学求值，参数替换成非数字会让表达式报错。
    照常替换（与 <field_result> 现有行为一致），失败即该规则失败并在 reason 中
    说明，不在这里拦截。
    """
    expression = getattr(rule, "expression", None) or ""
    system_prompt = getattr(rule, "system_prompt", None) or ""
    web_search = getattr(rule, "web_search", None)

    if not collect_rule_param_refs(rule):
        return RenderedRule(expression, system_prompt, web_search, {})

    used: Dict[str, str] = {}

    def _res(text: Any) -> str:
        if not isinstance(text, str) or not text:
            return text or ""
        for key in collect_param_refs(text):
            value = params.get(key)
            used[key] = "" if value is None else str(value)
        return resolve_param_refs(text, params)

    if isinstance(web_search, dict):
        web_search = copy.deepcopy(web_search)
        if isinstance(web_search.get("query"), str):
            web_search["query"] = _res(web_search["query"])

    return RenderedRule(
        expression=_res(expression),
        system_prompt=_res(system_prompt),
        web_search=web_search,
        params_used=used,
    )
