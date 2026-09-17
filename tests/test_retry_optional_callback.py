"""指定阶段异步重试的回调地址可选，覆盖实际 HTTP 与后台任务调度。"""

from contextlib import asynccontextmanager
import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.anyio
@pytest.mark.parametrize("stage", [
    "tableing", "table_name_validating", "chunking", "embedding", "extracting", "analyzing",
])
@pytest.mark.parametrize("callback_url", [None, "http://callback.local/result"])
async def test_retry_async_callback_optional(client, monkeypatch, stage, callback_url):
    from app import app
    from model import database
    from model.database import get_db
    router = importlib.import_module("blue_print.file_router")
    calls = []

    class Session:
        async def execute(self, stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
                file_id="file-1", file_name="example.pdf", type_id="default",
            ))

    async def dependency():
        yield Session()

    @asynccontextmanager
    async def session_factory():
        yield Session()

    @asynccontextmanager
    async def slot(*args, **kwargs):
        yield

    async def run(file_id, requested_stage, session, **kwargs):
        calls.append((file_id, requested_stage, kwargs))

    monkeypatch.setattr(database, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(router, "pipeline_slot", slot)
    monkeypatch.setattr(router, "run_from_stage", run)
    app.dependency_overrides[get_db] = dependency
    try:
        params = {"mode": "async"}
        if callback_url:
            params["callback_url"] = callback_url
        response = await client.post(f"/file/file-1/retry/{stage}", params=params)
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200
    assert response.json()["code"] == 200
    expected_stage = "tableing" if stage == "table_name_validating" else stage
    assert calls == [("file-1", expected_stage, {"callback_url": callback_url, "callback_mode": "full"})]
