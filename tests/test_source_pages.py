"""source_pages 页码归一纯函数测试。"""

from __future__ import annotations

from service.extraction_service import (
    parse_page_num_str,
    collect_ref_pages,
    derive_source_pages,
)


# ── parse_page_num_str ──────────────────────────────────────

def test_parse_int_page_num():
    """page 检索逐页 ref 的 page_num 是 int，直接取。"""
    assert parse_page_num_str(12) == [12]


def test_parse_single_str():
    assert parse_page_num_str("12") == [12]


def test_parse_range_expands():
    """区间展开成逐页，这是本次改动的核心诉求。"""
    assert parse_page_num_str("12-15") == [12, 13, 14, 15]


def test_parse_comma_list():
    assert parse_page_num_str("1,3,5") == [1, 3, 5]
    assert parse_page_num_str("1，3") == [1, 3]


def test_parse_huge_range_takes_first_five():
    """跨度超 5 取前五页，不是退回起始页。"""
    assert parse_page_num_str("1-200") == [1, 2, 3, 4, 5]
    assert parse_page_num_str("10-99") == [10, 11, 12, 13, 14]


def test_parse_boundary_span_exactly_five():
    assert parse_page_num_str("3-7") == [3, 4, 5, 6, 7]


def test_parse_unparseable_returns_empty():
    assert parse_page_num_str("all") == []
    assert parse_page_num_str("") == []
    assert parse_page_num_str(None) == []
    assert parse_page_num_str(0) == []
    assert parse_page_num_str(-3) == []
    assert parse_page_num_str(True) == []
    assert parse_page_num_str("9-2") == []


# ── collect_ref_pages ───────────────────────────────────────

def test_collect_prefers_bboxes():
    """有 bboxes 时以 bboxes 的 int 页码为准（最精确）。"""
    refs = {
        "金额": [
            {"page_num": "12-15", "bboxes": [
                {"page_num": 12, "bbox": [0, 0, 1, 1], "page_size": [595, 842]},
                {"page_num": 13, "bbox": [0, 0, 1, 1], "page_size": [595, 842]},
            ]}
        ]
    }
    assert collect_ref_pages(refs) == [12, 13]


def test_collect_uses_page_nums_when_no_bboxes():
    refs = {"金额": [{"page_num": "1-2", "page_nums": [1, 2]}]}
    assert collect_ref_pages(refs) == [1, 2]


def test_collect_falls_back_to_page_num_string():
    refs = {"金额": [{"page_num": "3-5"}]}
    assert collect_ref_pages(refs) == [3, 4, 5]


def test_collect_vl_key_pages():
    refs = {"_vl": {"method": "vl_locate", "total_pages": 48, "key_pages": [12, 13, 15]}}
    assert collect_ref_pages(refs) == [12, 13, 15]


def test_collect_vl_progressive_null_key_pages():
    """vl_progressive 的 key_pages 是 null，无法定位具体页 → 空。"""
    refs = {"_vl": {"method": "vl_progressive", "total_pages": 48, "key_pages": None}}
    assert collect_ref_pages(refs) == []


def test_collect_skips_metadata_keys():
    """_texts / _resolved_refs / 存量 _model_pages 等元数据键不参与命中页计算。"""
    refs = {
        "金额": [{"page_num": "3"}],
        "_texts": {"金额": "..."},
        "_model_pages": [99],
        "_resolved_refs": {"f1": "v"},
        "_page_link": {"source_field": "f1"},
    }
    assert collect_ref_pages(refs) == [3]


def test_collect_dedups_and_sorts():
    refs = {"a": [{"page_num": "5"}, {"page_num": "3"}], "b": [{"page_num": "5"}]}
    assert collect_ref_pages(refs) == [3, 5]


def test_collect_tolerates_garbage():
    assert collect_ref_pages(None) == []
    assert collect_ref_pages({}) == []
    assert collect_ref_pages({"a": "not-a-list"}) == []
    assert collect_ref_pages({"a": [None, "not-a-dict", {}]}) == []


# ── derive_source_pages ─────────────────────────────────────

def test_derive_prefers_model_pages():
    refs = {"金额": [{"page_num": "7"}]}
    assert derive_source_pages([1, 3], refs) == [1, 3]


def test_derive_falls_back_to_ref_pages():
    """模型没自报 → 用程序命中页兜底，这是本次改动的主目的。"""
    refs = {"金额": [{"page_num": "7"}]}
    assert derive_source_pages([], refs) == [7]
    assert derive_source_pages(None, refs) == [7]


def test_derive_both_empty():
    """失败字段 / 无命中 → 空数组，但键仍存在（调用方保证）。"""
    assert derive_source_pages(None, None) == []
    assert derive_source_pages([], {}) == []


def test_derive_normalizes_model_pages():
    assert derive_source_pages([3, 1, 3], None) == [1, 3]
