"""业务调用日志的持久化与结果快照。"""

from __future__ import annotations

import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import update
from loguru import logger

from model.database import get_session_factory
from model.tables import File, InvocationLog


def new_log_id() -> str:
    return uuid.uuid4().hex


async def create_log(**values: Any) -> str:
    log_id = values.pop("log_id", None) or new_log_id()
    try:
        async with get_session_factory()() as session:
            session.add(InvocationLog(log_id=log_id, **values))
            await session.commit()
    except Exception:
        # 日志表升级期间不阻断原有业务调用；服务初始化会自动建表。
        logger.exception("创建调用日志失败: log_id={}", log_id)
    return log_id


async def update_log(log_id: str, **values: Any) -> None:
    values["updated_at"] = datetime.now()
    async with get_session_factory()() as session:
        await session.execute(update(InvocationLog).where(InvocationLog.log_id == log_id).values(**values))
        await session.commit()


async def safe_update_log(log_id: str | None, **values: Any) -> None:
    """记录更新失败不改变已经执行的业务结果。"""
    if not log_id:
        return
    try:
        await update_log(log_id, **values)
    except Exception:
        logger.exception("更新调用日志失败: log_id={}", log_id)


@asynccontextmanager
async def track_analysis_call(log_id: str | None):
    await safe_update_log(log_id, status="running", started_at=datetime.now())
    try:
        yield
    except asyncio.CancelledError:
        await safe_update_log(log_id, status="failed", error="流式连接关闭，独立分析已中断", finished_at=datetime.now())
        raise
    except Exception as exc:
        await safe_update_log(log_id, status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=datetime.now())
        raise


async def complete_analysis_log(log_id: str | None, result: dict) -> None:
    """批次正常返回也可能含失败项/规则，列表标成部分失败便于筛查。"""
    failed = any(item.get("failed", 0) or item.get("error") for item in result.get("items", []))
    await safe_update_log(log_id, status="partial_failed" if failed else "complete",
                          result={"analysis": result}, finished_at=datetime.now())


@asynccontextmanager
async def track_file_call(log_id: str | None, file_id: str, session):
    """仅正式上传和重试使用；调试接口不进入此上下文。"""
    await safe_update_log(log_id, status="running", started_at=datetime.now())
    error = None
    try:
        yield
    except asyncio.CancelledError:
        error = "流式连接关闭，文件处理已中断"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if log_id:
            try:
                # 业务写入失败时先释放事务，否则查询快照也会失败。
                await session.rollback()
                await finalize_file_log(log_id, file_id, error)
            except Exception:
                logger.exception("保存文件调用日志快照失败: log_id={}", log_id)
                await safe_update_log(log_id, status="failed", error=error or "保存结果快照失败", finished_at=datetime.now())


async def finalize_file_log(log_id: str, file_id: str, error: str | None = None) -> None:
    """从文件及结果表生成稳定快照，避免详情依赖文件是否后来被删除。"""
    async with get_session_factory()() as session:
        file = await session.get(File, file_id)
        if file is None:
            await session.execute(update(InvocationLog).where(InvocationLog.log_id == log_id).values(
                status="failed", error=error or "文件记录不存在", finished_at=datetime.now(), updated_at=datetime.now()
            ))
            await session.commit()
            return
        # 复用公开结果接口的页码归一化与字段名称，保证下载 JSON 与查询一致。
        from blue_print.file_router import get_extraction_results, get_analysis_results
        extraction = await get_extraction_results(file_id, session)
        analysis = await get_analysis_results(file_id, session)
        stage_names = ("parsing", "tableing", "chunking", "embedding", "extracting", "analyzing")
        stage_times = {}
        for stage in stage_names:
            start = getattr(file, f"start_{stage}_time", None)
            end = getattr(file, f"end_{stage}_time", None)
            stage_times[stage] = {
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "duration_ms": int((end - start).total_seconds() * 1000) if start and end else None,
            }
        result = {"extraction": extraction.data, "analysis": analysis.data}
        status = "complete" if file.progress == "complete" and not error else "failed"
        await session.execute(update(InvocationLog).where(InvocationLog.log_id == log_id).values(
            status=status, type_id=file.type_id, file_name=file.file_name, file_size=file.file_size,
            stage_times=stage_times, result=result, error=error or file.error,
            finished_at=datetime.now(), updated_at=datetime.now(),
        ))
        await session.commit()


async def recover_interrupted_logs(session) -> None:
    """单 worker 重启后，在途调用不能继续运行。"""
    await session.execute(update(InvocationLog).where(
        InvocationLog.status.in_(["queued", "running"])
    ).values(status="failed", error="服务重启，调用中断，请重新提交", finished_at=datetime.now()))
    await session.commit()
