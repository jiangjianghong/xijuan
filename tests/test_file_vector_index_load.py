"""全量拉取测试：打桩 Milvus collection，验证拉取、降级与字段裁剪。"""

import numpy as np

from service import file_vector_index


class FakeIterator:
    """模拟 pymilvus Collection.query_iterator：next() 逐批返回，空列表结束。"""

    def __init__(self, batches, recorder):
        self._batches = list(batches)
        self._recorder = recorder
        self.closed = False

    def next(self):
        return self._batches.pop(0) if self._batches else []

    def close(self):
        self.closed = True


class FakeCollection:
    def __init__(self, batches, recorder):
        self._batches = batches
        self._recorder = recorder

    def query_iterator(self, **kwargs):
        self._recorder.update(kwargs)
        return FakeIterator(self._batches, self._recorder)


def _install(monkeypatch, batches, recorder):
    class FakeClient:
        def ensure_collection(self):
            return FakeCollection(batches, recorder)

    monkeypatch.setattr(file_vector_index, "get_milvus_client", lambda: FakeClient())


def _parent(chunk_id):
    class Row:
        def __init__(self, cid):
            self.chunk_id = cid
            self.chunk_content = f"父块{cid}"
            self.chunk_index = 0
            self.start_pos = 0
            self.end_pos = 10
            self.page_num = "1"

    return Row(chunk_id)


async def test_load_builds_normalized_matrix(monkeypatch):
    """拉取后矩阵已归一化，行序与 sub_ids / parent_ids 一致。"""
    recorder = {}
    _install(monkeypatch, [[
        {"chunk_id": "p1_s0", "parent_chunk_id": "p1", "embedding": [3.0, 4.0]},
        {"chunk_id": "p1_s1", "parent_chunk_id": "p1", "embedding": [0.0, 1.0]},
    ]], recorder)

    index = await file_vector_index.load_file_vector_index("f1", [_parent("p1")])

    assert index.sub_ids == ("p1_s0", "p1_s1")
    assert index.parent_ids == ("p1", "p1")
    assert np.allclose(np.linalg.norm(index.matrix, axis=1), 1.0)
    assert index.degraded is False
    assert index.parents["p1"].chunk_content == "父块p1"


async def test_load_never_requests_chunk_content(monkeypatch):
    """output_fields 必须不含 chunk_content——子块存了完整文本，
    拉全量时带上它会让传输量雪崩。父块文本从快照取。"""
    recorder = {}
    _install(monkeypatch, [[
        {"chunk_id": "p1_s0", "parent_chunk_id": "p1", "embedding": [1.0, 0.0]},
    ]], recorder)

    await file_vector_index.load_file_vector_index("f1", [_parent("p1")])

    assert "chunk_content" not in recorder["output_fields"]
    assert set(recorder["output_fields"]) == {"chunk_id", "parent_chunk_id", "embedding"}


async def test_load_filters_by_file_id(monkeypatch):
    """expr 必须按 file_id 过滤，否则会把整库向量拉进内存。"""
    recorder = {}
    _install(monkeypatch, [[
        {"chunk_id": "p1_s0", "parent_chunk_id": "p1", "embedding": [1.0, 0.0]},
    ]], recorder)

    await file_vector_index.load_file_vector_index("f1", [_parent("p1")])

    assert recorder["expr"] == 'file_id == "f1"'


async def test_load_drains_multiple_batches(monkeypatch):
    """query_iterator 分批返回，必须全部取完。"""
    recorder = {}
    _install(monkeypatch, [
        [{"chunk_id": "p1_s0", "parent_chunk_id": "p1", "embedding": [1.0, 0.0]}],
        [{"chunk_id": "p2_s0", "parent_chunk_id": "p2", "embedding": [0.0, 1.0]}],
    ], recorder)

    index = await file_vector_index.load_file_vector_index(
        "f1", [_parent("p1"), _parent("p2")]
    )

    assert index.size == 2
    assert index.sub_ids == ("p1_s0", "p2_s0")


async def test_load_degrades_when_over_threshold(monkeypatch):
    """子块数超阈值时置 degraded 并丢掉矩阵，避免把内存打爆。"""
    recorder = {}
    rows = [
        {"chunk_id": f"p1_s{i}", "parent_chunk_id": "p1", "embedding": [1.0, 0.0]}
        for i in range(5)
    ]
    _install(monkeypatch, [rows], recorder)
    monkeypatch.setattr(
        file_vector_index, "_max_subchunks", lambda: 3
    )

    index = await file_vector_index.load_file_vector_index("f1", [_parent("p1")])

    assert index.degraded is True
    assert index.matrix is None
    assert index.parents["p1"] is not None  # 父块表仍可用，供 ANN 路径映射


async def test_load_empty_file_returns_empty_index(monkeypatch):
    """文件在新 collection 里没有任何向量（存量文件）时返回空索引，不抛错。"""
    recorder = {}
    _install(monkeypatch, [[]], recorder)

    index = await file_vector_index.load_file_vector_index("f1", [])

    assert index.size == 0
    assert index.matrix is None
    assert index.degraded is False
