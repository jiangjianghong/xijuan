"""vl_progressive：逐批扫描 + 伪历史累积 + 最终文本聚合。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import fitz

from service.vl_service._common import build_image_messages, parse_vl_json_response
from service.vl_service._defaults import DEFAULT_BATCH_PROMPT
from utils.vl_client import render_pages_to_b64, vl_chat


_NO_INFO_KEYWORD = "无相关信息"


async def vl_progressive_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    field_hints: str,
    batch_size: int = 2,
    max_pixels: int = 4_000_000,
    batch_prompt_template: str | None = None,
    progress_cb: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """VL 逐批扫描 + 最后一次文本聚合。

    每批：渲染 batch_size 页，VL 自判相关性，输出摘要或"无相关信息"。
    最后：用纯文本（无图）调一次 VL，把累积摘要 + vl_extract_prompt 合并产出 {value, reason}。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    refs: dict[str, Any] = {
        "method": "vl_progressive",
        "total_pages": total_pages,
        "key_pages": None,
        "batches_with_info": 0,
        "vl_total_tokens": 0,
    }

    if total_pages == 0:
        return "", "", refs

    template = batch_prompt_template or DEFAULT_BATCH_PROMPT
    accumulated: list[str] = []

    for batch_start in range(0, total_pages, batch_size):
        batch_pages = list(range(batch_start, min(batch_start + batch_size, total_pages)))
        b64_images = render_pages_to_b64(
            file_bytes, batch_pages, scale=2.0, max_pixels=max_pixels
        )

        page_label = (
            f"第{batch_pages[0] + 1}页"
            if len(batch_pages) == 1
            else f"第{batch_pages[0] + 1}-{batch_pages[-1] + 1}页"
        )

        history = (
            "【已扫描页面的累积信息】：\n" + "\n".join(accumulated) + "\n\n"
            if accumulated
            else ""
        )

        prompt = template.format(
            history=history,
            field_hints=field_hints,
            page_label=page_label,
            total_pages=total_pages,
        )

        messages = build_image_messages(
            prompt=prompt, b64_images=b64_images, system_prompt=vl_system_prompt
        )

        resp = await vl_chat(messages)
        raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        usage = resp.get("usage") or {}
        refs["vl_total_tokens"] += usage.get("total_tokens", 0)

        has_info = _NO_INFO_KEYWORD not in raw[:20]
        if has_info and raw:
            accumulated.append(f"- {page_label}：{raw}")
            refs["batches_with_info"] += 1

        if progress_cb:
            await progress_cb({
                "page_label": page_label,
                "has_info": has_info,
                "summary_preview": raw[:100] if has_info else "",
                "batch_index": batch_start // batch_size,
                "total_batches": (total_pages + batch_size - 1) // batch_size,
            })

    # 最终聚合（文本无图）
    if not accumulated:
        return "", "文档全程无相关信息", refs

    accumulated_text = "\n".join(accumulated)
    final_prompt = (
        f"以下是逐页扫描得到的累积信息：\n{accumulated_text}\n\n{vl_extract_prompt}"
    )
    final_messages = build_image_messages(
        prompt=final_prompt, b64_images=[], system_prompt=vl_system_prompt
    )
    resp = await vl_chat(final_messages)
    raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    usage = resp.get("usage") or {}
    refs["vl_total_tokens"] += usage.get("total_tokens", 0)

    value, reason = parse_vl_json_response(raw)
    return value, reason, refs
