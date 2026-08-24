"""Milvus schema 与配置项测试：只验证结构声明，不连真实 Milvus。"""

from utils.config import MilvusConfig


def test_milvus_config_has_bruteforce_threshold_default():
    """子块数超阈值时抽取链路回落 ANN，这是内存兜底线。"""
    cfg = MilvusConfig()
    assert cfg.max_bruteforce_subchunks == 20000


def test_milvus_config_has_score_ratio_default():
    """相对分差默认 0.85：只保留与最高分同档的结果，取代 top_k 硬切。"""
    cfg = MilvusConfig()
    assert cfg.score_ratio == 0.85


def test_milvus_config_has_max_results_default():
    """未配 top_k 时的安全上限，防止阈值过松导致注入 prompt 的文本爆掉。"""
    cfg = MilvusConfig()
    assert cfg.max_results == 20


def test_collection_schema_declares_parent_chunk_id():
    """子块记录必须带 parent_chunk_id，否则命中后无法映射回父块。"""
    from utils.milvus_client import build_collection_schema

    schema = build_collection_schema(dim=8)
    names = [f.name for f in schema.fields]

    assert "parent_chunk_id" in names
    assert names.index("chunk_id") == 0  # 主键仍是 chunk_id（现为子块 id）


def test_collection_schema_parent_chunk_id_is_varchar_64():
    """父块 id 是 32 位 sha256 前缀，64 足够。"""
    from utils.milvus_client import build_collection_schema

    schema = build_collection_schema(dim=8)
    field = next(f for f in schema.fields if f.name == "parent_chunk_id")

    assert field.params["max_length"] == 64


def test_insert_columns_cover_parent_chunk_id():
    """insert 的行转列必须覆盖新字段，漏了会在写入时报维度不匹配。"""
    from utils.milvus_client import INSERT_COLUMNS

    assert "parent_chunk_id" in INSERT_COLUMNS
    assert INSERT_COLUMNS[0] == "chunk_id"
    assert INSERT_COLUMNS[-1] == "embedding"
