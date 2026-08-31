"""<param> 占位符的收集与解析。"""

from __future__ import annotations

from service.type_params import (
    PARAM_REF_PATTERN,
    collect_param_refs,
    resolve_param_refs,
)


def test_collect_param_refs_dedups_in_order():
    text = "<param>b</param> 和 <param>a</param> 再来一次 <param>b</param>"
    assert collect_param_refs(text) == ["b", "a"]


def test_collect_param_refs_ignores_non_string():
    assert collect_param_refs(None) == []
    assert collect_param_refs(123) == []
    assert collect_param_refs("") == []


def test_collect_param_refs_strips_whitespace():
    assert collect_param_refs("<param> current_date </param>") == ["current_date"]


def test_resolve_param_refs_substitutes():
    out = resolve_param_refs("今天是<param>d</param>。", {"d": "2026-08-31"})
    assert out == "今天是2026-08-31。"


def test_resolve_param_refs_missing_becomes_empty():
    assert resolve_param_refs("[<param>nope</param>]", {}) == "[]"


def test_resolve_param_refs_none_value_becomes_empty():
    assert resolve_param_refs("[<param>d</param>]", {"d": None}) == "[]"


def test_resolved_value_is_not_rescanned():
    """参数值里含 <param> 字面量时不被二次解析（re.sub 单趟替换）。"""
    out = resolve_param_refs(
        "<param>a</param>", {"a": "<param>b</param>", "b": "炸了"}
    )
    assert out == "<param>b</param>"


def test_pattern_is_non_greedy():
    assert PARAM_REF_PATTERN.findall("<param>a</param>x<param>b</param>") == ["a", "b"]


# ── 引用扫描与配置渲染 ──────────────────────────────────────

from model.tables import AnalysisRule, ExtractionField  # noqa: E402
from service.type_params import (  # noqa: E402
    collect_field_param_refs,
    collect_rule_param_refs,
    render_field_params,
    render_rule_params,
)


def _text_field(**kw) -> ExtractionField:
    base = dict(
        field_id="f1", type_id="t1", field_name="字段", source_type="text",
        search_type="context", enabled=1, priority=0,
    )
    base.update(kw)
    return ExtractionField(**base)


def test_collect_field_param_refs_covers_all_positions():
    field = _text_field(
        text_extract_prompt="今天是<param>d</param>",
        text_system_prompt="<param>role</param>",
        search_config={"keywords": ["<param>year</param>年报"], "query_text": "<param>q</param>"},
    )
    assert set(collect_field_param_refs(field)) == {"d", "role", "year", "q"}


def test_collect_field_param_refs_covers_table_and_vl():
    field = _text_field(
        source_type="table",
        table_name_pattern="<param>year</param>年度表",
        table_match_keywords=["<param>kw</param>"],
        table_match_prompt="<param>tmp</param>",
        vl_config={"field_hints": "<param>hint</param>"},
    )
    assert set(collect_field_param_refs(field)) == {"year", "kw", "tmp", "hint"}


def test_render_field_params_returns_same_object_when_no_refs():
    """无引用时不克隆，省一次深拷贝（绝大多数字段都不引用参数）。"""
    field = _text_field(text_extract_prompt="没有占位符")
    rendered, provenance = render_field_params(field, {"d": "x"})
    assert rendered is field
    assert provenance == {}


def test_render_field_params_does_not_mutate_original():
    field = _text_field(
        text_extract_prompt="今天是<param>d</param>",
        search_config={"keywords": ["<param>year</param>年报"]},
    )
    rendered, provenance = render_field_params(field, {"d": "2026-08-31", "year": "2025"})

    assert rendered is not field
    assert rendered.text_extract_prompt == "今天是2026-08-31"
    assert rendered.search_config["keywords"] == ["2025年报"]
    assert provenance == {"_params": {"d": "2026-08-31", "year": "2025"}}
    # 原对象纹丝不动，否则 commit 会把渲染结果写回 extraction_field
    assert field.text_extract_prompt == "今天是<param>d</param>"
    assert field.search_config["keywords"] == ["<param>year</param>年报"]


def test_render_field_params_drops_emptied_keywords():
    field = _text_field(search_config={"keywords": ["<param>missing</param>", "固定词"]})
    rendered, _ = render_field_params(field, {})
    assert rendered.search_config["keywords"] == ["固定词"]


def test_collect_rule_param_refs_covers_expression_and_web_search():
    rule = AnalysisRule(
        rule_id="r1", type_id="t1", rule_name="规则", rule_type="judge",
        expression="截至<param>d</param>是否有效",
        system_prompt="<param>role</param>",
        web_search={"enabled": True, "query": "<param>company</param> 资质"},
    )
    assert set(collect_rule_param_refs(rule)) == {"d", "role", "company"}


def test_render_rule_params_renders_three_positions():
    rule = AnalysisRule(
        rule_id="r1", type_id="t1", rule_name="规则", rule_type="judge",
        expression="截至<param>d</param>是否有效",
        system_prompt="你是<param>role</param>",
        web_search={"enabled": True, "query": "<param>company</param> 资质"},
    )
    rendered = render_rule_params(rule, {"d": "2026-08-31", "role": "审核员", "company": "甲公司"})

    assert rendered.expression == "截至2026-08-31是否有效"
    assert rendered.system_prompt == "你是审核员"
    assert rendered.web_search["query"] == "甲公司 资质"
    assert rendered.params_used == {"d": "2026-08-31", "role": "审核员", "company": "甲公司"}
    # 原对象不动
    assert rule.web_search["query"] == "<param>company</param> 资质"


def test_render_rule_params_no_refs_yields_empty_params_used():
    rule = AnalysisRule(
        rule_id="r1", type_id="t1", rule_name="规则", rule_type="calc",
        expression="1 + 1", system_prompt=None, web_search=None,
    )
    rendered = render_rule_params(rule, {"d": "x"})
    assert rendered.expression == "1 + 1"
    assert rendered.system_prompt == ""
    assert rendered.web_search is None
    assert rendered.params_used == {}
