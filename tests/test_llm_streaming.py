"""Debug chat completion streams OpenAI-compatible text deltas."""

from __future__ import annotations

import json

import httpx

from utils import llm_client
from utils.config import AppConfig


async def test_chat_completion_stream_yields_sse_deltas_and_sets_stream_flag(monkeypatch):
    config = AppConfig.model_validate({
        "extraction": {
            "base_url": "http://model.test/v1",
            "model": "debug-model",
            "api_key": "test-key",
            "retry_count": 1,
            "enable_thinking": True,
        },
    })
    monkeypatch.setattr(llm_client, "get_config", lambda: config)
    requests = []

    def handler(request):
        requests.append(request)
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "check fields "}}]},
            {"choices": [{"delta": {"reasoning_content": "before answering"}}]},
            {"choices": [{"delta": {"content": "{\"value\":"}}]},
            {"choices": [{"delta": {"content": "\"ok\"}"}}]},
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        body += "data: [DONE]\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    events = [
        event async for event in llm_client.chat_completion_stream_events("debug prompt")
    ]

    assert events == [
        {"type": "reasoning", "content": "check fields "},
        {"type": "reasoning", "content": "before answering"},
        {"type": "content", "content": '{"value":'},
        {"type": "content", "content": '"ok"}'},
    ]
    deltas = [part async for part in llm_client.chat_completion_stream("debug prompt")]
    assert deltas == ['{"value":', '"ok"}']
    assert len(requests) == 2
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert json.loads(requests[0].content)["stream"] is True
    assert json.loads(requests[0].content)["enable_thinking"] is True
