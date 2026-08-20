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
