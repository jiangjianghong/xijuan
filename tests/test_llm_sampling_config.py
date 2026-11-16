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
    cfg.extraction.extra_params = {
        "enable_thinking": True,
        "extraction_only": True,
        "extra_body": {"extraction": True},
    }
    cfg.table_name_validation.base_url = "http://localhost:8000/v1"
    cfg.table_name_validation.model = "table-model"
    cfg.table_name_validation.top_p = 0.7
    cfg.table_name_validation.extra_params = {"extra_body": {"table": True}}
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
    assert payloads[0]["enable_thinking"] is True
    assert "extraction_only" not in payloads[0]
    assert payloads[0]["extra_body"] == {"table": True}


def test_table_validation_explicit_null_does_not_inherit_thinking():
    cfg = AppConfig()
    cfg.extraction.extra_params = {"enable_thinking": True}
    cfg.table_name_validation.extra_params = {"enable_thinking": None}

    params = llm_client._resolve_extra_params(
        "table_name_validation",
        cfg.table_name_validation,
        cfg.extraction,
        None,
    )

    assert "enable_thinking" in params
    assert params["enable_thinking"] is None


async def test_extra_params_merge_at_request_root_and_override_sampling(monkeypatch):
    cfg = AppConfig()
    cfg.extraction.temperature = 0.4
    cfg.extraction.top_p = 0.3
    cfg.extraction.extra_params = {"configured_param": "kept", "temperature": 0.6}
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
    await llm_client.chat_completion(
        "prompt",
        extra_params={
            "temperature": 0.9,
            "top_p": 0.8,
            "enable_thinking": True,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        },
    )

    payload = payloads[0]
    assert payload["temperature"] == 0.9
    assert payload["top_p"] == 0.8
    assert payload["enable_thinking"] is True
    assert payload["configured_param"] == "kept"
    assert payload["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
