"""VL 视觉模型客户端：HTTP 调用、全局并发治理、PDF 渲染工具。"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import fitz
from PIL import Image

from utils.config import get_config


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_pdf_storage_dir() -> Path:
    """按 vl_model.pdf_storage_dir 配置返回绝对路径目录。"""
    cfg = get_config().vl_model
    p = Path(cfg.pdf_storage_dir)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def pdf_path(file_id: str) -> Path:
    """返回 {pdf_storage_dir}/{file_id}.pdf 的绝对路径（不保证文件存在）。"""
    return _get_pdf_storage_dir() / f"{file_id}.pdf"


def parse_page_range(page_range: str, total_pages: int) -> list[int]:
    """解析 "1-2,5" / "all" 这类字符串为 0-indexed 页码列表。"""
    if not page_range:
        return []
    if page_range == "all":
        return list(range(total_pages))
    pages: list[int] = []
    for part in page_range.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            pages.extend(range(int(start_s) - 1, int(end_s)))
        else:
            pages.append(int(part) - 1)
    return [p for p in pages if 0 <= p < total_pages]


def _compute_safe_scale(
    base_w: float, base_h: float, target_scale: float, max_pixels: int | None
) -> float:
    """如果 target_scale 渲染会超 max_pixels，按比例降到刚好不超。"""
    if max_pixels is None:
        return target_scale
    target_pixels = (base_w * target_scale) * (base_h * target_scale)
    if target_pixels <= max_pixels:
        return target_scale
    return (max_pixels / (base_w * base_h)) ** 0.5


def render_pages_to_b64(
    file_bytes: bytes,
    pages: list[int],
    *,
    scale: float = 2.0,
    max_pixels: int | None = None,
    jpeg_quality: int | None = None,
) -> list[str]:
    """渲染指定页为 base64 字符串列表。

    - jpeg_quality=None：输出 PNG（推荐用于含文字 PDF）
    - jpeg_quality 给数值：输出 JPEG（仅彩色扫描件用，省体积）
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        results: list[str] = []
        for page_idx in pages:
            page = doc.load_page(page_idx)
            rect = page.rect
            actual_scale = _compute_safe_scale(rect.width, rect.height, scale, max_pixels)
            pix = page.get_pixmap(matrix=fitz.Matrix(actual_scale, actual_scale))

            if jpeg_quality is None:
                img_bytes = pix.tobytes("png")
            else:
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                img_bytes = buf.getvalue()

            results.append(base64.b64encode(img_bytes).decode("ascii"))
        return results
    finally:
        doc.close()


def render_thumbnail(doc: fitz.Document, page_idx: int, *, scale: float = 0.75) -> Image.Image:
    """渲染单页为低清 PIL Image（vl_locate 第一轮用）。"""
    page = doc.load_page(page_idx)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_hires(
    doc: fitz.Document,
    page_idx: int,
    *,
    scale: float = 2.0,
    max_pixels: int | None = None,
) -> str:
    """渲染单页为高清 base64 PNG（vl_locate 第二轮 / 通用高清场景）。"""
    page = doc.load_page(page_idx)
    rect = page.rect
    actual_scale = _compute_safe_scale(rect.width, rect.height, scale, max_pixels)
    pix = page.get_pixmap(matrix=fitz.Matrix(actual_scale, actual_scale))
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def make_grid_image(
    images: list[Image.Image], *, cols: int = 3, padding: int = 30
) -> str:
    """把多张缩略图拼成 cols 列的网格 PNG，行间留 padding 白边，返回 base64。"""
    if not images:
        return ""
    rows_count = (len(images) + cols - 1) // cols
    w = max(img.width for img in images)
    h = max(img.height for img in images)
    grid = Image.new("RGB", (cols * w, rows_count * (h + padding)), "white")
    for i, img in enumerate(images):
        row, col = divmod(i, cols)
        grid.paste(img, (col * w, row * (h + padding) + padding))
    buf = io.BytesIO()
    grid.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")
