from contextlib import asynccontextmanager

import httpx

from utils.config import AppConfig
from utils.model_config import model_config_context
from utils import rerank_client


async def test_rerank_uses_cohere_endpoint_request_override_and_global_pool(monkeypatch):
    config = AppConfig()
    calls = []
    pools = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(("timeout", kwargs["timeout"]))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json, headers):
            calls.append((url, json, headers))
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"results": [
                {"index": 2, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.81},
            ]})

    class Limiter:
        @asynccontextmanager
        async def context(self, context):
            pools.append(context)
            yield

    monkeypatch.setattr(rerank_client, "get_config", lambda: config)
    monkeypatch.setattr(rerank_client.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        rerank_client, "get_limiter",
        lambda name, limit: pools.append((name, limit)) or Limiter(),
    )

    with model_config_context({"rerank": {
        "base_url": "https://rerank.test/v2",
        "model": "request-reranker",
        "api_key": "request-secret",
        "timeout": 13,
    }}):
        indexes = await rerank_client.rerank_indices(
            "query", ["first", "second", "third"], 2
        )

    assert indexes == [2, 0]
    assert calls[0] == ("timeout", 13)
    assert calls[1][0] == "https://rerank.test/v2/rerank"
    assert calls[1][1] == {
        "model": "request-reranker",
        "query": "query",
        "documents": ["first", "second", "third"],
        "top_n": 2,
    }
    assert calls[1][2]["Authorization"] == "Bearer request-secret"
    assert pools[0] == ("global_rerank", config.concurrency.global_rerank)
    assert pools[1]["stage"] == "rerank"
    assert pools[1]["candidate_count"] == 3
