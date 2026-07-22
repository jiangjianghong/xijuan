"""custom 规则在 copy_from / export-import 中携带 is_formatted/output_schema。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

_SCHEMA = [{"key": "公司名称", "type": "string", "example": "华为", "desc": "全称"}]


@pytest.mark.anyio
async def test_export_import_roundtrip_custom(client: AsyncClient):
    src, dst = "cust_src_type", "cust_dst_type"
    resp = await client.post("/doctype", json={"type_id": src, "type_name": src})
    assert resp.status_code == 200, resp.text
    try:
        await client.post("/extraction/fields", json={
            "field_id": "cust_f", "type_id": src, "field_name": "公司名称",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["公司"]},
            "text_extract_prompt": "从<search_result>公司</search_result>提取",
        })
        await client.post("/analysis/rules", json={
            "rule_id": "cust_r", "type_id": src, "rule_name": "自定义规则",
            "rule_type": "custom",
            "expression": "根据<field_result>cust_f</field_result>生成",
            "depend_fields": ["cust_f"], "is_formatted": 1, "output_schema": _SCHEMA,
        })

        resp = await client.get(f"/doctype/{src}/export")
        payload = resp.json()["data"]
        exported = payload["rules"][0]
        assert exported["is_formatted"] == 1
        assert exported["output_schema"][0]["key"] == "公司名称"

        resp = await client.post("/doctype/import", json={
            "target_type_id": dst, "payload": payload,
        })
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/analysis/rules?type_id={dst}")
        rule = resp.json()["data"][0]
        assert rule["rule_type"] == "custom"
        assert rule["is_formatted"] == 1
        assert rule["output_schema"][0]["key"] == "公司名称"
    finally:
        await client.delete(f"/doctype/{src}?force=true")
        await client.delete(f"/doctype/{dst}?force=true")


@pytest.mark.anyio
async def test_copy_from_keeps_custom_fields(client: AsyncClient):
    src, dst = "cust_cp_src", "cust_cp_dst"
    for tid in (src, dst):
        resp = await client.post("/doctype", json={"type_id": tid, "type_name": tid})
        assert resp.status_code == 200, resp.text
    try:
        await client.post("/extraction/fields", json={
            "field_id": "cp_f", "type_id": src, "field_name": "公司名称",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["公司"]},
            "text_extract_prompt": "从<search_result>公司</search_result>提取",
        })
        await client.post("/analysis/rules", json={
            "rule_id": "cp_r", "type_id": src, "rule_name": "自定义规则",
            "rule_type": "custom",
            "expression": "根据<field_result>cp_f</field_result>生成",
            "depend_fields": ["cp_f"], "is_formatted": 1, "output_schema": _SCHEMA,
        })
        resp = await client.post(f"/doctype/{dst}/copy_from", json={"source_type_id": src})
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/analysis/rules?type_id={dst}")
        rule = resp.json()["data"][0]
        assert rule["is_formatted"] == 1
        assert rule["output_schema"][0]["key"] == "公司名称"
    finally:
        await client.delete(f"/doctype/{src}?force=true")
        await client.delete(f"/doctype/{dst}?force=true")
