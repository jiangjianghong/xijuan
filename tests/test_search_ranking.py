"""关键词检索相关度打分与截断的单测（纯函数，无需数据库）。"""

from service.extraction_snapshot import ChunkRow


def _chunk(index: int, text: str) -> ChunkRow:
    """构造一条只读分块快照，只有 chunk_content 参与打分。"""
    return ChunkRow(
        chunk_id=f"c{index}",
        chunk_index=index,
        chunk_content=text,
        start_pos=index * 100,
        end_pos=index * 100 + len(text),
        page_num="1",
    )


def test_compute_keyword_weights_rare_keyword_weighs_more():
    """罕见关键词的 IDF 应显著高于满篇都有的关键词。"""
    from service.search_ranking import compute_keyword_weights

    chunks = [_chunk(i, "项目名称 招标人") for i in range(9)]
    chunks.append(_chunk(9, "工程名称 项目名称 招标人"))

    weights = compute_keyword_weights(["项目名称", "工程名称"], chunks)
    assert weights["工程名称"] > weights["项目名称"]


def test_compute_keyword_weights_is_case_insensitive():
    """df 统计大小写不敏感，与 search_chunk_db 的匹配口径一致。"""
    from service.search_ranking import compute_keyword_weights

    chunks = [_chunk(0, "Contract No. 123"), _chunk(1, "无关内容")]
    weights = compute_keyword_weights(["contract"], chunks)
    # df=1, N=2 -> ln(1 + 2/2) = ln(2)
    assert abs(weights["contract"] - 0.6931471805599453) < 1e-9


def test_compute_keyword_weights_no_chunks_degrades_to_uniform():
    """无分块时全部退化为 1.0，相关度等价于纯覆盖度计数。"""
    from service.search_ranking import compute_keyword_weights

    assert compute_keyword_weights(["A", "B"], []) == {"A": 1.0, "B": 1.0}


def test_compute_keyword_weights_skips_empty_keywords():
    """空关键词不进权重表，避免空串命中一切。"""
    from service.search_ranking import compute_keyword_weights

    weights = compute_keyword_weights(["", "金额"], [_chunk(0, "金额100")])
    assert list(weights) == ["金额"]


def test_score_segment_sums_weights_of_present_keywords():
    """片段分数 = 其中出现的不同关键词权重之和。"""
    from service.search_ranking import score_segment

    weights = {"项目名称": 2.0, "工程名称": 4.0, "招标人": 1.0}
    assert score_segment("本项目名称为XX，工程名称同上", weights) == 6.0


def test_score_segment_counts_each_keyword_once():
    """同一关键词重复出现只计一次，避免关键词堆砌的目录页拿高分。"""
    from service.search_ranking import score_segment

    assert score_segment("金额金额金额", {"金额": 3.0}) == 3.0


def test_score_segment_empty_returns_zero():
    """空片段得 0 分。"""
    from service.search_ranking import score_segment

    assert score_segment("", {"金额": 3.0}) == 0.0


# ---------------------------------------------------------------------------
# rank_and_truncate：分组排序 → 每关键词限额 → 轮转合并到总量上限
# ---------------------------------------------------------------------------

def _hit(keyword: str, position: int, context: str) -> dict:
    return {"keyword": keyword, "position": position, "context": context}


def test_rank_single_keyword_degrades_to_position_order():
    """单关键词时分数全相等，退化成按位置升序——与改动前行为逐字一致。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("金额", 300, "金额三"), _hit("金额", 100, "金额一"),
               _hit("金额", 200, "金额二")]
    out = rank_and_truncate(
        results, weights={"金额": 2.0}, segment_key="context",
        order_key="position", max_results=5, max_total=0, sort_order="relevance",
    )
    assert [r["position"] for r in out] == [100, 200, 300]


def test_rank_prefers_co_occurring_segment_over_earlier_one():
    """共现罕见关键词的片段，排在文档更靠前的孤立命中之前。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("项目名称", 120, "项目名称"),
               _hit("项目名称", 15200, "项目名称与工程名称")]
    out = rank_and_truncate(
        results, weights={"项目名称": 2.38, "工程名称": 4.62},
        segment_key="context", order_key="position",
        max_results=5, max_total=0, sort_order="relevance",
    )
    assert [r["position"] for r in out] == [15200, 120]


def test_rank_quota_is_per_keyword_not_global():
    """高频关键词不再挤掉低频关键词（回归保护：backlog L21）。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("A", i, "A") for i in range(10)]
    results.append(_hit("B", 9999, "B"))
    out = rank_and_truncate(
        results, weights={"A": 1.0, "B": 1.0}, segment_key="context",
        order_key="position", max_results=3, max_total=0, sort_order="asc",
    )
    assert sum(1 for r in out if r["keyword"] == "A") == 3
    assert sum(1 for r in out if r["keyword"] == "B") == 1


def test_rank_round_robin_keeps_every_keyword_under_total_cap():
    """总量上限用轮转裁剪，每个关键词至少保住一条。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("A", i, "A") for i in range(5)]
    results += [_hit("B", 100 + i, "B") for i in range(5)]
    results += [_hit("C", 200 + i, "C") for i in range(5)]
    out = rank_and_truncate(
        results, weights={"A": 5.0, "B": 1.0, "C": 1.0}, segment_key="context",
        order_key="position", max_results=3, max_total=5, sort_order="relevance",
    )
    assert len(out) == 5
    assert {r["keyword"] for r in out} == {"A", "B", "C"}


def test_rank_max_total_zero_means_unlimited():
    """max_total=0 表示不限总量。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("A", i, "A") for i in range(4)]
    out = rank_and_truncate(
        results, weights={"A": 1.0}, segment_key="context", order_key="position",
        max_results=10, max_total=0, sort_order="relevance",
    )
    assert len(out) == 4


def test_rank_desc_still_orders_by_position_descending():
    """desc 保持原语义：按位置倒序。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("A", 1, "A"), _hit("A", 5, "A"), _hit("A", 3, "A")]
    out = rank_and_truncate(
        results, weights={"A": 1.0}, segment_key="context", order_key="position",
        max_results=5, max_total=0, sort_order="desc",
    )
    assert [r["position"] for r in out] == [5, 3, 1]


def test_rank_strips_internal_score_key():
    """内部打分键不得泄漏到返回结果（会流进 source_refs）。"""
    from service.search_ranking import rank_and_truncate

    out = rank_and_truncate(
        [_hit("A", 1, "A")], weights={"A": 1.0}, segment_key="context",
        order_key="position", max_results=5, max_total=0, sort_order="relevance",
    )
    assert "_score" not in out[0]


def test_rank_empty_input_returns_empty():
    """空输入返回空列表。"""
    from service.search_ranking import rank_and_truncate

    assert rank_and_truncate(
        [], weights={}, segment_key="context", order_key="position",
        max_results=5, max_total=0, sort_order="relevance",
    ) == []


def test_rank_zero_max_results_returns_empty_not_crash():
    """max_results=0 返回空列表（与被替换的 results[:0] 行为一致），不得抛异常。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("A", 1, "A"), _hit("B", 2, "B")]
    assert rank_and_truncate(
        results, weights={"A": 1.0, "B": 1.0}, segment_key="context",
        order_key="position", max_results=0, max_total=0, sort_order="relevance",
    ) == []


def test_rank_negative_max_results_does_not_crash():
    """负数 max_results 不得抛异常（旧代码 results[:-1] 也不崩）。"""
    from service.search_ranking import rank_and_truncate

    results = [_hit("A", 1, "A"), _hit("A", 2, "A"), _hit("A", 3, "A")]
    out = rank_and_truncate(
        results, weights={"A": 1.0}, segment_key="context",
        order_key="position", max_results=-1, max_total=0, sort_order="relevance",
    )
    assert isinstance(out, list)


def test_rank_zero_max_results_asc_returns_empty():
    """asc 模式下同样不崩（该分支的组间排序读的是 order_key 不是 _score）。"""
    from service.search_ranking import rank_and_truncate

    assert rank_and_truncate(
        [_hit("A", 1, "A")], weights={"A": 1.0}, segment_key="context",
        order_key="position", max_results=0, max_total=0, sort_order="asc",
    ) == []
