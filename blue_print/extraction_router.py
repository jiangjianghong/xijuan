"""字段提取配置路由：/extraction/*"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    ExtractionFieldCreate,
    ExtractionFieldResponse,
    ExtractionTestRequest,
    ExtractionTestResponse,
    ResponseWrapper,
)
from model.tables import ExtractionField, ExtractionResult, FileContent, FileTable
from service.extraction_service import (
    extract_table_field,
    extract_text_field,
    search_chunk_db,
    search_context,
    search_rule,
    search_section,
    search_vector_db,
)

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.get("/fields", response_model=ResponseWrapper)
async def list_fields(db: AsyncSession = Depends(get_db)):
    """获取字段提取配置列表。"""
    stmt = select(ExtractionField).order_by(ExtractionField.priority)
    result = await db.execute(stmt)
    fields = result.scalars().all()

    return ResponseWrapper(
        data=[
            ExtractionFieldResponse(
                field_id=f.field_id,
                field_name=f.field_name,
                source_type=f.source_type,
                enabled=f.enabled,
                priority=f.priority,
                table_name_pattern=f.table_name_pattern,
                table_match_type=f.table_match_type,
                table_system_prompt=f.table_system_prompt,
                table_extract_prompt=f.table_extract_prompt,
                search_type=f.search_type,
                search_config=f.search_config,
                text_system_prompt=f.text_system_prompt,
                text_extract_prompt=f.text_extract_prompt,
                created_at=f.created_at,
                updated_at=f.updated_at,
            ).model_dump()
            for f in fields
        ]
    )


@router.post("/fields", response_model=ResponseWrapper)
async def upsert_field(
    field: ExtractionFieldCreate, db: AsyncSession = Depends(get_db)
):
    """新增/更新字段提取配置（根据 field_id 判断 upsert）。"""
    stmt = select(ExtractionField).where(ExtractionField.field_id == field.field_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # 更新
        existing.field_name = field.field_name
        existing.source_type = field.source_type
        existing.enabled = field.enabled
        existing.priority = field.priority
        existing.table_name_pattern = field.table_name_pattern
        existing.table_match_type = field.table_match_type
        existing.table_system_prompt = field.table_system_prompt
        existing.table_extract_prompt = field.table_extract_prompt
        existing.search_type = field.search_type
        existing.search_config = field.search_config
        existing.text_system_prompt = field.text_system_prompt
        existing.text_extract_prompt = field.text_extract_prompt
        await db.commit()
        return ResponseWrapper(message="字段配置已更新", data={"field_id": field.field_id})
    else:
        # 新增
        new_field = ExtractionField(
            field_id=field.field_id,
            field_name=field.field_name,
            source_type=field.source_type,
            enabled=field.enabled,
            priority=field.priority,
            table_name_pattern=field.table_name_pattern,
            table_match_type=field.table_match_type,
            table_system_prompt=field.table_system_prompt,
            table_extract_prompt=field.table_extract_prompt,
            search_type=field.search_type,
            search_config=field.search_config,
            text_system_prompt=field.text_system_prompt,
            text_extract_prompt=field.text_extract_prompt,
        )
        db.add(new_field)
        await db.commit()
        return ResponseWrapper(message="字段配置已创建", data={"field_id": field.field_id})


@router.delete("/fields/{field_id}", response_model=ResponseWrapper)
async def delete_field(field_id: str, db: AsyncSession = Depends(get_db)):
    """删除字段提取配置。"""
    stmt = select(ExtractionField).where(ExtractionField.field_id == field_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if not existing:
        raise HTTPException(status_code=404, detail="字段配置不存在")

    await db.delete(existing)
    await db.commit()
    return ResponseWrapper(message="字段配置已删除")


@router.get("/fields/{field_id}/check", response_model=ResponseWrapper)
async def check_field(field_id: str, db: AsyncSession = Depends(get_db)):
    """检查 field_id 是否已存在。"""
    stmt = select(ExtractionField).where(ExtractionField.field_id == field_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    return ResponseWrapper(data={"exists": existing is not None})


@router.post("/test", response_model=ResponseWrapper)
async def test_extraction(
    req: ExtractionTestRequest, db: AsyncSession = Depends(get_db)
):
    """字段提取调试接口（支持两种模式）。

    模式 1: field_id + file_id - 使用已保存的字段配置
    模式 2: config + file_id - 使用临时配置
    """
    file_id = req.file_id
    search_results: List[Dict[str, Any]] = []
    llm_input = ""
    llm_output = ""
    extracted_value = ""
    reason = ""

    # 构建临时 ExtractionField 对象
    if req.field_id:
        # 模式 1: 从数据库加载配置
        stmt = select(ExtractionField).where(ExtractionField.field_id == req.field_id)
        result = await db.execute(stmt)
        field = result.scalar_one_or_none()

        if not field:
            raise HTTPException(status_code=404, detail="字段配置不存在")
    elif req.config:
        # 模式 2: 使用临时配置
        config = req.config
        field = ExtractionField(
            field_id="__test__",
            field_name=config.get("field_name", "测试字段"),
            source_type=config.get("source_type", "text"),
            table_name_pattern=config.get("table_name_pattern"),
            table_match_type=config.get("table_match_type"),
            table_system_prompt=config.get("table_system_prompt"),
            table_extract_prompt=config.get("table_extract_prompt"),
            search_type=config.get("search_type"),
            search_config=config.get("search_config"),
            text_system_prompt=config.get("text_system_prompt"),
            text_extract_prompt=config.get("text_extract_prompt"),
        )
    else:
        raise HTTPException(status_code=400, detail="必须提供 field_id 或 config")

    try:
        if field.source_type == "table":
            # 表格类提取
            stmt = select(FileTable).where(FileTable.file_id == file_id)
            result = await db.execute(stmt)
            tables = result.scalars().all()

            search_results = [
                {"table_name": t.table_name, "table_content": t.table_content[:500] + "..." if len(t.table_content) > 500 else t.table_content}
                for t in tables
            ]

            extracted_value, reason = await extract_table_field(file_id, field, db)
            llm_input = field.table_extract_prompt or ""
            llm_output = extracted_value

        else:
            # 文本类提取
            stmt = select(FileContent).where(FileContent.file_id == file_id)
            result = await db.execute(stmt)
            file_content = result.scalar_one_or_none()

            if not file_content:
                raise HTTPException(status_code=404, detail="文件内容不存在")

            content = file_content.file_content
            search_type = field.search_type or "context"
            search_config = field.search_config or {}

            # 执行检索
            if search_type == "context":
                search_results = await search_context(content, search_config)
            elif search_type == "section":
                search_results = await search_section(content, search_config)
            elif search_type == "rule":
                search_results = await search_rule(content, search_config)
            elif search_type == "chunk_db":
                search_results = await search_chunk_db(file_id, search_config, db)
            elif search_type == "vector_db":
                search_results = await search_vector_db(file_id, search_config)

            # 执行提取
            extracted_value, reason = await extract_text_field(file_id, field, db)
            llm_input = field.text_extract_prompt or ""
            llm_output = extracted_value

    except Exception as e:
        logger.error("提取测试失败: {}", e)
        raise HTTPException(status_code=500, detail=str(e))

    return ResponseWrapper(
        data=ExtractionTestResponse(
            search_results=search_results,
            llm_input=llm_input,
            llm_output=llm_output,
            extracted_value=extracted_value,
            reason=reason,
        ).model_dump()
    )
