"""insert 列与 collection schema 的对位契约：全程不连真实 Milvus。

线上事故（2026-08-25）：collection_name 指向父子分块改造前建的旧库（9 字段、
无 parent_chunk_id），新代码插 10 列，pymilvus 报
`expect 9 list, got 10`——而 ensure_collection 见库已存在就直接复用、不校验
schema，错误一路潜伏到 485 个向量都算完才爆。

这里锁三件事：schema 字段与 INSERT_COLUMNS 严格同序等长、子块行恰好覆盖全部
insert 列、以及 VARCHAR 列宽容得下真实数据。
"""

from __future__ import annotations

import pytest

from service import embedding_service
from service.subchunk_service import split_into_subchunks
from utils.config import MilvusConfig
from utils.milvus_client import INSERT_COLUMNS, MilvusClient, build_collection_schema


def _parent(chunk_id="p1", content="内容", page_num="1"):
    return {
        "file_id": "f" * 32,
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "total_chunks": 1,
        "chunk_content": content,
        "start_pos": 0,
        "end_pos": len(content),
        "page_num": page_num,
    }


def test_insert_columns_match_schema_field_order_exactly():
    """列数与顺序都必须与 schema 一致。

    列数不一致 → pymilvus 直接报 `expect N list, got M`；
    顺序不一致 → 不报错，只会让向量与文本静默对不上（更坏）。
    """
    schema_names = [f.name for f in build_collection_schema(dim=8).fields]

    assert schema_names == INSERT_COLUMNS


def test_insert_builds_exactly_one_list_per_schema_field():
    """行转列的结果长度 == schema 字段数，正是线上报错的那个判定条件。"""
    captured = {}

    class FakeCollection:
        def insert(self, insert_data):
            captured["data"] = insert_data

        def flush(self):
            pass

    client = MilvusClient(config=MilvusConfig())
    client._collection = FakeCollection()

    row = {name: 0 for name in INSERT_COLUMNS}
    row["embedding"] = [0.1] * 8
    client.insert([row])

    expected_field_count = len(build_collection_schema(dim=8).fields)
    assert len(captured["data"]) == expected_field_count
    assert all(len(column) == 1 for column in captured["data"])


async def test_subchunk_rows_cover_every_insert_column(monkeypatch):
    """真实子块 → 真实 submit_to_milvus 构造的行，键恰好等于 INSERT_COLUMNS。

    少一个键 → insert 里 row[key] 抛 KeyError；多一个键 → 该字段被静默丢弃。
    """
    inserted = {}

    class FakeClient:
        def insert(self, data):
            inserted["data"] = data

    monkeypatch.setattr(embedding_service, "get_milvus_client", lambda: FakeClient())

    long_text = "。".join(f"第{i}句内容填充到足够长度用于触发子块切分" for i in range(30))
    sub_chunks = split_into_subchunks([_parent(content=long_text)])
    assert len(sub_chunks) > 1  # 确认真的切出了多个子块，不是空跑

    await embedding_service.submit_to_milvus(
        sub_chunks, [[0.1] * 8 for _ in sub_chunks]
    )

    for row in inserted["data"]:
        assert set(row) == set(INSERT_COLUMNS)


@pytest.mark.parametrize("page_num", ["1", "1-3", "999-1000"])
def test_page_num_fits_varchar_limit(page_num):
    """page_num 列宽 20 字节，lookup_page_num 只产出 "1" / "1-3" 两种形态。"""
    field = next(f for f in build_collection_schema(dim=8).fields if f.name == "page_num")

    assert len(page_num.encode("utf-8")) <= field.params["max_length"]


def test_subchunk_id_fits_varchar_limit():
    """子块 id 是 {32位父块id}_s{序号}，必须塞得进 chunk_id 的 64 字节。"""
    parent_id = "a" * 32
    long_text = "。".join(f"第{i}句内容填充到足够长度用于触发子块切分" for i in range(80))
    sub_chunks = split_into_subchunks([_parent(chunk_id=parent_id, content=long_text)])

    field = next(f for f in build_collection_schema(dim=8).fields if f.name == "chunk_id")
    limit = field.params["max_length"]

    assert len(sub_chunks) > 10  # 序号进到两位数，确保覆盖较长的 id
    for sub in sub_chunks:
        assert len(sub["chunk_id"].encode("utf-8")) <= limit
