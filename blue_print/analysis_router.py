"""逻辑分析配置路由：/analysis/*"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    AnalysisRuleCreate,
    AnalysisRuleResponse,
    AnalysisTestRequest,
    AnalysisTestResponse,
    ResponseWrapper,
)
from model.tables import AnalysisRule, ExtractionResult
from service.analysis_service import execute_calc, execute_judge, resolve_expression
from utils.config import get_config

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/rules", response_model=ResponseWrapper)
async def list_rules(db: AsyncSession = Depends(get_db)):
    """获取逻辑分析配置列表。"""
    stmt = select(AnalysisRule).order_by(AnalysisRule.priority)
    result = await db.execute(stmt)
    rules = result.scalars().all()

    return ResponseWrapper(
        data=[
            AnalysisRuleResponse(
                rule_id=r.rule_id,
                rule_name=r.rule_name,
                rule_type=r.rule_type,
                expression=r.expression,
                depend_fields=r.depend_fields,
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
    """新增/更新逻辑分析配置（根据 rule_id 判断 upsert）。"""
    stmt = select(AnalysisRule).where(AnalysisRule.rule_id == rule.rule_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # 更新
        existing.rule_name = rule.rule_name
        existing.rule_type = rule.rule_type
        existing.expression = rule.expression
        existing.depend_fields = rule.depend_fields
        existing.enabled = rule.enabled
        existing.priority = rule.priority
        await db.commit()
        return ResponseWrapper(message="规则配置已更新", data={"rule_id": rule.rule_id})
    else:
        # 新增
        new_rule = AnalysisRule(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            expression=rule.expression,
            depend_fields=rule.depend_fields,
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
        depend_fields = rule.depend_fields or []
    elif req.config:
        # 模式 2: 使用临时配置
        config = req.config
        rule_type = config.get("rule_type", "judge")
        expression = config.get("expression", "")
        depend_fields = config.get("depend_fields", [])
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
            result_value, reason = await execute_judge(expression_resolved)
        elif rule_type == "calc":
            result_value, reason = await execute_calc(expression_resolved, cfg.calc_precision)
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
