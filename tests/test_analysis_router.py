"""analysis 路由测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_list_rules(client: AsyncClient):
    """测试获取规则列表。"""
    resp = await client.get("/analysis/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 200


@pytest.mark.anyio
async def test_check_rule(client: AsyncClient):
    """测试检查规则是否存在。"""
    resp = await client.get("/analysis/rules/test_rule/check")
    assert resp.status_code == 200
