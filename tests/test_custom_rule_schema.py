"""custom 自定义规则的 schema 校验测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from model.schemas import AnalysisRuleCreate

_SCHEMA = [
    {"key": "公司名称", "type": "string", "example": "华为", "desc": "营业执照全称"},
]


def _payload(**overrides):
    payload = {
        "rule_id": "c_rule",
        "rule_name": "自定义测试",
        "rule_type": "custom",
        "expression": "根据<field_result>a</field_result>生成公司档案",
        "depend_fields": ["a"],
    }
    payload.update(overrides)
    return payload


def test_custom_plain_ok():
    rule = AnalysisRuleCreate(**_payload())
    assert rule.rule_type == "custom"
    assert rule.is_formatted == 0


def test_custom_requires_field_result_placeholder():
    with pytest.raises(ValidationError, match="field_result"):
        AnalysisRuleCreate(**_payload(expression="没有占位符"))


def test_custom_formatted_ok():
    rule = AnalysisRuleCreate(**_payload(is_formatted=1, output_schema=_SCHEMA))
    assert rule.is_formatted == 1
    assert rule.output_schema[0]["key"] == "公司名称"


def test_custom_formatted_requires_schema():
    with pytest.raises(ValidationError, match="output_schema"):
        AnalysisRuleCreate(**_payload(is_formatted=1, output_schema=None))


def test_custom_formatted_rejects_bad_schema():
    with pytest.raises(ValidationError):
        AnalysisRuleCreate(**_payload(is_formatted=1, output_schema=[{"key": "", "type": "string"}]))


def test_is_formatted_rejects_out_of_range():
    with pytest.raises(ValidationError):
        AnalysisRuleCreate(**_payload(is_formatted=2))


def test_custom_allows_web_search():
    rule = AnalysisRuleCreate(**_payload(
        expression="结合<web_search_result/>与<field_result>a</field_result>生成",
        web_search={"enabled": True, "query": "<field_result>a</field_result> 资讯"},
    ))
    assert rule.web_search["enabled"] is True


def test_judge_still_ok():
    rule = AnalysisRuleCreate(
        rule_id="j", rule_name="判断", rule_type="judge",
        expression="判断<field_result>a</field_result>", depend_fields=["a"],
    )
    assert rule.rule_type == "judge"
