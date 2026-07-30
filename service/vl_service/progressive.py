"""vl_progressive：逐批扫描 + 伪历史累积 + 最终文本聚合。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import fitz

from service.vl_service._common import build_image_messages, parse_vl_json_response
from service.vl_service._defaults import DEFAULT_BATCH_PROMPT
from utils.vl_client import render_pages_to_b64, resolve_target_pages, vl_chat


_NO_INFO_KEYWORD = "无相关信息"


def _format_page_label(pages_0idx: list[int]) -> str:
    """批次页码 → 中文标注。连续用区间，不连续用逗号列举。

    限页后批次可能不连续（例如只扫第 3、9 页），标注必须如实反映，
    否则累积摘要里的页码引用会指错页。
    """
    nums = [p + 1 for p in pages_0idx]
    if len(nums) == 1:
        return f"第{nums[0]}页"
    if nums[-1] - nums[0] + 1 == len(nums):
        return f"第{nums[0]}-{nums[-1]}页"
    return "第" + ",".join(str(n) for n in nums) + "页"


async def vl_progressive_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    field_hints: str,
    page_range: str = "all",
    max_pages: int | None = None,
    batch_size: int = 2,
    max_pixels: int = 4_000_000,
    batch_prompt_template: str | None = None,
    progress_cb: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """VL 逐批扫描 + 最后一次文本聚合。

    每批：渲染 batch_size 页，VL 自判相关性，输出摘要或"无相关信息"。
    最后：用纯文本（无图）调一次 VL，把累积摘要 + vl_extract_prompt 合并产出 {value, reason}。

    page_range / max_pages 限定扫描范围；不传则扫全文（行为与限页前一致）。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    target_pages, capped = resolve_target_pages(page_range, total_pages, max_pages)

    refs: dict[str, Any] = {
        "method": "vl_progressive",
        "total_pages": total_pages,
        "key_pages": None,
        "target_pages": [p + 1 for p in target_pages],
        "pages_capped": capped,
        "batches_with_info": 0,
        "vl_total_tokens": 0,
    }

    if total_pages == 0 or not target_pages:
        return "", "", refs

    template = batch_prompt_template or DEFAULT_BATCH_PROMPT
    accumulated: list[str] = []

    # 全文扫描时 scan_scope 为空串，提示词与限页前完全一致
    scan_scope = (
        ""
        if len(target_pages) == total_pages
        else "本次仅扫描第 {} 页，共 {} 页".format(
            ",".join(str(p + 1) for p in target_pages), len(target_pages)
        )
    )
    total_batches = (len(target_pages) + batch_size - 1) // batch_size

    for batch_index, batch_start in enumerate(range(0, len(target_pages), batch_size)):
        batch_pages = target_pages[batch_start : batch_start + batch_size]
        b64_images = render_pages_to_b64(
            file_bytes, batch_pages, scale=2.0, max_pixels=max_pixels
        )

        page_label = _format_page_label(batch_pages)

        history = (
            "【已扫描页面的累积信息】：\n" + "\n".join(accumulated) + "\n\n"
            if accumulated
            else ""
        )

        # scan_scope 是新增占位符：老的自定义模板不含它，str.format() 对
        # 模板未用到的 kwargs 是宽容的，因此不会报错
        prompt = template.format(
            history=history,
            field_hints=field_hints,
            page_label=page_label,
            total_pages=total_pages,
            scan_scope=scan_scope,
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
                "batch_index": batch_index,
                "total_batches": total_batches,
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
