"""文件片段上下文查询测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from model.database import get_db
from model.schemas import FileContextQueryRequest
from service.file_context_service import (
    build_file_context_response,
    find_context_matches,
)


def test_find_context_matches_uses_half_open_match_end_for_page_lookup():
    """命中刚好止于下一页锚点时，不应把下一页算进片段页码。"""
    content = "AB"
    mapping = [
        {"start_pos": 0, "end_pos": 1, "page_num": 1},
        {"start_pos": 1, "end_pos": 2, "page_num": 2},
    ]

    matches = find_context_matches(
        content=content,
        query="A",
        context_before=0,
        context_after=0,
        case_sensitive=True,
        page_mapping=mapping,
    )

    assert matches[0]["match_start_pos"] == 0
    assert matches[0]["match_end_pos"] == 1
    assert matches[0]["page_num"] == "1"


def test_build_file_context_response_returns_matches_and_all_chunks():
    """响应组装应返回命中上下文、页码和该文件全部 chunks。"""
    file_id = "test_file_context_query"
    content = "第一页合同金额为100万元。\n第二页合同金额为200万元。"
    second_page_start = content.index("第二页")
    mapping = [
        {
            "start_pos": 0,
            "end_pos": 10,
            "page_num": 1,
            "bbox": [10, 20, 100, 40],
            "page_size": [612, 792],
        },
        {
            "start_pos": second_page_start,
            "end_pos": second_page_start + 10,
            "page_num": 2,
            "bbox": [10, 50, 100, 70],
            "page_size": [612, 792],
        },
    ]
    chunks = [
        SimpleNamespace(
            file_id=file_id,
            chunk_id="chunk_1",
            chunk_index=0,
            total_chunks=2,
            chunk_content=content[:second_page_start],
            start_pos=0,
            end_pos=second_page_start,
            page_num="1",
        ),
        SimpleNamespace(
            file_id=file_id,
            chunk_id="chunk_2",
            chunk_index=1,
            total_chunks=2,
            chunk_content=content[second_page_start:],
            start_pos=second_page_start,
            end_pos=len(content),
            page_num="2",
        ),
    ]
    request = FileContextQueryRequest(
        file_id=file_id,
        query="合同金额",
        context_before=2,
        context_after=4,
    )

    data = build_file_context_response(request, content, mapping, chunks)

    assert data["file_id"] == file_id
    assert data["matched"] is True
    assert data["match_count"] == 2
    assert [m["page_num"] for m in data["matches"]] == ["1", "2"]
    assert data["matches"][0]["context"] == "一页合同金额为100"
    assert data["matches"][0]["bboxes"][0]["page_num"] == 1

    assert len(data["chunks"]) == 2
    assert [c["chunk_id"] for c in data["chunks"]] == ["chunk_1", "chunk_2"]
    assert [c["hit"] for c in data["chunks"]] == [True, True]
    assert [c["hit_count"] for c in data["chunks"]] == [1, 1]
    assert data["chunks"][0]["start_pos"] == 0
    assert data["chunks"][1]["end_pos"] == len(content)


def test_build_file_context_response_no_match_still_returns_all_chunks():
    """无命中时 matches 为空，但仍按需求返回全部 chunks。"""
    file_id = "test_file_context_no_match"
    content = "没有目标词的内容"
    chunks = [
        SimpleNamespace(
            file_id=file_id,
            chunk_id="chunk_1",
            chunk_index=0,
            total_chunks=1,
            chunk_content=content,
            start_pos=0,
            end_pos=len(content),
            page_num="",
        )
    ]
    request = FileContextQueryRequest(file_id=file_id, query="合同金额")

    data = build_file_context_response(request, content, [], chunks)

    assert data["matched"] is False
    assert data["match_count"] == 0
    assert data["matches"] == []
    assert len(data["chunks"]) == 1
    assert data["chunks"][0]["hit"] is False
    assert data["chunks"][0]["hit_count"] == 0


@pytest.mark.anyio
async def test_file_context_query_route_uses_file_id_from_body(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """路由应从请求体接收 file_id，而不是 URL path。"""
    import importlib

    from app import app

    file_router_module = importlib.import_module("blue_print.file_router")

    async def _fake_db():
        yield object()

    async def _fake_query_file_context(request, session):
        assert request.file_id == "body_file_id"
        assert request.query == "合同金额"
        return {
            "file_id": request.file_id,
            "query": request.query,
            "query_type": request.query_type,
            "matched": False,
            "match_count": 0,
            "matches": [],
            "chunks": [],
        }

    app.dependency_overrides[get_db] = _fake_db
    monkeypatch.setattr(
        file_router_module, "query_file_context", _fake_query_file_context
    )
    try:
        resp = await client.post(
            "/file/context_query",
            json={"file_id": "body_file_id", "query": "合同金额"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["file_id"] == "body_file_id"
    assert data["query"] == "合同金额"
