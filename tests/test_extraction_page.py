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


from service.extraction_service import slice_by_page_range


def _make_mapping():
    """构造一个 5 页文档的 page_mapping。

    md 内容假设为：
      "PAGE1_AAA" + "PAGE2_BBB" + ... + "PAGE5_EEE"
    每页一个 block，每个 block 长度 9，block 之间无间隔。
    """
    return [
        {"start_pos": 0, "end_pos": 9, "page_num": 1},
        {"start_pos": 9, "end_pos": 18, "page_num": 2},
        {"start_pos": 18, "end_pos": 27, "page_num": 3},
        {"start_pos": 27, "end_pos": 36, "page_num": 4},
        {"start_pos": 36, "end_pos": 45, "page_num": 5},
    ]


_MD_5P = "PAGE1_AAA" + "PAGE2_BBB" + "PAGE3_CCC" + "PAGE4_DDD" + "PAGE5_EEE"


def test_slice_by_page_range_middle():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 2, 4, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE2_BBB" + "PAGE3_CCC" + "PAGE4_DDD"
    assert r["start_pos"] == 9
    assert r["end_pos"] == 36
    assert r["length"] == 27
    assert r["truncated"] is False


def test_slice_by_page_range_single():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 3, 3, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE3_CCC"


def test_slice_by_page_range_to_end():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 4, 5, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE4_DDD" + "PAGE5_EEE"
    assert r["end_pos"] == len(_MD_5P)


def test_slice_by_page_range_overflow_end():
    """end_page 越界 → 切到末尾。"""
    r = slice_by_page_range(_MD_5P, _make_mapping(), 5, 99, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE5_EEE"


def test_slice_by_page_range_start_overflow():
    """start_page 越过末页 → ok=False。"""
    r = slice_by_page_range(_MD_5P, _make_mapping(), 10, 20, 30000)
    assert r["ok"] is False
    assert "10-20" in r["reason"]


def test_slice_by_page_range_empty_mapping():
    r = slice_by_page_range(_MD_5P, [], 1, 3, 30000)
    assert r["ok"] is False
    assert "page_mapping" in r["reason"]


def test_slice_by_page_range_truncate():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 1, 5, 10)
    assert r["ok"] is True
    assert r["text"] == "PAGE1_AAAP"
    assert r["length"] == 10
    assert r["truncated"] is True
    assert r["start_pos"] == 0
    assert r["end_pos"] == 10


def test_slice_by_page_range_empty_md():
    r = slice_by_page_range("", _make_mapping(), 1, 1, 30000)
    assert r["ok"] is False

