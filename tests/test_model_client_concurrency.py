from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from utils import concurrency
from utils.config import AppConfig, replace_config
from utils.llm_client import chat_completion, get_embeddings


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def limited_config():
    original = replace_config
    from utils.config import get_config

    previous = get_config()
    replace_config(
        AppConfig(
            extraction={
                "base_url": "http://llm.test/v1",
                "model": "llm",
                "timeout": 10,
                "retry_count": 1,
            },
            embedding={
                "base_url": "http://embedding.test/v1",
                "model": "embedding",
                "timeout": 10,
                "retry_count": 1,
            },
            concurrency={
                "global_llm": 2,
                "global_embedding": 2,
            },
        )
    )
    concurrency.clear_limiters()
    yield
    replace_config(previous)
    concurrency.clear_limiters()


@pytest.mark.asyncio
async def test_chat_completion_obeys_global_llm_limit(monkeypatch, limited_config):
    active = 0
    peak = 0

    async def fake_post(self, url, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await asyncio.gather(*(chat_completion("hello", max_retries=1) for _ in range(8)))

    assert peak == 2


@pytest.mark.asyncio
async def test_embeddings_obey_global_embedding_limit(monkeypatch, limited_config):
    active = 0
    peak = 0

    async def fake_post(self, url, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _Response({"data": [{"embedding": [1.0]}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await asyncio.gather(*(
        get_embeddings([f"text-{i}"], batch_size=1, max_retries=1)
        for i in range(8)
    ))

    assert peak == 2
