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
