"""Milvus ANN 候选迭代检索测试。"""

from types import SimpleNamespace

from utils.config import MilvusConfig
from utils.milvus_client import MilvusClient


class FakeIterator:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.closed = False

    def next(self):
        return next(self.pages, [])

    def close(self):
        self.closed = True


class FakeCollection:
    def __init__(self, pages):
        self.pages = pages
        self.kwargs = None
        self.iterator = None

    def search_iterator(self, **kwargs):
        self.kwargs = kwargs
        self.iterator = FakeIterator(self.pages)
        return self.iterator


def _hit(parent_id, score):
    return SimpleNamespace(
        distance=score,
        entity={"parent_chunk_id": parent_id},
    )


def _client(collection):
    client = MilvusClient.__new__(MilvusClient)
    client.config = MilvusConfig(metric_type="COSINE")
    client._collection = collection
    return client


def test_search_candidates_iterates_until_combined_threshold_and_closes():
    collection = FakeCollection([
        [_hit("p1", 0.96), _hit("p2", 0.86)],
        [_hit("p3", 0.80), _hit("p4", 0.70)],
    ])

    hits = _client(collection).search_candidates(
        [1.0, 0.0],
        file_id="file-1",
        score_threshold=0.75,
        score_ratio=0.85,
    )

    assert hits == [
        {"parent_chunk_id": "p1", "score": 0.96},
        {"parent_chunk_id": "p2", "score": 0.86},
    ]
    assert collection.kwargs["limit"] == -1
    assert collection.kwargs["batch_size"] == 1000
    assert collection.kwargs["expr"] == 'file_id == "file-1"'
    assert collection.kwargs["output_fields"] == ["parent_chunk_id"]
    assert collection.iterator.closed


def test_search_candidates_collects_all_pages_when_no_score_cutoff():
    collection = FakeCollection([
        [_hit("p1", 0.6)],
        [_hit("p2", 0.5)],
        [],
    ])

    hits = _client(collection).search_candidates(
        [1.0, 0.0], score_ratio=0.0
    )

    assert [hit["parent_chunk_id"] for hit in hits] == ["p1", "p2"]
    assert collection.iterator.closed
