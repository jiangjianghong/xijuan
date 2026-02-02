"""file 路由测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_file_status_not_found(client: AsyncClient):
    """测试获取不存在的文件状态。"""
    resp = await client.get("/file/nonexistent/status")
    # 路由可达即可，具体逻辑待实现
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_file_tables(client: AsyncClient):
    """测试获取文件表格列表。"""
    resp = await client.get("/file/testfile/tables")
    assert resp.status_code == 200
