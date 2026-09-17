"""分析模型配置隔离与缺失配置回归。"""

import httpx
import pytest

from utils.config import AppConfig
from utils import llm_client


@pytest.mark.asyncio
async def test_analysis_request_uses_only_analysis_config(monkeypatch):
    cfg = AppConfig.model_validate({
        "extraction": {"base_url": "http://extract.test/v1", "model": "extract",
                       "api_key": "extract-secret", "enable_thinking": True,
                       "extra_body": {"extract_only": True}},
        "analysis": {"base_url": "http://analysis.test/v1", "model": "judge",
                     "api_key": "", "judge_timeout": 17, "retry_count": 1,
                     "extra_body": {"analysis_only": True}},
    })
    monkeypatch.setattr(llm_client, "get_config", lambda: cfg)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 17

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(200, request=httpx.Request("POST", url),
                                  json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", Client)
    assert await llm_client.chat_completion("test", config_group="analysis") == "ok"
    url, request = calls[0]
    assert url == "http://analysis.test/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer EMPTY"
    assert request["json"]["model"] == "judge"
    assert request["json"]["extra_body"] == {"analysis_only": True}
    assert "enable_thinking" not in request["json"]


@pytest.mark.asyncio
async def test_unconfigured_analysis_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(llm_client, "get_config", lambda: AppConfig())
    with pytest.raises(ValueError, match="分析模型.*配置"):
        await llm_client.chat_completion("test", config_group="analysis")
