"""独立文档智能体工作台的 HTTP 入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select

from model.database import get_session_factory
from model.tables import File as FileModel
from utils.config import get_config
from xijuan_agent.agent import stream_agent


router = APIRouter(prefix="/agent", tags=["智能体工作台"])
_PAGE = Path(__file__).resolve().parent / "ui" / "agent.html"


class AgentRunRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    # 兼容最初的单文件 Agent 客户端。
    file_id: str | None = Field(default=None, max_length=64)
    question: str = Field(min_length=1, max_length=12000)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=240)

    @model_validator(mode="after")
    def normalize_references(self) -> "AgentRunRequest":
        ids = [str(value).strip() for value in self.file_ids if str(value).strip()]
        if self.file_id and self.file_id.strip():
            ids.insert(0, self.file_id.strip())
        self.file_ids = list(dict.fromkeys(ids))
        if not self.file_ids:
            raise ValueError("file_ids 至少需要一个已处理完成的文件")
        if len(self.file_ids) > 20:
            raise ValueError("单条消息最多引用 20 个文件")
        self.question = self.question.strip()
        if not self.question:
            raise ValueError("question 不能为空")
        return self


@router.get("", include_in_schema=False)
async def agent_page():
    return FileResponse(_PAGE, media_type="text/html; charset=utf-8")


@router.get("/files")
async def list_agent_files(
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
):
    """给工作台选择器使用的已处理文件搜索列表。"""
    async with get_session_factory()() as session:
        filters = [FileModel.progress == "complete"]
        query = q.strip()
        if query:
            escaped_query = query.replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped_query}%"
            filters.append(or_(FileModel.file_name.ilike(pattern, escape="\\"), FileModel.file_id.ilike(pattern, escape="\\")))
        total = await session.scalar(select(func.count(FileModel.file_id)).where(*filters)) or 0
        rows = (
            await session.execute(
                select(FileModel.file_id, FileModel.file_name, FileModel.create_time, FileModel.type_id)
                .where(*filters)
                .order_by(FileModel.create_time.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    return {
        "items": [
            {
                "file_id": row.file_id,
                "file_name": row.file_name,
                "create_time": row.create_time.isoformat() if row.create_time else None,
                "type_id": row.type_id or "default",
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def run_agent_stream(payload: AgentRunRequest):
    question = payload.question
    file_ids = payload.file_ids

    config = get_config().agent
    if not config.base_url.strip() or not config.model.strip():
        raise HTTPException(status_code=503, detail="请先在系统设置的 Agent 分组配置模型服务地址和模型名称")

    async with get_session_factory()() as session:
        rows = (
            await session.execute(select(FileModel).where(FileModel.file_id.in_(file_ids)))
        ).scalars().all()
        by_id = {row.file_id: row for row in rows}
        missing = [file_id for file_id in file_ids if file_id not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"文件不存在：{', '.join(missing)}")
        incomplete = [row.file_name or row.file_id for row in rows if row.progress != "complete"]
        if incomplete:
            raise HTTPException(status_code=409, detail=f"文件尚未处理完成：{', '.join(incomplete)}")

    return StreamingResponse(
        stream_agent(file_ids, question, payload.history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )

