"""文档类型管理：list 分页/筛选 / 批量删除 / 血缘（copy_from 记录来源、promote/demote）。

项目维度已彻底移除，相关测试一并删除。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_list_backward_compatible_array(client: AsyncClient):
    """不传参数：返回数组（向后兼容）。"""
    r = await client.get("/doctype/list")
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)


@pytest.mark.anyio
async def test_list_paginated_shape_and_filters(client: AsyncClient):
    # 造一个普通类型（is_template=0, is_default=0），代表"副本/普通"
    await client.post(
        "/doctype",
        json={
            "type_id": "dm_plain",
            "type_name": "DM普通",
            "max_parse_pages": 5,
            "enable_embedding": 0,
        },
    )
    try:
        # 分页：返回 {items,total}
        r = await client.get("/doctype/list?page=1&page_size=100")
        data = r.json()["data"]
        assert "items" in data and "total" in data
        assert isinstance(data["items"], list)

        # 搜索命中，且新字段存在
        r = await client.get("/doctype/list?q=dm_plain&page=1&page_size=10")
        items = r.json()["data"]["items"]
        assert len(items) == 1 and items[0]["type_id"] == "dm_plain"
        assert items[0]["is_template"] == 0
        assert items[0]["max_parse_pages"] == 5
        assert items[0]["enable_embedding"] == 0
        assert "project_id" not in items[0]

        # scope=template：含 default（is_default=1），不含 dm_plain
        r = await client.get("/doctype/list?scope=template&page=1&page_size=500")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert "default" in ids and "dm_plain" not in ids

        # scope=copy：含 dm_plain，不含 default
        r = await client.get("/doctype/list?scope=copy&page=1&page_size=500")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert "dm_plain" in ids and "default" not in ids
    finally:
        await client.delete("/doctype/dm_plain?force=true")


@pytest.mark.anyio
async def test_doctype_runtime_config_defaults(client: AsyncClient):
    """运行配置不传时默认解析全部页并执行向量化。"""
    await client.post("/doctype", json={"type_id": "dm_defaults", "type_name": "默认配置"})
    try:
        r = await client.get("/doctype/list?q=dm_defaults&page=1&page_size=10")
        item = r.json()["data"]["items"][0]
        assert item["max_parse_pages"] is None
        assert item["enable_embedding"] == 1
    finally:
        await client.delete("/doctype/dm_defaults?force=true")


@pytest.mark.anyio
async def test_copy_from_records_parent(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_src", "type_name": "源"})
    await client.post("/doctype", json={"type_id": "dm_tgt", "type_name": "目标"})
    try:
        r = await client.post("/doctype/dm_tgt/copy_from", json={"source_type_id": "dm_src"})
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_tgt&page=1&page_size=10")
        item = r.json()["data"]["items"][0]
        assert item["parent_type_id"] == "dm_src"
    finally:
        await client.delete("/doctype/dm_src?force=true")
        await client.delete("/doctype/dm_tgt?force=true")


@pytest.mark.anyio
async def test_update_doctype_renames_type_id_and_cascades(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_rename_src", "type_name": "源"})
    await client.post("/doctype", json={"type_id": "dm_rename_child", "type_name": "子"})
    try:
        await client.post(
            "/doctype/dm_rename_child/copy_from",
            json={"source_type_id": "dm_rename_src"},
        )
        r = await client.post(
            "/extraction/fields",
            json={
                "field_id": "dm_rename_field",
                "type_id": "dm_rename_src",
                "field_name": "金额",
                "source_type": "text",
                "enabled": 1,
                "priority": 0,
                "search_type": "context",
                "search_config": {},
            },
        )
        assert r.status_code == 200
        r = await client.post(
            "/analysis/rules",
            json={
                "rule_id": "dm_rename_rule",
                "type_id": "dm_rename_src",
                "rule_name": "金额检查",
                "rule_type": "judge",
                "expression": "<field_result>dm_rename_field</field_result> 是否存在",
                "depend_fields": ["dm_rename_field"],
                "enabled": 1,
                "priority": 0,
            },
        )
        assert r.status_code == 200

        r = await client.put(
            "/doctype/dm_rename_src",
            json={
                "type_id": "dm_rename_new",
                "type_name": "新源",
                "max_parse_pages": 9,
                "enable_embedding": 0,
                "enabled": 1,
            },
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["renamed"] is True
        assert data["updated_fields"] == 1
        assert data["updated_rules"] == 1
        assert data["updated_children"] == 1

        r = await client.get("/doctype/list?q=dm_rename_new&page=1&page_size=10")
        item = r.json()["data"]["items"][0]
        assert item["type_id"] == "dm_rename_new"
        assert item["type_name"] == "新源"
        assert item["max_parse_pages"] == 9
        assert item["enable_embedding"] == 0

        r = await client.get("/doctype/list?q=dm_rename_child&page=1&page_size=10")
        assert r.json()["data"]["items"][0]["parent_type_id"] == "dm_rename_new"

        r = await client.get("/extraction/fields?type_id=dm_rename_new")
        assert any(f["field_id"] == "dm_rename_field" for f in r.json()["data"])
        r = await client.get("/analysis/rules?type_id=dm_rename_new")
        assert any(rule["rule_id"] == "dm_rename_rule" for rule in r.json()["data"])
    finally:
        await client.delete("/doctype/dm_rename_src?force=true")
        await client.delete("/doctype/dm_rename_new?force=true")
        await client.delete("/doctype/dm_rename_child?force=true")


@pytest.mark.anyio
async def test_promote_demote(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_pm", "type_name": "待提升"})
    try:
        r = await client.post("/doctype/dm_pm/promote")
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_pm&page=1&page_size=10")
        assert r.json()["data"]["items"][0]["is_template"] == 1
        # 提升后进入 template 过滤
        r = await client.get("/doctype/list?scope=template&page=1&page_size=500")
        assert any(i["type_id"] == "dm_pm" for i in r.json()["data"]["items"])

        r = await client.post("/doctype/dm_pm/demote")
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_pm&page=1&page_size=10")
        assert r.json()["data"]["items"][0]["is_template"] == 0

        # 默认类型不可操作
        r = await client.post("/doctype/default/promote")
        assert r.status_code == 400
    finally:
        await client.delete("/doctype/dm_pm?force=true")


@pytest.mark.anyio
async def test_batch_delete(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_d1", "type_name": "D1"})
    await client.post("/doctype", json={"type_id": "dm_d2", "type_name": "D2"})
    # 批量删除（含一个不存在的 + default 应被跳过）
    r = await client.post(
        "/doctype/batch_delete",
        json={"type_ids": ["dm_d1", "dm_d2", "default", "no_such"], "force": True},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["deleted"] == 2
    by_id = {x["type_id"]: x for x in data["results"]}
    assert by_id["dm_d1"]["ok"] is True
    assert by_id["default"]["ok"] is False  # 默认类型被跳过
    assert by_id["no_such"]["ok"] is False

    # 确认已删
    r = await client.get("/doctype/list?scope=copy&page=1&page_size=500")
    ids = [i["type_id"] for i in r.json()["data"]["items"]]
    assert "dm_d1" not in ids and "dm_d2" not in ids
    # default 仍在
    r = await client.get("/doctype/list?page=1&page_size=500")
    assert any(i["type_id"] == "default" for i in r.json()["data"]["items"])
