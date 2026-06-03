"""file 路由测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_file_status_not_found(client: AsyncClient):
    """不存在的文件应返回 404。"""
    resp = await client.get("/file/nonexistent/status")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_file_tables(client: AsyncClient):
    """测试获取文件表格列表。"""
    resp = await client.get("/file/testfile/tables")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_file_outline_route(client: AsyncClient):
    """测试获取文件大纲(路由可达 + 空集回退)。"""
    resp = await client.get("/file/nonexistent/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
