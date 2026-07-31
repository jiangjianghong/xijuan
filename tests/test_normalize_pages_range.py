"""_normalize_pages 对范围串的展开测试。"""

from __future__ import annotations

from service.extraction_service import _normalize_pages


def test_range_string_expands():
    assert _normalize_pages(["2-4"]) == [2, 3, 4]


def test_range_with_cjk_page_words_expands():
    assert _normalize_pages("第2页到第4页") == [2, 3, 4]


def test_range_with_tilde_expands():
    assert _normalize_pages(["7~9"]) == [7, 8, 9]


def test_absurd_range_falls_back_to_first_number():
    """跨度超上限（异常输入）不展开，退回只取起始页，避免灌入上千个页码。"""
    assert _normalize_pages(["1-9999"]) == [1]


def test_reversed_range_falls_back_to_first_number():
    assert _normalize_pages(["9-2"]) == [9]


def test_existing_behaviour_unchanged():
    """既有归一化行为不得回归。"""
    assert _normalize_pages([3, 1, 3]) == [1, 3]
    assert _normalize_pages("第3页") == [3]
    assert _normalize_pages("1,2,3") == [1, 2, 3]
    assert _normalize_pages(None) == []
    assert _normalize_pages(["abc"]) == []
