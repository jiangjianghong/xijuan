"""逻辑分析网络搜索：schema 校验 + 服务层测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
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


# ── API 透传 ────────────────────────────────────────────────

_API_RULE = {
    "rule_id": "ws_api_test_rule",
    "rule_name": "搜索透传测试",
    "rule_type": "judge",
    "expression": "结合<web_search_result/>判断<field_result>a</field_result>",
    "depend_fields": ["a"],
    "web_search": {"enabled": True, "query": "<field_result>a</field_result> 资讯", "count": 3, "freshness": "oneYear"},
}


@pytest.mark.anyio
async def test_upsert_and_list_rule_with_web_search(client: AsyncClient):
    """upsert 透传 web_search 并能在列表读回。"""
    resp = await client.post("/analysis/rules", json=_API_RULE)
    assert resp.status_code == 200, resp.text

    try:
        resp = await client.get("/analysis/rules")
        rules = resp.json()["data"]
        rule = next(r for r in rules if r["rule_id"] == "ws_api_test_rule")
        assert rule["web_search"]["enabled"] is True
        assert rule["web_search"]["count"] == 3

        # 更新为关闭
        updated = dict(_API_RULE, web_search={"enabled": False})
        resp = await client.post("/analysis/rules", json=updated)
        assert resp.status_code == 200

        resp = await client.get("/analysis/rules")
        rule = next(r for r in resp.json()["data"] if r["rule_id"] == "ws_api_test_rule")
        assert rule["web_search"] == {"enabled": False}
    finally:
        await client.delete("/analysis/rules/ws_api_test_rule")


@pytest.mark.anyio
async def test_upsert_rule_web_search_validation(client: AsyncClient):
    """开启搜索但缺占位符 → 422。"""
    bad = dict(_API_RULE, rule_id="ws_bad_rule", expression="判断<field_result>a</field_result>")
    resp = await client.post("/analysis/rules", json=bad)
    assert resp.status_code == 422


# ── copy_from 占位符重映射 ──────────────────────────────────

@pytest.mark.anyio
async def test_copy_from_remaps_placeholders(client: AsyncClient):
    """copy_from 重映射 expression 与 web_search.query 中的 <field_result> 占位符。"""
    src_type, dst_type = "ws_src_type", "ws_dst_type"
    field_id, rule_id = "ws_src_field", "ws_src_rule"

    # 建源/目标类型
    for tid in (src_type, dst_type):
        resp = await client.post("/doctype", json={"type_id": tid, "type_name": tid})
        assert resp.status_code == 200, resp.text

    try:
        # 源类型建字段 + 带搜索的规则
        resp = await client.post("/extraction/fields", json={
            "field_id": field_id,
            "type_id": src_type,
            "field_name": "公司名称",
            "source_type": "text",
            "search_type": "context",
            "search_config": {"keywords": ["公司"]},
            "text_extract_prompt": "从<search_result>公司</search_result>提取公司名称",
        })
        assert resp.status_code == 200, resp.text

        resp = await client.post("/analysis/rules", json={
            "rule_id": rule_id,
            "type_id": src_type,
            "rule_name": "搜索重映射测试",
            "rule_type": "judge",
            "expression": "结合<web_search_result/>判断<field_result>ws_src_field</field_result>是否被处罚",
            "depend_fields": [field_id],
            "web_search": {"enabled": True, "query": "<field_result>ws_src_field</field_result> 行政处罚"},
        })
        assert resp.status_code == 200, resp.text

        # 复制到目标类型
        resp = await client.post(f"/doctype/{dst_type}/copy_from", json={"source_type_id": src_type})
        assert resp.status_code == 200, resp.text

        # 读回目标类型的字段/规则
        resp = await client.get(f"/extraction/fields?type_id={dst_type}")
        new_field_id = resp.json()["data"][0]["field_id"]
        assert new_field_id != field_id

        resp = await client.get(f"/analysis/rules?type_id={dst_type}")
        rule = resp.json()["data"][0]
        assert rule["depend_fields"] == [new_field_id]
        # expression 占位符已重映射
        assert f"<field_result>{new_field_id}</field_result>" in rule["expression"]
        assert f"<field_result>{field_id}</field_result>" not in rule["expression"]
        # web_search.query 占位符已重映射，其余键原样保留
        assert rule["web_search"]["enabled"] is True
        assert f"<field_result>{new_field_id}</field_result>" in rule["web_search"]["query"]
    finally:
        # 级联清理（带配置的类型需 force）
        await client.delete(f"/doctype/{src_type}?force=true")
        await client.delete(f"/doctype/{dst_type}?force=true")


@pytest.mark.anyio
async def test_import_remaps_rule_placeholders(client: AsyncClient):
    """doctype/import 生成新 field_id 后，同步重映射规则占位符。"""
    target_type = "ws_import_dst_type"
    source_field_id = "ws_import_src_field"

    payload = {
        "type_id": "ws_import_src_type",
        "type_name": "导入源类型",
        "version": 1,
        "fields": [
            {
                "field_id": source_field_id,
                "field_name": "公司名称",
                "source_type": "text",
                "search_type": "context",
                "search_config": {"keywords": ["公司"]},
                "text_extract_prompt": "从<search_result>公司</search_result>提取公司名称",
            }
        ],
        "rules": [
            {
                "rule_id": "ws_import_src_rule",
                "rule_name": "导入重映射测试",
                "rule_type": "judge",
                "expression": "结合<web_search_result/>判断<field_result>ws_import_src_field</field_result>是否被处罚",
                "depend_field_names": ["公司名称"],
                "web_search": {
                    "enabled": True,
                    "query": "<field_result>ws_import_src_field</field_result> 行政处罚",
                },
            }
        ],
    }

    try:
        resp = await client.post(
            "/doctype/import",
            json={"target_type_id": target_type, "payload": payload},
        )
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/extraction/fields?type_id={target_type}")
        new_field_id = resp.json()["data"][0]["field_id"]
        assert new_field_id != source_field_id

        resp = await client.get(f"/analysis/rules?type_id={target_type}")
        rule = resp.json()["data"][0]
        assert rule["depend_fields"] == [new_field_id]
        assert f"<field_result>{new_field_id}</field_result>" in rule["expression"]
        assert f"<field_result>{source_field_id}</field_result>" not in rule["expression"]
        assert f"<field_result>{new_field_id}</field_result>" in rule["web_search"]["query"]
        assert f"<field_result>{source_field_id}</field_result>" not in rule["web_search"]["query"]
    finally:
        await client.delete(f"/doctype/{target_type}?force=true")
