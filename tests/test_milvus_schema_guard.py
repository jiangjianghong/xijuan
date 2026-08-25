"""ensure_collection 的 schema 守卫：不连真实 Milvus。

线上事故（2026-08-25）：collection_name 指向父子分块改造前建的旧库，
ensure_collection 见 has_collection 为真就直接复用、不看 schema，于是错误
潜伏到 embedding 末尾的 insert 才爆成 `expect 9 list, got 10`——既看不出是
配置指错了库，又白烧了一整轮 embedding 调用。

守卫要求：复用已存在的 collection 前必须比对字段（名字 + 顺序 + 向量维度），
不一致就抛可读异常。
"""

from __future__ import annotations

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema

from utils import milvus_client as mc
from utils.config import MilvusConfig
from utils.milvus_client import (
    INSERT_COLUMNS,
    MilvusClient,
    MilvusSchemaMismatchError,
    build_collection_schema,
)

DIM = 8


def _legacy_schema(dim: int = DIM) -> CollectionSchema:
    """父子分块改造前的 9 字段 schema（无 parent_chunk_id），即线上那个库。"""
    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="total_chunks", dtype=DataType.INT64),
        FieldSchema(name="chunk_content", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="start_pos", dtype=DataType.INT64),
        FieldSchema(name="end_pos", dtype=DataType.INT64),
        FieldSchema(name="page_num", dtype=DataType.VARCHAR, max_length=20),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    return CollectionSchema(fields=fields, description="file_chunks")


class FakeCollection:
    def __init__(self, schema):
        self.schema = schema
        self.loaded = False

    def load(self):
        self.loaded = True


def _patch_existing(monkeypatch, schema):
    """让 has_collection 为真、Collection(name) 返回带指定 schema 的假对象。"""
    monkeypatch.setattr(mc.utility, "has_collection", lambda name: True)
    fake = FakeCollection(schema)
    monkeypatch.setattr(mc, "Collection", lambda *a, **kw: fake)
    return fake


def _client():
    return MilvusClient(config=MilvusConfig(collection_name="xijuan"))


def test_reuse_rejects_legacy_schema_missing_parent_chunk_id(monkeypatch):
    """线上原样复现：旧库缺 parent_chunk_id，必须当场抛错而不是留到 insert。"""
    _patch_existing(monkeypatch, _legacy_schema())

    with pytest.raises(MilvusSchemaMismatchError) as exc:
        _client().ensure_collection(embedding_dim=DIM)

    message = str(exc.value)
    assert "parent_chunk_id" in message  # 点名到底差哪个字段
    assert "xijuan" in message  # 点名是哪个 collection
    assert "collection_name" in message  # 给出可执行动作


def test_reuse_rejects_field_order_mismatch(monkeypatch):
    """字段名齐全但顺序不同也要拒绝——错位不报错，只让向量与文本静默对不上。"""
    fields = list(build_collection_schema(dim=DIM).fields)
    fields[1], fields[2] = fields[2], fields[1]  # file_id 与 parent_chunk_id 互换
    _patch_existing(monkeypatch, CollectionSchema(fields=fields))

    with pytest.raises(MilvusSchemaMismatchError, match="顺序"):
        _client().ensure_collection(embedding_dim=DIM)


def test_reuse_rejects_dim_mismatch(monkeypatch):
    """维度不一致同样插不进去，提前暴露。"""
    _patch_existing(monkeypatch, build_collection_schema(dim=DIM * 2))

    with pytest.raises(MilvusSchemaMismatchError, match="维度"):
        _client().ensure_collection(embedding_dim=DIM)


def test_reuse_accepts_matching_schema(monkeypatch):
    """schema 一致时照常复用并 load，不引入额外阻碍。"""
    fake = _patch_existing(monkeypatch, build_collection_schema(dim=DIM))

    collection = _client().ensure_collection(embedding_dim=DIM)

    assert collection is fake
    assert fake.loaded is True


def test_brand_new_name_creates_current_schema(monkeypatch):
    """换一个没用过的名字：走创建分支，按当前 10 字段 schema 建库并建索引。"""
    created = {}

    monkeypatch.setattr(mc.utility, "has_collection", lambda name: False)

    class NewCollection:
        def __init__(self, name, schema):
            created["name"] = name
            created["schema"] = schema

        def create_index(self, field_name, index_params):
            created["index_field"] = field_name
            created["index_params"] = index_params

        def load(self):
            created["loaded"] = True

    monkeypatch.setattr(mc, "Collection", lambda name, schema: NewCollection(name, schema))

    _client().ensure_collection(embedding_dim=DIM)

    assert created["name"] == "xijuan"
    assert [f.name for f in created["schema"].fields] == INSERT_COLUMNS
    assert created["index_field"] == "embedding"
    assert created["loaded"] is True
