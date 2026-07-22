"""custom 规则 CRUD 透传 + 同步调试 API 测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

_SCHEMA = [{"key": "公司名称", "type": "string", "example": "华为", "desc": "全称"}]

_RULE = {
    "rule_id": "custom_api_rule",
    "rule_name": "自定义透传",
    "rule_type": "custom",
    "expression": "根据<field_result>a</field_result>生成公司档案",
    "depend_fields": ["a"],
    "is_formatted": 1,
    "output_schema": _SCHEMA,
}


@pytest.mark.anyio
async def test_upsert_and_read_back_custom(client: AsyncClient):
    resp = await client.post("/analysis/rules", json=_RULE)
    assert resp.status_code == 200, resp.text
    try:
        resp = await client.get("/analysis/rules")
        rule = next(r for r in resp.json()["data"] if r["rule_id"] == "custom_api_rule")
        assert rule["rule_type"] == "custom"
        assert rule["is_formatted"] == 1
        assert rule["output_schema"][0]["key"] == "公司名称"
    finally:
        await client.delete("/analysis/rules/custom_api_rule")


@pytest.mark.anyio
async def test_upsert_custom_formatted_requires_schema(client: AsyncClient):
    bad = dict(_RULE, rule_id="custom_bad", output_schema=None)
    resp = await client.post("/analysis/rules", json=bad)
    assert resp.status_code == 422
