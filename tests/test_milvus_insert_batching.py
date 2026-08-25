"""insert 分批：gRPC 报文体积上限保护，不连真实 Milvus。

线上事故（2026-08-25，紧接 schema 事故之后）：5982 个子块 × 4096 维一次性
insert，报文 99,682,886 字节，撞上 Milvus 服务端默认 maxRecvMsgSize
67,108,864（64 MiB），报 `RESOURCE_EXHAUSTED: received message larger than
max`。4096 维下每行约 16.4KB，天花板只有约 4000 行——第一个文件 485 子块
侥幸通过，第二个 5982 就炸。

embedding 的 batch_size 只管 embedding 接口的批次，与 Milvus insert 无关。
"""

from __future__ import annotations

import pytest

from utils.config import MilvusConfig
from utils.milvus_client import (
    INSERT_COLUMNS,
    MilvusClient,
    estimate_row_bytes,
    plan_insert_batches,
)

DIM = 4096
GRPC_DEFAULT_LIMIT = 67_108_864  # Milvus 服务端 maxRecvMsgSize 默认值


class FakeCollection:
    """记录每次 insert 的列数据与 flush 次数。"""

    def __init__(self):
        self.inserts = []
        self.flush_count = 0

    def insert(self, insert_data):
        self.inserts.append(insert_data)

    def flush(self):
        self.flush_count += 1


def _rows(count: int, vector):
    """构造 count 行。vector 复用同一个 list 引用，避免测试自己吃掉几百 MB。"""
    return [
        {
            "chunk_id": f"{'a' * 32}_s{i}",
            "parent_chunk_id": "a" * 32,
            "file_id": "f" * 32,
            "chunk_index": i,
            "total_chunks": count,
            "chunk_content": "子块文本内容",
            "start_pos": 0,
            "end_pos": 10,
            "page_num": "1",
            "embedding": vector,
        }
        for i in range(count)
    ]


def _client(collection, max_insert_bytes=None):
    config = MilvusConfig()
    if max_insert_bytes is not None:
        config = MilvusConfig(max_insert_bytes=max_insert_bytes)
    client = MilvusClient(config=config)
    client._collection = collection
    return client


def test_estimate_row_bytes_dominated_by_vector():
    """向量是报文主体：4 字节/维，估算不能忽略它。"""
    row = {"chunk_content": "abc", "embedding": [0.0] * DIM}

    size = estimate_row_bytes(row)

    assert size >= DIM * 4


def test_production_payload_splits_into_batches():
    """线上原样：5982 行 × 4096 维，必须切成多批，且每批估算都在 64 MiB 内。"""
    rows = _rows(5982, [0.0] * DIM)
    limit = MilvusConfig().max_insert_bytes

    batches = plan_insert_batches(rows, limit)

    assert len(batches) > 1
    assert sum(len(b) for b in batches) == len(rows)
    for batch in batches:
        assert sum(estimate_row_bytes(r) for r in batch) <= GRPC_DEFAULT_LIMIT


def test_batches_preserve_row_order():
    """分批不能打乱顺序——向量与 chunk 的对应关系全靠顺序。"""
    rows = _rows(5000, [0.0] * DIM)

    batches = plan_insert_batches(rows, MilvusConfig().max_insert_bytes)

    flat = [r for batch in batches for r in batch]
    assert [r["chunk_id"] for r in flat] == [r["chunk_id"] for r in rows]


def test_small_payload_stays_single_batch():
    """小批量不引入额外往返。"""
    rows = _rows(10, [0.0] * DIM)

    batches = plan_insert_batches(rows, MilvusConfig().max_insert_bytes)

    assert len(batches) == 1


def test_oversized_single_row_still_emitted():
    """单行就超限时也要发出去，交给服务端报错，不能静默丢数据。"""
    rows = _rows(1, [0.0] * DIM)

    batches = plan_insert_batches(rows, max_bytes=1)

    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_insert_sends_one_grpc_call_per_batch():
    """每批一次 insert，列数恒等于 schema 字段数。"""
    collection = FakeCollection()
    rows = _rows(5982, [0.0] * DIM)

    _client(collection).insert(rows)

    assert len(collection.inserts) > 1
    for insert_data in collection.inserts:
        assert len(insert_data) == len(INSERT_COLUMNS)


def test_insert_flushes_once_across_all_batches():
    """flush 很贵，分批不能把它放大成每批一次。"""
    collection = FakeCollection()

    _client(collection).insert(_rows(5982, [0.0] * DIM))

    assert collection.flush_count == 1


def test_insert_preserves_every_row_across_batches():
    """跨批后总行数与顺序都不能变。"""
    collection = FakeCollection()
    rows = _rows(5982, [0.0] * DIM)
    chunk_id_pos = INSERT_COLUMNS.index("chunk_id")

    _client(collection).insert(rows)

    sent = [cid for insert_data in collection.inserts for cid in insert_data[chunk_id_pos]]
    assert sent == [r["chunk_id"] for r in rows]


def test_max_insert_bytes_default_leaves_grpc_headroom():
    """默认值必须显著低于服务端 64 MiB，给估算误差留余量。"""
    assert MilvusConfig().max_insert_bytes < GRPC_DEFAULT_LIMIT


@pytest.mark.parametrize("dim", [1024, 1536, 4096])
def test_batch_size_adapts_to_vector_dim(dim):
    """维度越高每批行数越少——估算按维度走，不是写死行数。"""
    rows = _rows(4000, [0.0] * dim)

    batches = plan_insert_batches(rows, MilvusConfig().max_insert_bytes)

    for batch in batches:
        assert sum(estimate_row_bytes(r) for r in batch) <= GRPC_DEFAULT_LIMIT
