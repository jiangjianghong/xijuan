"""关键词类检索的相关度打分与两层截断。

context / rule / chunk_db 三个方法共用这里的排序与截断逻辑：

- 相关度 = 片段内命中的**不同**关键词的 IDF 之和（覆盖度 × 稀有度）。
  同一关键词多次出现只计一次——词频在这里是噪声，目录页会因关键词
  堆砌拿到高分。
- 截断分两层：先每关键词各限 max_results，再轮转合并到 max_total。
  顺序不能颠倒，轮转也不能换成按全局分数一刀切，否则低频关键词整组
  被挤掉，占位符又会静默变空。

IDF 语料用该文件的 chunks（extraction_snapshot 已加载，零额外查询）。
要的信号是「这个词在这份文档里罕不罕见」，不是全局罕见度。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence


def compute_keyword_weights(
    keywords: Sequence[str],
    chunks: Sequence[Any],
) -> Dict[str, float]:
    """按 IDF 计算每个关键词的权重，罕见词权重高。

        IDF(kw) = ln(1 + N / (1 + df(kw)))

    N 为该文件的 chunk 总数，df 为含该关键词的 chunk 数。df 命中判定
    大小写不敏感，与 search_chunk_db 的过滤口径保持一致。

    Args:
        keywords: 配置的关键词列表，空串会被跳过。
        chunks: 该文件的分块快照，元素需有 chunk_content 属性。

    Returns:
        {关键词原文: 权重}。chunks 为空时全部为 1.0，此时相关度退化为
        纯覆盖度计数（命中了几个不同关键词）。
    """
    valid = [kw for kw in keywords if kw]
    total = len(chunks)
    if total == 0:
        return {kw: 1.0 for kw in valid}

    lowered_chunks = [(getattr(c, "chunk_content", "") or "").lower() for c in chunks]

    weights: Dict[str, float] = {}
    for kw in valid:
        needle = kw.lower()
        df = sum(1 for text in lowered_chunks if needle in text)
        weights[kw] = math.log(1 + total / (1 + df))
    return weights


def score_segment(segment: str, weights: Dict[str, float]) -> float:
    """片段的相关度：其中出现的**不同**关键词的权重之和。

    Args:
        segment: 命中片段正文。
        weights: compute_keyword_weights 的输出。

    Returns:
        相关度分数，无命中为 0.0。
    """
    if not segment:
        return 0.0
    lowered = segment.lower()
    return sum(w for kw, w in weights.items() if kw.lower() in lowered)
