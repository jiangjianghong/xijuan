"""MinerU 客户端参数组装测试。"""

from __future__ import annotations

import pytest

from service import mineru_client


@pytest.mark.anyio
async def test_parse_pdf_max_parse_pages_sets_end_page(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": {"demo": {"md_content": "ok", "middle_json": {}}}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, files, data):
            seen["url"] = url
            seen["data"] = data
            return FakeResponse()

    monkeypatch.setattr(mineru_client.httpx, "AsyncClient", FakeAsyncClient)

    result = await mineru_client.parse_pdf(
        "demo.pdf",
        b"%PDF",
        file_id="fid",
        base_url="http://mineru.example",
        timeout=1,
        max_parse_pages=3,
    )

    assert result["md_content"] == "ok"
    assert seen["url"] == "http://mineru.example/file_parse"
    assert seen["data"]["start_page_id"] == "0"
    assert seen["data"]["end_page_id"] == "2"


@pytest.mark.anyio
async def test_parse_pdf_without_max_parse_pages_parses_all(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": {"demo": {"md_content": "ok", "middle_json": ""}}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, files, data):
            seen["data"] = data
            return FakeResponse()

    monkeypatch.setattr(mineru_client.httpx, "AsyncClient", FakeAsyncClient)

    await mineru_client.parse_pdf(
        "demo.pdf",
        b"%PDF",
        file_id="fid",
        base_url="http://mineru.example",
        timeout=1,
    )

    assert seen["data"]["end_page_id"] == "99999"


@pytest.mark.anyio
async def test_parse_pdf_requests_and_returns_content_list(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": {"demo": {
                "md_content": "ok",
                "middle_json": {},
                "content_list": [{"type": "text", "text": "第一段", "page_idx": 0}],
            }}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, files, data):
            seen["data"] = data
            return FakeResponse()

    monkeypatch.setattr(mineru_client.httpx, "AsyncClient", FakeAsyncClient)

    result = await mineru_client.parse_pdf(
        "demo.pdf", b"%PDF", file_id="fid",
        base_url="http://mineru.example", timeout=1,
    )

    assert seen["data"]["return_content_list"] == "true"
    # list 归一为 JSON 字符串(与 middle_json 同款处理)
    import json as _json
    assert isinstance(result["content_list"], str)
    assert _json.loads(result["content_list"])[0]["text"] == "第一段"


@pytest.mark.anyio
async def test_parse_pdf_missing_content_list_returns_empty_str(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": {"demo": {"md_content": "ok", "middle_json": ""}}}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, files, data):
            return FakeResponse()

    monkeypatch.setattr(mineru_client.httpx, "AsyncClient", FakeAsyncClient)

    result = await mineru_client.parse_pdf(
        "demo.pdf", b"%PDF", file_id="fid",
        base_url="http://mineru.example", timeout=1,
    )
    assert result["content_list"] == ""
