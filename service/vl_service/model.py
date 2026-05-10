"""vl_model：标准全量 VL 抽取（指定页一次性塞给 VL）。"""

from __future__ import annotations

from typing import Any

import fitz

from service.vl_service._common import build_image_messages, parse_vl_json_response
from utils.vl_client import parse_page_range, render_pages_to_b64, vl_chat


async def vl_model_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    page_range: str = "all",
    max_pixels: int = 4_000_000,
) -> tuple[str, str, dict[str, Any]]:
    """VL 全量抽取：渲染 page_range 页 → 一次调 VL → 直接产 {value, reason}。

    Args:
        file_bytes: PDF 二进制。
        vl_extract_prompt: 用户配置的最终提示词，必须要求 VL 输出 {value, reason}。
        vl_system_prompt: 可选系统提示。
        page_range: "all" / "1-3,5" 等。
        max_pixels: 单图像素上限。

    Returns:
        (value, reason, source_refs) — source_refs 是
        {method, total_pages, key_pages, vl_total_tokens}。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    pages_0idx = parse_page_range(page_range, total_pages)
    refs: dict[str, Any] = {
        "method": "vl_model",
        "total_pages": total_pages,
        "key_pages": [p + 1 for p in pages_0idx],
        "vl_total_tokens": 0,
    }

    if not pages_0idx:
        return "", "", refs

    b64_images = render_pages_to_b64(file_bytes, pages_0idx, scale=2.0, max_pixels=max_pixels)

    messages = build_image_messages(
        prompt=vl_extract_prompt,
        b64_images=b64_images,
        system_prompt=vl_system_prompt,
    )

    resp = await vl_chat(messages)
    raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    usage = resp.get("usage") or {}
    refs["vl_total_tokens"] = usage.get("total_tokens", 0)

    value, reason = parse_vl_json_response(raw)
    return value, reason, refs
