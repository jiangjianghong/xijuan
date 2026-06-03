"""文档类型路由：/doctype/*

文档类型用于隔离不同格式文件的抽取字段与逻辑规则配置。
- 每个文件归属唯一类型（files.type_id）
- 抽取字段、逻辑规则按 type_id 隔离
- 配置不共享：复制是显式动作，复制后两份独立
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_db
from model.schemas import (
    BatchAssignProjectRequest,
    CopyConfigsRequest,
    CopyConfigsResponse,
    DocTypeCreate,
    DocTypeResponse,
    ExportFieldItem,
    ExportPayload,
    ExportRuleItem,
    ImportConfigsRequest,
    ImportConfigsResponse,
    ProjectCreate,
    ProjectResponse,
    ResponseWrapper,
)
from model.tables import (
    AnalysisResult,
    AnalysisRule,
    DocType,
    ExtractionField,
    ExtractionResult,
    File as FileModel,
    FileChunk,
    FileContent,
    FileTable,
    Project,
)
from utils.milvus_client import MilvusClient

router = APIRouter(prefix="/doctype", tags=["doctype"])


def _new_id(prefix: str = "") -> str:
    """生成 32 位 ID（用于复制时的新字段/规则 ID）。"""
    raw = uuid.uuid4().hex
    return (prefix + raw)[:32] if prefix else raw[:32]


# ─────────────────────────────────────────────────────────────
# 类型 CRUD
# ─────────────────────────────────────────────────────────────


@router.get("/list", response_model=ResponseWrapper)
async def list_doctypes(
    q: Optional[str] = Query(None, description="模糊搜 type_id/type_name"),
    scope: str = Query("all", pattern=r"^(all|template|copy)$"),
    project_id: Optional[str] = Query(None, description="项目过滤；__ungrouped__ 表示未分组"),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    sort: str = Query("created_at", pattern=r"^(created_at|type_name)$"),
    db: AsyncSession = Depends(get_db),
):
    """列出文档类型。

    - 传齐 page+page_size：返回 {items, total}（分页）
    - 否则：原样返回数组（向后兼容；不传任何参数即旧行为）
    - q/scope/project_id/sort 仅在传入时生效
    - 计数（file/field/rule）对结果集 type_id 用 3 条 GROUP BY 聚合，避免 N+1
    """
    base = select(DocType)
    if q:
        like = f"%{q}%"
        base = base.where(or_(DocType.type_id.like(like), DocType.type_name.like(like)))
    if scope == "template":
        base = base.where(or_(DocType.is_template == 1, DocType.is_default == 1))
    elif scope == "copy":
        base = base.where(DocType.is_template == 0, DocType.is_default == 0)
    if project_id == "__ungrouped__":
        base = base.where(DocType.project_id.is_(None))
    elif project_id:
        base = base.where(DocType.project_id == project_id)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0

    sort_col = DocType.type_name.asc() if sort == "type_name" else DocType.created_at.desc()
    stmt = base.order_by(DocType.is_default.desc(), sort_col)

    paginated = bool(page and page_size)
    if paginated:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    types = (await db.execute(stmt)).scalars().all()
    type_ids = [t.type_id for t in types]

    # 计数：3 条 GROUP BY，仅覆盖当前结果集
    file_counts: dict = {}
    field_counts: dict = {}
    rule_counts: dict = {}
    if type_ids:
        for col, target in (
            (FileModel.type_id, file_counts),
            (ExtractionField.type_id, field_counts),
            (AnalysisRule.type_id, rule_counts),
        ):
            rows = (
                await db.execute(
                    select(col, func.count()).where(col.in_(type_ids)).group_by(col)
                )
            ).fetchall()
            target.update({r[0]: r[1] for r in rows})

    # 项目名映射
    proj_ids = [t.project_id for t in types if t.project_id]
    proj_name_map: dict = {}
    if proj_ids:
        rows = (
            await db.execute(
                select(Project.project_id, Project.project_name).where(
                    Project.project_id.in_(proj_ids)
                )
            )
        ).fetchall()
        proj_name_map = {r[0]: r[1] for r in rows}

    items = []
    for t in types:
        items.append(
            {
                **DocTypeResponse(
                    type_id=t.type_id,
                    type_name=t.type_name,
                    description=t.description,
                    is_default=t.is_default,
                    enabled=t.enabled,
                    is_template=t.is_template,
                    parent_type_id=t.parent_type_id,
                    project_id=t.project_id,
                    project_name=proj_name_map.get(t.project_id),
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                ).model_dump(),
                "file_count": file_counts.get(t.type_id, 0),
                "field_count": field_counts.get(t.type_id, 0),
                "rule_count": rule_counts.get(t.type_id, 0),
            }
        )

    if paginated:
        return ResponseWrapper(data={"items": items, "total": total})
    return ResponseWrapper(data=items)


@router.post("", response_model=ResponseWrapper)
async def upsert_doctype(payload: DocTypeCreate, db: AsyncSession = Depends(get_db)):
    """创建/更新类型（按 type_id upsert）。"""
    stmt = select(DocType).where(DocType.type_id == payload.type_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        # default 类型禁止改 type_id（这里通过 upsert 保护）
        existing.type_name = payload.type_name
        existing.description = payload.description
        existing.enabled = payload.enabled
        await db.commit()
        return ResponseWrapper(message="类型已更新", data={"type_id": payload.type_id})

    new_t = DocType(
        type_id=payload.type_id,
        type_name=payload.type_name,
        description=payload.description,
        is_default=0,
        enabled=payload.enabled,
    )
    db.add(new_t)
    await db.commit()
    return ResponseWrapper(message="类型已创建", data={"type_id": payload.type_id})


@router.delete("/{type_id}", response_model=ResponseWrapper)
async def delete_doctype(
    type_id: str,
    force: bool = Query(False, description="是否级联删除该类型下所有文件与配置"),
    db: AsyncSession = Depends(get_db),
):
    """删除类型。
    - 默认类型（is_default=1）禁止删除
    - 默认拒绝有关联文件/字段/规则的类型；force=true 时级联删除
    """
    stmt = select(DocType).where(DocType.type_id == type_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="类型不存在")
    if existing.is_default == 1:
        raise HTTPException(status_code=400, detail="默认类型不可删除")

    file_count = (await db.execute(
        select(func.count(FileModel.file_id)).where(FileModel.type_id == type_id)
    )).scalar() or 0
    field_count = (await db.execute(
        select(func.count(ExtractionField.field_id)).where(ExtractionField.type_id == type_id)
    )).scalar() or 0
    rule_count = (await db.execute(
        select(func.count(AnalysisRule.rule_id)).where(AnalysisRule.type_id == type_id)
    )).scalar() or 0

    if (file_count or field_count or rule_count) and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"类型下仍有数据：文件 {file_count}，字段 {field_count}，规则 {rule_count}。"
                " 传 force=true 级联删除"
            ),
        )

    if force:
        # 找出所有该类型下的文件 ID
        file_ids = [
            row[0]
            for row in (
                await db.execute(select(FileModel.file_id).where(FileModel.type_id == type_id))
            ).fetchall()
        ]

        # 删除文件级关联数据
        if file_ids:
            await db.execute(delete(FileContent).where(FileContent.file_id.in_(file_ids)))
            await db.execute(delete(FileTable).where(FileTable.file_id.in_(file_ids)))
            await db.execute(delete(FileChunk).where(FileChunk.file_id.in_(file_ids)))
            await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id.in_(file_ids)))
            await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id.in_(file_ids)))
            await db.execute(delete(FileModel).where(FileModel.file_id.in_(file_ids)))

            try:
                milvus_client = MilvusClient()
                milvus_client.connect()
                for fid in file_ids:
                    try:
                        milvus_client.delete_by_file_id(fid)
                    except Exception as e:
                        logger.warning("Milvus 删除 file_id={} 失败: {}", fid, e)
            except Exception as e:
                logger.warning("Milvus 连接失败: {}", e)

            # 级联清理 PDF 持久化文件
            try:
                from utils import vl_client as _vl_client_for_storage
                for fid in file_ids:
                    try:
                        _vl_client_for_storage.pdf_path(fid).unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning("doctype 级联清理 PDF 失败 file_id={}: {}", fid, e)
            except Exception as e:
                logger.warning("vl_client 导入失败，跳过 PDF 清理: {}", e)

        # 删除字段/规则
        await db.execute(delete(ExtractionField).where(ExtractionField.type_id == type_id))
        await db.execute(delete(AnalysisRule).where(AnalysisRule.type_id == type_id))

    await db.delete(existing)
    await db.commit()
    return ResponseWrapper(
        message="类型已删除",
        data={
            "type_id": type_id,
            "deleted_files": file_count if force else 0,
            "deleted_fields": field_count if force else 0,
            "deleted_rules": rule_count if force else 0,
        },
    )


# ─────────────────────────────────────────────────────────────
# 复制配置：从源类型复制字段/规则到目标类型（独立副本）
# ─────────────────────────────────────────────────────────────


@router.post("/{type_id}/copy_from", response_model=ResponseWrapper)
async def copy_configs(
    type_id: str,
    req: CopyConfigsRequest,
    db: AsyncSession = Depends(get_db),
):
    """从 source_type_id 复制字段/规则到当前 type_id（独立副本）。

    - field_ids/rule_ids 为空表示复制全部
    - on_conflict=skip：目标已有同 field_name/rule_name 时跳过
    - on_conflict=rename：自动改名加" (副本)"
    - 规则的 depend_fields 按 field_name 重映射到目标类型对应的新 field_id；
      若目标类型缺少同名字段，记入 missing_dependencies 返回。
    """
    if type_id == req.source_type_id:
        raise HTTPException(status_code=400, detail="源类型与目标类型不能相同")

    target = (await db.execute(select(DocType).where(DocType.type_id == type_id))).scalar_one_or_none()
    source = (await db.execute(select(DocType).where(DocType.type_id == req.source_type_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="目标类型不存在")
    if not source:
        raise HTTPException(status_code=404, detail="源类型不存在")

    on_conflict = req.on_conflict or "rename"
    resp = CopyConfigsResponse()

    # ── 1. 复制字段 ──
    field_stmt = select(ExtractionField).where(ExtractionField.type_id == req.source_type_id)
    if req.field_ids:
        field_stmt = field_stmt.where(ExtractionField.field_id.in_(req.field_ids))
    src_fields = (await db.execute(field_stmt)).scalars().all()

    target_field_names = set(
        row[0]
        for row in (
            await db.execute(
                select(ExtractionField.field_name).where(ExtractionField.type_id == type_id)
            )
        ).fetchall()
    )

    # field_name -> new field_id 映射，用于规则的 depend_fields 重映射
    name_to_new_field_id: dict = {}

    for src in src_fields:
        new_name = src.field_name
        if new_name in target_field_names:
            if on_conflict == "skip":
                resp.skipped_fields += 1
                continue
            new_name = f"{new_name} (副本)"
            # 防多次复制重名
            suffix = 2
            while new_name in target_field_names:
                new_name = f"{src.field_name} (副本{suffix})"
                suffix += 1

        new_field_id = _new_id()
        new_field = ExtractionField(
            field_id=new_field_id,
            type_id=type_id,
            field_name=new_name,
            source_type=src.source_type,
            enabled=src.enabled,
            priority=src.priority,
            table_name_pattern=src.table_name_pattern,
            table_match_type=src.table_match_type,
            table_match_keywords=src.table_match_keywords,
            table_match_max_results=src.table_match_max_results,
            table_system_prompt=src.table_system_prompt,
            table_extract_prompt=src.table_extract_prompt,
            search_type=src.search_type,
            search_config=src.search_config,
            text_system_prompt=src.text_system_prompt,
            text_extract_prompt=src.text_extract_prompt,
            vl_method=src.vl_method,
            vl_config=src.vl_config,
            vl_system_prompt=src.vl_system_prompt,
            vl_extract_prompt=src.vl_extract_prompt,
        )
        db.add(new_field)
        target_field_names.add(new_name)
        # 记录原字段名 -> 目标新 field_id（用于规则重映射）
        name_to_new_field_id[src.field_name] = new_field_id
        resp.copied_fields += 1

    # 也加入目标类型已有的同名字段（若复制规则时依赖了已存在字段）
    if not name_to_new_field_id:
        # 仅当未复制任何字段时才查全表回填，减少开销
        pass
    target_existing_fields = (
        await db.execute(
            select(ExtractionField.field_id, ExtractionField.field_name)
            .where(ExtractionField.type_id == type_id)
        )
    ).fetchall()
    name_to_target_field_id = {r[1]: r[0] for r in target_existing_fields}
    # 新增的优先于已有的（覆盖映射）
    name_to_target_field_id.update(name_to_new_field_id)

    # 源类型 field_id -> field_name（解析 depend_fields）
    src_all_fields = (
        await db.execute(
            select(ExtractionField.field_id, ExtractionField.field_name)
            .where(ExtractionField.type_id == req.source_type_id)
        )
    ).fetchall()
    src_field_id_to_name = {r[0]: r[1] for r in src_all_fields}

    # ── 2. 复制规则 ──
    rule_stmt = select(AnalysisRule).where(AnalysisRule.type_id == req.source_type_id)
    if req.rule_ids:
        rule_stmt = rule_stmt.where(AnalysisRule.rule_id.in_(req.rule_ids))
    src_rules = (await db.execute(rule_stmt)).scalars().all()

    target_rule_names = set(
        row[0]
        for row in (
            await db.execute(
                select(AnalysisRule.rule_name).where(AnalysisRule.type_id == type_id)
            )
        ).fetchall()
    )

    missing_deps: List[str] = []

    for src in src_rules:
        new_name = src.rule_name
        if new_name in target_rule_names:
            if on_conflict == "skip":
                resp.skipped_rules += 1
                continue
            new_name = f"{new_name} (副本)"
            suffix = 2
            while new_name in target_rule_names:
                new_name = f"{src.rule_name} (副本{suffix})"
                suffix += 1

        # 重映射 depend_fields：源 field_id -> 源 field_name -> 目标 field_id
        new_depend_fields: List[str] = []
        for src_fid in (src.depend_fields or []):
            fname = src_field_id_to_name.get(src_fid)
            if fname and fname in name_to_target_field_id:
                new_depend_fields.append(name_to_target_field_id[fname])
            else:
                missing_deps.append(f"{src.rule_name}::{fname or src_fid}")

        new_rule = AnalysisRule(
            rule_id=_new_id(),
            type_id=type_id,
            rule_name=new_name,
            rule_type=src.rule_type,
            expression=src.expression,
            system_prompt=src.system_prompt,
            depend_fields=new_depend_fields if new_depend_fields else None,
            enabled=src.enabled,
            priority=src.priority,
        )
        db.add(new_rule)
        target_rule_names.add(new_name)
        resp.copied_rules += 1

    resp.missing_dependencies = missing_deps

    # 记录血缘：目标由源复制而来（默认类型不 reparent）
    if target.is_default != 1:
        target.parent_type_id = req.source_type_id
        # 目标未分组时继承源项目；已分组不覆盖
        if target.project_id is None and source.project_id is not None:
            target.project_id = source.project_id

    await db.commit()

    return ResponseWrapper(message="配置复制完成", data=resp.model_dump())


# ─────────────────────────────────────────────────────────────
# 导出/导入：JSON 载荷可跨环境迁移配置
# ─────────────────────────────────────────────────────────────


@router.get("/{type_id}/export", response_model=ResponseWrapper)
async def export_configs(type_id: str, db: AsyncSession = Depends(get_db)):
    """导出指定类型的所有字段+规则为 JSON 载荷。

    返回的 JSON 可直接保存为文件，并通过 POST /doctype/import 导入到其他环境。
    规则的 depend_fields 在导出时按 field_name 序列化，导入时按 field_name 在目标
    类型中重映射，避免依赖原始 field_id。
    """
    target = (await db.execute(select(DocType).where(DocType.type_id == type_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="类型不存在")

    fields = (
        await db.execute(
            select(ExtractionField)
            .where(ExtractionField.type_id == type_id)
            .order_by(ExtractionField.priority)
        )
    ).scalars().all()
    rules = (
        await db.execute(
            select(AnalysisRule)
            .where(AnalysisRule.type_id == type_id)
            .order_by(AnalysisRule.priority)
        )
    ).scalars().all()

    field_id_to_name = {f.field_id: f.field_name for f in fields}

    payload = ExportPayload(
        type_id=target.type_id,
        type_name=target.type_name,
        description=target.description,
        version=1,
        fields=[
            ExportFieldItem(
                field_id=f.field_id,
                field_name=f.field_name,
                source_type=f.source_type,
                enabled=f.enabled,
                priority=f.priority,
                table_name_pattern=f.table_name_pattern,
                table_match_type=f.table_match_type,
                table_match_keywords=f.table_match_keywords,
                table_match_max_results=f.table_match_max_results,
                table_system_prompt=f.table_system_prompt,
                table_extract_prompt=f.table_extract_prompt,
                search_type=f.search_type,
                search_config=f.search_config,
                text_system_prompt=f.text_system_prompt,
                text_extract_prompt=f.text_extract_prompt,
                vl_method=f.vl_method,
                vl_config=f.vl_config,
                vl_system_prompt=f.vl_system_prompt,
                vl_extract_prompt=f.vl_extract_prompt,
            )
            for f in fields
        ],
        rules=[
            ExportRuleItem(
                rule_id=r.rule_id,
                rule_name=r.rule_name,
                rule_type=r.rule_type,
                expression=r.expression,
                system_prompt=r.system_prompt,
                depend_field_names=[
                    field_id_to_name[fid]
                    for fid in (r.depend_fields or [])
                    if fid in field_id_to_name
                ],
                enabled=r.enabled,
                priority=r.priority,
            )
            for r in rules
        ],
    )
    return ResponseWrapper(data=payload.model_dump())


@router.post("/import", response_model=ResponseWrapper)
async def import_configs(req: ImportConfigsRequest, db: AsyncSession = Depends(get_db)):
    """从 JSON 载荷导入字段+规则到目标类型。

    - 目标类型不存在且 create_type_if_missing=true 时自动创建
    - 字段以新生成的 field_id 写入（不复用源 field_id），避免跨类型冲突
    - 规则的 depend_field_names 按字段名重映射到目标类型的字段；
      若目标缺失某依赖，记入 missing_dependencies 返回（依赖该字段的位置由规则引用）
    """
    payload = req.payload
    target_type_id = (req.target_type_id or payload.type_id or "").strip()
    if not target_type_id:
        raise HTTPException(status_code=400, detail="target_type_id 不能为空")

    on_conflict = req.on_conflict or "rename"
    resp = ImportConfigsResponse(target_type_id=target_type_id)

    # 1. 处理目标类型
    target = (await db.execute(select(DocType).where(DocType.type_id == target_type_id))).scalar_one_or_none()
    if not target:
        if not req.create_type_if_missing:
            raise HTTPException(status_code=404, detail=f"目标类型 {target_type_id} 不存在")
        new_t = DocType(
            type_id=target_type_id,
            type_name=payload.type_name or target_type_id,
            description=payload.description,
            is_default=0,
            enabled=1,
        )
        db.add(new_t)
        await db.flush()
        resp.created_type = True

    # 2. 复制字段（始终生成新 field_id，避免与全局 field_id 冲突）
    target_field_names = set(
        row[0]
        for row in (
            await db.execute(
                select(ExtractionField.field_name).where(ExtractionField.type_id == target_type_id)
            )
        ).fetchall()
    )

    name_to_new_field_id: dict = {}

    for src in payload.fields:
        new_name = src.field_name
        if new_name in target_field_names:
            if on_conflict == "skip":
                resp.skipped_fields += 1
                continue
            new_name = f"{new_name} (副本)"
            suffix = 2
            while new_name in target_field_names:
                new_name = f"{src.field_name} (副本{suffix})"
                suffix += 1

        new_field_id = _new_id()
        new_field = ExtractionField(
            field_id=new_field_id,
            type_id=target_type_id,
            field_name=new_name,
            source_type=src.source_type,
            enabled=src.enabled,
            priority=src.priority,
            table_name_pattern=src.table_name_pattern,
            table_match_type=src.table_match_type,
            table_match_keywords=src.table_match_keywords,
            table_match_max_results=src.table_match_max_results,
            table_system_prompt=src.table_system_prompt,
            table_extract_prompt=src.table_extract_prompt,
            search_type=src.search_type,
            search_config=src.search_config,
            text_system_prompt=src.text_system_prompt,
            text_extract_prompt=src.text_extract_prompt,
            vl_method=src.vl_method,
            vl_config=src.vl_config,
            vl_system_prompt=src.vl_system_prompt,
            vl_extract_prompt=src.vl_extract_prompt,
        )
        db.add(new_field)
        target_field_names.add(new_name)
        # 用源字段名做映射键（导出时已是源端原名）
        name_to_new_field_id[src.field_name] = new_field_id
        resp.copied_fields += 1

    # 把目标类型已存在的同名字段也纳入映射（用于规则依赖回退）
    target_existing = (
        await db.execute(
            select(ExtractionField.field_id, ExtractionField.field_name)
            .where(ExtractionField.type_id == target_type_id)
        )
    ).fetchall()
    name_to_target_field_id = {r[1]: r[0] for r in target_existing}
    name_to_target_field_id.update(name_to_new_field_id)

    # 3. 复制规则
    target_rule_names = set(
        row[0]
        for row in (
            await db.execute(
                select(AnalysisRule.rule_name).where(AnalysisRule.type_id == target_type_id)
            )
        ).fetchall()
    )

    missing_deps: List[str] = []

    for src in payload.rules:
        new_name = src.rule_name
        if new_name in target_rule_names:
            if on_conflict == "skip":
                resp.skipped_rules += 1
                continue
            new_name = f"{new_name} (副本)"
            suffix = 2
            while new_name in target_rule_names:
                new_name = f"{src.rule_name} (副本{suffix})"
                suffix += 1

        new_depend_fields: List[str] = []
        for fname in (src.depend_field_names or []):
            if fname in name_to_target_field_id:
                new_depend_fields.append(name_to_target_field_id[fname])
            else:
                missing_deps.append(f"{src.rule_name}::{fname}")

        new_rule = AnalysisRule(
            rule_id=_new_id(),
            type_id=target_type_id,
            rule_name=new_name,
            rule_type=src.rule_type,
            expression=src.expression,
            system_prompt=src.system_prompt,
            depend_fields=new_depend_fields if new_depend_fields else None,
            enabled=src.enabled,
            priority=src.priority,
        )
        db.add(new_rule)
        target_rule_names.add(new_name)
        resp.copied_rules += 1

    resp.missing_dependencies = missing_deps
    await db.commit()

    return ResponseWrapper(message="配置导入完成", data=resp.model_dump())


# ─────────────────────────────────────────────────────────────
# 项目（纯算法端分类，一个 type 只属一个项目）
# ─────────────────────────────────────────────────────────────


@router.get("/projects", response_model=ResponseWrapper)
async def list_projects(db: AsyncSession = Depends(get_db)):
    """列出所有项目，附带每个项目下的 type 数。"""
    projects = (
        await db.execute(select(Project).order_by(Project.created_at))
    ).scalars().all()

    rows = (
        await db.execute(
            select(DocType.project_id, func.count(DocType.type_id))
            .where(DocType.project_id.isnot(None))
            .group_by(DocType.project_id)
        )
    ).fetchall()
    count_map = {r[0]: r[1] for r in rows}

    items = [
        ProjectResponse(
            project_id=p.project_id,
            project_name=p.project_name,
            description=p.description,
            type_count=count_map.get(p.project_id, 0),
            created_at=p.created_at,
            updated_at=p.updated_at,
        ).model_dump()
        for p in projects
    ]
    return ResponseWrapper(data=items)


@router.post("/projects", response_model=ResponseWrapper)
async def upsert_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """创建/改名项目（按 project_id upsert）。"""
    existing = (
        await db.execute(select(Project).where(Project.project_id == payload.project_id))
    ).scalar_one_or_none()
    if existing:
        existing.project_name = payload.project_name
        existing.description = payload.description
        await db.commit()
        return ResponseWrapper(message="项目已更新", data={"project_id": payload.project_id})

    db.add(
        Project(
            project_id=payload.project_id,
            project_name=payload.project_name,
            description=payload.description,
        )
    )
    await db.commit()
    return ResponseWrapper(message="项目已创建", data={"project_id": payload.project_id})


@router.delete("/projects/{project_id}", response_model=ResponseWrapper)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """删除项目：成员 type 的 project_id 置空，再删项目本身（不删 type）。"""
    existing = (
        await db.execute(select(Project).where(Project.project_id == project_id))
    ).scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="项目不存在")

    await db.execute(
        update(DocType).where(DocType.project_id == project_id).values(project_id=None)
    )
    await db.delete(existing)
    await db.commit()
    return ResponseWrapper(message="项目已删除", data={"project_id": project_id})


@router.post("/batch_assign_project", response_model=ResponseWrapper)
async def batch_assign_project(
    req: BatchAssignProjectRequest, db: AsyncSession = Depends(get_db)
):
    """批量把 type 归入项目；project_id 为 null 表示移出（未分组）。"""
    if req.project_id is not None:
        exists = (
            await db.execute(select(Project).where(Project.project_id == req.project_id))
        ).scalar_one_or_none()
        if not exists:
            raise HTTPException(status_code=404, detail="项目不存在")
    if req.type_ids:
        await db.execute(
            update(DocType)
            .where(DocType.type_id.in_(req.type_ids))
            .values(project_id=req.project_id)
        )
    await db.commit()
    return ResponseWrapper(
        message="批量归类完成",
        data={"count": len(req.type_ids), "project_id": req.project_id},
    )
