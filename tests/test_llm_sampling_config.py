"""文本 LLM 的标准采样参数应从声明式配置注入请求顶层。"""

import copy

import httpx
import pytest

from utils import llm_client
from utils.config import AppConfig


@pytest.mark.parametrize(
    "group,expected_temperature,expected_top_p",
    [("extraction", 0.1, None), ("analysis", 0.3, 0.8)],
)
async def test_chat_completion_sends_sampling_config_on_top_level(
    monkeypatch, group, expected_temperature, expected_top_p
):
    cfg = AppConfig()
    cfg.analysis.temperature = 0.3
    cfg.analysis.top_p = 0.8
    cfg.analysis.base_url = "http://localhost:8000/v1"
    cfg.analysis.model = "analysis-model"
    monkeypatch.setattr(llm_client, "get_config", lambda: cfg)
    payloads = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json, **kwargs):
            payloads.append(copy.deepcopy(json))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "ok"}}]},
            )

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", Client)
    await llm_client.chat_completion("prompt", config_group=group)

    assert payloads[0]["temperature"] == expected_temperature
    if expected_top_p is None:
        assert "top_p" not in payloads[0]
    else:
        assert payloads[0]["top_p"] == expected_top_p


async def test_table_validation_sampling_overrides_or_inherits_extraction(monkeypatch):
    cfg = AppConfig()
    cfg.extraction.temperature = 0.4
    cfg.extraction.top_p = 0.9
    cfg.table_name_validation.base_url = "http://localhost:8000/v1"
    cfg.table_name_validation.model = "table-model"
    cfg.table_name_validation.top_p = 0.7
    monkeypatch.setattr(llm_client, "get_config", lambda: cfg)
    payloads = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json, **kwargs):
            payloads.append(copy.deepcopy(json))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "ok"}}]},
            )

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", Client)
    await llm_client.chat_completion("prompt", config_group="table_name_validation")
    assert payloads[0]["temperature"] == 0.4
    assert payloads[0]["top_p"] == 0.7
    assert "temperature" not in payloads[0].get("extra_body", {})
    assert "top_p" not in payloads[0].get("extra_body", {})
