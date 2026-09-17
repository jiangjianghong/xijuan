"""独立分析任务状态与结果持久化，每次操作使用独立会话。"""

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_session_factory
from model.tables import AnalysisTask


async def create_task(task_id: str) -> None:
    """先提交排队记录，再向调用方返回任务编号。"""
    async with get_session_factory()() as session:
        session.add(AnalysisTask(task_id=task_id, status="queued"))
        await session.commit()


async def set_task_state(
    task_id: str, status: str, *, result: dict | None = None, error: str | None = None,
) -> None:
    """完整更新状态；结果提交完成之后才能发送终态回调。"""
    async with get_session_factory()() as session:
        changed = await session.execute(
            update(AnalysisTask).where(AnalysisTask.task_id == task_id).values(
                status=status, result=result, error=error,
            )
        )
        if not changed.rowcount:
            raise LookupError(f"独立分析任务不存在: {task_id}")
        await session.commit()


async def get_task(task_id: str) -> dict[str, Any] | None:
    """读取快照，避免把绑定数据库会话的 ORM 对象传出。"""
    async with get_session_factory()() as session:
        task = await session.get(AnalysisTask, task_id)
        if task is None:
            return None
        return {
            "task_id": task.task_id,
            "status": task.status,
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }


async def recover_interrupted_tasks(session: AsyncSession) -> None:
    """单 worker 启动时将无法继续执行的后台任务标记为失败。"""
    await session.execute(
        update(AnalysisTask)
        .where(AnalysisTask.status.in_(["queued", "analyzing"]))
        .values(status="analysis_failed", error="服务重启，独立分析任务中断，请重新提交")
    )
    await session.commit()
