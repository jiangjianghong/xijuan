"""文件阶段状态标记：把文件置为失败态，且这个动作自身绝不抛异常。

独立成模块而非放进 pipeline_service，是因为 parse_service 也要用，
而 pipeline_service 已经 import parse_service（反向 import 会成环）。
放 utils/errors.py 不合适（那是无依赖的纯文案工具），放 model/database.py
也不合适（那是纯引擎/会话管理）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_session_factory, rollback_if_broken
from model.tables import File
from utils.errors import format_exception


_FAILURE_STATUS_RETRY_DELAYS = (1, 2, 4)


async def mark_file_failed(
    session: AsyncSession,
    file_id: str,
    progress: str,
    exc: BaseException,
    **extra: Any,
) -> None:
    """把文件标记为失败态。

    先修复会话：DB 写失败后 SQLAlchemy 会话处于 DEACTIVE，此时任何 execute
    都会抛 PendingRollbackError，反而掩盖 exc 这个真正的失败原因，并让文件
    progress 永久卡在 *ing 态（2026-07-28 线上事故即此）。

    自身异常一律吞掉并记日志 —— 标记失败失败了也不能盖住原始异常，
    调用方靠 raise 把 exc 继续往上冒。

    Args:
        session: 数据库会话。
        file_id: 文件 ID。
        progress: 目标失败态，如 "extracting_failed"。
        exc: 触发失败的原始异常，其文案写入 files.error。
        **extra: 需要一并写入 files 的其他列。
    """
    try:
        await rollback_if_broken(session)
        await session.execute(
            update(File)
            .where(File.file_id == file_id)
            .values(progress=progress, error=format_exception(exc), **extra)
        )
        await session.commit()
    except Exception as mark_exc:
        logger.error(
            "标记失败态失败（不影响原始异常上抛）: file_id={}, progress={}, error={}",
            file_id,
            progress,
            format_exception(mark_exc),
        )


async def mark_file_failed_with_retry(
    session: AsyncSession | None,
    file_id: str,
    progress: str,
    exc: BaseException,
    **extra: Any,
) -> bool:
    """使用独立会话写失败状态；首次失败后按 1/2/4 秒间隔最多重试三次。"""
    if session is not None:
        try:
            await session.rollback()
        except Exception as rollback_exc:
            logger.warning(
                "失败状态写入前回滚原会话失败: file_id={}, error={}",
                file_id,
                format_exception(rollback_exc),
            )

    failure_message = format_exception(exc)
    for attempt in range(len(_FAILURE_STATUS_RETRY_DELAYS) + 1):
        try:
            async with get_session_factory()() as retry_session:
                await retry_session.execute(
                    update(File)
                    .where(File.file_id == file_id)
                    .values(progress=progress, error=failure_message, **extra)
                )
                await retry_session.commit()
            if attempt:
                logger.info(
                    "失败状态重试写入成功: file_id={}, progress={}, attempt={}",
                    file_id,
                    progress,
                    attempt + 1,
                )
            return True
        except Exception as mark_exc:
            if attempt < len(_FAILURE_STATUS_RETRY_DELAYS):
                delay = _FAILURE_STATUS_RETRY_DELAYS[attempt]
                logger.warning(
                    "写入失败状态失败，{} 秒后重试: file_id={}, progress={}, attempt={}/{}, error={}",
                    delay,
                    file_id,
                    progress,
                    attempt + 1,
                    len(_FAILURE_STATUS_RETRY_DELAYS) + 1,
                    format_exception(mark_exc),
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "失败状态写入重试耗尽: file_id={}, progress={}, original_error={}, mark_error={}",
                    file_id,
                    progress,
                    failure_message,
                    format_exception(mark_exc),
                )
    return False
