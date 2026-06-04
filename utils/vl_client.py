"""VL 视觉模型客户端：HTTP 调用、全局并发治理、PDF 渲染工具。"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any, Optional

import fitz
import httpx
from loguru import logger
from PIL import Image

from utils.config import get_config


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VLAPIError(RuntimeError):
    """VL API 调用最终失败（重试耗尽 / 4xx 非 429）。"""


_global_sem: asyncio.Semaphore | None = None


def _get_global_sem() -> asyncio.Semaphore:
    global _global_sem
    if _global_sem is None:
        cfg = get_config().vl_model
        _global_sem = asyncio.Semaphore(cfg.global_max_concurrency)
    return _global_sem


async def vl_chat(
    messages: list[dict[str, Any]],
    *,
    max_tokens: Optional[int] = None,
    extra_body: Optional[dict[str, Any]] = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """调用 VL 模型的 OpenAI 兼容 chat/completions 接口。

    返回原始 JSON dict，调用方自己解 choices/usage。

    重试：max_retries 次指数退避（1s/2s/4s）；4xx（除 429）直接抛 VLAPIError。
    全局并发限：vl_model.global_max_concurrency。
    """
    cfg = get_config().vl_model

    # 合并配置的 extra_body 与调用方传入的 extra_body（参数优先）
    body_extras: dict[str, Any] = dict(cfg.extra_body)
    if extra_body:
        body_extras.update(extra_body)

    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": max_tokens or cfg.max_tokens,
    }
    if body_extras:
        payload["extra_body"] = body_extras

    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"

    sem = _get_global_sem()
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with sem:
                async with httpx.AsyncClient(timeout=cfg.timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else None
            if status is not None and 400 <= status < 500 and status != 429:
                raise VLAPIError(f"VL API {status}: {e}") from e
            last_exc = e
            if attempt + 1 == max_retries:
                raise VLAPIError(f"VL API 重试 {max_retries} 次仍失败: {e}") from e
            wait = 2 ** attempt
            logger.warning(
                "vl_chat HTTP {} 第 {}/{} 次重试，{}s 后",
                status,
                attempt + 1,
                max_retries,
                wait,
            )
            await asyncio.sleep(wait)
        except httpx.RequestError as e:
            last_exc = e
            if attempt + 1 == max_retries:
                raise VLAPIError(f"VL API 网络错误重试 {max_retries} 次: {e}") from e
            wait = 2 ** attempt
            logger.warning(
                "vl_chat RequestError 第 {}/{} 次重试，{}s 后: {}",
                attempt + 1,
                max_retries,
                wait,
                e,
            )
            await asyncio.sleep(wait)

    raise VLAPIError(f"vl_chat 重试耗尽: {last_exc}")


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
