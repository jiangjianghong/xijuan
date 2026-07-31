"""split_span_by_pages 逐页切分纯函数测试。"""

from __future__ import annotations

from utils.page_mapping import split_span_by_pages

# 每页 100 字符的规整映射：第1页@0、第2页@100、第3页@200
_MAPPING = [
    {"start_pos": 0, "end_pos": 10, "page_num": 1},
    {"start_pos": 50, "end_pos": 60, "page_num": 1},
    {"start_pos": 100, "end_pos": 110, "page_num": 2},
    {"start_pos": 200, "end_pos": 210, "page_num": 3},
]


def test_single_page_returns_one_segment():
    segs = split_span_by_pages(_MAPPING, 10, 80)
    assert segs == [{"page_num": 1, "start_pos": 10, "end_pos": 80}]


def test_span_across_two_pages():
    segs = split_span_by_pages(_MAPPING, 60, 150)
    assert segs == [
        {"page_num": 1, "start_pos": 60, "end_pos": 100},
        {"page_num": 2, "start_pos": 100, "end_pos": 150},
    ]


def test_span_across_three_pages():
    segs = split_span_by_pages(_MAPPING, 0, 250)
    assert [s["page_num"] for s in segs] == [1, 2, 3]
    assert segs[0]["start_pos"] == 0
    assert segs[-1]["end_pos"] == 250


def test_segments_cover_span_seamlessly():
    """各段首尾相接、无缝覆盖原区间——切分不得丢字符或重叠。"""
    segs = split_span_by_pages(_MAPPING, 30, 240)
    assert segs[0]["start_pos"] == 30
    assert segs[-1]["end_pos"] == 240
    for prev, cur in zip(segs, segs[1:]):
        assert prev["end_pos"] == cur["start_pos"]


def test_empty_mapping_returns_empty():
    assert split_span_by_pages([], 0, 100) == []


def test_empty_span_returns_empty():
    assert split_span_by_pages(_MAPPING, 100, 100) == []
    assert split_span_by_pages(_MAPPING, 150, 100) == []


def test_span_before_first_anchor_uses_first_page():
    """区间早于首个锚点时沿用首锚页码，与 lookup_page_num 的 idx<0 兜底一致。"""
    mapping = [{"start_pos": 100, "end_pos": 110, "page_num": 5}]
    assert split_span_by_pages(mapping, 0, 50) == [
        {"page_num": 5, "start_pos": 0, "end_pos": 50}
    ]


def test_str_page_num_is_normalized_to_int():
    """历史 fixture 的 page_num 是字符串，页码必须归一成 int。"""
    mapping = [
        {"start_pos": 0, "page_num": "1"},
        {"start_pos": 100, "page_num": "2"},
    ]
    segs = split_span_by_pages(mapping, 0, 150)
    assert [s["page_num"] for s in segs] == [1, 2]


def test_fake_anchor_with_impossible_jump_is_dropped():
    """378 字不可能跨 291 页——该切点必为假锚，整段沿用可信页码。"""
    mapping = [
        {"start_pos": 0, "page_num": 146},
        {"start_pos": 200, "page_num": 437},
    ]
    segs = split_span_by_pages(mapping, 0, 378)
    assert segs == [{"page_num": 146, "start_pos": 0, "end_pos": 378}]


def test_consecutive_page_turn_always_trusted():
    """连续翻页（跳变 1 页）永远信任，即使该页只剩 20 字。"""
    mapping = [
        {"start_pos": 0, "page_num": 1},
        {"start_pos": 20, "page_num": 2},
    ]
    segs = split_span_by_pages(mapping, 0, 100)
    assert [s["page_num"] for s in segs] == [1, 2]


def test_real_page_skip_with_enough_chars_is_trusted():
    """跳 2 页但中间有充足字符（如整页图片）→ 采信该切点。"""
    mapping = [
        {"start_pos": 0, "page_num": 1},
        {"start_pos": 300, "page_num": 3},
    ]
    segs = split_span_by_pages(mapping, 0, 500)
    assert [s["page_num"] for s in segs] == [1, 3]


def test_fake_anchor_rejection_keeps_later_anchors_consistent():
    """假锚被拒后基准不推进，后续锚点被同一判据持续拒绝，整段页码保持一致。"""
    mapping = [
        {"start_pos": 0, "page_num": 146},
        {"start_pos": 100, "page_num": 437},
        {"start_pos": 200, "page_num": 438},
    ]
    segs = split_span_by_pages(mapping, 0, 378)
    assert segs == [{"page_num": 146, "start_pos": 0, "end_pos": 378}]
