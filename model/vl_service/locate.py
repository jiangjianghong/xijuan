"""vl_locate：缩略图并行定位 + 关键页高清提取。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import fitz
from loguru import logger

from service.vl_service._common import (
    build_image_messages,
    parse_vl_json_response,
    strip_think_tags,
)
from service.vl_service._defaults import DEFAULT_LOCATE_PROMPT
from utils.vl_client import (
    make_grid_image,
    render_hires,
    render_thumbnail,
    vl_chat,
)


async def vl_locate_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    field_hints: str,
    grid_pages: int = 6,
    grid_cols: int = 3,
    max_concurrent: int = 20,
    thumb_scale: float = 0.75,
    key_pages_limit: int = 6,
    fallback_pages: int = 3,
    max_pixels: int = 4_000_000,
    locate_prompt_template: str | None = None,
    progress_cb: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """两轮 VL 抽取：缩略图网格并行定位 → 关键页高清提取。"""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)

    refs: dict[str, Any] = {
        "method": "vl_locate",
        "total_pages": total_pages,
        "key_pages": [],
        "vl_total_tokens": 0,
    }

    if total_pages == 0:
        doc.close()
        return "", "", refs

    template = locate_prompt_template or DEFAULT_LOCATE_PROMPT
    grid_rows = (grid_pages + grid_cols - 1) // grid_cols

    grids: list[tuple[str, list[int]]] = []
    for batch_start in range(0, total_pages, grid_pages):
        page_indices = list(range(batch_start, min(batch_start + grid_pages, total_pages)))
        thumbnails = [render_thumbnail(doc, idx, scale=thumb_scale) for idx in page_indices]
        b64 = make_grid_image(thumbnails, cols=grid_cols)
        grids.append((b64, page_indices))

    total_grids = len(grids)
    sem = asyncio.Semaphore(max_concurrent)

    async def scan_grid(grid_idx: int, b64: str, page_indices: list[int]) -> tuple[list[int], int]:
        page_labels = ", ".join(str(idx + 1) for idx in page_indices)
        position_lines = []
        for i, idx in enumerate(page_indices):
            row, col = divmod(i, grid_cols)
            position_lines.append(f"第{row+1}行第{col+1}列=第{idx+1}页")
        position_map = "；".join(position_lines)

        prompt = template.format(
            field_hints=field_hints,
            page_labels=page_labels,
            position_map=position_map,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
        )

        messages = build_image_messages(
            prompt=prompt, b64_images=[b64], system_prompt=vl_system_prompt
        )

        try:
            async with sem:
                resp = await vl_chat(messages, max_tokens=512)
        except Exception as e:
            logger.warning("vl_locate 网格 {} 调用失败: {}", grid_idx + 1, e)
            return [], 0

        raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        raw = strip_think_tags(raw)
        usage = resp.get("usage") or {}
        tokens = usage.get("total_tokens", 0)

        valid_set = {idx + 1 for idx in page_indices}
        found: list[int] = []
        try:
            s, e_idx = raw.find("{"), raw.rfind("}")
            if s != -1 and e_idx != -1:
                obj = json.loads(raw[s : e_idx + 1])
                raw_pages = [int(p) for p in obj.get("found_pages", [])]
                found = [p for p in raw_pages if p in valid_set]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        if progress_cb:
            await progress_cb({
                "phase": "locate",
                "grid_idx": grid_idx + 1,
                "total_grids": total_grids,
                "page_labels": page_labels,
                "found_pages": found,
            })

        return [p - 1 for p in found], tokens

    tasks = [scan_grid(i, b64, pages) for i, (b64, pages) in enumerate(grids)]
    results = await asyncio.gather(*tasks)
    for _, tokens in results:
        refs["vl_total_tokens"] += tokens

    key_pages_0idx = sorted(
        {p for found_0idx, _ in results for p in found_0idx if 0 <= p < total_pages}
    )
    if len(key_pages_0idx) > key_pages_limit:
        key_pages_0idx = key_pages_0idx[:key_pages_limit]

    if not key_pages_0idx:
        key_pages_0idx = list(range(min(fallback_pages, total_pages)))

    refs["key_pages"] = [p + 1 for p in key_pages_0idx]

    if progress_cb:
        await progress_cb({
            "phase": "extract",
            "key_pages": refs["key_pages"],
        })

    # 第二轮：关键页高清提取
    b64_hires = [render_hires(doc, idx, scale=2.0, max_pixels=max_pixels) for idx in key_pages_0idx]
    doc.close()

    extract_messages = build_image_messages(
        prompt=vl_extract_prompt, b64_images=b64_hires, system_prompt=vl_system_prompt
    )
    resp = await vl_chat(extract_messages)
    raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    raw = strip_think_tags(raw)
    usage = resp.get("usage") or {}
    refs["vl_total_tokens"] += usage.get("total_tokens", 0)

    value, reason = parse_vl_json_response(raw)
    return value, reason, refs
