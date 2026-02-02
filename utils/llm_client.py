"""LLM 客户端：封装 OpenAI 兼容 API 的异步调用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

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
    api_key = api_key or "EMPTY"
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
