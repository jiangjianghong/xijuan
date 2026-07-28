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


async def test_advanced_empty_upstream_marks_failed(seeded_file, monkeypatch):
    """P0 回归：上游普通字段抽空 → 进阶字段必须记为失败，而不是「成功但值为空」。"""

    async def fake_search_context(content, config):
        # 关键词被剔空（上游没值）→ 无命中
        if not config.get("keywords"):
            return []
        return [
            {
                "keyword": config["keywords"][0],
                "context": "片段",
                "start_pos": 0,
                "end_pos": 2,
                "position": 0,
            }
        ]

    async def fake_chat(prompt, messages=None):
        # 普通字段抽不到值
        return '{"value": "", "reason": "未找到", "pages": []}'

    monkeypatch.setattr(es, "search_context", fake_search_context)
    monkeypatch.setattr(es, "chat_completion", fake_chat)

    from model.database import get_session_factory

    sf = get_session_factory()
    async with sf() as s:
        await es.run_extraction(seeded_file, s)

    async with sf() as s:
        adv = await s.get(ExtractionResult, (ADV_FILE, "adv_b"))
        # 失败分支落的是「空值 + 失败原因 + 无来源」，而不是带 _resolved_refs 的成功记录
        assert adv is not None
        assert adv.extracted_value == ""
        assert adv.source_refs is None
        # 原因要指出是上游字段没取到值，而不是笼统的「未提取到结果」
        assert "basic_a" in (adv.reason or "")


async def test_test_endpoint_resolves_advanced_refs(seeded_file, client: AsyncClient, monkeypatch):
    """非流式 /extraction/test 对进阶字段也要先解析引用（审查 §2.2）。"""
    seen = {}

    async def fake_search_context(content, config):
        seen["kw"] = list(config.get("keywords", []))
        return [
            {"keyword": config["keywords"][0], "context": "注册资本为100万",
             "start_pos": 0, "end_pos": 8, "position": 0}
        ]

    async def fake_chat(prompt, messages=None):
        return '{"value": "100万", "reason": "r", "pages": [2]}'

    monkeypatch.setattr(es, "search_context", fake_search_context)
    monkeypatch.setattr(es, "chat_completion", fake_chat)

    # 先写一条普通字段的已有结果，供调试解析引用
    from model.database import get_session_factory

    sf = get_session_factory()
    async with sf() as s:
        await s.merge(ExtractionResult(
            file_id=ADV_FILE, field_id="basic_a", extracted_value="华为公司",
            reason="r", source_refs={"_model_pages": [2, 4]},
        ))
        await s.commit()

    r = await client.post("/extraction/test", json={
        "file_id": ADV_FILE,
        "config": {
            "field_name": "注册资本",
            "source_type": "text",
            "is_advanced": 1,
            "search_type": "context",
            "search_config": {"keywords": ["<field_result>basic_a</field_result>"]},
            "text_extract_prompt": "从 <search_result><field_result>basic_a</field_result></search_result> 提取",
        },
    })
    assert r.status_code == 200
    data = r.json()["data"]
    # 关键词已被替换为普通字段的值，且溯源透出
    assert seen["kw"] == ["华为公司"]
    assert data["resolved_refs"]["_resolved_refs"] == {"basic_a": "华为公司"}


async def test_copy_from_reports_missing_advanced_refs(client: AsyncClient):
    """只复制进阶字段、不复制它引用的普通字段 → 缺失依赖要上报（审查 §2.3）。"""
    await client.post("/doctype", json={"type_id": "misssrc", "type_name": "源"})
    await client.post("/doctype", json={"type_id": "missdst", "type_name": "目标"})
    try:
        await client.post("/extraction/fields", json={
            "field_id": "miss_A", "type_id": "misssrc", "field_name": "甲方",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["甲方"]},
            "text_extract_prompt": "从 <search_result>甲方</search_result> 提取",
        })
        await client.post("/extraction/fields", json={
            "field_id": "miss_B", "type_id": "misssrc", "field_name": "资本",
            "source_type": "text", "is_advanced": 1, "search_type": "context",
            "search_config": {"keywords": ["<field_result>miss_A</field_result>"]},
            "text_extract_prompt": "从 <search_result>x</search_result> 提取",
        })

        # 只复制进阶字段 B，不带它依赖的 A
        r = await client.post("/doctype/missdst/copy_from", json={
            "source_type_id": "misssrc", "field_ids": ["miss_B"],
        })
        assert r.status_code == 200
        missing = r.json()["data"]["missing_dependencies"]
        assert any("miss_A" in m for m in missing), missing
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["missdst", "misssrc"], "force": True}
        )


async def test_delete_referenced_basic_field_blocked(client: AsyncClient):
    """被进阶字段引用的普通字段默认不可删（409），force=true 可强删（审查 §3.1）。"""
    await client.post("/doctype", json={"type_id": "delref", "type_name": "删除保护"})
    try:
        await client.post("/extraction/fields", json={
            "field_id": "delref_A", "type_id": "delref", "field_name": "甲方",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["甲方"]},
            "text_extract_prompt": "从 <search_result>甲方</search_result> 提取",
        })
        await client.post("/extraction/fields", json={
            "field_id": "delref_B", "type_id": "delref", "field_name": "资本",
            "source_type": "text", "is_advanced": 1, "search_type": "context",
            "search_config": {"keywords": ["<field_result>delref_A</field_result>"]},
            "text_extract_prompt": "从 <search_result>x</search_result> 提取",
        })

        r = await client.delete("/extraction/fields/delref_A")
        assert r.status_code == 409
        assert "delref_B" in r.json()["detail"]

        r = await client.delete("/extraction/fields/delref_A?force=true")
        assert r.status_code == 200
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["delref"], "force": True}
        )


async def test_referenced_basic_field_cannot_become_advanced(client: AsyncClient):
    """被引用的普通字段不能改成进阶字段（审查 §3.1）。"""
    await client.post("/doctype", json={"type_id": "promref", "type_name": "改层级"})
    try:
        base = {
            "field_id": "promref_A", "type_id": "promref", "field_name": "甲方",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["甲方"]},
            "text_extract_prompt": "从 <search_result>甲方</search_result> 提取",
        }
        await client.post("/extraction/fields", json=base)
        await client.post("/extraction/fields", json={
            "field_id": "promref_B", "type_id": "promref", "field_name": "资本",
            "source_type": "text", "is_advanced": 1, "search_type": "context",
            "search_config": {"keywords": ["<field_result>promref_A</field_result>"]},
            "text_extract_prompt": "从 <search_result>x</search_result> 提取",
        })

        r = await client.post("/extraction/fields", json={**base, "is_advanced": 1})
        assert r.status_code == 400
        assert "promref_B" in r.json()["detail"]
    finally:
        await client.post(
            "/doctype/batch_delete", json={"type_ids": ["promref"], "force": True}
        )


PAGE_TYPE = "advpage_type"
PAGE_FILE = "advpage_file"
# 5 页文档，每页一个 block、每块 9 字符
_MD_5P = "PAGE1_AAAPAGE2_BBBPAGE3_CCCPAGE4_DDDPAGE5_EEE"
_MAPPING_5P = [
    {"start_pos": 0, "end_pos": 9, "page_num": 1},
    {"start_pos": 9, "end_pos": 18, "page_num": 2},
    {"start_pos": 18, "end_pos": 27, "page_num": 3},
    {"start_pos": 27, "end_pos": 36, "page_num": 4},
    {"start_pos": 36, "end_pos": 45, "page_num": 5},
]


@pytest.fixture
async def seeded_page_file():
    """建「普通字段（会自报页码）+ page 联动的进阶字段」。"""
    from model.database import get_session_factory

    sf = get_session_factory()
    async with sf() as s:
        await s.merge(DocType(type_id=PAGE_TYPE, type_name="页码联动测试"))
        await s.merge(
            File(file_id=PAGE_FILE, type_id=PAGE_TYPE, file_name="p.pdf", progress="extracting")
        )
        await s.merge(
            FileContent(file_id=PAGE_FILE, file_content=_MD_5P, page_mapping=_MAPPING_5P)
        )
        await s.merge(ExtractionField(
            field_id="pg_basic", type_id=PAGE_TYPE, field_name="锚点", source_type="text",
            enabled=1, priority=1, is_advanced=0, search_type="context",
            search_config={"keywords": ["PAGE2"], "context_before": 0,
                           "context_after": 5, "max_results": 1},
            text_extract_prompt="从 <search_result>PAGE2</search_result> 提取，并返回 pages",
        ))
        await s.merge(ExtractionField(
            field_id="pg_adv", type_id=PAGE_TYPE, field_name="联动取文", source_type="text",
            enabled=1, priority=2, is_advanced=1, depend_fields=["pg_basic"],
            search_type="page",
            search_config={"page_source_field": "pg_basic", "max_pages": 2, "max_length": 30000},
            text_extract_prompt="从 <search_result>page_content</search_result> 提取",
        ))
        await s.commit()

    yield PAGE_FILE

    async with sf() as s:
        await s.execute(delete(ExtractionResult).where(ExtractionResult.file_id == PAGE_FILE))
        await s.execute(delete(ExtractionField).where(ExtractionField.type_id == PAGE_TYPE))
        for table, key in ((FileContent, PAGE_FILE), (File, PAGE_FILE), (DocType, PAGE_TYPE)):
            obj = await s.get(table, key)
            if obj:
                await s.delete(obj)
        await s.commit()


async def test_page_link_end_to_end(seeded_page_file, monkeypatch):
    """普通字段自报页码 [2,4] + max_pages=2 → 进阶字段只读第 2-3 页。"""
    prompts: list[str] = []

    async def fake_search_context(content, config):
        return [{"keyword": "PAGE2", "context": "PAGE2_BBB",
                 "start_pos": 9, "end_pos": 18, "position": 9}]

    async def fake_chat(prompt, messages=None):
        prompts.append(prompt)
        if len(prompts) == 1:  # 普通字段：自报参考了第 2、4 页
            return '{"value": "锚点值", "reason": "r", "pages": [2, 4]}'
        return '{"value": "联动值", "reason": "r", "pages": []}'

    monkeypatch.setattr(es, "search_context", fake_search_context)
    monkeypatch.setattr(es, "chat_completion", fake_chat)

    from model.database import get_session_factory

    sf = get_session_factory()
    async with sf() as s:
        await es.run_extraction(seeded_page_file, s)

    # 进阶字段的 prompt 里只应有第 2、3 页内容（[2,4] 跨 3 页，被 max_pages=2 收敛为 2-3）
    adv_prompt = prompts[1]
    assert "PAGE2_BBB" in adv_prompt and "PAGE3_CCC" in adv_prompt
    assert "PAGE4_DDD" not in adv_prompt and "PAGE1_AAA" not in adv_prompt

    async with sf() as s:
        row = await s.get(ExtractionResult, (PAGE_FILE, "pg_adv"))
        assert row is not None and row.extracted_value == "联动值"
        assert row.source_refs["_page_link"] == {
            "source_field": "pg_basic", "model_pages": [2, 4],
            "derived_range": [2, 3], "capped": True,
        }


async def test_stream_extraction_runs_two_phases(seeded_file, monkeypatch):
    """run_extraction_stream 同样两阶段：普通字段先跑，进阶字段拿到它的值。"""
    calls: list[list[str]] = []

    async def fake_search_context(content, config):
        kws = list(config.get("keywords", []))
        calls.append(kws)
        return [{"keyword": kws[0] if kws else "", "context": "注册资本为100万",
                 "start_pos": 0, "end_pos": 8, "position": 0}]

    async def fake_chat(prompt, messages=None):
        return '{"value": "华为公司", "reason": "r", "pages": []}'

    monkeypatch.setattr(es, "search_context", fake_search_context)
    monkeypatch.setattr(es, "chat_completion", fake_chat)

    from model.database import get_session_factory

    sf = get_session_factory()
    events = []
    async with sf() as s:
        async for evt in es.run_extraction_stream(seeded_file, s):
            events.append(evt)

    assert calls == [["甲方"], ["华为公司"]]
    # 流里每个字段一条结果（无 event 包装，直接是结果 dict）
    assert [e["field_id"] for e in events] == ["basic_a", "adv_b"]
    adv_evt = events[1]
    assert adv_evt["success"] is True
    assert adv_evt["source_refs"]["_resolved_refs"] == {"basic_a": "华为公司"}
