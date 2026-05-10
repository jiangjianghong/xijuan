"""VL 视觉模型客户端：HTTP 调用、全局并发治理、PDF 渲染工具。"""

from __future__ import annotations

from pathlib import Path

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
