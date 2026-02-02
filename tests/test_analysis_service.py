"""逻辑分析服务测试。"""

from __future__ import annotations

from service.analysis_service import resolve_expression


def test_resolve_expression_empty():
    """测试空表达式。"""
    result = resolve_expression("", {})
    assert result == ""


def test_resolve_expression_basic():
    """测试基本占位符替换。"""
    expression = "<field_result>total</field_result> + 100"
    values = {"total": "200"}
    result = resolve_expression(expression, values)
    assert result == "200 + 100"


def test_resolve_expression_multiple():
    """测试多个占位符替换。"""
    expression = "<field_result>a</field_result>*<field_result>b</field_result>"
    values = {"a": "10", "b": "20"}
    result = resolve_expression(expression, values)
    assert result == "10*20"
