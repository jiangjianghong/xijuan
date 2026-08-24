"""单文件向量索引测试：打分与筛选全是纯函数，不连 Milvus。"""

import numpy as np

from service.file_vector_index import (
    FileVectorIndex,
    l2_normalize,
    select_parent_hits,
)


def _index(vectors, parent_ids, degraded=False):
    """构造一个索引；vectors 会被归一化，与真实加载路径一致。"""
    matrix = l2_normalize(np.array(vectors, dtype=np.float32))
    return FileVectorIndex(
        file_id="f1",
        sub_ids=tuple(f"s{i}" for i in range(len(parent_ids))),
        parent_ids=tuple(parent_ids),
        matrix=matrix,
        parents={},
        degraded=degraded,
    )


def test_l2_normalize_makes_unit_vectors():
    """归一化后点积 == 余弦相似度，与 Milvus 的 COSINE 口径一致。"""
    out = l2_normalize(np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32))

    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)
    assert np.allclose(out[0], [0.6, 0.8])


def test_l2_normalize_survives_zero_vector():
    """零向量不能除零炸掉——embedding 接口偶发返回全零。"""
    out = l2_normalize(np.array([[0.0, 0.0], [1.0, 0.0]], dtype=np.float32))

    assert not np.isnan(out).any()
    assert np.allclose(out[0], [0.0, 0.0])


def test_select_parent_hits_ranks_by_score_desc():
    """结果按分数降序，与 query 完全同向的子块排第一。"""
    index = _index([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]], ["pa", "pb", "pc"])

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=None, score_ratio=0.0, top_k=None, max_results=10,
    )

    assert [p for p, _s in hits] == ["pa", "pc", "pb"]


def test_select_parent_hits_dedupes_parents_by_best_subchunk():
    """同一父块多个子块命中时只出现一次，取最高子块分作为父块分。"""
    index = _index([[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]], ["pa", "pa", "pb"])

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=None, score_ratio=0.0, top_k=None, max_results=10,
    )

    assert [p for p, _s in hits] == ["pa", "pb"]
    assert abs(hits[0][1] - 1.0) < 1e-5  # 取 0.6 与 1.0 中的高分


def test_select_parent_hits_applies_relative_ratio():
    """相对分差：只保留与最高分同档的结果，取代 top_k 硬切。"""
    index = _index([[1.0, 0.0], [0.9, 0.436], [0.0, 1.0]], ["pa", "pb", "pc"])

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=None, score_ratio=0.85, top_k=None, max_results=10,
    )

    # pa=1.0 pb≈0.9 保留（>=0.85），pc=0.0 被分差刷掉
    assert [p for p, _s in hits] == ["pa", "pb"]


def test_select_parent_hits_applies_absolute_threshold():
    """绝对阈值与相对分差同时生效，两道都要过。"""
    index = _index([[1.0, 0.0], [0.9, 0.436]], ["pa", "pb"])

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=0.95, score_ratio=0.0, top_k=None, max_results=10,
    )

    assert [p for p, _s in hits] == ["pa"]


def test_select_parent_hits_respects_explicit_top_k():
    """显式配了 top_k 就尊重存量配置，按它截断。"""
    index = _index([[1.0, 0.0], [0.99, 0.1], [0.98, 0.2]], ["pa", "pb", "pc"])

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=None, score_ratio=0.0, top_k=2, max_results=10,
    )

    assert len(hits) == 2


def test_select_parent_hits_falls_back_to_max_results():
    """未配 top_k 时用 max_results 兜底，防止阈值过松把 prompt 撑爆。"""
    index = _index([[1.0, 0.0]] * 30, [f"p{i}" for i in range(30)])

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=None, score_ratio=0.0, top_k=None, max_results=5,
    )

    assert len(hits) == 5


def test_select_parent_hits_on_empty_index():
    """空索引返回空列表，不抛异常。"""
    index = FileVectorIndex(
        file_id="f1", sub_ids=(), parent_ids=(), matrix=None,
        parents={}, degraded=False,
    )

    hits = select_parent_hits(
        index, np.array([1.0, 0.0], dtype=np.float32),
        score_threshold=None, score_ratio=0.85, top_k=None, max_results=10,
    )

    assert hits == []


def test_index_size_reports_subchunk_count():
    index = _index([[1.0, 0.0], [0.0, 1.0]], ["pa", "pb"])
    assert index.size == 2
