"""LLM 客户端：封装 OpenAI 兼容 API 的异步调用。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from utils.config import get_config


async def chat_completion(
    prompt: str,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[int] = None,
    messages: Optional[List[Dict[str, str]]] = None,
) -> str:
    """调用 OpenAI 兼容 chat/completions 接口。

    Args:
        prompt: 用户 prompt（当 messages 为 None 时使用）。
        base_url: API 地址，默认从配置读取。
        model: 模型名称，默认从配置读取。
        api_key: API Key，默认从配置读取。
        timeout: 超时秒数，默认从配置读取。
        messages: 自定义 messages 列表，优先于 prompt。

    Returns:
        LLM 返回的文本内容。
    """
    cfg = get_config().extraction
    base_url = base_url or cfg.llm_base_url
    model = model or cfg.llm_model
    api_key = api_key or cfg.llm_api_key or "EMPTY"
    timeout = timeout or cfg.llm_timeout

    if messages is None:
        messages = [{"role": "user", "content": prompt}]

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    url = f"{base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def get_embeddings(
    texts: List[str],
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: Optional[int] = None,
    timeout: Optional[int] = None,
    max_retries: int = 3,
) -> List[List[float]]:
    """批量调用 OpenAI 兼容 /embeddings 接口获取向量。

    Args:
        texts: 待向量化的文本列表。
        base_url: API 地址，默认从 embedding 配置读取。
        model: 模型名称，默认从 embedding 配置读取。
        api_key: API Key，默认从 embedding 配置读取。
        batch_size: 每批处理数量，默认从配置读取。
        timeout: 超时秒数，默认从 embedding 配置读取。
        max_retries: 最大重试次数。

    Returns:
        与 texts 等长的向量列表。
    """
    cfg = get_config().embedding
    base_url = base_url or cfg.base_url
    model = model or cfg.model_name
    api_key = api_key or cfg.api_key or "EMPTY"
    batch_size = batch_size or cfg.batch_size
    timeout = timeout or cfg.timeout

    url = f"{base_url.rstrip('/')}/embeddings"

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    all_embeddings: List[List[float]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            payload: Dict[str, Any] = {
                "model": model,
                "input": batch,
            }

            for attempt in range(max_retries):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    batch_embeddings = [item["embedding"] for item in data["data"]]
                    all_embeddings.extend(batch_embeddings)
                    logger.debug("Embedding batch {}/{} 完成", i // batch_size + 1, (len(texts) + batch_size - 1) // batch_size)
                    break
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    wait_time = 2 ** attempt
                    logger.warning("Embedding 请求失败 (尝试 {}/{}): {}, 等待 {}s 后重试", attempt + 1, max_retries, str(e), wait_time)
                    if attempt + 1 == max_retries:
                        raise
                    await asyncio.sleep(wait_time)

    return all_embeddings
