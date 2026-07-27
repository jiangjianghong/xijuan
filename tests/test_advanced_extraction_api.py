"""进阶字段：两阶段执行、保存校验、复制/导出导入重映射。

需要可用的 MySQL 连接（与其他 API 测试一致）。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

import service.extraction_service as es
from model.tables import DocType, ExtractionField, ExtractionResult, File, FileContent

# 独立类型，避免与共享 dev 库里 default 类型的既有字段互相干扰
ADV_TYPE = "advtest_type"
ADV_FILE = "advtest_file"


@pytest.fixture
async def seeded_file():
    """建「独立类型 + 文件 + 普通字段 A + 引用 A 的进阶字段 B」，用完清理。"""
    from model.database import get_session_factory

    sf = get_session_factory()
    async with sf() as s:
        await s.merge(DocType(type_id=ADV_TYPE, type_name="进阶测试类型"))
        await s.merge(
            File(file_id=ADV_FILE, type_id=ADV_TYPE, file_name="t.pdf", progress="extracting")
        )
        await s.merge(
            FileContent(
                file_id=ADV_FILE,
                file_content="甲方是华为公司。华为公司的注册资本为100万。",
                page_mapping=[],
            )
        )
        await s.merge(
            ExtractionField(
                field_id="basic_a",
                type_id=ADV_TYPE,
                field_name="甲方",
                source_type="text",
                enabled=1,
                priority=1,
                is_advanced=0,
                search_type="context",
                search_config={
                    "keywords": ["甲方"],
                    "context_before": 0,
                    "context_after": 20,
                    "max_results": 1,
                },
                text_extract_prompt="从 <search_result>甲方</search_result> 提取甲方名称",
            )
        )
        await s.merge(
            ExtractionField(
                field_id="adv_b",
                type_id=ADV_TYPE,
                field_name="注册资本",
                source_type="text",
                enabled=1,
                priority=2,
                is_advanced=1,
                depend_fields=["basic_a"],
                search_type="context",
                search_config={
                    "keywords": ["<field_result>basic_a</field_result>"],
                    "context_before": 0,
                    "context_after": 30,
                    "max_results": 1,
                },
                text_extract_prompt=(
                    "从 <search_result><field_result>basic_a</field_result></search_result> 提取注册资本"
                ),
            )
        )
        await s.commit()

    yield ADV_FILE

    async with sf() as s:
        await s.execute(delete(ExtractionResult).where(ExtractionResult.file_id == ADV_FILE))
        await s.execute(delete(ExtractionField).where(ExtractionField.type_id == ADV_TYPE))
        for table, key in ((FileContent, ADV_FILE), (File, ADV_FILE), (DocType, ADV_TYPE)):
            obj = await s.get(table, key)
            if obj:
                await s.delete(obj)
        await s.commit()


async def test_two_phase_advanced_uses_basic_value(seeded_file, monkeypatch):
    """进阶字段解析后关键词应是普通字段的值（华为公司），而非占位符。"""
    calls: list[list[str]] = []

    async def fake_search_context(content, config):
        kws = list(config.get("keywords", []))
        calls.append(kws)
        return [
            {
                "keyword": kws[0] if kws else "",
                "context": "注册资本为100万",
                "start_pos": 0,
                "end_pos": 8,
                "position": 0,
            }
        ]

    async def fake_chat(prompt, messages=None):
        return '{"value": "华为公司", "reason": "r", "pages": [2]}'

    monkeypatch.setattr(es, "search_context", fake_search_context)
    monkeypatch.setattr(es, "chat_completion", fake_chat)

    from model.database import get_session_factory

    sf = get_session_factory()
    async with sf() as s:
        await es.run_extraction(seeded_file, s)

    # 阶段1（普通字段）先跑，阶段2（进阶字段）后跑
    assert calls == [["甲方"], ["华为公司"]]

    async with sf() as s:
        row = await s.get(ExtractionResult, (ADV_FILE, "adv_b"))
        assert row is not None
        # provenance：记录引用实际填入的值
        assert row.source_refs["_resolved_refs"] == {"basic_a": "华为公司"}


async def test_upsert_advanced_rejects_missing_ref(client: AsyncClient):
    resp = await client.post(
        "/extraction/fields",
        json={
            "field_id": "adv_bad",
            "type_id": "default",
            "field_name": "坏进阶",
            "source_type": "text",
            "is_advanced": 1,
            "search_type": "context",
            "search_config": {"keywords": ["<field_result>nope_missing</field_result>"]},
            "text_extract_prompt": "从 <search_result>x</search_result> 提取",
        },
    )
    assert resp.status_code == 400
    assert "nope_missing" in resp.json()["detail"]


async def test_upsert_advanced_rejects_advanced_ref(client: AsyncClient):
    """进阶字段只能引用普通字段，引用另一个进阶字段应 400。"""
    await client.post("/doctype", json={"type_id": "advref", "type_name": "进阶引用"})
    try:
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "advref_base",
                "type_id": "advref",
                "field_name": "普通",
                "source_type": "text",
                "search_type": "context",
                "search_config": {"keywords": ["x"]},
                "text_extract_prompt": "从 <search_result>x</search_result> 提取",
            },
        )
        r = await client.post(
            "/extraction/fields",
            json={
                "field_id": "advref_first",
                "type_id": "advref",
                "field_name": "进阶一",
                "source_type": "text",
                "is_advanced": 1,
                "search_type": "context",
                "search_config": {"keywords": ["<field_result>advref_base</field_result>"]},
                "text_extract_prompt": "从 <search_result>x</search_result> 提取",
            },
        )
        assert r.status_code == 200

        r = await client.post(
            "/extraction/fields",
            json={
                "field_id": "advref_second",
                "type_id": "advref",
                "field_name": "进阶二",
                "source_type": "text",
                "is_advanced": 1,
                "search_type": "context",
                "search_config": {"keywords": ["<field_result>advref_first</field_result>"]},
                "text_extract_prompt": "从 <search_result>x</search_result> 提取",
            },
        )
        assert r.status_code == 400
        assert "advref_first" in r.json()["detail"]
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["advref"], "force": True}
        )


async def test_list_fields_returns_is_advanced(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "advlist", "type_name": "列表回传"})
    try:
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "basic_list",
                "type_id": "advlist",
                "field_name": "普通",
                "source_type": "text",
                "search_type": "context",
                "search_config": {"keywords": ["x"]},
                "text_extract_prompt": "从 <search_result>x</search_result> 提取",
            },
        )
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "adv_list",
                "type_id": "advlist",
                "field_name": "进阶",
                "source_type": "text",
                "is_advanced": 1,
                "search_type": "context",
                "search_config": {"keywords": ["<field_result>basic_list</field_result>"]},
                "text_extract_prompt": "从 <search_result>x</search_result> 提取",
            },
        )
        resp = await client.get("/extraction/fields?type_id=advlist")
        assert resp.status_code == 200
        items = resp.json()["data"]
        basic = next(r for r in items if r["field_id"] == "basic_list")
        adv = next(r for r in items if r["field_id"] == "adv_list")
        assert basic["is_advanced"] == 0
        assert basic["depend_fields"] is None
        # depend_fields 由服务端扫描配置算出
        assert adv["is_advanced"] == 1
        assert adv["depend_fields"] == ["basic_list"]
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["advlist"], "force": True}
        )


async def test_copy_from_remaps_advanced_refs(client: AsyncClient):
    """复制后进阶字段的引用应指向复制生成的新 field_id。"""
    await client.post("/doctype", json={"type_id": "srcadv", "type_name": "源"})
    await client.post("/doctype", json={"type_id": "dstadv", "type_name": "目标"})
    try:
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "srcadv_A",
                "type_id": "srcadv",
                "field_name": "甲方",
                "source_type": "text",
                "search_type": "context",
                "search_config": {"keywords": ["甲方"]},
                "text_extract_prompt": "从 <search_result>甲方</search_result> 提取",
            },
        )
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "srcadv_B",
                "type_id": "srcadv",
                "field_name": "资本",
                "source_type": "text",
                "is_advanced": 1,
                "search_type": "page",
                "search_config": {
                    "keywords": ["<field_result>srcadv_A</field_result>"],
                    "page_source_field": "srcadv_A",
                    "max_pages": 3,
                },
                "text_extract_prompt": (
                    "从 <search_result>page_content</search_result> 提取"
                    "<field_result>srcadv_A</field_result>"
                ),
            },
        )
        r = await client.post("/doctype/dstadv/copy_from", json={"source_type_id": "srcadv"})
        assert r.status_code == 200

        fields = (await client.get("/extraction/fields?type_id=dstadv")).json()["data"]
        adv = next(f for f in fields if f["is_advanced"] == 1)
        a_copy = next(f for f in fields if f["is_advanced"] == 0)
        new_id = a_copy["field_id"]
        assert new_id != "srcadv_A"  # 副本用新 id

        assert adv["search_config"]["keywords"] == [f"<field_result>{new_id}</field_result>"]
        assert adv["search_config"]["page_source_field"] == new_id
        assert f"<field_result>{new_id}</field_result>" in adv["text_extract_prompt"]
        assert adv["depend_fields"] == [new_id]
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["dstadv", "srcadv"], "force": True}
        )


async def test_export_import_roundtrip_advanced(client: AsyncClient):
    """导出携带 is_advanced/depend_fields，导入后引用重映射到新 id。"""
    await client.post("/doctype", json={"type_id": "expadv", "type_name": "导出源"})
    try:
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "expadv_A",
                "type_id": "expadv",
                "field_name": "甲方",
                "source_type": "text",
                "search_type": "context",
                "search_config": {"keywords": ["甲方"]},
                "text_extract_prompt": "从 <search_result>甲方</search_result> 提取",
            },
        )
        await client.post(
            "/extraction/fields",
            json={
                "field_id": "expadv_B",
                "type_id": "expadv",
                "field_name": "资本",
                "source_type": "text",
                "is_advanced": 1,
                "search_type": "page",
                "search_config": {
                    "keywords": ["<field_result>expadv_A</field_result>"],
                    "page_source_field": "expadv_A",
                },
                "text_extract_prompt": "从 <search_result>page_content</search_result> 提取",
            },
        )

        payload = (await client.get("/doctype/expadv/export")).json()["data"]
        adv_item = next(f for f in payload["fields"] if f["field_id"] == "expadv_B")
        assert adv_item["is_advanced"] == 1
        assert adv_item["depend_fields"] == ["expadv_A"]

        r = await client.post(
            "/doctype/import",
            json={
                "payload": payload,
                "target_type_id": "impadv",
                "create_type_if_missing": True,
            },
        )
        assert r.status_code == 200

        fields = (await client.get("/extraction/fields?type_id=impadv")).json()["data"]
        adv = next(f for f in fields if f["is_advanced"] == 1)
        a_copy = next(f for f in fields if f["is_advanced"] == 0)
        new_id = a_copy["field_id"]
        assert adv["search_config"]["keywords"] == [f"<field_result>{new_id}</field_result>"]
        assert adv["search_config"]["page_source_field"] == new_id
        assert adv["depend_fields"] == [new_id]
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["impadv", "expadv"], "force": True}
        )
