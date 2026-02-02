"""文件相关路由：/file/*"""

from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    FileChunkItem,
    FileStatusResponse,
    FileTableItem,
    ResponseWrapper,
    ExtractionResultItem,
    AnalysisResultItem,
)

router = APIRouter(prefix="/file", tags=["file"])


@router.post("/parse", response_model=ResponseWrapper)
async def parse_file(
    file: UploadFile = File(...),
    mode: str = "async",
    db: AsyncSession = Depends(get_db),
):
    """提交文件解析（支持 sync/async/stream）。"""
    # TODO: 实现文件解析提交
    return ResponseWrapper(message="not implemented")


@router.get("/{file_id}/status", response_model=ResponseWrapper)
async def get_file_status(file_id: str, db: AsyncSession = Depends(get_db)):
    """查询文件处理进度。"""
    # TODO: 查询文件状态
    return ResponseWrapper(data=None)


@router.delete("/{file_id}", response_model=ResponseWrapper)
async def delete_file(file_id: str, db: AsyncSession = Depends(get_db)):
    """删除文件及所有关联数据。"""
    # TODO: 删除文件及关联数据
    return ResponseWrapper(message="not implemented")


@router.post("/{file_id}/retry/{stage}", response_model=ResponseWrapper)
async def retry_file(file_id: str, stage: str, db: AsyncSession = Depends(get_db)):
    """从指定阶段重试。"""
    # TODO: 实现重试
    return ResponseWrapper(message="not implemented")


@router.get("/{file_id}/tables", response_model=ResponseWrapper)
async def get_file_tables(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件表格列表。"""
    # TODO: 查询 file_table
    return ResponseWrapper(data=[])


@router.get("/{file_id}/chunks", response_model=ResponseWrapper)
async def get_file_chunks(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件分块列表。"""
    # TODO: 查询 file_chunk
    return ResponseWrapper(data=[])


@router.get("/{file_id}/extraction", response_model=ResponseWrapper)
async def get_extraction_results(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件字段提取结果。"""
    # TODO: 查询 extraction_result
    return ResponseWrapper(data=[])


@router.get("/{file_id}/analysis", response_model=ResponseWrapper)
async def get_analysis_results(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件逻辑分析结果。"""
    # TODO: 查询 analysis_result
    return ResponseWrapper(data=[])
