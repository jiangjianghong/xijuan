"""utils/vl_client.py 单元测试。"""

from __future__ import annotations

import pytest

from utils import vl_client


def test_pdf_path_relative_dir(tmp_path, monkeypatch):
    """pdf_path() 应该按配置的 pdf_storage_dir 拼接绝对路径。"""
    fake_dir = tmp_path / "uploads"
    monkeypatch.setattr(
        vl_client,
        "_get_pdf_storage_dir",
        lambda: fake_dir,
    )
    p = vl_client.pdf_path("abc123")
    assert p == fake_dir / "abc123.pdf"


def test_parse_page_range_all():
    assert vl_client.parse_page_range("all", 5) == [0, 1, 2, 3, 4]


def test_parse_page_range_single_and_range():
    assert vl_client.parse_page_range("1,3-4", 10) == [0, 2, 3]


def test_parse_page_range_clamp_out_of_bounds():
    """超界页直接丢弃。"""
    assert vl_client.parse_page_range("1-100", 3) == [0, 1, 2]


def test_parse_page_range_empty_string():
    assert vl_client.parse_page_range("", 5) == []
