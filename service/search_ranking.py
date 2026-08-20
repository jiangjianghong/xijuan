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


def rank_and_truncate(
    results: List[Dict[str, Any]],
    *,
    weights: Dict[str, float],
    segment_key: str,
    order_key: str,
    max_results: int,
    max_total: int = 0,
    sort_order: str = "relevance",
) -> List[Dict[str, Any]]:
    """按关键词分组排序 → 每组限额 → 轮转合并到总量上限。

    三步顺序不可调换：
      1. 组内排序（relevance 按分数降序、并列按位置升序；asc/desc 按位置）
      2. 组内截断到 max_results —— 低频关键词不会被高频挤掉
      3. 轮转（round-robin）合并到 max_total —— **不能**换成按全局分数
         一刀切，那会让低分关键词整组消失，占位符又变空

    只要 max_total >= 关键词数，每个关键词至少保留一条。

    Args:
        results: 检索方法产出的原始命中，每条须含 keyword 与 order_key。
        weights: compute_keyword_weights 的输出。
        segment_key: 命中片段正文所在的键（context / extracted_text / chunk_content）。
        order_key: 位置序键（position / chunk_index）。
        max_results: 每个关键词最多保留几条。
        max_total: 总条数上限，<=0 表示不限。
        sort_order: relevance / asc / desc。

    Returns:
        截断后的命中列表。
    """
    if not results:
        return []

    # asc/desc 不需要分数，省掉一次全量扫描
    if sort_order == "relevance":
        for r in results:
            r["_score"] = score_segment(r.get(segment_key, "") or "", weights)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        groups.setdefault(r.get("keyword", "") or "", []).append(r)

    reverse_pos = sort_order == "desc"
    for items in groups.values():
        if sort_order == "relevance":
            # 并列按位置升序：单关键词时分数全相等，行为退化成与 asc 一致
            items.sort(key=lambda x: (-x["_score"], x.get(order_key) or 0))
        else:
            items.sort(key=lambda x: x.get(order_key) or 0, reverse=reverse_pos)
        del items[max_results:]

    # max_results<=0 会把分组清空，空分组不能参与组间排序（读 g[0] 会越界）
    non_empty = [g for g in groups.values() if g]

    # 组间顺序：相关度模式让最相关的关键词先出，总量紧张时它多拿一条
    if sort_order == "relevance":
        ordered = sorted(non_empty, key=lambda g: -g[0]["_score"])
    else:
        ordered = sorted(
            non_empty,
            key=lambda g: g[0].get(order_key) or 0,
            reverse=reverse_pos,
        )

    limit = max_total if max_total and max_total > 0 else None
    merged: List[Dict[str, Any]] = []
    deepest = max((len(g) for g in ordered), default=0)
    for depth in range(deepest):
        if limit is not None and len(merged) >= limit:
            break
        for group in ordered:
            if depth >= len(group):
                continue
            if limit is not None and len(merged) >= limit:
                break
            merged.append(group[depth])

    for r in merged:
        r.pop("_score", None)
    return merged
