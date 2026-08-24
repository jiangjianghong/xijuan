"""LLM 客户端：封装 OpenAI 兼容 API 的异步调用。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from utils.concurrency import (
    get_limiter,
    register_task_limiter,
    unregister_task_limiter,
    work_item,
)
from utils.config import get_config


async def chat_completion(
    prompt: str,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[int] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    max_retries: Optional[int] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> str:
    """调用 OpenAI 兼容 chat/completions 接口。

    Args:
        prompt: 用户 prompt（当 messages 为 None 时使用）。
        base_url: API 地址，默认从配置读取。
        model: 模型名称，默认从配置读取。
        api_key: API Key，默认从配置读取。
        timeout: 超时秒数，默认从配置读取。
        messages: 自定义 messages 列表，优先于 prompt。
        max_retries: 最大重试次数，默认从配置读取。
        extra_body: 额外请求参数，与配置中的 extra_body 合并（参数优先）。

    Returns:
        LLM 返回的文本内容。
    """
    app_cfg = get_config()
    cfg = app_cfg.extraction
    base_url = base_url or cfg.base_url
    model = model or cfg.model
    api_key = api_key or cfg.api_key or "EMPTY"
    timeout = timeout or cfg.timeout
    retry_count = max_retries or cfg.retry_count or 1

    if messages is None:
        messages = [{"role": "user", "content": prompt}]

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 合并配置的 extra_body 与调用方传入的 extra_body（参数优先）
    merged_extra: Dict[str, Any] = dict(cfg.extra_body)
    if extra_body:
        merged_extra.update(extra_body)

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if merged_extra:
        payload["extra_body"] = merged_extra

    url = f"{base_url.rstrip('/')}/chat/completions"
    limiter = get_limiter("global_llm", app_cfg.concurrency.global_llm)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retry_count):
            try:
                async with limiter.context({"stage": "model_request", "model": model}):
                    resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else None
                # 4xx（除 429）通常是参数/权限/模型错误，直接抛出不重试
                if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                    raise

                if attempt + 1 == retry_count:
                    raise

                wait_time = 2 ** attempt
                logger.warning(
                    "chat_completion 请求失败(HTTP)，尝试 {}/{}，status={}，等待 {}s 后重试",
                    attempt + 1,
                    retry_count,
                    status_code,
                    wait_time,
                )
                await asyncio.sleep(wait_time)
            except httpx.RequestError as e:
                if attempt + 1 == retry_count:
                    raise

                wait_time = 2 ** attempt
                logger.warning(
                    "chat_completion 请求失败(Request)，尝试 {}/{}，type={}，repr={}，等待 {}s 后重试",
                    attempt + 1,
                    retry_count,
                    type(e).__name__,
                    repr(e),
                    wait_time,
                )
                await asyncio.sleep(wait_time)


async def get_embeddings(
    texts: List[str],
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: Optional[int] = None,
    timeout: Optional[int] = None,
    max_retries: int = 3,
    task_id: Optional[str] = None,
) -> List[List[float]]:
    """批量调用 OpenAI 兼容 /embeddings 接口获取向量。

    批次之间并发执行，结果**按批次索引回填**——顺序错乱会让向量与 chunk
    静默错位，不会抛异常。

    Args:
        texts: 待向量化的文本列表。
        base_url: API 地址，默认从 embedding 配置读取。
        model: 模型名称，默认从 embedding 配置读取。
        api_key: API Key，默认从 embedding 配置读取。
        batch_size: 每批处理数量，默认从配置读取。
        timeout: 超时秒数，默认从 embedding 配置读取。
        max_retries: 最大重试次数。
        task_id: 传入时按该 id 注册 task_embedding 单文件闸门（管线向量化传
            file_id）；不传则只受 global_embedding 约束，供 /search 与
            vector_db 检索这类单条查询使用。

    Returns:
        与 texts 等长、顺序一致的向量列表。
    """
    app_cfg = get_config()
    cfg = app_cfg.embedding
    base_url = base_url or cfg.base_url
    model = model or cfg.model
    api_key = api_key or cfg.api_key or "EMPTY"
    batch_size = batch_size or cfg.batch_size
    timeout = timeout or cfg.timeout

    url = f"{base_url.rstrip('/')}/embeddings"
    limiter = get_limiter("global_embedding", app_cfg.concurrency.global_embedding)
    task_limiter = None
    if task_id:
        task_limiter = register_task_limiter(
            "task_embedding",
            task_id,
            app_cfg.concurrency.task_embedding,
            {"file_id": task_id, "stage": "embedding"},
        )

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    MAX_INPUT_LENGTH = 8192

    batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
    slots: List[Optional[List[List[float]]]] = [None] * len(batches)
    context: Dict[str, Any] = {"stage": "embedding", "model": model}
    if task_id:
        context["file_id"] = task_id

    async with httpx.AsyncClient(timeout=timeout) as client:

        async def _run_batch(batch_index: int, batch: List[str]) -> None:
            payload: Dict[str, Any] = {
                "model": model,
                "input": [
                    text[:MAX_INPUT_LENGTH] if len(text) > MAX_INPUT_LENGTH else text
                    for text in batch
                ],
            }
            for attempt in range(max_retries):
                try:
                    with work_item():
                        async with AsyncExitStack() as stack:
                            if task_limiter is not None:
                                await stack.enter_async_context(task_limiter.context(context))
                            await stack.enter_async_context(limiter.context(context))
                            resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    slots[batch_index] = [item["embedding"] for item in data["data"]]
                    logger.debug("Embedding batch {}/{} 完成", batch_index + 1, len(batches))
                    return
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code if e.response else None
                    # 4xx（除 429）通常是参数/权限/模型错误，直接抛出不重试
                    if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                        raise

                    if attempt + 1 == max_retries:
                        raise

                    wait_time = 2 ** attempt
                    error_detail = e.response.text if e.response else str(e)
                    logger.warning(
                        "Embedding 请求失败 (尝试 {}/{}): status={}, 响应: {}, 等待 {}s 后重试",
                        attempt + 1, max_retries, status_code, error_detail, wait_time,
                    )
                    await asyncio.sleep(wait_time)
                except httpx.RequestError as e:
                    if attempt + 1 == max_retries:
                        raise

                    wait_time = 2 ** attempt
                    logger.warning(
                        "Embedding 请求失败 (尝试 {}/{}): {}, 等待 {}s 后重试",
                        attempt + 1, max_retries, str(e), wait_time,
                    )
                    await asyncio.sleep(wait_time)

        try:
            await asyncio.gather(*(
                _run_batch(index, batch) for index, batch in enumerate(batches)
            ))
        finally:
            if task_id:
                unregister_task_limiter("task_embedding", task_id)

    return [vector for slot in slots if slot is not None for vector in slot]
