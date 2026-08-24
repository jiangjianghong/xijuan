"""Pipeline 并发闸门。

concurrency.global_pipeline 的语义是「同时处理的文件数」，令牌在整条管线
（parsing → complete）全程持有。抢不到令牌的文件先落 queued 状态，
让前端能区分「排队中」与「卡住了」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy import update

from model.tables import File
from utils.concurrency import get_limiter, limiter_context
from utils.config import get_config


async def mark_file_queued(file_id: str) -> None:
    """把文件标记为排队中。用独立短会话并立即提交，让前端轮询可见。"""
    from model.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            update(File)
            .where(File.file_id == file_id)
            .values(progress="queued", error=None)
        )
        await session.commit()


@asynccontextmanager
async def pipeline_slot(
    file_id: str,
    limit: int | None = None,
    mark_queued: Callable[[str], Awaitable[None]] | None = None,
    file_name: str | None = None,
) -> AsyncIterator[None]:
    """占用一个 global_pipeline 令牌，全程持有到管线结束。

    有空位时直接进入，不写 queued，避免状态在 queued/parsing 之间闪烁。
    没空位时先标 queued 再阻塞等待。

    同时把 file_id / file_name 绑定为并发环境上下文，管线内各阶段
    （tableing / extracting / analyzing）的 limiter 事件自动携带，
    运行台侧窗因此能显示文件名而不是只有 32 位 ID。

    Args:
        file_id: 文件 ID。
        limit: 并发上限，缺省读 concurrency.global_pipeline。
        mark_queued: 标记排队状态的回调，缺省用 mark_file_queued（测试可替换）。
        file_name: 文件名，仅用于可观测性，缺省时侧窗回退显示 file_id。
    """
    if limit is None:
        limit = get_config().concurrency.global_pipeline
    if mark_queued is None:
        mark_queued = mark_file_queued

    limiter = get_limiter("global_pipeline", limit)
    context = {"file_id": file_id, "stage": "pipeline"}

    if limiter.available() <= 0:
        # 标记失败不能拖垮管线：排队状态只是可观测性，不是正确性依赖
        try:
            await mark_queued(file_id)
            logger.info("管线并发已满，文件进入排队: {}", file_id)
        except Exception as e:
            logger.warning("标记排队状态失败（不影响管线）: file_id={}, error={}", file_id, e)

    ambient = {"file_id": file_id}
    if file_name:
        ambient["file_name"] = file_name

    with limiter_context(**ambient):
        async with limiter.context(context):
            yield
