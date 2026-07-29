"""文件阶段状态标记：把文件置为失败态，且这个动作自身绝不抛异常。

独立成模块而非放进 pipeline_service，是因为 parse_service 也要用，
而 pipeline_service 已经 import parse_service（反向 import 会成环）。
放 utils/errors.py 不合适（那是无依赖的纯文案工具），放 model/database.py
也不合适（那是纯引擎/会话管理）。
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import rollback_if_broken
from model.tables import File
from utils.errors import format_exception


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
