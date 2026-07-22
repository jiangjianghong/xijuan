"""逻辑分析配置路由：/analysis/*"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db, get_session_factory
from model.schemas import (
    AnalysisRunModeEnum,
    AnalysisRunRequest,
    AnalysisRunResponse,
    AnalysisRuleCreate,
    AnalysisRuleResponse,
    AnalysisTestRequest,
    AnalysisTestResponse,
    ResponseWrapper,
)
from model.tables import AnalysisRule, ExtractionResult
from service.analysis_service import apply_web_search, execute_calc, execute_custom, execute_judge, resolve_expression, test_rule_analysis_stream
from service.analysis_run_service import run_analysis_batch
from utils.callback import build_analysis_task_payload, notify_analysis_task_callback
from utils.config import get_config

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/rules", response_model=ResponseWrapper)
async def list_rules(type_id: str = "", db: AsyncSession = Depends(get_db)):
    """获取逻辑分析配置列表。可选按 type_id 过滤。"""
    stmt = select(AnalysisRule).order_by(AnalysisRule.priority)
    if type_id:
        stmt = stmt.where(AnalysisRule.type_id == type_id)
    result = await db.execute(stmt)
    rules = result.scalars().all()

    return ResponseWrapper(
        data=[
            AnalysisRuleResponse(
                rule_id=r.rule_id,
                type_id=r.type_id or "default",
                rule_name=r.rule_name,
                rule_type=r.rule_type,
                expression=r.expression,
                system_prompt=r.system_prompt,
                depend_fields=r.depend_fields,
                web_search=r.web_search,
                is_formatted=r.is_formatted if r.is_formatted is not None else 0,
                output_schema=r.output_schema,
                enabled=r.enabled,
                priority=r.priority,
                created_at=r.created_at,
                updated_at=r.updated_at,
            ).model_dump()
            for r in rules
        ]
    )


@router.post("/rules", response_model=ResponseWrapper)
async def upsert_rule(
    rule: AnalysisRuleCreate, db: AsyncSession = Depends(get_db)
):
    """新增/更新逻辑分析配置（根据 rule_id 判断 upsert）。

    rule_id 全局唯一；若已存在记录归属于其他 type_id，返回 409。
    """
    stmt = select(AnalysisRule).where(AnalysisRule.rule_id == rule.rule_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    target_type_id = rule.type_id or "default"

    if existing:
        if (existing.type_id or "default") != target_type_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"rule_id={rule.rule_id} 已被 type_id={existing.type_id} 占用，"
                    "请换一个 rule_id 或先删除原记录"
                ),
            )
        # 更新
        existing.type_id = target_type_id
        existing.rule_name = rule.rule_name
        existing.rule_type = rule.rule_type
        existing.expression = rule.expression
        existing.system_prompt = rule.system_prompt
        existing.depend_fields = rule.depend_fields
        existing.web_search = rule.web_search
        existing.is_formatted = rule.is_formatted
        existing.output_schema = rule.output_schema
        existing.enabled = rule.enabled
        existing.priority = rule.priority
        await db.commit()
        return ResponseWrapper(message="规则配置已更新", data={"rule_id": rule.rule_id})
    else:
        # 新增
        new_rule = AnalysisRule(
            rule_id=rule.rule_id,
            type_id=target_type_id,
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            expression=rule.expression,
            system_prompt=rule.system_prompt,
            depend_fields=rule.depend_fields,
            web_search=rule.web_search,
            is_formatted=rule.is_formatted,
            output_schema=rule.output_schema,
            enabled=rule.enabled,
            priority=rule.priority,
        )
        db.add(new_rule)
        await db.commit()
        return ResponseWrapper(message="规则配置已创建", data={"rule_id": rule.rule_id})


@router.delete("/rules/{rule_id}", response_model=ResponseWrapper)
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    """删除逻辑分析配置。"""
    stmt = select(AnalysisRule).where(AnalysisRule.rule_id == rule_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if not existing:
        raise HTTPException(status_code=404, detail="规则配置不存在")

    await db.delete(existing)
    await db.commit()
    return ResponseWrapper(message="规则配置已删除")


@router.get("/rules/{rule_id}/check", response_model=ResponseWrapper)
async def check_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    """检查 rule_id 是否已存在。"""
    stmt = select(AnalysisRule).where(AnalysisRule.rule_id == rule_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    return ResponseWrapper(data={"exists": existing is not None})


@router.post("/test", response_model=ResponseWrapper)
async def test_analysis(
    req: AnalysisTestRequest, db: AsyncSession = Depends(get_db)
):
    """逻辑分析调试接口（支持两种模式）。

    模式 1: rule_id + file_id - 使用已保存的规则配置
    模式 2: config + file_id - 使用临时配置
    """
    file_id = req.file_id
    input_values: Dict[str, str] = {}
    expression_resolved = ""
    result_value = ""
    reason = ""

    if req.rule_id:
        # 模式 1: 从数据库加载配置
        stmt = select(AnalysisRule).where(AnalysisRule.rule_id == req.rule_id)
        result = await db.execute(stmt)
        rule = result.scalar_one_or_none()

        if not rule:
            raise HTTPException(status_code=404, detail="规则配置不存在")

        rule_type = rule.rule_type
        expression = rule.expression
        system_prompt = rule.system_prompt or ""
        depend_fields = rule.depend_fields or []
        web_search = rule.web_search
        is_formatted = rule.is_formatted
        output_schema = rule.output_schema
    elif req.config:
        # 模式 2: 使用临时配置
        config = req.config
        rule_type = config.get("rule_type", "judge")
        expression = config.get("expression", "")
        system_prompt = config.get("system_prompt", "")
        depend_fields = config.get("depend_fields", [])
        web_search = config.get("web_search")
        is_formatted = config.get("is_formatted", 0)
        output_schema = config.get("output_schema")
    else:
        raise HTTPException(status_code=400, detail="必须提供 rule_id 或 config")

    try:
        # 获取依赖字段值
        stmt = select(ExtractionResult).where(ExtractionResult.file_id == file_id)
        result = await db.execute(stmt)
        extraction_results = result.scalars().all()
        field_values: Dict[str, str] = {
            er.field_id: er.extracted_value for er in extraction_results
        }

        # 构建 input_values
        for field_id in depend_fields:
            input_values[field_id] = field_values.get(field_id, "")

        # 解析表达式
        expression_resolved = resolve_expression(expression, field_values)

        # 执行计算/判断
        cfg = get_config().analysis
        if rule_type == "judge":
            expression_resolved, _ws_ref = await apply_web_search(
                expression_resolved, web_search, field_values
            )
            result_value, reason = await execute_judge(expression_resolved, system_prompt=system_prompt)
        elif rule_type == "calc":
            result_value, reason = await execute_calc(expression_resolved, cfg.calc_precision)
        elif rule_type == "custom":
            expression_resolved, _ws_ref = await apply_web_search(
                expression_resolved, web_search, field_values
            )
            result_value, reason = await execute_custom(
                expression_resolved,
                is_formatted=bool(is_formatted),
                output_schema=output_schema,
                system_prompt=system_prompt,
            )
        else:
            result_value = f"未知规则类型: {rule_type}"
            reason = ""

    except Exception as e:
        logger.error("分析测试失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

    return ResponseWrapper(
        data=AnalysisTestResponse(
            input_values=input_values,
            expression_resolved=expression_resolved,
            result_value=result_value,
            reason=reason,
        ).model_dump()
    )


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_analysis_task_id() -> str:
    return uuid.uuid4().hex


def _dump_run_items(req: AnalysisRunRequest) -> list[dict[str, Any]]:
    return [item.model_dump() for item in req.items]


async def _run_analysis_with_session(
    items: list[dict[str, Any]],
    *,
    on_rule_done=None,
) -> Dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await run_analysis_batch(
            items,
            session,
            on_rule_done=on_rule_done,
        )


async def _run_analysis_task_background(
    task_id: str,
    items: list[dict[str, Any]],
    callback_url: str,
) -> None:
    """运行 async 模式任务并推送开始、逐规则和任务终态。"""

    await notify_analysis_task_callback(
        callback_url,
        task_id,
        "analyzing",
    )
    try:
        async def on_rule_done(data: Dict[str, Any]) -> None:
            await notify_analysis_task_callback(
                callback_url,
                task_id,
                "analyzing",
                event="rule_done",
                data=data,
            )

        result = await _run_analysis_with_session(
            items,
            on_rule_done=on_rule_done,
        )
        await notify_analysis_task_callback(
            callback_url,
            task_id,
            "complete",
            event="task_done",
            data=result,
        )
    except Exception as exc:
        logger.exception(
            "独立逻辑分析后台任务失败: task_id={}, type={}, error={}",
            task_id,
            type(exc).__name__,
            exc,
        )
        await notify_analysis_task_callback(
            callback_url,
            task_id,
            "analysis_failed",
            event="task_failed",
            data={"error": f"{type(exc).__name__}: {exc}"},
        )


async def _analysis_run_stream(
    task_id: str,
    items: list[dict[str, Any]],
):
    """把批量执行的异步回调桥接为 SSE 事件。"""

    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def worker() -> None:
        await queue.put((
            "analyzing",
            build_analysis_task_payload(task_id, "analyzing"),
        ))
        try:
            async def on_rule_done(data: Dict[str, Any]) -> None:
                await queue.put((
                    "rule_done",
                    build_analysis_task_payload(
                        task_id,
                        "analyzing",
                        event="rule_done",
                        data=data,
                    ),
                ))

            result = await _run_analysis_with_session(
                items,
                on_rule_done=on_rule_done,
            )
            await queue.put((
                "task_done",
                build_analysis_task_payload(
                    task_id,
                    "complete",
                    event="task_done",
                    data=result,
                ),
            ))
        except Exception as exc:
            logger.exception(
                "独立逻辑分析流任务失败: task_id={}, type={}, error={}",
                task_id,
                type(exc).__name__,
                exc,
            )
            await queue.put((
                "task_failed",
                build_analysis_task_payload(
                    task_id,
                    "analysis_failed",
                    event="task_failed",
                    data={"error": f"{type(exc).__name__}: {exc}"},
                ),
            ))
        finally:
            await queue.put((sentinel, None))

    worker_task = asyncio.create_task(worker())
    try:
        while True:
            event, payload = await queue.get()
            if event is sentinel:
                break
            yield _sse_event(event, payload)
    finally:
        if not worker_task.done():
            worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)


@router.post("/run")
async def run_independent_analysis(
    req: AnalysisRunRequest,
    background_tasks: BackgroundTasks,
):
    """使用外部字段值批量执行逻辑规则，支持 sync/async/stream。"""

    items = _dump_run_items(req)
    if req.mode == AnalysisRunModeEnum.sync:
        try:
            data = await _run_analysis_with_session(items)
        except Exception as exc:
            logger.exception(
                "独立逻辑分析失败: type={}, error={}",
                type(exc).__name__,
                exc,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ResponseWrapper(
            message="逻辑分析完成",
            data=AnalysisRunResponse.model_validate(data).model_dump(),
        )

    task_id = _new_analysis_task_id()
    if req.mode == AnalysisRunModeEnum.async_:
        background_tasks.add_task(
            _run_analysis_task_background,
            task_id,
            items,
            str(req.callback_url),
        )
        return ResponseWrapper(
            message="分析任务已提交（异步）",
            data={"task_id": task_id},
        )

    return StreamingResponse(
        _analysis_run_stream(task_id, items),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/test/stream")
async def test_analysis_stream(
    req: AnalysisTestRequest, db: AsyncSession = Depends(get_db)
):
    """逻辑分析调试流式接口，分步返回依赖字段值、表达式解析、LLM 提示词/响应和分析结果。

    模式 1: rule_id + file_id - 使用已保存的规则配置
    模式 2: config + file_id - 使用临时配置
    """
    file_id = req.file_id

    if req.rule_id:
        stmt = select(AnalysisRule).where(AnalysisRule.rule_id == req.rule_id)
        result = await db.execute(stmt)
        rule = result.scalar_one_or_none()
        if not rule:
            raise HTTPException(status_code=404, detail="规则配置不存在")
        rule_type = rule.rule_type
        expression = rule.expression
        system_prompt = rule.system_prompt or ""
        depend_fields = rule.depend_fields or []
        web_search = rule.web_search
        is_formatted = rule.is_formatted
        output_schema = rule.output_schema
    elif req.config:
        config = req.config
        rule_type = config.get("rule_type", "judge")
        expression = config.get("expression", "")
        system_prompt = config.get("system_prompt", "")
        depend_fields = config.get("depend_fields", [])
        web_search = config.get("web_search")
        is_formatted = config.get("is_formatted", 0)
        output_schema = config.get("output_schema")
    else:
        raise HTTPException(status_code=400, detail="必须提供 rule_id 或 config")

    async def event_generator():
        async for item in test_rule_analysis_stream(
            file_id, rule_type, expression, depend_fields, system_prompt, db,
            web_search=web_search, is_formatted=is_formatted, output_schema=output_schema,
        ):
            yield _sse_event(item["event"], item["data"])

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
