"""进程内并发运行时只读监控接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from model.schemas import ResponseWrapper
from service.runtime_monitor_service import DEFAULT_WINDOW, build_snapshot, history_payload


router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("/concurrency", response_model=ResponseWrapper)
async def get_concurrency_snapshot(
    window: str = Query(
        DEFAULT_WINDOW,
        description="压力趋势时间窗口：60s / 5m / 30m，非法值回退 60s",
    ),
):
    """返回当前 worker 的并发池快照与压力历史。"""
    data = build_snapshot()
    data["history"] = history_payload(window)
    return ResponseWrapper(data=data)
