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

