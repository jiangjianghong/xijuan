"""extraction 路由测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_list_fields(client: AsyncClient):
    """测试获取字段列表。"""
    resp = await client.get("/extraction/fields")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 200


@pytest.mark.anyio
async def test_check_field(client: AsyncClient):
    """测试检查字段是否存在。"""
    resp = await client.get("/extraction/fields/test_field/check")
    assert resp.status_code == 200
