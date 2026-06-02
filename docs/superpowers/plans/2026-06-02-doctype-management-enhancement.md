# 文档类型管理增强(面向海量 type) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给文档类型加上「模板/副本」血缘与「项目」分类两个维度,并把管理界面升级为可搜索/筛选/分页/批量清理/只读查看,以应对快速增长的 type 数量。

**Architecture:** 后端先行(TDD,pytest),前端后接(无 JS 测试夹具,手动浏览器验证)。数据层给 `doc_type` 加 3 列(`is_template/parent_type_id/project_id`)、新增 `project` 表;`GET /doctype/list` 改造为按需分页搜索并修掉 N+1 计数;新增 promote/demote、批量删除、批量归类、项目 CRUD 等 additive 接口;`copy_from` 增加记录血缘 + 继承项目的副作用。对外向后兼容、后端零改造。

**Tech Stack:** FastAPI · 异步 SQLAlchemy(aiomysql)· Pydantic · pytest-asyncio(`asyncio_mode=auto`,真实 DB)· 原生 JS 前端(`ui/js/*.js` + `ui/index.html`)。

**对应 spec:** `docs/superpowers/specs/2026-06-02-doctype-management-enhancement-design.md`

**全局约定:**
- 提交信息:conventional 前缀保留英文,描述用中文(项目惯例)。
- 后端测试集中在 `tests/test_doctype_management.py`;测试用 `dm_` 前缀的临时 type/project,断言后清理,避免污染真实库。
- 运行单测:`uv run pytest tests/test_doctype_management.py -v`。

---

## Task 1: 数据模型 + 迁移(Project 表 + doc_type 三列)

**Files:**
- Modify: `model/tables.py`(新增 `Project`;`DocType` 加 3 列)
- Modify: `service/init_service.py`(`migrations` 加 3 条 ALTER;`index_migrations` 加 2 条索引)
- Test: `tests/test_doctype_migration.py`

- [ ] **Step 1: 写迁移验证测试(先失败)**

Create `tests/test_doctype_migration.py`:

```python
"""doc_type 新列与 project 表迁移验证。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from model.database import get_engine
from service.init_service import init_database


@pytest.mark.anyio
async def test_doctype_new_columns_and_project_table_exist():
    await init_database()  # 幂等：建表 + 补列
    engine = get_engine()
    async with engine.connect() as conn:
        for col in ("is_template", "parent_type_id", "project_id"):
            r = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                    "AND COLUMN_NAME = :c"
                ),
                {"c": col},
            )
            assert r.scalar() == 1, f"doc_type 缺少列 {col}"

        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            )
        )
        assert r.scalar() == 1, "缺少 project 表"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_migration.py -v`
Expected: FAIL —— `is_template` 列不存在(`assert ... == 1`)。

- [ ] **Step 3: `model/tables.py` 加 `Project` 模型 + `DocType` 三列**

在 `DocType` 类内,`is_default` 行之后插入三列:

```python
    is_template: Mapped[int] = mapped_column(TINYINT, default=0)
    parent_type_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

在 `DocType` 类**之后**(`# ── 1. files 表` 注释之前)新增:

```python
class Project(Base):
    __tablename__ = "project"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
```

> `String/Text/TINYINT/DateTime/func/Mapped/mapped_column` 均已在文件顶部导入(`DocType` 已用)。

- [ ] **Step 4: `service/init_service.py` 补列与索引迁移**

在 `migrations` 列表(现 `extraction_field`/`files` 那批)末尾追加 3 行:

```python
            ("doc_type", "is_template", "TINYINT NOT NULL DEFAULT 0"),
            ("doc_type", "parent_type_id", "VARCHAR(64) NULL"),
            ("doc_type", "project_id", "VARCHAR(64) NULL"),
```

在 `index_migrations` 列表末尾追加 2 行:

```python
            ("doc_type", "ix_doc_type_parent_type_id", "parent_type_id"),
            ("doc_type", "ix_doc_type_project_id", "project_id"),
```

> `project` 表由同一个 `begin()` 块里已有的 `Base.metadata.create_all` 自动创建,无需手写建表语句。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_migration.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add model/tables.py service/init_service.py tests/test_doctype_migration.py
git commit -m "feat: doc_type 增加 is_template/parent_type_id/project_id 列与 project 表"
```

---

## Task 2: 项目 CRUD 接口(/doctype/projects)

**Files:**
- Modify: `model/schemas.py`(新增 `ProjectCreate` / `ProjectResponse`)
- Modify: `blue_print/doctype_router.py`(新增 3 个项目接口 + 导入)
- Test: `tests/test_doctype_management.py`

- [ ] **Step 1: 写项目 CRUD 测试(先失败)**

Create `tests/test_doctype_management.py`:

```python
"""文档类型管理增强：项目 CRUD / list 改造 / 批量 / 血缘 / 提升。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_project_crud_and_unbind(client: AsyncClient):
    # 创建
    r = await client.post("/doctype/projects", json={"project_id": "dm_p1", "project_name": "项目一"})
    assert r.status_code == 200
    # 列表含新项目
    r = await client.get("/doctype/projects")
    data = r.json()["data"]
    assert any(p["project_id"] == "dm_p1" for p in data)
    # 改名(upsert)
    r = await client.post("/doctype/projects", json={"project_id": "dm_p1", "project_name": "项目一改"})
    r = await client.get("/doctype/projects")
    p = next(p for p in r.json()["data"] if p["project_id"] == "dm_p1")
    assert p["project_name"] == "项目一改"
    # 删除
    r = await client.delete("/doctype/projects/dm_p1")
    assert r.status_code == 200
    r = await client.get("/doctype/projects")
    assert not any(p["project_id"] == "dm_p1" for p in r.json()["data"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_management.py::test_project_crud_and_unbind -v`
Expected: FAIL —— `POST /doctype/projects` 返回 404/405(路由不存在)。

- [ ] **Step 3: `model/schemas.py` 新增项目 schema**

在文档类型 schema 区(`ImportConfigsResponse` 之后、`# ── 文件相关` 之前)插入:

```python
class ProjectCreate(BaseModel):
    project_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    project_name: str = Field(..., max_length=200)
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    project_id: str
    project_name: str
    description: Optional[str] = None
    type_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

- [ ] **Step 4: `blue_print/doctype_router.py` 补导入**

把顶部 sqlalchemy 导入改为含 `or_, update`:

```python
from sqlalchemy import delete, func, or_, select, update
```

**只导入本任务已存在的 schema**(`BatchAssignProjectRequest` / `DocTypeBatchDeleteRequest` 由 Task 4 / Task 7 创建并在各自任务补导入,**此处不要提前导入**,否则 ImportError)。schema 导入块加入 `ProjectCreate, ProjectResponse`;`model.tables` 导入块加入 `Project`:

```python
from model.schemas import (
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
```

- [ ] **Step 5: 新增项目 CRUD 路由**

在 `doctype_router.py` 末尾追加:

```python
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
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_management.py::test_project_crud_and_unbind -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add model/schemas.py blue_print/doctype_router.py tests/test_doctype_management.py
git commit -m "feat: 新增项目 CRUD 接口 /doctype/projects"
```

---

## Task 3: 改造 GET /doctype/list(搜索/筛选/分页/计数优化/新字段)

**Files:**
- Modify: `model/schemas.py`(扩展 `DocTypeResponse`)
- Modify: `blue_print/doctype_router.py`(重写 `list_doctypes`)
- Test: `tests/test_doctype_management.py`

- [ ] **Step 1: 写 list 行为测试(先失败)**

追加到 `tests/test_doctype_management.py`:

```python
@pytest.mark.anyio
async def test_list_backward_compatible_array(client: AsyncClient):
    """不传参数：返回数组（向后兼容）。"""
    r = await client.get("/doctype/list")
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)


@pytest.mark.anyio
async def test_list_paginated_shape_and_filters(client: AsyncClient):
    # 造一个普通类型（is_template=0, is_default=0），代表“副本/普通”
    await client.post("/doctype", json={"type_id": "dm_plain", "type_name": "DM普通"})
    try:
        # 分页：返回 {items,total}
        r = await client.get("/doctype/list?page=1&page_size=100")
        data = r.json()["data"]
        assert "items" in data and "total" in data
        assert isinstance(data["items"], list)

        # 搜索命中，且新字段存在
        r = await client.get("/doctype/list?q=dm_plain&page=1&page_size=10")
        items = r.json()["data"]["items"]
        assert len(items) == 1 and items[0]["type_id"] == "dm_plain"
        assert items[0]["is_template"] == 0
        assert items[0]["project_id"] is None

        # scope=template：含 default（is_default=1），不含 dm_plain
        r = await client.get("/doctype/list?scope=template&page=1&page_size=500")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert "default" in ids and "dm_plain" not in ids

        # scope=copy：含 dm_plain，不含 default
        r = await client.get("/doctype/list?scope=copy&page=1&page_size=500")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert "dm_plain" in ids and "default" not in ids
    finally:
        await client.delete("/doctype/dm_plain?force=true")
```

> 本任务的测试**不依赖** promote(用 `default` 代表 `is_default` 的「模板侧」、`dm_plain` 代表「副本侧」)。`is_template=1` 的真实模板由 Task 6 验证。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_management.py -k list -v`
Expected: FAIL —— 分页请求返回的仍是数组(无 `items`/`total`),且响应缺少 `is_template`/`project_id` 字段。

- [ ] **Step 3: 扩展 `DocTypeResponse`**

`model/schemas.py` 的 `DocTypeResponse` 加 4 个可选字段:

```python
class DocTypeResponse(BaseModel):
    type_id: str
    type_name: str
    description: Optional[str] = None
    is_default: int = 0
    enabled: int = 1
    is_template: int = 0
    parent_type_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

- [ ] **Step 4: 重写 `list_doctypes`**

把 `blue_print/doctype_router.py` 的 `list_doctypes` 整个替换为:

```python
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_management.py -k list -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add model/schemas.py blue_print/doctype_router.py tests/test_doctype_management.py
git commit -m "feat: GET /doctype/list 支持搜索/筛选/分页并修复计数 N+1"
```

---

## Task 4: 批量归类接口(/doctype/batch_assign_project)

**Files:**
- Modify: `model/schemas.py`(新增 `BatchAssignProjectRequest`)
- Modify: `blue_print/doctype_router.py`(新增 `batch_assign_project`)
- Test: `tests/test_doctype_management.py`

- [ ] **Step 1: 写批量归类测试(先失败)**

追加:

```python
@pytest.mark.anyio
async def test_batch_assign_and_project_delete_unbinds(client: AsyncClient):
    await client.post("/doctype/projects", json={"project_id": "dm_pa", "project_name": "归类项目"})
    await client.post("/doctype", json={"type_id": "dm_a1", "type_name": "A1"})
    await client.post("/doctype", json={"type_id": "dm_a2", "type_name": "A2"})
    try:
        # 归类
        r = await client.post(
            "/doctype/batch_assign_project",
            json={"type_ids": ["dm_a1", "dm_a2"], "project_id": "dm_pa"},
        )
        assert r.status_code == 200
        r = await client.get("/doctype/list?project_id=dm_pa&page=1&page_size=10")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert set(ids) == {"dm_a1", "dm_a2"}

        # 项目不存在 → 404
        r = await client.post(
            "/doctype/batch_assign_project",
            json={"type_ids": ["dm_a1"], "project_id": "no_such"},
        )
        assert r.status_code == 404

        # 删项目 → 成员解绑（变未分组），type 仍在
        await client.delete("/doctype/projects/dm_pa")
        r = await client.get("/doctype/list?project_id=__ungrouped__&page=1&page_size=500")
        ids = [i["type_id"] for i in r.json()["data"]["items"]]
        assert "dm_a1" in ids and "dm_a2" in ids
    finally:
        await client.delete("/doctype/dm_a1?force=true")
        await client.delete("/doctype/dm_a2?force=true")
        await client.delete("/doctype/projects/dm_pa")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_management.py::test_batch_assign_and_project_delete_unbinds -v`
Expected: FAIL —— `/doctype/batch_assign_project` 路由不存在。

- [ ] **Step 3: `model/schemas.py` 新增 schema**

在项目 schema 之后追加:

```python
class BatchAssignProjectRequest(BaseModel):
    type_ids: List[str]
    project_id: Optional[str] = None  # None 表示移出项目（未分组）
```

- [ ] **Step 4: 新增 `batch_assign_project` 路由**

先在 `doctype_router.py` 的 `from model.schemas import (...)` 块补入 `BatchAssignProjectRequest`(按字母序放在 `CopyConfigsRequest` 之前)。然后在项目 CRUD 之后追加:

```python
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_management.py::test_batch_assign_and_project_delete_unbinds -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add model/schemas.py blue_print/doctype_router.py tests/test_doctype_management.py
git commit -m "feat: 新增批量归类接口 /doctype/batch_assign_project"
```

---

## Task 5: copy_from 记录血缘 + 继承项目

**Files:**
- Modify: `blue_print/doctype_router.py`(`copy_configs` 末尾加副作用)
- Test: `tests/test_doctype_management.py`

- [ ] **Step 1: 写血缘/继承测试(先失败)**

追加:

```python
@pytest.mark.anyio
async def test_copy_from_records_parent_and_inherits_project(client: AsyncClient):
    await client.post("/doctype/projects", json={"project_id": "dm_pc", "project_name": "源项目"})
    await client.post("/doctype", json={"type_id": "dm_src", "type_name": "源"})
    await client.post(
        "/doctype/batch_assign_project",
        json={"type_ids": ["dm_src"], "project_id": "dm_pc"},
    )
    await client.post("/doctype", json={"type_id": "dm_tgt", "type_name": "目标"})
    try:
        r = await client.post("/doctype/dm_tgt/copy_from", json={"source_type_id": "dm_src"})
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_tgt&page=1&page_size=10")
        item = r.json()["data"]["items"][0]
        assert item["parent_type_id"] == "dm_src"
        assert item["project_id"] == "dm_pc"  # 目标原未分组 → 继承源项目
    finally:
        await client.delete("/doctype/dm_src?force=true")
        await client.delete("/doctype/dm_tgt?force=true")
        await client.delete("/doctype/projects/dm_pc")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_management.py::test_copy_from_records_parent_and_inherits_project -v`
Expected: FAIL —— `parent_type_id` 为 None。

- [ ] **Step 3: `copy_configs` 末尾加副作用**

在 `copy_configs` 函数里,把结尾的

```python
    resp.missing_dependencies = missing_deps
    await db.commit()

    return ResponseWrapper(message="配置复制完成", data=resp.model_dump())
```

改为:

```python
    resp.missing_dependencies = missing_deps

    # 记录血缘：目标由源复制而来（默认类型不 reparent）
    if target.is_default != 1:
        target.parent_type_id = req.source_type_id
        # 目标未分组时继承源项目；已分组不覆盖
        if target.project_id is None and source.project_id is not None:
            target.project_id = source.project_id

    await db.commit()

    return ResponseWrapper(message="配置复制完成", data=resp.model_dump())
```

> `target` / `source` 已在函数开头通过 `scalar_one_or_none()` 加载并归属当前 session,改属性后 `commit()` 即持久化。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_management.py::test_copy_from_records_parent_and_inherits_project -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add blue_print/doctype_router.py tests/test_doctype_management.py
git commit -m "feat: copy_from 记录 parent_type_id 并继承源项目"
```

---

## Task 6: 副本↔模板提升/取消(promote/demote)

**Files:**
- Modify: `blue_print/doctype_router.py`(新增 promote/demote)
- Test: `tests/test_doctype_management.py`

- [ ] **Step 1: 写 promote/demote 测试(先失败)**

追加:

```python
@pytest.mark.anyio
async def test_promote_demote(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_pm", "type_name": "待提升"})
    try:
        r = await client.post("/doctype/dm_pm/promote")
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_pm&page=1&page_size=10")
        assert r.json()["data"]["items"][0]["is_template"] == 1
        # 提升后进入 template 过滤
        r = await client.get("/doctype/list?scope=template&page=1&page_size=500")
        assert any(i["type_id"] == "dm_pm" for i in r.json()["data"]["items"])

        r = await client.post("/doctype/dm_pm/demote")
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_pm&page=1&page_size=10")
        assert r.json()["data"]["items"][0]["is_template"] == 0

        # 默认类型不可操作
        r = await client.post("/doctype/default/promote")
        assert r.status_code == 400
    finally:
        await client.delete("/doctype/dm_pm?force=true")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_management.py::test_promote_demote -v`
Expected: FAIL —— promote 路由不存在。

- [ ] **Step 3: 新增 promote/demote 路由**

在 `doctype_router.py`(`batch_assign_project` 之后)追加:

```python
@router.post("/{type_id}/promote", response_model=ResponseWrapper)
async def promote_type(type_id: str, db: AsyncSession = Depends(get_db)):
    """把副本/普通类型标记为模板（保留 parent_type_id 作血缘）。"""
    t = (
        await db.execute(select(DocType).where(DocType.type_id == type_id))
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="类型不存在")
    if t.is_default == 1:
        raise HTTPException(status_code=400, detail="默认类型无需标记")
    t.is_template = 1
    await db.commit()
    return ResponseWrapper(message="已标记为模板", data={"type_id": type_id})


@router.post("/{type_id}/demote", response_model=ResponseWrapper)
async def demote_type(type_id: str, db: AsyncSession = Depends(get_db)):
    """取消模板标记。"""
    t = (
        await db.execute(select(DocType).where(DocType.type_id == type_id))
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="类型不存在")
    if t.is_default == 1:
        raise HTTPException(status_code=400, detail="默认类型不可操作")
    t.is_template = 0
    await db.commit()
    return ResponseWrapper(message="已取消模板标记", data={"type_id": type_id})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_management.py::test_promote_demote -v`
Expected: PASS。若 Task 3 中标了 `xfail`,现在移除标记并重跑 `-k list` 应全绿。

- [ ] **Step 5: 提交**

```bash
git add blue_print/doctype_router.py tests/test_doctype_management.py
git commit -m "feat: 新增 promote/demote 接口（副本↔模板）"
```

---

## Task 7: 批量删除(/doctype/batch_delete)+ 抽公共删除 helper

**Files:**
- Modify: `model/schemas.py`(新增 `DocTypeBatchDeleteRequest`)
- Modify: `blue_print/doctype_router.py`(抽 `_delete_one_type`;重写 `delete_doctype`;新增 `batch_delete_types`)
- Test: `tests/test_doctype_management.py`

- [ ] **Step 1: 写批量删除测试(先失败)**

追加:

```python
@pytest.mark.anyio
async def test_batch_delete(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_d1", "type_name": "D1"})
    await client.post("/doctype", json={"type_id": "dm_d2", "type_name": "D2"})
    # 批量删除（含一个不存在的 + default 应被跳过）
    r = await client.post(
        "/doctype/batch_delete",
        json={"type_ids": ["dm_d1", "dm_d2", "default", "no_such"], "force": True},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["deleted"] == 2
    by_id = {x["type_id"]: x for x in data["results"]}
    assert by_id["dm_d1"]["ok"] is True
    assert by_id["default"]["ok"] is False  # 默认类型被跳过
    assert by_id["no_such"]["ok"] is False

    # 确认已删
    r = await client.get("/doctype/list?scope=copy&page=1&page_size=500")
    ids = [i["type_id"] for i in r.json()["data"]["items"]]
    assert "dm_d1" not in ids and "dm_d2" not in ids
    # default 仍在
    r = await client.get("/doctype/list?page=1&page_size=500")
    assert any(i["type_id"] == "default" for i in r.json()["data"]["items"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_doctype_management.py::test_batch_delete -v`
Expected: FAIL —— `/doctype/batch_delete` 路由不存在。

- [ ] **Step 3: `model/schemas.py` 新增 schema**

```python
class DocTypeBatchDeleteRequest(BaseModel):
    type_ids: List[str]
    force: bool = False
```

> 注意与既有 `BatchDeleteRequest`(文件用,`file_ids`)区分,**勿复用**。

- [ ] **Step 4: 抽 `_delete_one_type` helper 并改写 delete/batch**

先在 `doctype_router.py` 的 `from model.schemas import (...)` 块补入 `DocTypeBatchDeleteRequest`(按字母序放在 `DocTypeCreate` 之前)。然后把现有 `delete_doctype` 函数体抽成不 commit 的 helper,并让单删/批删复用。用下面整段替换现有 `delete_doctype`:

```python
async def _delete_one_type(type_id: str, force: bool, db: AsyncSession) -> dict:
    """删除单个类型（不 commit，由调用方统一提交）。

    返回 {type_id, ok, reason?, deleted_files, deleted_fields, deleted_rules}。
    不抛异常，便于批量场景逐条记录结果。
    """
    existing = (
        await db.execute(select(DocType).where(DocType.type_id == type_id))
    ).scalar_one_or_none()
    if not existing:
        return {"type_id": type_id, "ok": False, "reason": "类型不存在"}
    if existing.is_default == 1:
        return {"type_id": type_id, "ok": False, "reason": "默认类型不可删除"}

    file_count = (
        await db.execute(
            select(func.count(FileModel.file_id)).where(FileModel.type_id == type_id)
        )
    ).scalar() or 0
    field_count = (
        await db.execute(
            select(func.count(ExtractionField.field_id)).where(
                ExtractionField.type_id == type_id
            )
        )
    ).scalar() or 0
    rule_count = (
        await db.execute(
            select(func.count(AnalysisRule.rule_id)).where(AnalysisRule.type_id == type_id)
        )
    ).scalar() or 0

    if (file_count or field_count or rule_count) and not force:
        return {
            "type_id": type_id,
            "ok": False,
            "reason": (
                f"类型下仍有数据：文件 {file_count}，字段 {field_count}，规则 {rule_count}。"
                " 传 force=true 级联删除"
            ),
        }

    if force:
        file_ids = [
            row[0]
            for row in (
                await db.execute(
                    select(FileModel.file_id).where(FileModel.type_id == type_id)
                )
            ).fetchall()
        ]
        if file_ids:
            await db.execute(delete(FileContent).where(FileContent.file_id.in_(file_ids)))
            await db.execute(delete(FileTable).where(FileTable.file_id.in_(file_ids)))
            await db.execute(delete(FileChunk).where(FileChunk.file_id.in_(file_ids)))
            await db.execute(
                delete(ExtractionResult).where(ExtractionResult.file_id.in_(file_ids))
            )
            await db.execute(
                delete(AnalysisResult).where(AnalysisResult.file_id.in_(file_ids))
            )
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

            try:
                from utils import vl_client as _vl_client_for_storage

                for fid in file_ids:
                    try:
                        _vl_client_for_storage.pdf_path(fid).unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning("级联清理 PDF 失败 file_id={}: {}", fid, e)
            except Exception as e:
                logger.warning("vl_client 导入失败，跳过 PDF 清理: {}", e)

        await db.execute(delete(ExtractionField).where(ExtractionField.type_id == type_id))
        await db.execute(delete(AnalysisRule).where(AnalysisRule.type_id == type_id))

    await db.delete(existing)
    return {
        "type_id": type_id,
        "ok": True,
        "deleted_files": file_count if force else 0,
        "deleted_fields": field_count if force else 0,
        "deleted_rules": rule_count if force else 0,
    }


@router.delete("/{type_id}", response_model=ResponseWrapper)
async def delete_doctype(
    type_id: str,
    force: bool = Query(False, description="是否级联删除该类型下所有文件与配置"),
    db: AsyncSession = Depends(get_db),
):
    """删除类型（单个）。默认类型不可删；有关联数据需 force=true。"""
    res = await _delete_one_type(type_id, force, db)
    if not res["ok"]:
        reason = res["reason"]
        if reason == "类型不存在":
            raise HTTPException(status_code=404, detail=reason)
        if "默认类型" in reason:
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=409, detail=reason)
    await db.commit()
    return ResponseWrapper(
        message="类型已删除",
        data={
            "type_id": type_id,
            "deleted_files": res["deleted_files"],
            "deleted_fields": res["deleted_fields"],
            "deleted_rules": res["deleted_rules"],
        },
    )


@router.post("/batch_delete", response_model=ResponseWrapper)
async def batch_delete_types(
    req: DocTypeBatchDeleteRequest, db: AsyncSession = Depends(get_db)
):
    """批量删除类型；逐条记录结果，默认类型/不存在/有数据未 force 的会被跳过。"""
    results = [await _delete_one_type(tid, req.force, db) for tid in req.type_ids]
    await db.commit()
    deleted = sum(1 for r in results if r["ok"])
    return ResponseWrapper(
        message=f"批量删除完成：成功 {deleted}/{len(results)}",
        data={"results": results, "deleted": deleted},
    )
```

> 这段同时定义了 helper、单删、批删,**整体替换原 `delete_doctype`**。原 `copy_from`/`export`/`import` 等保持不动。注意 `_delete_one_type` 不能用 `@router` 装饰(它不是路由)。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_doctype_management.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: 回归现有测试**

Run: `uv run pytest -q`
Expected: 既有用例不回归(尤其 `test_file_router*`、`test_extraction*`)。

- [ ] **Step 7: 提交**

```bash
git add model/schemas.py blue_print/doctype_router.py tests/test_doctype_management.py
git commit -m "feat: 新增批量删除接口 /doctype/batch_delete 并抽公共删除逻辑"
```

---

## Task 8: 前端 api.js 新增方法

**Files:**
- Modify: `ui/js/api.js`(文档类型区追加方法)

> 前端无 JS 测试夹具,以下任务为 实现 → 浏览器手动验证 → 提交。

- [ ] **Step 1: 在 `ui/js/api.js` 的「文档类型」区追加方法**

在 `getDocTypes()` 之后插入(保留 `getDocTypes` 供选择器轻量取用):

```javascript
    /**
     * 文档类型列表（带搜索/筛选/分页）。
     * 传 page+pageSize → 返回 {items,total}；否则返回数组。
     */
    async listDocTypes(params = {}) {
        const qs = new URLSearchParams();
        if (params.q) qs.set('q', params.q);
        if (params.scope && params.scope !== 'all') qs.set('scope', params.scope);
        if (params.projectId) qs.set('project_id', params.projectId);
        if (params.page) qs.set('page', params.page);
        if (params.pageSize) qs.set('page_size', params.pageSize);
        if (params.sort) qs.set('sort', params.sort);
        const query = qs.toString();
        const result = await this.request('/doctype/list' + (query ? `?${query}` : ''));
        return result.data;
    },

    async promoteType(typeId) {
        return this.request(`/doctype/${encodeURIComponent(typeId)}/promote`, { method: 'POST' });
    },

    async demoteType(typeId) {
        return this.request(`/doctype/${encodeURIComponent(typeId)}/demote`, { method: 'POST' });
    },

    async batchDeleteTypes(typeIds, force = false) {
        const result = await this.request('/doctype/batch_delete', {
            method: 'POST',
            body: JSON.stringify({ type_ids: typeIds, force }),
        });
        return result.data;
    },

    async batchAssignProject(typeIds, projectId) {
        const result = await this.request('/doctype/batch_assign_project', {
            method: 'POST',
            body: JSON.stringify({ type_ids: typeIds, project_id: projectId ?? null }),
        });
        return result.data;
    },

    async getProjects() {
        const result = await this.request('/doctype/projects');
        return result.data;
    },

    async saveProject(data) {
        return this.request('/doctype/projects', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    async deleteProject(projectId) {
        return this.request(`/doctype/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' });
    },
```

- [ ] **Step 2: 浏览器控制台验证**

启动:`python app.py`,打开 `http://localhost:5019/ui`,F12 控制台执行:

```javascript
await API.listDocTypes({ page: 1, pageSize: 5 });   // → {items:[...], total:N}
await API.getProjects();                             // → []（或已有项目）
```
Expected: 形态正确,无报错。

- [ ] **Step 3: 提交**

```bash
git add ui/js/api.js
git commit -m "feat: api.js 增加类型分页/提升/批量/项目等方法"
```

---

## Task 9: 顶部选择器只显模板 + 默认 + 当前

**Files:**
- Modify: `ui/js/doctype.js`(`refresh` / `renderSelector`)

- [ ] **Step 1: 改 `refresh()` 与 `renderSelector()`**

把 `doctype.js` 的 `refresh()` 改为分别取「选择器用模板列表」和「弹窗用分页数据」。先替换 `refresh` 与 `renderSelector`:

```javascript
    async refresh() {
        // 选择器只需要模板 + 默认（数组形态）
        try {
            this.selectorTypes = await API.listDocTypes({ scope: 'template' });
        } catch (e) {
            console.error('加载文档类型失败:', e);
            this.selectorTypes = [{ type_id: 'default', type_name: '默认类型', is_default: 1, file_count: 0 }];
        }
        // 保留 this.types 供复制弹窗等旧逻辑使用（来源候选 = 模板+默认，合理）
        this.types = this.selectorTypes;
        this.renderSelector();
    },

    renderSelector() {
        const sel = document.getElementById('doctype-selector');
        if (!sel) return;
        const current = API.getCurrentTypeId();
        const list = (this.selectorTypes || []).slice();
        // 当前选中若不是模板/默认，则补进选择器，避免“看不到自己”
        if (current && !list.some(t => t.type_id === current)) {
            list.push({ type_id: current, type_name: current, file_count: 0 });
        }
        sel.innerHTML = list.map(t =>
            `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (${t.file_count || 0})</option>`
        ).join('');
        const exists = list.some(t => t.type_id === current);
        sel.value = exists ? current : 'default';
        if (!exists) API.setCurrentTypeId('default');
    },
```

并在 `DocTypeManager` 顶部状态里**保留** `types: []`,在其后补充以下字段:

```javascript
    selectorTypes: [],     // 选择器用（模板+默认）
    manage: {              // 管理弹窗状态
        items: [], total: 0, page: 1, pageSize: 20,
        q: '', scope: 'all', projectId: '', selected: new Set(),
    },
    projects: [],
```

> 不要删除 `types`:`openCopyDialog` / `onSourceTypeChange` 仍引用它(`refresh` 已把它指向 `selectorTypes`)。`openManageDialog` / `renderManageTable` / `deleteType` 在 Task 11 重写。`onSelectorChange` 末尾对 `this.refresh()` 的调用保持不变。

- [ ] **Step 2: 浏览器验证**

刷新 `/ui`:顶部「文档类型」下拉应只剩模板 + 默认(+当前若为副本)。切换类型仍能刷新文件列表/规则配置。

- [ ] **Step 3: 提交**

```bash
git add ui/js/doctype.js
git commit -m "feat: 顶部类型选择器只展示模板与默认类型"
```

---

## Task 10: 管理弹窗 HTML 重构 + 项目管理/只读查看子弹窗

**Files:**
- Modify: `ui/index.html`(替换 doctype 管理弹窗 body;新增两个子弹窗)

- [ ] **Step 1: 替换管理弹窗内容**

把 `ui/index.html` 中 `<div id="doctype-modal-overlay" ...>` 整块(到其闭合 `</div>`,约 520-557 行)替换为:

```html
    <div id="doctype-modal-overlay" class="rule-modal-overlay">
        <div class="rule-modal" style="max-width:920px;">
            <div class="rule-modal-header">
                <h3>文档类型管理</h3>
                <button class="rule-modal-close" onclick="DocTypeManager.closeManageDialog()">&times;</button>
            </div>
            <div class="rule-modal-body">
                <!-- 工具条 -->
                <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:12px;">
                    <input id="dt-search" type="text" placeholder="搜索 类型ID / 名称" class="form-input" style="width:220px;"
                           oninput="DocTypeManager.onSearchInput()">
                    <select id="dt-scope" class="form-select" style="width:120px;" onchange="DocTypeManager.onFilterChange()">
                        <option value="all">全部</option>
                        <option value="template">模板</option>
                        <option value="copy">副本</option>
                    </select>
                    <select id="dt-project" class="form-select" style="width:160px;" onchange="DocTypeManager.onFilterChange()">
                        <option value="">全部项目</option>
                        <option value="__ungrouped__">未分组</option>
                    </select>
                    <div style="flex:1;"></div>
                    <button class="btn btn-secondary" onclick="DocTypeManager.openProjectDialog()">项目管理</button>
                    <button class="btn btn-secondary" onclick="DocTypeManager.triggerImport()">导入</button>
                </div>
                <!-- 新增表单 -->
                <div style="display:flex; gap:8px; align-items:flex-end; margin-bottom:12px;">
                    <div style="flex:1;">
                        <label style="font-size:12px; color:#4F775D;">类型 ID</label>
                        <input id="doctype-new-id" type="text" placeholder="如：contract" class="form-input" style="width:100%;">
                    </div>
                    <div style="flex:1;">
                        <label style="font-size:12px; color:#4F775D;">类型名称</label>
                        <input id="doctype-new-name" type="text" placeholder="如：采购合同" class="form-input" style="width:100%;">
                    </div>
                    <div style="flex:1;">
                        <label style="font-size:12px; color:#4F775D;">归属项目（选填）</label>
                        <select id="doctype-new-project" class="form-select" style="width:100%;"><option value="">未分组</option></select>
                    </div>
                    <button class="btn btn-primary" onclick="DocTypeManager.createTypeFromForm()">新增</button>
                </div>
                <!-- 表格 -->
                <table class="rule-table">
                    <thead>
                        <tr>
                            <th style="width:32px;"><input type="checkbox" id="dt-check-all" onclick="DocTypeManager.toggleSelectAll(this.checked)"></th>
                            <th>类型 ID</th>
                            <th>名称</th>
                            <th>标签</th>
                            <th>项目</th>
                            <th>来源</th>
                            <th>文件</th>
                            <th>字段</th>
                            <th>规则</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody id="doctype-table-body"></tbody>
                </table>
            </div>
            <div class="rule-modal-footer" style="display:flex; align-items:center; gap:12px;">
                <span id="dt-selected-count" style="font-size:12px; color:#4F775D;">已选 0 项</span>
                <button class="btn btn-danger" onclick="DocTypeManager.batchDelete()">批量删除</button>
                <select id="dt-batch-project" class="form-select" style="width:150px;">
                    <option value="">移动到…(未分组)</option>
                </select>
                <button class="btn btn-secondary" onclick="DocTypeManager.batchMove()">移动</button>
                <div style="flex:1;"></div>
                <button class="btn btn-ghost" onclick="DocTypeManager.prevPage()">◀</button>
                <span id="dt-page-info" style="font-size:12px; color:#4F775D;">1 / 1</span>
                <button class="btn btn-ghost" onclick="DocTypeManager.nextPage()">▶</button>
                <span id="dt-total" style="font-size:12px; color:#4F775D;">共 0 个</span>
            </div>
        </div>
    </div>
```

- [ ] **Step 2: 在 Copy Configs Modal 之后新增两个子弹窗**

在 `<div id="copy-modal-overlay" ...>...</div>` 之后插入:

```html
    <!-- Project Manage Modal -->
    <div id="project-modal-overlay" class="rule-modal-overlay">
        <div class="rule-modal" style="max-width:560px;">
            <div class="rule-modal-header">
                <h3>项目管理</h3>
                <button class="rule-modal-close" onclick="DocTypeManager.closeProjectDialog()">&times;</button>
            </div>
            <div class="rule-modal-body">
                <div style="display:flex; gap:8px; align-items:flex-end; margin-bottom:12px;">
                    <div style="flex:1;">
                        <label style="font-size:12px; color:#4F775D;">项目 ID</label>
                        <input id="project-new-id" type="text" placeholder="如：contract_group" class="form-input" style="width:100%;">
                    </div>
                    <div style="flex:1;">
                        <label style="font-size:12px; color:#4F775D;">项目名称</label>
                        <input id="project-new-name" type="text" placeholder="如：合同组" class="form-input" style="width:100%;">
                    </div>
                    <button class="btn btn-primary" onclick="DocTypeManager.createProjectFromForm()">新增/改名</button>
                </div>
                <table class="rule-table">
                    <thead><tr><th>项目 ID</th><th>名称</th><th>类型数</th><th>操作</th></tr></thead>
                    <tbody id="project-table-body"></tbody>
                </table>
            </div>
            <div class="rule-modal-footer">
                <button class="btn btn-ghost" onclick="DocTypeManager.closeProjectDialog()">关闭</button>
            </div>
        </div>
    </div>

    <!-- Type Config Readonly Modal -->
    <div id="typeview-modal-overlay" class="rule-modal-overlay">
        <div class="rule-modal" style="max-width:760px;">
            <div class="rule-modal-header">
                <h3 id="typeview-title">查看配置</h3>
                <button class="rule-modal-close" onclick="DocTypeManager.closeTypeView()">&times;</button>
            </div>
            <div class="rule-modal-body" id="typeview-body" style="max-height:60vh; overflow:auto;"></div>
            <div class="rule-modal-footer">
                <button class="btn btn-ghost" onclick="DocTypeManager.closeTypeView()">关闭</button>
            </div>
        </div>
    </div>
```

- [ ] **Step 3: 浏览器验证(结构)**

刷新 `/ui` → 点「管理」。弹窗应显示:工具条(搜索/scope/项目/项目管理/导入)、新增表单、带复选框表头的表格、底部批量与分页条。表格暂为空(JS 在 Task 11 接管)。点「项目管理」按钮此刻可能报错(JS 未实现)——下个任务补。

- [ ] **Step 4: 提交**

```bash
git add ui/index.html
git commit -m "feat: 重构文档类型管理弹窗并新增项目管理/只读查看子弹窗"
```

---

## Task 11: 管理弹窗 JS(加载/搜索/筛选/分页/选择/批量/提升/查看/项目)

**Files:**
- Modify: `ui/js/doctype.js`(重写管理弹窗相关方法)

- [ ] **Step 1: 替换 `openManageDialog` 并新增管理逻辑**

把 `doctype.js` 里旧的 `renderManageTable` 整个方法删除,`openManageDialog` 替换为下面整块(其余 `closeManageDialog`、复制/导入/导出方法保留):

```javascript
    openManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.add('active');
        this.manage.page = 1;
        this.manage.selected = new Set();
        this.loadProjectsIntoFilters();
        this.loadManage();
    },

    async loadProjectsIntoFilters() {
        try {
            this.projects = await API.getProjects();
        } catch (e) { this.projects = []; }
        const opts = this.projects.map(p =>
            `<option value="${escapeHtml(p.project_id)}">${escapeHtml(p.project_name)} (${p.type_count})</option>`
        ).join('');
        const projFilter = document.getElementById('dt-project');
        if (projFilter) projFilter.innerHTML =
            `<option value="">全部项目</option><option value="__ungrouped__">未分组</option>` + opts;
        const newProj = document.getElementById('doctype-new-project');
        if (newProj) newProj.innerHTML = `<option value="">未分组</option>` + opts;
        const batchProj = document.getElementById('dt-batch-project');
        if (batchProj) batchProj.innerHTML = `<option value="">移动到…(未分组)</option>` + opts;
    },

    async loadManage() {
        const m = this.manage;
        let data;
        try {
            data = await API.listDocTypes({
                q: m.q, scope: m.scope, projectId: m.projectId,
                page: m.page, pageSize: m.pageSize,
            });
        } catch (e) {
            Toast.error('加载失败: ' + e.message);
            return;
        }
        m.items = data.items || [];
        m.total = data.total || 0;
        this.renderManageTable();
    },

    renderManageTable() {
        const m = this.manage;
        const tbody = document.getElementById('doctype-table-body');
        if (!tbody) return;
        tbody.innerHTML = m.items.map(t => {
            const isDef = t.is_default === 1;
            const checked = m.selected.has(t.type_id) ? 'checked' : '';
            const tag = t.is_template === 1
                ? '<span style="color:#8DAA91;font-size:11px;">模板</span>'
                : (isDef ? '<span style="color:#8DAA91;font-size:11px;">默认</span>' : '—');
            const proj = t.project_name ? escapeHtml(t.project_name) : '—';
            const src = t.parent_type_id ? '←' + escapeHtml(t.parent_type_id) : '—';
            const tplBtn = isDef ? '' : (t.is_template === 1
                ? `<button class="btn btn-ghost" onclick="DocTypeManager.demote('${escapeAttr(t.type_id)}')">取消模板</button>`
                : `<button class="btn btn-ghost" onclick="DocTypeManager.promote('${escapeAttr(t.type_id)}')">设为模板</button>`);
            const delBtn = isDef
                ? `<button class="btn btn-ghost" disabled title="默认类型不可删除">删除</button>`
                : `<button class="btn btn-danger" onclick="DocTypeManager.deleteType('${escapeAttr(t.type_id)}')">删除</button>`;
            const checkbox = isDef ? '' :
                `<input type="checkbox" ${checked} onclick="DocTypeManager.toggleSelect('${escapeAttr(t.type_id)}', this.checked)">`;
            return `<tr>
                <td>${checkbox}</td>
                <td><code>${escapeHtml(t.type_id)}</code></td>
                <td>${escapeHtml(t.type_name)}</td>
                <td>${tag}</td>
                <td>${proj}</td>
                <td>${src}</td>
                <td>${t.file_count || 0}</td>
                <td>${t.field_count || 0}</td>
                <td>${t.rule_count || 0}</td>
                <td style="white-space:nowrap;">
                    <button class="btn btn-ghost" onclick="DocTypeManager.viewType('${escapeAttr(t.type_id)}')">查看</button>
                    ${tplBtn}
                    <button class="btn btn-ghost" onclick="DocTypeManager.exportType('${escapeAttr(t.type_id)}')">导出</button>
                    ${delBtn}
                </td>
            </tr>`;
        }).join('');

        const totalPages = Math.max(1, Math.ceil(m.total / m.pageSize));
        document.getElementById('dt-page-info').textContent = `${m.page} / ${totalPages}`;
        document.getElementById('dt-total').textContent = `共 ${m.total} 个`;
        document.getElementById('dt-selected-count').textContent = `已选 ${m.selected.size} 项`;
        const allBox = document.getElementById('dt-check-all');
        if (allBox) allBox.checked = m.items.length > 0 &&
            m.items.filter(t => t.is_default !== 1).every(t => m.selected.has(t.type_id));
    },

    onSearchInput() {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            this.manage.q = document.getElementById('dt-search').value.trim();
            this.manage.page = 1;
            this.loadManage();
        }, 300);
    },

    onFilterChange() {
        this.manage.scope = document.getElementById('dt-scope').value;
        this.manage.projectId = document.getElementById('dt-project').value;
        this.manage.page = 1;
        this.loadManage();
    },

    prevPage() { if (this.manage.page > 1) { this.manage.page--; this.loadManage(); } },
    nextPage() {
        const totalPages = Math.max(1, Math.ceil(this.manage.total / this.manage.pageSize));
        if (this.manage.page < totalPages) { this.manage.page++; this.loadManage(); }
    },

    toggleSelect(typeId, checked) {
        if (checked) this.manage.selected.add(typeId); else this.manage.selected.delete(typeId);
        document.getElementById('dt-selected-count').textContent = `已选 ${this.manage.selected.size} 项`;
    },
    toggleSelectAll(checked) {
        this.manage.items.filter(t => t.is_default !== 1).forEach(t => {
            if (checked) this.manage.selected.add(t.type_id); else this.manage.selected.delete(t.type_id);
        });
        this.renderManageTable();
    },

    async promote(typeId) {
        try { await API.promoteType(typeId); Toast.success('已设为模板'); await this.loadManage(); await this.refresh(); }
        catch (e) { Toast.error('操作失败: ' + e.message); }
    },
    async demote(typeId) {
        try { await API.demoteType(typeId); Toast.success('已取消模板'); await this.loadManage(); await this.refresh(); }
        catch (e) { Toast.error('操作失败: ' + e.message); }
    },

    async batchDelete() {
        const ids = Array.from(this.manage.selected);
        if (ids.length === 0) { Toast.error('未选择任何类型'); return; }
        if (!confirm(`确认删除选中的 ${ids.length} 个类型？有数据的将级联删除，不可撤销！`)) return;
        try {
            const res = await API.batchDeleteTypes(ids, true);
            Toast.success(`批量删除：成功 ${res.deleted}/${ids.length}`);
            const failed = res.results.filter(r => !r.ok);
            if (failed.length) alert('以下未删除：\n' + failed.map(f => `${f.type_id}：${f.reason}`).join('\n'));
            this.manage.selected = new Set();
            await this.loadManage();
            await this.refresh();
        } catch (e) { Toast.error('批量删除失败: ' + e.message); }
    },

    async batchMove() {
        const ids = Array.from(this.manage.selected);
        if (ids.length === 0) { Toast.error('未选择任何类型'); return; }
        const pid = document.getElementById('dt-batch-project').value || null;
        try {
            await API.batchAssignProject(ids, pid);
            Toast.success(`已移动 ${ids.length} 个类型`);
            this.manage.selected = new Set();
            await this.loadProjectsIntoFilters();
            await this.loadManage();
        } catch (e) { Toast.error('移动失败: ' + e.message); }
    },

    async viewType(typeId) {
        try {
            const payload = await API.exportDocType(typeId);
            const fields = (payload.fields || []).map(f =>
                `<li><code>${escapeHtml(f.field_name)}</code> · ${escapeHtml(f.source_type)}${f.search_type ? '/' + escapeHtml(f.search_type) : ''}</li>`
            ).join('') || '<li style="color:#999;">无字段</li>';
            const rules = (payload.rules || []).map(r =>
                `<li><code>${escapeHtml(r.rule_name)}</code> · ${escapeHtml(r.rule_type)}</li>`
            ).join('') || '<li style="color:#999;">无规则</li>';
            document.getElementById('typeview-title').textContent = `查看配置：${payload.type_name || typeId}`;
            document.getElementById('typeview-body').innerHTML =
                `<h4 style="margin:0 0 8px;">字段（${(payload.fields || []).length}）</h4><ul>${fields}</ul>
                 <h4 style="margin:16px 0 8px;">规则（${(payload.rules || []).length}）</h4><ul>${rules}</ul>`;
            document.getElementById('typeview-modal-overlay').classList.add('active');
        } catch (e) { Toast.error('查看失败: ' + e.message); }
    },
    closeTypeView() { document.getElementById('typeview-modal-overlay').classList.remove('active'); },

    // ─── 项目管理子弹窗 ───
    async openProjectDialog() {
        document.getElementById('project-modal-overlay').classList.add('active');
        await this.renderProjectTable();
    },
    closeProjectDialog() {
        document.getElementById('project-modal-overlay').classList.remove('active');
        this.loadProjectsIntoFilters();
    },
    async renderProjectTable() {
        try { this.projects = await API.getProjects(); } catch (e) { this.projects = []; }
        const tbody = document.getElementById('project-table-body');
        tbody.innerHTML = this.projects.map(p => `<tr>
            <td><code>${escapeHtml(p.project_id)}</code></td>
            <td>${escapeHtml(p.project_name)}</td>
            <td>${p.type_count}</td>
            <td><button class="btn btn-danger" onclick="DocTypeManager.deleteProjectById('${escapeAttr(p.project_id)}')">删除</button></td>
        </tr>`).join('') || '<tr><td colspan="4" style="color:#999;">暂无项目</td></tr>';
    },
    async createProjectFromForm() {
        const id = (document.getElementById('project-new-id').value || '').trim();
        const name = (document.getElementById('project-new-name').value || '').trim();
        if (!id || !name) { Toast.error('项目 ID 和名称都是必填'); return; }
        if (!/^[a-zA-Z0-9_-]+$/.test(id)) { Toast.error('项目 ID 只能含英文/数字/_/-'); return; }
        try {
            await API.saveProject({ project_id: id, project_name: name });
            document.getElementById('project-new-id').value = '';
            document.getElementById('project-new-name').value = '';
            Toast.success('项目已保存');
            await this.renderProjectTable();
        } catch (e) { Toast.error('保存失败: ' + e.message); }
    },
    async deleteProjectById(projectId) {
        if (!confirm(`删除项目 "${projectId}"？其下类型将变为未分组（类型本身不删）。`)) return;
        try {
            await API.deleteProject(projectId);
            Toast.success('项目已删除');
            await this.renderProjectTable();
            await this.loadManage();
        } catch (e) { Toast.error('删除失败: ' + e.message); }
    },
```

- [ ] **Step 2: 调整 `createTypeFromForm` 支持归属项目;`deleteType` 走单删后刷新分页**

把 `createTypeFromForm` 内 `await API.saveDocType({ type_id, type_name, enabled: 1 });` 之后补一段归类,并在成功后刷新管理列表;并把 `deleteType` 成功分支的 `await this.refresh()` 改为同时刷新弹窗。具体:

`createTypeFromForm` 中,创建成功后替换收尾:

```javascript
            await API.saveDocType({ type_id, type_name, enabled: 1 });
            const pid = document.getElementById('doctype-new-project')?.value || null;
            if (pid) { try { await API.batchAssignProject([type_id], pid); } catch (e) {} }
            idEl.value = '';
            nameEl.value = '';
            Toast.success('类型已创建');
            await this.loadProjectsIntoFilters();
            await this.loadManage();
            await this.refresh();
```

`deleteType` 需两处改动:

1. 把开头的查找来源从 `this.types` 改为当前分页数据(副本不在 `this.types` 里):

```javascript
        const t = this.manage.items.find(x => x.type_id === typeId);
```

2. 成功分支:把原来的 `await this.refresh(); this.onSelectorChange();` 替换为:

```javascript
            this.manage.selected.delete(typeId);
            await this.loadManage();
            await this.refresh();
```

- [ ] **Step 3: 浏览器全流程验证(关键)**

启动 `python app.py` → `/ui` → 管理:
1. 列表分页显示;搜索框输入过滤;scope 切「模板/副本」过滤;项目下拉过滤
2. 勾选若干 → 底部「已选 N 项」;「批量删除」弹确认并删除;「移动到…」选项目后「移动」生效
3. 行内「设为模板/取消模板」即时生效,顶部选择器随之更新
4. 「查看」弹只读层,列出字段/规则
5. 「项目管理」→ 新建/改名/删除项目;删除后该项目类型变未分组
6. 新增类型可选归属项目
Expected: 均无报错,数据正确。

- [ ] **Step 4: 提交**

```bash
git add ui/js/doctype.js
git commit -m "feat: 管理弹窗支持搜索/筛选/分页/批量/提升/只读查看/项目管理"
```

---

## Task 12: 更新 CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`(Document Type Isolation 一节)

- [ ] **Step 1: 在 Document Type Isolation 段补充**

在该段落末尾追加:

```markdown
- 类型有两个正交维度:**血缘**(`is_template` 标记 + `parent_type_id` 复制来源,`copy_from` 自动记录、`POST /doctype/{id}/promote|demote` 切换模板标记)与**项目**(`doc_type.project_id` → `project` 表,纯算法端分类,一个 type 只属一个项目,不影响文件处理/流水线)。
- `GET /doctype/list` 支持 `q/scope(all|template|copy)/project_id(含 __ungrouped__)/page/page_size/sort`;**传齐 page+page_size 返回 `{items,total}`,否则原样返回数组**(向后兼容)。计数用 3 条 GROUP BY 避免 N+1。
- 批量接口:`POST /doctype/batch_delete`(`{type_ids,force}`)、`POST /doctype/batch_assign_project`(`{type_ids,project_id|null}`)。项目 CRUD:`GET/POST /doctype/projects`、`DELETE /doctype/projects/{id}`(删项目仅解绑成员,不删 type)。
- 顶部选择器只展示模板 + 默认 + 当前选中;副本的搜索/查看/清理在「管理」弹窗内完成。「只读查看配置」复用 `GET /doctype/{id}/export`。
- 存量(本次改动前)副本无 `parent_type_id`/`project_id`,需初期手工标模板、归项目;此后经 `copy_from` 新建的副本自动继承。
```

- [ ] **Step 2: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 补充类型血缘/项目维度与新接口说明"
```

---

## 最终验收

- [ ] 全量后端测试:`uv run pytest -q` 全绿(重点 `tests/test_doctype_management.py`、`tests/test_doctype_migration.py`,且无回归)
- [ ] 浏览器手测 Task 11 Step 3 的 1–6 项全部通过
- [ ] 后端兼容性抽查:`GET /doctype/list`(无参)仍返回数组;旧的 `POST /doctype`、`POST /doctype/{id}/copy_from` 入参不变仍可用
- [ ] 选择器在大量副本下仍简短(只显模板/默认/当前)

## 风险与回滚

- **路由匹配**:新增的 `/projects`、`/batch_delete`、`/batch_assign_project` 均为字面路径,与 `/{type_id}/...` 段数不同,不冲突;promote/demote 为 `/{type_id}/promote` 两段式,与 copy_from 同构。若出现误匹配,确保字面路径路由声明在参数化路由之前。
- **响应形态**:`/doctype/list` 分页与否形态不同,前端 `loadManage` 始终传分页参数,选择器始终用数组路径,二者不混用。
- **回滚**:前端三个提交(api.js/选择器/弹窗)可单独 revert 回旧弹窗;后端新接口为 additive,revert 不影响既有流程;DB 新列/新表保留无害(可不回滚)。
