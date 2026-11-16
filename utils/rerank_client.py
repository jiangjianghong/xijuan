"""Cohere-compatible rerank API client."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from utils.concurrency import get_limiter
from utils.config import get_config
from utils.model_config import model_override, resolve_model_config


class RerankNotConfiguredError(RuntimeError):
    """Rerank connection settings are incomplete."""


async def rerank_indices(query: str, documents: list[str], top_n: int) -> list[int]:
    """Return candidate indexes in Cohere rerank order."""
    if not documents or top_n < 1:
        return []

    app_cfg = resolve_model_config(get_config())
    cfg = app_cfg.rerank
    base_url = model_override("rerank", "base_url", cfg.base_url).strip()
    model = model_override("rerank", "model", cfg.model).strip()
    api_key = model_override("rerank", "api_key", cfg.api_key)
    timeout = model_override("rerank", "timeout", cfg.timeout)
    retry_count = model_override("rerank", "retry_count", cfg.retry_count)
    if not base_url or not model:
        raise RerankNotConfiguredError("Rerank 尚未配置服务地址或模型名称")

    url = f"{base_url.rstrip('/')}/rerank"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": min(top_n, len(documents)),
    }
    limiter = get_limiter("global_rerank", app_cfg.concurrency.global_rerank)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retry_count):
            try:
                async with limiter.context({
                    "stage": "rerank",
                    "model": model,
                    "candidate_count": len(documents),
                }):
                    response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                results = response.json().get("results")
                if not isinstance(results, list):
                    raise RuntimeError("Rerank 响应缺少 results 列表")
                indexes: list[int] = []
                seen: set[int] = set()
                for item in results:
                    index = item.get("index") if isinstance(item, dict) else None
                    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(documents) and index not in seen:
                        indexes.append(index)
                        seen.add(index)
                if not indexes:
                    raise RuntimeError("Rerank 响应没有有效候选序号")
                return indexes[:payload["top_n"]]
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response else None
                if status is not None and 400 <= status < 500 and status != 429:
                    detail = (exc.response.text or "")[:500] if exc.response else ""
                    raise RuntimeError(f"Rerank 请求失败: HTTP {status}\n{detail}") from exc
                if attempt + 1 == retry_count:
                    raise
                wait_time = 2 ** attempt
                logger.warning(
                    "Rerank 请求失败(HTTP)，尝试 {}/{}，status={}，等待 {}s 后重试",
                    attempt + 1, retry_count, status, wait_time,
                )
                await asyncio.sleep(wait_time)
            except httpx.RequestError as exc:
                if attempt + 1 == retry_count:
                    raise
                wait_time = 2 ** attempt
                logger.warning(
                    "Rerank 请求失败(Request)，尝试 {}/{}，type={}，等待 {}s 后重试",
                    attempt + 1, retry_count, type(exc).__name__, wait_time,
                )
                await asyncio.sleep(wait_time)

    return []
