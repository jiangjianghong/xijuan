"""调用日志：只记录正式业务调用，提供分页、详情和结果 JSON 下载。"""

from __future__ import annotations

import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import ResponseWrapper
from model.tables import File, InvocationLog

router = APIRouter(prefix="/invocations", tags=["invocations"])


def _summary(row) -> dict:
    now = row.finished_at or datetime.now()
    parameters = row.parameters or {}
    items = parameters.get("items", [])
    return {
        "log_id": row.log_id, "kind": row.kind, "status": row.status,
        "file_id": row.file_id, "task_id": row.task_id, "type_id": row.type_id,
        "file_name": row.file_name, "file_size": row.file_size,
        "mode": parameters.get("mode"), "stage": parameters.get("stage"),
        "source": parameters.get("source"), "item_count": len(items),
        "biz_ids": [item.get("biz_id") for item in items],
        "created_at": row.created_at, "started_at": row.started_at,
        "finished_at": row.finished_at, "updated_at": row.updated_at,
        "duration_ms": max(0, int((now - row.created_at).total_seconds() * 1000)),
        "error": row.error,
    }


@router.get("", response_model=ResponseWrapper)
async def list_invocations(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    kind: str = "",
    status: str = "",
    q: str = Query("", max_length=512), type_id: str = "",
    db: AsyncSession = Depends(get_db),
):
    # 列表不读取输入原文和结果大 JSON；展开详情时再读取。
    columns = [column for column in InvocationLog.__table__.columns
               if column.name not in {"request_input", "stage_times", "result"}]
    stmt = select(*columns)
    count = select(func.count(InvocationLog.log_id))
    conditions = []
    if kind:
        conditions.append(InvocationLog.kind == kind)
    if status:
        conditions.append(InvocationLog.status == status)
    if type_id:
        conditions.append(InvocationLog.type_id == type_id)
    if q.strip():
        # escape=True 将用户的 %/_ 当作普通文本，避免意外扩大搜索。
        conditions.append(or_(*(column.contains(q.strip(), autoescape=True) for column in (
            InvocationLog.file_name, InvocationLog.file_id, InvocationLog.task_id, InvocationLog.log_id
        ))))
    rows = (await db.execute(stmt.where(*conditions).order_by(
        InvocationLog.created_at.desc(), InvocationLog.log_id.desc()
    ).offset((page - 1) * page_size).limit(page_size))).all()
    total = int((await db.execute(count.where(*conditions))).scalar() or 0)
    return ResponseWrapper(data={"items": [_summary(row) for row in rows], "total": total,
                                 "page": page, "page_size": page_size})


@router.get("/{log_id}/result.json")
async def download_invocation_result(log_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(InvocationLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="调用日志不存在")
    if row.result is None:
        raise HTTPException(status_code=409, detail="当前调用尚无可下载的结果")
    # 只包含抽取/分析结果，原始输入、解析中间产物不混入结果文件。
    payload = {key: row.result[key] for key in ("extraction", "analysis") if key in row.result}
    return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="invocation-{row.log_id}.json"'})


@router.get("/{log_id}", response_model=ResponseWrapper)
async def get_invocation(log_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(InvocationLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="调用日志不存在")
    detail = _summary(row)
    detail.update(request_input=row.request_input, parameters=row.parameters,
                  stage_times=row.stage_times or {}, result=row.result)
    # 在途文件的阶段时间由文件表实时提供，结束后只读当次调用的快照。
    if row.file_id and row.status in {"queued", "running"}:
        file = await db.get(File, row.file_id)
        if file:
            detail["progress"] = file.progress
            detail["stage_times"] = {
                stage: {"start": getattr(file, f"start_{stage}_time"),
                        "end": getattr(file, f"end_{stage}_time")}
                for stage in ("parsing", "tableing", "chunking", "embedding", "extracting", "analyzing")
            }
    return ResponseWrapper(data=detail)
