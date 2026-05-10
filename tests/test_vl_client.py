"""utils/vl_client.py 单元测试。"""

from __future__ import annotations

import base64
import io

import fitz
import pytest
from PIL import Image

from utils import vl_client


def _make_test_pdf_bytes(num_pages: int = 2) -> bytes:
    """生成一份多页 PDF（每页写"Page N"），返回字节。"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), f"Page {i + 1}", fontsize=24)
    return doc.tobytes()


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


def test_render_pages_to_b64_returns_png_b64():
    pdf_bytes = _make_test_pdf_bytes(2)
    images = vl_client.render_pages_to_b64(pdf_bytes, [0, 1], scale=1.0)
    assert len(images) == 2
    for b64 in images:
        raw = base64.b64decode(b64)
        # PNG signature
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_pages_to_b64_max_pixels_limits_size():
    """max_pixels 上限会触发缩小渲染。"""
    pdf_bytes = _make_test_pdf_bytes(1)
    big = vl_client.render_pages_to_b64(pdf_bytes, [0], scale=4.0)
    big_raw = base64.b64decode(big[0])
    small = vl_client.render_pages_to_b64(
        pdf_bytes, [0], scale=4.0, max_pixels=10_000
    )
    small_raw = base64.b64decode(small[0])
    assert len(small_raw) < len(big_raw)


def test_render_hires_returns_b64():
    pdf_bytes = _make_test_pdf_bytes(1)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        b64 = vl_client.render_hires(doc, 0, scale=1.0)
        raw = base64.b64decode(b64)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        doc.close()


def test_make_grid_image_empty():
    assert vl_client.make_grid_image([]) == ""


def test_make_grid_image_single():
    img = Image.new("RGB", (50, 60), "red")
    b64 = vl_client.make_grid_image([img], cols=3)
    assert b64
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_make_grid_image_multi_pages_layout():
    """4 张图、3 列 → 应该是 2 行布局。"""
    imgs = [Image.new("RGB", (50, 60), "red") for _ in range(4)]
    b64 = vl_client.make_grid_image(imgs, cols=3, padding=10)
    raw = base64.b64decode(b64)
    grid = Image.open(io.BytesIO(raw))
    # 3 列宽度 = 3*50；2 行高度 = 2*(60+10)
    assert grid.size == (150, 140)
