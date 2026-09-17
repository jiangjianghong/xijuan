"""_page_annotated_text 逐页标注与降级路径测试。"""

from __future__ import annotations

import pytest

from service.extraction_service import _page_annotated_text

_MAPPING = [
    {"start_pos": 0, "page_num": 1},
    {"start_pos": 10, "page_num": 2},
]


def test_cross_page_text_gets_per_page_markers():
    """跨页命中按页边界切开，每段各带真实单页标记。"""
    text = "AAAAAAAAAABBBBBBBBBB"  # 20 字，第 10 字处翻页
    out = _page_annotated_text(text, _MAPPING, 0, 20, "1-2")
    assert out == "【第1页】\nAAAAAAAAAA\n【第2页】\nBBBBBBBBBB"


def test_single_page_keeps_single_marker():
    """未跨页时与改动前行为逐字一致。"""
    out = _page_annotated_text("AAAAA", _MAPPING, 0, 5, "1")
    assert out == "【第1页】\nAAAAA"


def test_empty_mapping_falls_back_to_single_marker():
    out = _page_annotated_text("AAAAA", [], 0, 5, "1")
    assert out == "【第1页】\nAAAAA"


def test_length_mismatch_falls_back_to_single_marker():
    """文本长度与坐标跨度不吻合（如带表名前缀的表格 chunk）→ 不切分，
    沿用调用方给的兜底页码（此处是范围串，即改动前的行为）。"""
    out = _page_annotated_text("表格名称: 报价\nAAAAA", _MAPPING, 0, 20, "1-2")
    assert out == "【第1-2页】\n表格名称: 报价\nAAAAA"


def test_missing_coords_falls_back_to_single_marker():
    out = _page_annotated_text("AAAAA", _MAPPING, None, None, "2")
    assert out == "【第2页】\nAAAAA"


def test_no_page_info_returns_bare_text():
    """无 mapping 且无兜底页码时返回裸原文，保持现有 rule 检索行为。"""
    assert _page_annotated_text("工期为90天", [], None, None, "") == "工期为90天"


def test_debug_stream_join_matches_official_extraction():
    """调试流与正式抽取用同一个标注函数，产出必须逐字相同。"""
    import inspect

    from service import extraction_service

    src = inspect.getsource(extraction_service.test_field_extraction_stream)
    # 调试流不得再用裸 _page_prefix 拼接文本段，必须走 _page_annotated_text
    assert "_page_annotated_text(" in src
    assert "_page_prefix(_result_page_num" not in src


@pytest.mark.parametrize("direction", ["forward", "backward", "both"])
async def test_rule_trimmed_cross_page_result_keeps_per_page_markers(direction):
    """规则去除首尾空白后仍准确定位原文，并逐页注入模型。"""
    from service.extraction_service import search_rule, _build_text_source_refs

    content = "前缀。 \t甲关键词乙\n丙关键词丁 \t。后缀"
    boundary = content.index("丙")
    mapping = [
        {"start_pos": 0, "page_num": 1},
        {"start_pos": boundary, "page_num": 2},
    ]
    results = await search_rule(content, {
        "keywords": ["关键词"], "stop_words": ["。"],
        "direction": direction, "max_length": 200,
    })
    refs, texts = _build_text_source_refs("rule", results, mapping)
    assert "【第1-2页】" not in texts["关键词"]
    assert "【第1页】" in texts["关键词"]
    assert "【第2页】" in texts["关键词"]
    for result in results:
        assert content[result["start_pos"]:result["end_pos"]] == result["extracted_text"]
        assert content[result["position"]:].startswith("关键词")
    assert refs["_texts"] == texts
