"""extraction 路由测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from model.schemas import ExtractionFieldCreate


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


def test_text_field_requires_extract_prompt():
    """文本字段必须配置可替换的提取 Prompt。"""
    with pytest.raises(ValidationError):
        ExtractionFieldCreate(
            field_id="txt_no_prompt",
            field_name="文本字段",
            source_type="text",
            search_type="context",
            search_config={"keywords": ["文本"]},
        )


def test_table_field_requires_search_result_placeholder():
    """表格字段 Prompt 不能为空，且必须包含 search_result 占位符。"""
    with pytest.raises(ValidationError):
        ExtractionFieldCreate(
            field_id="tbl_bad_prompt",
            field_name="表格字段",
            source_type="table",
            table_match_type="contains",
            table_match_keywords=["表格"],
            table_extract_prompt="请提取表格字段",
        )

    field = ExtractionFieldCreate(
        field_id="tbl_ok_prompt",
        field_name="表格字段",
        source_type="table",
        table_match_type="contains",
        table_match_keywords=["表格"],
        table_extract_prompt="请从<search_result>表格</search_result>提取字段",
    )
    assert field.table_extract_prompt
