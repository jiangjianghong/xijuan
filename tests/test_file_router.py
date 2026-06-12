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


@pytest.mark.anyio
async def test_get_extraction_results_with_source_refs(client: AsyncClient):
    """提取结果应透出 source_refs（含检索原文）。"""
    from model.database import get_session_factory
    from model.tables import ExtractionResult

    file_id = "test_src_refs_file"
    refs = {
        "_texts": {"金额": "合同金额为100万元"},
        "金额": [{"type": "context", "start_pos": 1, "end_pos": 9,
                  "page_num": "1", "text": "合同金额为100万元"}],
    }
    factory = get_session_factory()
    async with factory() as session:
        session.add(ExtractionResult(
            file_id=file_id, field_id="f_amount",
            extracted_value="100万元", reason="r", source_refs=refs,
        ))
        await session.commit()

    try:
        resp = await client.get(f"/file/{file_id}/extraction")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["source_refs"] == refs
    finally:
        async with factory() as session:
            obj = await session.get(ExtractionResult, (file_id, "f_amount"))
            if obj:
                await session.delete(obj)
                await session.commit()


@pytest.mark.anyio
async def test_get_file_pdf_200(client: AsyncClient):
    """uploads 下存在 PDF 时应 200 并返回 application/pdf 原始字节。"""
    from utils import vl_client

    file_id = "test_pdf_endpoint_file"
    pdf = vl_client.pdf_path(file_id)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 minimal")
    try:
        resp = await client.get(f"/file/{file_id}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert resp.content == b"%PDF-1.4 minimal"
    finally:
        pdf.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_get_file_pdf_404(client: AsyncClient):
    """PDF 不存在时应 404。"""
    resp = await client.get("/file/nonexistent_pdf_file/pdf")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_file_pdf_path_traversal_blocked(client: AsyncClient):
    """file_id 含路径穿越字符时应 404（Windows 反斜杠穿越防护）。"""
    resp = await client.get("/file/..%5C..%5Csecret/pdf")
    assert resp.status_code == 404
