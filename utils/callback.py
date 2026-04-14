"""回调通知工具：在异步管线各阶段完成时向指定 URL 发送状态通知。"""

from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger


async def notify_callback(
    callback_url: Optional[str],
    file_id: str,
    status: str,
    *,
    timeout: float = 10.0,
) -> None:
    """向回调地址 POST 阶段状态。

    Args:
        callback_url: 回调地址，为 None 时静默跳过。
        file_id: 文件 ID。
        status: 当前阶段状态（parsing / tableing / chunking / embedding / extracting / analyzing / complete）。
        timeout: HTTP 请求超时（秒）。
    """
    if not callback_url:
        return

    payload = {"file_id": file_id, "status": status}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(callback_url, json=payload)
            logger.debug("回调通知已发送: url={}, payload={}, status_code={}", callback_url, payload, resp.status_code)
    except Exception as e:
        # 回调失败不应影响主流程
        logger.warning(
            "回调通知失败: url={}, payload={}, type={}, repr={}, error={}",
            callback_url,
            payload,
            type(e).__name__,
            repr(e),
            e,
        )
