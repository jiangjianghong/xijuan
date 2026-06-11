"""逻辑分析网络搜索：schema 校验 + 服务层测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from model.schemas import AnalysisRuleCreate


def _judge_payload(**overrides):
    payload = {
        "rule_id": "ws_rule",
        "rule_name": "搜索测试规则",
        "rule_type": "judge",
        "expression": "结合搜索结果<web_search_result/>判断<field_result>a</field_result>是否合规",
        "depend_fields": ["a"],
    }
    payload.update(overrides)
    return payload


def test_web_search_valid():
    rule = AnalysisRuleCreate(**_judge_payload(web_search={"enabled": True, "query": "<field_result>a</field_result> 处罚"}))
    assert rule.web_search["enabled"] is True


def test_web_search_none_ok():
    rule = AnalysisRuleCreate(**_judge_payload())
    assert rule.web_search is None


def test_web_search_disabled_skips_checks():
    """关闭状态下不要求 query 与占位符。"""
    rule = AnalysisRuleCreate(**_judge_payload(
        expression="判断<field_result>a</field_result>",
        web_search={"enabled": False},
    ))
    assert rule.web_search == {"enabled": False}


def test_web_search_requires_query():
    with pytest.raises(ValidationError, match="query"):
        AnalysisRuleCreate(**_judge_payload(web_search={"enabled": True, "query": "  "}))


def test_web_search_requires_placeholder():
    with pytest.raises(ValidationError, match="web_search_result"):
        AnalysisRuleCreate(**_judge_payload(
            expression="判断<field_result>a</field_result>",
            web_search={"enabled": True, "query": "处罚"},
        ))


def test_web_search_judge_only():
    with pytest.raises(ValidationError, match="judge"):
        AnalysisRuleCreate(**_judge_payload(
            rule_type="calc",
            expression="<field_result>a</field_result>*2",
            web_search={"enabled": True, "query": "处罚"},
        ))


# ── apply_web_search ────────────────────────────────────────

async def test_apply_web_search_disabled():
    from service.analysis_service import apply_web_search

    expr = "背景:<web_search_result/> 判断"
    out, ref = await apply_web_search(expr, None, {})
    assert out == expr
    assert ref is None

    out, ref = await apply_web_search(expr, {"enabled": False, "query": "x"}, {})
    assert out == expr
    assert ref is None


async def test_apply_web_search_replaces(monkeypatch):
    from service import analysis_service

    async def fake_search(query, *, count=None, freshness=None):
        assert count == 3
        return "[1] 搜索结果文本", [{"name": "搜索结果文本", "url": "https://a.com"}]

    monkeypatch.setattr(analysis_service, "bocha_web_search", fake_search)

    ws = {"enabled": True, "query": "<field_result>company</field_result> 行政处罚", "count": 3}
    out, ref = await analysis_service.apply_web_search(
        "背景:<web_search_result/>\n判断万科是否被处罚", ws, {"company": "万科"}
    )
    assert "[1] 搜索结果文本" in out
    assert "<web_search_result/>" not in out
    assert ref["query"] == "万科 行政处罚"
    assert ref["results"][0]["url"] == "https://a.com"


async def test_apply_web_search_failure_not_fatal(monkeypatch):
    from service import analysis_service

    async def fake_search(query, *, count=None, freshness=None):
        raise RuntimeError("接口超时")

    monkeypatch.setattr(analysis_service, "bocha_web_search", fake_search)

    ws = {"enabled": True, "query": "万科 处罚"}
    out, ref = await analysis_service.apply_web_search("背景:<web_search_result/>", ws, {})
    assert "网络搜索失败" in out
    assert ref["error"] == "接口超时"
    assert ref["results"] == []
