"""字段提取配置路由：/extraction/*"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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
    collect_depend_fields,
    derive_source_pages,
    extract_table_field,
    extract_text_field,
    extract_vl_field,
    resolve_advanced_field_from_db,
    search_chunk_db,
    search_context,
    search_rule,
    search_section,
    search_vector_db,
    test_field_extraction_stream,
)
from service.match_prompts import (
    DEFAULT_SECTION_MATCH_PROMPT,
    DEFAULT_TABLE_MATCH_PROMPT,
    MATCH_INDEX_OUTPUT_INSTRUCTION,
)
from service.vl_service._defaults import DEFAULT_BATCH_PROMPT, DEFAULT_LOCATE_PROMPT

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.get("/match-prompt-defaults", response_model=ResponseWrapper)
async def get_match_prompt_defaults():
    """下发各类提示词模板的系统默认值。

    前端据此渲染「高级配置」文本框并做「是否改过」比对，从而不必在
    ui/js 里保存副本 —— 副本一旦落后于后端，用户保存时会把旧模板固化进库。
    """
    return ResponseWrapper(
        data={
            "section": DEFAULT_SECTION_MATCH_PROMPT,
            "table": DEFAULT_TABLE_MATCH_PROMPT,
            "output_instruction": MATCH_INDEX_OUTPUT_INSTRUCTION,
            "vl_batch": DEFAULT_BATCH_PROMPT,
            "vl_locate": DEFAULT_LOCATE_PROMPT,
        }
    )


@router.get("/fields", response_model=ResponseWrapper)
async def list_fields(type_id: str = "", db: AsyncSession = Depends(get_db)):
    """获取字段提取配置列表。可选按 type_id 过滤。"""
    stmt = select(ExtractionField).order_by(ExtractionField.priority)
    if type_id:
        stmt = stmt.where(ExtractionField.type_id == type_id)
    result = await db.execute(stmt)
    fields = result.scalars().all()

    return ResponseWrapper(
        data=[
            ExtractionFieldResponse(
                field_id=f.field_id,
                type_id=f.type_id or "default",
                field_name=f.field_name,
                source_type=f.source_type,
                enabled=f.enabled,
                priority=f.priority,
                use_llm=f.use_llm if f.use_llm is not None else 1,
                table_name_pattern=f.table_name_pattern,
                table_match_type=f.table_match_type,
                table_match_keywords=f.table_match_keywords,
                table_match_max_results=f.table_match_max_results,
                table_system_prompt=f.table_system_prompt,
                table_match_prompt=f.table_match_prompt,
                table_extract_prompt=f.table_extract_prompt,
                search_type=f.search_type,
                search_config=f.search_config,
                text_system_prompt=f.text_system_prompt,
                text_extract_prompt=f.text_extract_prompt,
                vl_method=f.vl_method,
                vl_config=f.vl_config,
                vl_system_prompt=f.vl_system_prompt,
                vl_extract_prompt=f.vl_extract_prompt,
                is_advanced=f.is_advanced if f.is_advanced is not None else 0,
                depend_fields=f.depend_fields,
                created_at=f.created_at,
                updated_at=f.updated_at,
            ).model_dump()
            for f in fields
        ]
    )


async def _referencing_advanced_fields(
    db: AsyncSession, type_id: str, field_id: str
) -> List[ExtractionField]:
    """反查同类型下有哪些进阶字段引用了 field_id（读 depend_fields）。

    depend_fields 是 JSON 数组，跨库的 JSON 查询语法不统一，且单个类型下字段量很小，
    因此拉回来在 Python 里过滤。
    """
    rows = (
        await db.execute(
            select(ExtractionField).where(
                ExtractionField.type_id == type_id,
                ExtractionField.is_advanced == 1,
            )
        )
    ).scalars().all()
    return [f for f in rows if field_id in (f.depend_fields or [])]


def _describe_fields(fields: List[ExtractionField]) -> str:
    return "、".join(f"{f.field_name}({f.field_id})" for f in fields)


@router.post("/fields", response_model=ResponseWrapper)
async def upsert_field(
    field: ExtractionFieldCreate, db: AsyncSession = Depends(get_db)
):
    """新增/更新字段提取配置（根据 field_id 判断 upsert）。

    field_id 全局唯一；若已存在记录归属于其他 type_id，返回 409。
    """
    stmt = select(ExtractionField).where(ExtractionField.field_id == field.field_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    target_type_id = field.type_id or "default"

    # 进阶字段：扫描实际配置算出依赖，并校验引用必须是同类型的普通字段
    computed_depend: List[str] = []
    if field.is_advanced:
        computed_depend = collect_depend_fields(field)
        if computed_depend:
            rows = (await db.execute(
                select(ExtractionField.field_id, ExtractionField.is_advanced)
                .where(
                    ExtractionField.type_id == target_type_id,
                    ExtractionField.field_id.in_(computed_depend),
                )
            )).all()
            found = {fid: adv for fid, adv in rows}
            for dep in computed_depend:
                if dep not in found:
                    raise HTTPException(
                        status_code=400,
                        detail=f"引用的字段 {dep} 不存在于类型 {target_type_id}",
                    )
                if found[dep]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"进阶字段只能引用普通字段，{dep} 也是进阶字段",
                    )

    # 反向保护：已被进阶字段引用的普通字段，不能改成进阶字段
    # （否则「进阶只能引用普通」的不变量被绕过，且两者同阶段执行，引用方拿不到值）
    warn_msg = ""
    if existing and field.is_advanced and not (existing.is_advanced or 0):
        referrers = await _referencing_advanced_fields(db, target_type_id, field.field_id)
        if referrers:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"字段 {field.field_id} 正被进阶字段 {_describe_fields(referrers)} 引用，"
                    "不能改为进阶字段；请先解除引用"
                ),
            )
    # 禁用被引用的普通字段不拦截（可能是临时操作），但要让调用方知道后果
    if existing and not field.enabled and not field.is_advanced:
        referrers = await _referencing_advanced_fields(db, target_type_id, field.field_id)
        if referrers:
            warn_msg = (
                f"；注意：进阶字段 {_describe_fields(referrers)} 引用了该字段，"
                "禁用后它们的引用会解析为空"
            )
            logger.warning(
                "禁用了被引用的普通字段 {}，引用方: {}", field.field_id, _describe_fields(referrers)
            )

    if existing:
        if (existing.type_id or "default") != target_type_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"field_id={field.field_id} 已被 type_id={existing.type_id} 占用，"
                    "请换一个 field_id 或先删除原记录"
                ),
            )
        # 更新
        existing.type_id = target_type_id
        existing.field_name = field.field_name
        existing.source_type = field.source_type
        existing.enabled = field.enabled
        existing.priority = field.priority
        existing.use_llm = field.use_llm
        existing.table_name_pattern = field.table_name_pattern
        existing.table_match_type = field.table_match_type
        existing.table_match_keywords = field.table_match_keywords
        existing.table_match_max_results = field.table_match_max_results
        existing.table_system_prompt = field.table_system_prompt
        existing.table_match_prompt = field.table_match_prompt
        existing.table_extract_prompt = field.table_extract_prompt
        existing.search_type = field.search_type
        existing.search_config = field.search_config
        existing.text_system_prompt = field.text_system_prompt
        existing.text_extract_prompt = field.text_extract_prompt
        existing.vl_method = field.vl_method
        existing.vl_config = field.vl_config
        existing.vl_system_prompt = field.vl_system_prompt
        existing.vl_extract_prompt = field.vl_extract_prompt
        existing.is_advanced = field.is_advanced
        existing.depend_fields = computed_depend if field.is_advanced else None
        await db.commit()
        return ResponseWrapper(message="字段配置已更新" + warn_msg, data={"field_id": field.field_id})
    else:
        # 新增
        new_field = ExtractionField(
            field_id=field.field_id,
            type_id=target_type_id,
            field_name=field.field_name,
            source_type=field.source_type,
            enabled=field.enabled,
            priority=field.priority,
            use_llm=field.use_llm,
            table_name_pattern=field.table_name_pattern,
            table_match_type=field.table_match_type,
            table_match_keywords=field.table_match_keywords,
            table_match_max_results=field.table_match_max_results,
            table_system_prompt=field.table_system_prompt,
            table_match_prompt=field.table_match_prompt,
            table_extract_prompt=field.table_extract_prompt,
            search_type=field.search_type,
            search_config=field.search_config,
            text_system_prompt=field.text_system_prompt,
            text_extract_prompt=field.text_extract_prompt,
            vl_method=field.vl_method,
            vl_config=field.vl_config,
            vl_system_prompt=field.vl_system_prompt,
            vl_extract_prompt=field.vl_extract_prompt,
            is_advanced=field.is_advanced,
            depend_fields=computed_depend if field.is_advanced else None,
        )
        db.add(new_field)
        await db.commit()
        return ResponseWrapper(message="字段配置已创建", data={"field_id": field.field_id})


@router.delete("/fields/{field_id}", response_model=ResponseWrapper)
async def delete_field(
    field_id: str, force: bool = False, db: AsyncSession = Depends(get_db)
):
    """删除字段提取配置。

    若该字段正被同类型的进阶字段引用，默认返回 **409**（避免留下悬空引用——
    运行时会静默解析为空串）；确实要删可加 `force=true`。
    """
    stmt = select(ExtractionField).where(ExtractionField.field_id == field_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if not existing:
        raise HTTPException(status_code=404, detail="字段配置不存在")

    referrers = await _referencing_advanced_fields(
        db, existing.type_id or "default", field_id
    )
    if referrers and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"字段 {field_id} 正被进阶字段 {_describe_fields(referrers)} 引用，"
                "删除会造成悬空引用；请先解除引用，或加 force=true 强制删除"
            ),
        )
    if referrers:
        logger.warning(
            "强制删除被引用的字段 {}，引用方将解析为空: {}", field_id, _describe_fields(referrers)
        )

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


def _build_temp_field(config: Dict[str, Any]) -> ExtractionField:
    """由调试请求里的临时配置构造一个游离 ExtractionField（不入库）。

    `/test` 与 `/test/stream` 共用；`is_advanced` 必须透传，否则进阶字段在调试时
    不会解析 `<field_result>` 引用与页码联动，调出来的结果与正式抽取不一致。
    """
    return ExtractionField(
        field_id=config.get("field_id") or "__test__",
        field_name=config.get("field_name", "测试字段"),
        source_type=config.get("source_type", "text"),
        use_llm=config.get("use_llm", 1),
        is_advanced=config.get("is_advanced", 0) or 0,
        table_name_pattern=config.get("table_name_pattern"),
        table_match_type=config.get("table_match_type"),
        table_match_keywords=config.get("table_match_keywords"),
        table_match_max_results=config.get("table_match_max_results"),
        table_system_prompt=config.get("table_system_prompt"),
        table_match_prompt=config.get("table_match_prompt"),
        table_extract_prompt=config.get("table_extract_prompt"),
        search_type=config.get("search_type"),
        search_config=config.get("search_config"),
        text_system_prompt=config.get("text_system_prompt"),
        text_extract_prompt=config.get("text_extract_prompt"),
        vl_method=config.get("vl_method"),
        vl_config=config.get("vl_config"),
        vl_system_prompt=config.get("vl_system_prompt"),
        vl_extract_prompt=config.get("vl_extract_prompt"),
    )


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
        field = _build_temp_field(config)
    else:
        raise HTTPException(status_code=400, detail="必须提供 field_id 或 config")

    # 进阶字段：用该文件已落库的普通字段结果解析引用 / 页码联动后再调试
    resolved_refs_info: Dict[str, Any] = {}
    if getattr(field, "is_advanced", 0):
        try:
            field, resolved_refs_info = await resolve_advanced_field_from_db(file_id, field, db)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

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

            extracted_value, reason, refs, model_pages = await extract_table_field(file_id, field, db)
            llm_input = field.table_extract_prompt or ""
            llm_output = extracted_value

        elif field.source_type == "vl":
            # VL 类提取：直接调用 extract_vl_field，附带元信息
            extracted_value, reason, refs, model_pages = await extract_vl_field(file_id, field, db)
            search_results = (
                [
                    {
                        "type": "vl_meta",
                        "method": refs["_vl"]["method"],
                        "key_pages": refs["_vl"].get("key_pages"),
                        "vl_total_tokens": refs["_vl"].get("vl_total_tokens", 0),
                        "batches_with_info": refs["_vl"].get("batches_with_info"),
                    }
                ]
                if refs
                else []
            )
            llm_input = field.vl_extract_prompt or ""
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
            extracted_value, reason, refs, model_pages = await extract_text_field(file_id, field, db)
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
            pages=model_pages,
            source_pages=derive_source_pages(model_pages, refs),
            resolved_refs=resolved_refs_info or None,
        ).model_dump()
    )


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/test/stream")
async def test_extraction_stream(
    req: ExtractionTestRequest, db: AsyncSession = Depends(get_db)
):
    """字段提取调试流式接口，分步返回检索结果、提示词、LLM 响应和提取结果。"""
    file_id = req.file_id

    # 构建临时 ExtractionField 对象（复用 /test 端点的逻辑）
    if req.field_id:
        stmt = select(ExtractionField).where(ExtractionField.field_id == req.field_id)
        result = await db.execute(stmt)
        field = result.scalar_one_or_none()
        if not field:
            raise HTTPException(status_code=404, detail="字段配置不存在")
    elif req.config:
        config = req.config
        field = _build_temp_field(config)
    else:
        raise HTTPException(status_code=400, detail="必须提供 field_id 或 config")

    async def event_generator():
        async for item in test_field_extraction_stream(file_id, field, db):
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
