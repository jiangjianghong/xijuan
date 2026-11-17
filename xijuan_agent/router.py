"""独立文档智能体工作台的 HTTP 入口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from model.database import get_session_factory
from model.tables import File as FileModel
from utils.config import get_config
from xijuan_agent.agent import stream_agent


router = APIRouter(prefix="/agent", tags=["智能体工作台"])
_PAGE = Path(__file__).resolve().parent / "ui" / "agent.html"


class AgentRunRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=12000)


@router.get("", include_in_schema=False)
async def agent_page():
    return FileResponse(_PAGE, media_type="text/html; charset=utf-8")


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def run_agent_stream(payload: AgentRunRequest):
    question = payload.question.strip()
    file_id = payload.file_id.strip()
    if not question or not file_id:
        raise HTTPException(status_code=422, detail="file_id 与 question 不能为空")

    config = get_config().agent
    if not config.base_url.strip() or not config.model.strip():
        raise HTTPException(status_code=503, detail="请先在系统设置的 Agent 分组配置模型服务地址和模型名称")

    async with get_session_factory()() as session:
        file_row = (
            await session.execute(select(FileModel).where(FileModel.file_id == file_id))
        ).scalar_one_or_none()
        if file_row is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        if file_row.progress != "complete":
            raise HTTPException(status_code=409, detail="文件尚未处理完成，当前不能启动 Agent")

    return StreamingResponse(
        stream_agent(file_id, question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )

