"""文档类型管理增强：项目 CRUD / list 改造 / 批量 / 血缘 / 提升。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_project_crud_and_unbind(client: AsyncClient):
    # 创建
    r = await client.post("/doctype/projects", json={"project_id": "dm_p1", "project_name": "项目一"})
    assert r.status_code == 200
    # 列表含新项目
    r = await client.get("/doctype/projects")
    data = r.json()["data"]
    assert any(p["project_id"] == "dm_p1" for p in data)
    # 改名(upsert)
    r = await client.post("/doctype/projects", json={"project_id": "dm_p1", "project_name": "项目一改"})
    r = await client.get("/doctype/projects")
    p = next(p for p in r.json()["data"] if p["project_id"] == "dm_p1")
    assert p["project_name"] == "项目一改"
    # 删除
    r = await client.delete("/doctype/projects/dm_p1")
    assert r.status_code == 200
    r = await client.get("/doctype/projects")
    assert not any(p["project_id"] == "dm_p1" for p in r.json()["data"])


@pytest.mark.anyio
async def test_list_backward_compatible_array(client: AsyncClient):
    """不传参数：返回数组（向后兼容）。"""
    r = await client.get("/doctype/list")
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)


@pytest.mark.anyio
async def test_list_paginated_shape_and_filters(client: AsyncClient):
    # 造一个普通类型（is_template=0, is_default=0），代表"副本/普通"
    await client.post("/doctype", json={"type_id": "dm_plain", "type_name": "DM普通"})
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
        assert items[0]["project_id"] is None

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
async def test_batch_assign_and_project_delete_unbinds(client: AsyncClient):
    await client.post("/doctype/projects", json={"project_id": "dm_pa", "project_name": "归类项目"})
    await client.post("/doctype", json={"type_id": "dm_a1", "type_name": "A1"})
    await client.post("/doctype", json={"type_id": "dm_a2", "type_name": "A2"})
    try:
        # 归类
        r = await client.post(
            "/doctype/batch_assign_project",
            json={"type_ids": ["dm_a1", "dm_a2"], "project_id": "dm_pa"},
        )
        assert r.status_code == 200
        r = await client.get("/doctype/list?project_id=dm_pa&page=1&page_size=10")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert set(ids) == {"dm_a1", "dm_a2"}

        # 项目不存在 → 404
        r = await client.post(
            "/doctype/batch_assign_project",
            json={"type_ids": ["dm_a1"], "project_id": "no_such"},
        )
        assert r.status_code == 404

        # 删项目 → 成员解绑（变未分组），type 仍在
        await client.delete("/doctype/projects/dm_pa")
        r = await client.get("/doctype/list?project_id=__ungrouped__&page=1&page_size=500")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert "dm_a1" in ids and "dm_a2" in ids
    finally:
        await client.delete("/doctype/dm_a1?force=true")
        await client.delete("/doctype/dm_a2?force=true")
        await client.delete("/doctype/projects/dm_pa")


@pytest.mark.anyio
async def test_copy_from_records_parent_and_inherits_project(client: AsyncClient):
    await client.post("/doctype/projects", json={"project_id": "dm_pc", "project_name": "源项目"})
    await client.post("/doctype", json={"type_id": "dm_src", "type_name": "源"})
    await client.post(
        "/doctype/batch_assign_project",
        json={"type_ids": ["dm_src"], "project_id": "dm_pc"},
    )
    await client.post("/doctype", json={"type_id": "dm_tgt", "type_name": "目标"})
    try:
        r = await client.post("/doctype/dm_tgt/copy_from", json={"source_type_id": "dm_src"})
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_tgt&page=1&page_size=10")
        item = r.json()["data"]["items"][0]
        assert item["parent_type_id"] == "dm_src"
        assert item["project_id"] == "dm_pc"  # 目标原未分组 → 继承源项目
    finally:
        await client.delete("/doctype/dm_src?force=true")
        await client.delete("/doctype/dm_tgt?force=true")
        await client.delete("/doctype/projects/dm_pc")



