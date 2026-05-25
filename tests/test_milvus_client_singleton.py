"""Milvus 客户端单例测试。"""

from __future__ import annotations

import pytest

from utils import milvus_client as mc_module
from utils.milvus_client import MilvusClient, get_milvus_client


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前后都把单例复位,避免互相污染。"""
    mc_module._singleton = None
    yield
    mc_module._singleton = None


def test_get_milvus_client_returns_same_instance(monkeypatch):
    """连续调用返回同一个对象,connect/ensure_collection 只触发一次。"""
    call_counter = {"connect": 0, "ensure_collection": 0}

    def fake_connect(self):
        call_counter["connect"] += 1

    def fake_ensure_collection(self, embedding_dim=None):
        call_counter["ensure_collection"] += 1
        return object()

    monkeypatch.setattr(MilvusClient, "connect", fake_connect)
    monkeypatch.setattr(MilvusClient, "ensure_collection", fake_ensure_collection)

    c1 = get_milvus_client()
    c2 = get_milvus_client()
    c3 = get_milvus_client()

    assert c1 is c2 is c3
    assert call_counter["connect"] == 1
    assert call_counter["ensure_collection"] == 1


def test_get_milvus_client_first_call_failure_does_not_cache(monkeypatch):
    """首次创建失败时,单例不被缓存,下次调用可重试。"""
    call_counter = {"connect": 0}

    def fake_connect(self):
        call_counter["connect"] += 1
        if call_counter["connect"] == 1:
            raise RuntimeError("first attempt fails")

    monkeypatch.setattr(MilvusClient, "connect", fake_connect)
    monkeypatch.setattr(MilvusClient, "ensure_collection", lambda self, embedding_dim=None: object())

    with pytest.raises(RuntimeError):
        get_milvus_client()

    # 第二次:不再抛错,应该返回正常实例
    client = get_milvus_client()
    assert client is not None
    assert call_counter["connect"] == 2
