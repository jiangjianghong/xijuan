"""单文件向量索引：抽取链路的向量检索不走 ANN，改内存全量打分。

**为什么去 IVF**：抽取链路的向量检索恒带 file_id 过滤，范围只有单文件的
几百~几千个子块。IVF 是「先选簇再过滤」，nprobe=16 / nlist=4096 只探
16/4096 ≈ 0.4% 的簇，该文件的块大概率根本不落在被探到的簇里 → 静默丢召回，
且完全不可察。单文件几千条向量的暴力点积只要几十毫秒，相对一次抽取里
几十轮 LLM 调用完全可忽略。

Milvus 因此在抽取链路退化成「按 file_id 取向量」的 KV 存储；ANN 索引
之后只对 /search 的跨文件检索还有意义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class FileVectorIndex:
    """单个文件的全部子块向量 + 父块查找表。

    并发段只读，不持有 session、不做 IO。

    Attributes:
        sub_ids: 子块 id，与 matrix 的行一一对应。
        parent_ids: 每个子块所属的父块 id，与 sub_ids 等长。
        matrix: (N, dim) float32，**已 L2 归一化**，故点积即余弦。
            子块数超阈值（degraded）或文件无向量时为 None。
        parents: parent_chunk_id -> 父块对象。父块对象需有 chunk_id /
            chunk_content / chunk_index / start_pos / end_pos / page_num
            属性（extraction_snapshot.ChunkRow 满足）。此处用 duck typing
            而非直接 import ChunkRow，避免与 extraction_snapshot 循环依赖。
        degraded: True 表示子块数超过 max_bruteforce_subchunks，调用方
            应回落 ANN 路径。
    """

    file_id: str
    sub_ids: Tuple[str, ...]
    parent_ids: Tuple[str, ...]
    matrix: Optional[np.ndarray]
    parents: Dict[str, Any]
    degraded: bool

    @property
    def size(self) -> int:
        return len(self.sub_ids)


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """按行 L2 归一化，使点积等价于余弦相似度。

    与 Milvus 的 metric_type=COSINE 口径对齐。embedding 接口通常已返回
    单位向量，这里仍显式归一化作为防御；零向量除零保护避免产生 NaN。
    """
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def select_parent_hits(
    index: FileVectorIndex,
    query_vector: np.ndarray,
    *,
    score_threshold: Optional[float],
    score_ratio: float,
    top_k: Optional[int],
    max_results: int,
) -> List[Tuple[str, float]]:
    """对单个 query 全量打分，返回按分数降序、已去重的父块命中。

    同一父块的多个子块命中时，取**最高子块分**作为父块分——父块要么进
    prompt 要么不进，重复出现只会挤占别的父块。

    筛选顺序：绝对阈值 → 相对分差（score >= max_score * score_ratio）→
    条数截断（显式 top_k 优先，否则 max_results 兜底）。

    Args:
        index: 单文件向量索引。
        query_vector: 查询向量，内部会归一化。
        score_threshold: 绝对相似度下限，None 表示不设。
        score_ratio: 相对分差系数，0 表示不做相对筛选。
        top_k: 显式配置的返回条数上限；None 表示未配。
        max_results: 未配 top_k 时的安全上限。

    Returns:
        [(parent_chunk_id, score), ...]，按 score 降序。
    """
    if index.matrix is None or index.size == 0:
        return []

    query = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)
    query = l2_normalize(query)[0]

    scores = index.matrix @ query

    # 父块取最高子块分
    best: Dict[str, float] = {}
    for parent_id, score in zip(index.parent_ids, scores):
        value = float(score)
        if parent_id not in best or value > best[parent_id]:
            best[parent_id] = value

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)

    if score_threshold is not None:
        ranked = [(p, s) for p, s in ranked if s >= score_threshold]

    if ranked and score_ratio > 0:
        floor = ranked[0][1] * score_ratio
        ranked = [(p, s) for p, s in ranked if s >= floor]

    limit = top_k if top_k else max_results
    return ranked[:limit]
