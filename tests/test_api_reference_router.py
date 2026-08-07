"""API 手册只读接口测试。"""

from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_get_api_reference_returns_markdown_content(client):
    response = await client.get("/api-reference")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    assert payload["message"] == "success"
    assert payload["data"]["file_name"] == "API_FULL_REFERENCE.md"
    assert payload["data"]["content"].startswith("# 析卷 AI 全量接口手册")
    assert "GET /file/list" in payload["data"]["content"]
    assert payload["data"]["size"] > 0
    assert payload["data"]["updated_at"]
