"""进程内并发运行时只读监控接口。"""

from __future__ import annotations

from fastapi import APIRouter

from model.schemas import ResponseWrapper
from service.runtime_monitor_service import build_snapshot


router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("/concurrency", response_model=ResponseWrapper)
async def get_concurrency_snapshot():
    """返回当前 worker 的并发池和局部实例快照。"""
    return ResponseWrapper(data=build_snapshot())
