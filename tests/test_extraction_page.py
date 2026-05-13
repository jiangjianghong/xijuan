"""page 检索方式测试。"""

from __future__ import annotations

import pytest

from service.extraction_service import _parse_page_range


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5", (5, 5)),
        ("5-7", (5, 7)),
        ("  3 - 9 ", (3, 9)),
        ("1-1", (1, 1)),
        ("100", (100, 100)),
    ],
)
def test_parse_page_range_valid(raw, expected):
    assert _parse_page_range(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "0",
        "0-3",
        "5-3",
        "a",
        "5-a",
        "5-7-9",
        "-",
        "5-",
        "-5",
        None,
        5,
    ],
)
def test_parse_page_range_invalid(raw):
    assert _parse_page_range(raw) is None
