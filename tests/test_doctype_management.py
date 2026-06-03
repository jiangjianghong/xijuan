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
