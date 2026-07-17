"""回调通知工具：在异步管线各阶段完成时向指定 URL 发送状态通知。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from loguru import logger


async def _post_callback_payload(
    callback_url: Optional[str],
    payload: Dict[str, Any],
    *,
    timeout: float,
) -> None:
    """发送回调；任何网络或接收端错误都只记录日志。"""

    if not callback_url:
        return

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(callback_url, json=payload)
            logger.debug(
                "回调通知已发送: url={}, status={}, event={}, status_code={}",
                callback_url,
                payload.get("status"),
                payload.get("event", "-"),
                resp.status_code,
            )
    except Exception as e:
        logger.warning(
            "回调通知失败: url={}, status={}, event={}, type={}, error={}",
            callback_url,
            payload.get("status"),
            payload.get("event", "-"),
            type(e).__name__,
            e,
        )


async def notify_callback(
    callback_url: Optional[str],
    file_id: str,
    status: str,
    *,
    event: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 2.5,
) -> None:
    """向回调地址 POST 阶段状态。

    Payload 形式：
        {"file_id": ..., "status": ...}                            # 阶段入口（event/data 均不传）
        {"file_id": ..., "status": ..., "event": "field_done", "data": {...}}   # 单字段完成
        {"file_id": ..., "status": ..., "event": "rule_done",  "data": {...}}   # 单规则完成
        {"file_id": ..., "status": ..., "event": "stage_done", "data": {...}}   # 阶段完整数据

    Args:
        callback_url: 回调地址，为 None 时静默跳过。
        file_id: 文件 ID。
        status: 当前阶段状态（parsing / tableing / chunking / embedding / extracting / analyzing / complete）。
        event: 可选事件类型（field_done / rule_done / stage_done）。
        data: 可选事件数据，仅在 event 非空时携带。
        timeout: HTTP 请求超时（秒）。默认 2.5s，避免接收端慢拖累主流程。
    """
    payload: Dict[str, Any] = {"file_id": file_id, "status": status}
    if event:
        payload["event"] = event
        if data is not None:
            payload["data"] = data

    await _post_callback_payload(callback_url, payload, timeout=timeout)


def build_analysis_task_payload(
    task_id: str,
    status: str,
    *,
    event: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造独立分析任务的 callback/SSE 公共 envelope。"""

    payload: Dict[str, Any] = {"task_id": task_id, "status": status}
    if event:
        payload["event"] = event
        if data is not None:
            payload["data"] = data
    return payload


async def notify_analysis_task_callback(
    callback_url: Optional[str],
    task_id: str,
    status: str,
    *,
    event: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 2.5,
) -> None:
    """发送独立逻辑分析任务事件，失败不影响任务执行。"""

    await _post_callback_payload(
        callback_url,
        build_analysis_task_payload(
            task_id,
            status,
            event=event,
            data=data,
        ),
        timeout=timeout,
    )
