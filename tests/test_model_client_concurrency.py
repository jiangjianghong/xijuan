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


@pytest.fixture
def embedding_config():
    from utils.config import get_config

    previous = get_config()
    replace_config(
        AppConfig(
            embedding={
                "base_url": "http://embedding.test/v1",
                "model": "embedding",
                "timeout": 10,
                "retry_count": 1,
                "batch_size": 10,
            },
            concurrency={"global_embedding": 8, "task_embedding": 4},
        )
    )
    concurrency.clear_limiters()
    yield
    replace_config(previous)
    concurrency.clear_limiters()


@pytest.mark.asyncio
async def test_embeddings_keep_input_order_under_concurrency(monkeypatch, embedding_config):
    """并发下返回向量必须与输入 texts 一一对应——错位是静默的数据损坏。"""

    async def fake_post(self, url, **kwargs):
        inputs = kwargs["json"]["input"]
        # 第一批故意最慢，串行假设一旦残留就会顺序错乱
        await asyncio.sleep(0.05 if inputs[0] == "chunk-0" else 0.01)
        return _Response({"data": [{"embedding": [float(text.split("-")[1])]} for text in inputs]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    texts = [f"chunk-{i}" for i in range(45)]
    vectors = await get_embeddings(texts, task_id="file-a", max_retries=1)

    assert len(vectors) == 45
    assert vectors == [[float(i)] for i in range(45)]


@pytest.mark.asyncio
async def test_embeddings_run_batches_concurrently_under_task_limit(monkeypatch, embedding_config):
    active = 0
    peak = 0

    async def fake_post(self, url, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return _Response({"data": [{"embedding": [1.0]} for _ in kwargs["json"]["input"]]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await get_embeddings([f"chunk-{i}" for i in range(100)], task_id="file-b", max_retries=1)

    assert peak == 4


@pytest.mark.asyncio
async def test_embeddings_do_not_retry_client_errors(monkeypatch, embedding_config):
    """4xx（除 429）是参数/权限错误，重试只会拖长失败，与 chat_completion 一致。"""
    calls = 0

    async def fake_post(self, url, **kwargs):
        nonlocal calls
        calls += 1
        response = httpx.Response(400, request=httpx.Request("POST", url), text="bad input")
        raise httpx.HTTPStatusError("400", request=response.request, response=response)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        await get_embeddings(["chunk-0"], max_retries=3)

    assert calls == 1


@pytest.mark.asyncio
async def test_embeddings_retry_rate_limit(monkeypatch, embedding_config):
    """429 仍要重试。"""
    calls = 0

    async def fake_post(self, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = httpx.Response(429, request=httpx.Request("POST", url), text="slow down")
            raise httpx.HTTPStatusError("429", request=response.request, response=response)
        return _Response({"data": [{"embedding": [1.0]}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: real_sleep(0))

    vectors = await get_embeddings(["chunk-0"], max_retries=3)

    assert calls == 2
    assert vectors == [[1.0]]
