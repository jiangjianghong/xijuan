"""博查网络搜索客户端测试。"""

from __future__ import annotations

import httpx
import pytest

from utils.config import AppConfig


def test_web_search_config_defaults():
    """WebSearchConfig 默认值。"""
    cfg = AppConfig().web_search
    assert cfg.base_url == "https://api.bochaai.com/v1/web-search"
    assert cfg.api_key == ""
    assert cfg.count == 5
    assert cfg.summary is True
    assert cfg.freshness == "noLimit"
    assert cfg.timeout == 10
    assert cfg.retry_count == 2
    assert cfg.max_result_length == 4000


# ── format_search_results ───────────────────────────────────

def test_format_search_results_empty():
    from utils.web_search import format_search_results

    assert format_search_results([], 4000) == "（未搜索到相关结果）"


def test_format_search_results_basic():
    from utils.web_search import format_search_results

    results = [
        {
            "name": "标题一",
            "url": "https://a.com/1",
            "siteName": "站点A",
            "datePublished": "2026-01-02T00:00:00Z",
            "summary": "摘要内容一",
        },
        {"name": "标题二", "url": "https://b.com/2", "summary": "摘要内容二"},
    ]
    text = format_search_results(results, 4000)
    assert "[1] 标题一" in text
    assert "来源: 站点A | 2026-01-02" in text
    assert "摘要: 摘要内容一" in text
    assert "[2] 标题二" in text


def test_format_search_results_truncate():
    from utils.web_search import format_search_results

    results = [{"name": "标题", "summary": "长" * 500}]
    text = format_search_results(results, 50)
    assert len(text) == 50


# ── bocha_web_search ────────────────────────────────────────

_BOCHA_RESPONSE = {
    "code": 200,
    "data": {
        "webPages": {
            "totalEstimatedMatches": 100,
            "value": [
                {
                    "name": "标题一",
                    "url": "https://a.com/1",
                    "siteName": "站点A",
                    "datePublished": "2026-01-02T00:00:00Z",
                    "summary": "摘要内容一",
                },
                {"name": "标题二", "url": "https://b.com/2", "snippet": "片段二"},
            ],
        }
    },
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """捕获请求 payload 并返回固定响应。"""

    last_json = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.last_json = json
        return _FakeResponse(_BOCHA_RESPONSE)


async def test_bocha_web_search_parses_response(monkeypatch):
    from utils import web_search

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeAsyncClient)
    formatted, results = await web_search.bocha_web_search("万科 处罚", count=3, freshness="oneYear")

    assert "[1] 标题一" in formatted
    assert len(results) == 2
    # snippet 兜底到 summary 键
    assert results[1]["summary"] == "片段二"
    # 调用参数透传
    assert _FakeAsyncClient.last_json["query"] == "万科 处罚"
    assert _FakeAsyncClient.last_json["count"] == 3
    assert _FakeAsyncClient.last_json["freshness"] == "oneYear"


async def test_bocha_web_search_empty_query():
    from utils.web_search import bocha_web_search

    with pytest.raises(ValueError):
        await bocha_web_search("  ")


# ── 重试 / 失败路径 ─────────────────────────────────────────

def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """构造指定状态码的 HTTPStatusError。"""
    request = httpx.Request("POST", "https://x")
    return httpx.HTTPStatusError(
        "err", request=request, response=httpx.Response(status_code, request=request)
    )


def _make_flaky_client(errors, payload=None):
    """构造前几次 post 抛指定异常、之后返回固定响应的假客户端类。

    Args:
        errors: 依次抛出的异常列表，耗尽后返回正常响应。
        payload: 正常响应体，缺省用 _BOCHA_RESPONSE。
    """

    class _FlakyClient:
        call_count = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            idx = _FlakyClient.call_count
            _FlakyClient.call_count += 1
            if idx < len(errors):
                raise errors[idx]
            return _FakeResponse(payload or _BOCHA_RESPONSE)

    return _FlakyClient


async def _noop_sleep(_seconds):
    pass


async def test_bocha_web_search_retries_on_5xx(monkeypatch):
    """首次 5xx，重试后成功。"""
    from utils import web_search

    client_cls = _make_flaky_client([_http_status_error(500)])
    monkeypatch.setattr(web_search.httpx, "AsyncClient", client_cls)
    monkeypatch.setattr(web_search.asyncio, "sleep", _noop_sleep)

    formatted, results = await web_search.bocha_web_search("万科 处罚")

    assert client_cls.call_count == 2
    assert "[1] 标题一" in formatted
    assert len(results) == 2


async def test_bocha_web_search_4xx_no_retry(monkeypatch):
    """403 鉴权错误立即抛出，不重试。"""
    from utils import web_search

    client_cls = _make_flaky_client([_http_status_error(403), _http_status_error(403)])
    monkeypatch.setattr(web_search.httpx, "AsyncClient", client_cls)
    monkeypatch.setattr(web_search.asyncio, "sleep", _noop_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await web_search.bocha_web_search("万科 处罚")
    assert client_cls.call_count == 1


async def test_bocha_web_search_retry_exhausted(monkeypatch):
    """持续 5xx 重试耗尽后抛出最后一次异常。"""
    from utils import web_search
    from utils.config import get_config

    retry_count = get_config().web_search.retry_count or 1
    client_cls = _make_flaky_client([_http_status_error(500)] * (retry_count + 1))
    monkeypatch.setattr(web_search.httpx, "AsyncClient", client_cls)
    monkeypatch.setattr(web_search.asyncio, "sleep", _noop_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await web_search.bocha_web_search("万科 处罚")
    assert client_cls.call_count == retry_count


async def test_bocha_web_search_webpages_null(monkeypatch):
    """响应 webPages 为 null 时返回空结果而非崩溃。"""
    from utils import web_search

    client_cls = _make_flaky_client([], payload={"code": 200, "data": {"webPages": None}})
    monkeypatch.setattr(web_search.httpx, "AsyncClient", client_cls)

    formatted, results = await web_search.bocha_web_search("万科 处罚")

    assert formatted == "（未搜索到相关结果）"
    assert results == []
