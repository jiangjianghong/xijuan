"""逻辑分析配置路由：/analysis/*"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    AnalysisRuleCreate,
    AnalysisTestRequest,
    AnalysisTestResponse,
    ResponseWrapper,
)

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/rules", response_model=ResponseWrapper)
async def list_rules(db: AsyncSession = Depends(get_db)):
    """获取逻辑分析配置列表。"""
    # TODO: 查询所有 enabled 规则
    return ResponseWrapper(data=[])


@router.post("/rules", response_model=ResponseWrapper)
async def upsert_rule(
    rule: AnalysisRuleCreate, db: AsyncSession = Depends(get_db)
):
    """新增/更新逻辑分析配置（根据 rule_id 判断 upsert）。"""
    # TODO: 实现 upsert
    return ResponseWrapper(message="not implemented")


@router.delete("/rules/{rule_id}", response_model=ResponseWrapper)
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    """软删除逻辑分析配置（enabled=0）。"""
    # TODO: 软删除
    return ResponseWrapper(message="not implemented")


@router.get("/rules/{rule_id}/check", response_model=ResponseWrapper)
async def check_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    """检查 rule_id 是否已存在。"""
    # TODO: 检查存在性
    return ResponseWrapper(data={"exists": False})


@router.post("/test", response_model=ResponseWrapper)
async def test_analysis(
    req: AnalysisTestRequest, db: AsyncSession = Depends(get_db)
):
    """逻辑分析调试接口（支持两种模式）。"""
    # TODO: 实现调试
    return ResponseWrapper(data=AnalysisTestResponse().model_dump())
