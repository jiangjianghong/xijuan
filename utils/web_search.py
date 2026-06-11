"""网络搜索客户端：封装博查 Bocha AI Web Search API。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from utils.config import get_config


def format_search_results(results: List[Dict[str, Any]], max_length: int) -> str:
    """将结构化搜索结果格式化为注入 prompt 的文本。

    Args:
        results: [{name, url, siteName, datePublished, summary}] 列表。
        max_length: 最大字符数，超出末尾截断。

    Returns:
        按条编号的格式化文本；空列表返回提示文本。
    """
    if not results:
        return "（未搜索到相关结果）"

    parts: List[str] = []
    for i, r in enumerate(results, 1):
        lines = [f"[{i}] {r.get('name', '')}"]
        date = (r.get("datePublished") or "")[:10]
        meta = " | ".join(x for x in [r.get("siteName") or "", date] if x)
        if meta:
            lines.append(f"来源: {meta}")
        content = r.get("summary") or ""
        if content:
            lines.append(f"摘要: {content}")
        parts.append("\n".join(lines))

    text = "\n\n".join(parts)
    if len(text) > max_length:
        text = text[:max_length]
    return text


async def bocha_web_search(
    query: str,
    *,
    count: Optional[int] = None,
    freshness: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """调用博查 Web Search API。

    Args:
        query: 搜索词。
        count: 返回条数，缺省走配置。
        freshness: 时间范围（noLimit/oneDay/oneWeek/oneMonth/oneYear），缺省走配置。

    Returns:
        (格式化文本, 结构化结果列表) 元组。结构化列表每条含
        name/url/siteName/datePublished/summary 五键（summary 兜底 snippet）。

    Raises:
        ValueError: 搜索词为空。
        httpx.HTTPError: 重试耗尽后仍失败。
    """
    if not query or not query.strip():
        raise ValueError("搜索词为空")

    cfg = get_config().web_search
    payload: Dict[str, Any] = {
        "query": query.strip(),
        "count": count or cfg.count,
        "summary": cfg.summary,
        "freshness": freshness or cfg.freshness,
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    last_exc: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=cfg.timeout) as client:
        for attempt in range(cfg.retry_count):
            try:
                resp = await client.post(cfg.base_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                pages = (data.get("data") or data).get("webPages", {}).get("value", []) or []
                results = [
                    {
                        "name": p.get("name", ""),
                        "url": p.get("url", ""),
                        "siteName": p.get("siteName", ""),
                        "datePublished": p.get("datePublished", ""),
                        "summary": p.get("summary") or p.get("snippet", ""),
                    }
                    for p in pages
                ]
                return format_search_results(results, cfg.max_result_length), results
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else None
                # 4xx（除 429）是参数/鉴权错误，重试无意义
                if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                    raise
                last_exc = e
            except httpx.HTTPError as e:
                last_exc = e
            if attempt < cfg.retry_count - 1:
                logger.warning("博查搜索重试 {}/{}: {}", attempt + 1, cfg.retry_count, last_exc)
                await asyncio.sleep(2 ** attempt)

    assert last_exc is not None
    raise last_exc
