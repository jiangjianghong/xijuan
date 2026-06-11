"""博查网络搜索客户端测试。"""

from __future__ import annotations

import httpx
import pytest

from utils.config import AppConfig


def test_web_search_config_defaults():
    """WebSearchConfig 默认值。"""
    cfg = AppConfig().web_search
    assert cfg.base_url == "https://api.bochaai.com/v1/web-search"
    assert cfg.count == 5
    assert cfg.summary is True
    assert cfg.freshness == "noLimit"
    assert cfg.timeout == 10
    assert cfg.retry_count == 2
    assert cfg.max_result_length == 4000
