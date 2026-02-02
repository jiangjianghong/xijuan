"""字段提取配置路由：/extraction/*"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    ExtractionFieldCreate,
    ExtractionFieldResponse,
    ExtractionTestRequest,
    ExtractionTestResponse,
    ResponseWrapper,
)

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.get("/fields", response_model=ResponseWrapper)
async def list_fields(db: AsyncSession = Depends(get_db)):
    """获取字段提取配置列表。"""
    # TODO: 查询所有 enabled 字段
    return ResponseWrapper(data=[])


@router.post("/fields", response_model=ResponseWrapper)
async def upsert_field(
    field: ExtractionFieldCreate, db: AsyncSession = Depends(get_db)
):
    """新增/更新字段提取配置（根据 field_id 判断 upsert）。"""
    # TODO: 实现 upsert
    return ResponseWrapper(message="not implemented")


@router.delete("/fields/{field_id}", response_model=ResponseWrapper)
async def delete_field(field_id: str, db: AsyncSession = Depends(get_db)):
    """软删除字段提取配置（enabled=0）。"""
    # TODO: 软删除
    return ResponseWrapper(message="not implemented")


@router.get("/fields/{field_id}/check", response_model=ResponseWrapper)
async def check_field(field_id: str, db: AsyncSession = Depends(get_db)):
    """检查 field_id 是否已存在。"""
    # TODO: 检查存在性
    return ResponseWrapper(data={"exists": False})


@router.post("/test", response_model=ResponseWrapper)
async def test_extraction(
    req: ExtractionTestRequest, db: AsyncSession = Depends(get_db)
):
    """字段提取调试接口（支持两种模式）。"""
    # TODO: 实现调试
    return ResponseWrapper(data=ExtractionTestResponse().model_dump())
