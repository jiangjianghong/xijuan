# 砍掉「项目」维度 + 文档类型管理 UI 重做 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 彻底移除「项目」维度（含数据库 `doc_type.project_id` 列与 `project` 表），并重做文档类型「管理」弹窗，使任意类型都能「选用」、统一「新建类型」入口（空白/派生/导入）、瘦身表格与底栏。

**Architecture:** 后端先做（模型/Schema/路由一体改 → 启动幂等迁移 DROP → 重写测试跑绿），再做前端（api.js → doctype.js 全量重写 → index.html 弹窗重构 → style.css），最后同步文档并人工验证。「血缘」维度（`is_template` / `parent_type_id`、promote/demote、copy_from 记录来源）**全部保留**；只删「项目」。

**Tech Stack:** FastAPI + 异步 SQLAlchemy(aiomysql) + Pydantic v2；前端原生 JS + Tailwind CDN + 自定义 CSS；pytest-asyncio（需真实 MySQL）。

**两条核心修复（贯穿全程）：**
1. **打破死循环**——「选用」按钮让任意类型（含副本）都能设为当前类型；不再被迫把所有类型标成模板。
2. **统一造类型**——三条路径（空白 / 从类型派生 / 导入 JSON）收进一个「新建类型」弹窗；导入不再用原生 `prompt/confirm/alert`。

**有意的取舍（执行者请知悉，勿当作遗漏）：**
- 规则配置页的「从其他类型复制」（把所选源类型的字段/规则**合并进当前类型**）**保留不动**；同时**新增**管理弹窗里的「复制为新类型 / 从类型派生」（**克隆出新类型**）。两个方向并存，互不替代。
- `dt-scope` 仍是 `<select>`（`CustomSelect` 会把它渲染成 pill），不做成分段控件，刻意缩小改动面。
- 行内「⋯」菜单是向下展开的浮层；管理弹窗 body 是 `overflow:auto`，**最底部行的菜单可能被裁切**。已知小限制，分页 20 条 + 全屏弹窗下极少触发，本计划不处理。

---

## 文件结构（改动总览）

| 文件 | 责任 | 改动 |
|---|---|---|
| `model/tables.py` | ORM | 删 `DocType.project_id` 列、删整个 `Project` 类 |
| `model/schemas.py` | Pydantic | 删 `DocTypeResponse.project_id/project_name`、删 `ProjectCreate/ProjectResponse/BatchAssignProjectRequest` |
| `blue_print/doctype_router.py` | 路由 | 删项目 4 个接口、list 的项目筛选/拼接、copy_from 项目继承、相关 import |
| `service/init_service.py` | 启动迁移 | 停止建列/建表 + 新增幂等 `DROP COLUMN project_id` / `DROP TABLE project` |
| `tests/test_doctype_migration.py` | 测试 | 改为断言血缘列保留、项目列/表已删 |
| `tests/test_doctype_management.py` | 测试 | 删 2 个项目测试、裁剪 2 个测试 |
| `ui/js/api.js` | API 封装 | 删 `getProjects/saveProject/deleteProject/batchAssignProject`、`listDocTypes` 去掉 `projectId` |
| `ui/js/doctype.js` | 管理逻辑 | **全量重写**：删项目相关 + 旧新增表单 + 旧导入入口；**保留**复制弹窗逻辑(openCopyDialog/executeCopy)；加 `selectType`、统一类型表单、新表格、行内菜单 |
| `ui/index.html` | 标记 | 重构管理弹窗；用「类型表单弹窗」替换**项目弹窗**；**保留**复制弹窗与规则页复制按钮 |
| `ui/css/style.css` | 样式 | 新增 表格名称/ID、标签、行内操作、⋯ 菜单样式 |
| `scripts/gen_openapi.py` + `docs/openapi.json` | API 文档 | 删项目相关条目并重新生成 |
| `CLAUDE.md` / `docs/API_DOCUMENTATION.md` | 文档 | 删项目维度描述 |

---

## Task 1: 后端代码移除（tables + schemas + router 一体）

> 三处 import 互相耦合（`doctype_router` import `Project` 与项目 Schema），必须同一提交内一起改，否则中途 `app` 无法 import。本任务**不跑 pytest**（项目相关旧测试要到 Task 3 才修；此处只保证 `app` 能 import）。

**Files:**
- Modify: `model/tables.py`
- Modify: `model/schemas.py`
- Modify: `blue_print/doctype_router.py`

### `model/tables.py`

- [ ] **Step 1：删 `DocType.project_id` 列**

替换：
```python
    is_template: Mapped[int] = mapped_column(TINYINT, default=0)
    parent_type_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[int] = mapped_column(TINYINT, default=1)
```
为：
```python
    is_template: Mapped[int] = mapped_column(TINYINT, default=0)
    parent_type_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[int] = mapped_column(TINYINT, default=1)
```

- [ ] **Step 2：删整个 `Project` 类**

删除这一段（连同上下空行，保留下方 `# ── 1. files 表 ──` 注释）：
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

### `model/schemas.py`

- [ ] **Step 3：`DocTypeResponse` 删项目两字段**

替换：
```python
    is_template: int = 0
    parent_type_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    created_at: Optional[datetime] = None
```
为：
```python
    is_template: int = 0
    parent_type_id: Optional[str] = None
    created_at: Optional[datetime] = None
```

- [ ] **Step 4：删 `ProjectCreate` / `ProjectResponse` / `BatchAssignProjectRequest`**

删除这一整段（保留其后的 `DocTypeBatchDeleteRequest`）：
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


class BatchAssignProjectRequest(BaseModel):
    type_ids: List[str]
    project_id: Optional[str] = None  # None 表示移出项目（未分组）


```

### `blue_print/doctype_router.py`

- [ ] **Step 5：清理 import**

替换：
```python
from sqlalchemy import delete, func, or_, select, update
```
为：
```python
from sqlalchemy import delete, func, or_, select
```

替换 schemas import 块：
```python
from model.schemas import (
    BatchAssignProjectRequest,
    CopyConfigsRequest,
    CopyConfigsResponse,
    DocTypeBatchDeleteRequest,
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
```
为：
```python
from model.schemas import (
    CopyConfigsRequest,
    CopyConfigsResponse,
    DocTypeBatchDeleteRequest,
    DocTypeCreate,
    DocTypeResponse,
    ExportFieldItem,
    ExportPayload,
    ExportRuleItem,
    ImportConfigsRequest,
    ImportConfigsResponse,
    ResponseWrapper,
)
```

替换 tables import 块（删 `Project,`）：
```python
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
为：
```python
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
)
```

- [ ] **Step 6：`list_doctypes` 删 project 查询参数**

替换：
```python
    scope: str = Query("all", pattern=r"^(all|template|copy)$"),
    project_id: Optional[str] = Query(None, description="项目过滤；__ungrouped__ 表示未分组"),
    page: Optional[int] = Query(None, ge=1),
```
为：
```python
    scope: str = Query("all", pattern=r"^(all|template|copy)$"),
    page: Optional[int] = Query(None, ge=1),
```

替换 docstring 行：
```python
    - q/scope/project_id/sort 仅在传入时生效
```
为：
```python
    - q/scope/sort 仅在传入时生效
```

- [ ] **Step 7：`list_doctypes` 删 project 过滤块**

替换：
```python
    elif scope == "copy":
        base = base.where(DocType.is_template == 0, DocType.is_default == 0)
    if project_id == "__ungrouped__":
        base = base.where(DocType.project_id.is_(None))
    elif project_id:
        base = base.where(DocType.project_id == project_id)

    total = (
```
为：
```python
    elif scope == "copy":
        base = base.where(DocType.is_template == 0, DocType.is_default == 0)

    total = (
```

- [ ] **Step 8：`list_doctypes` 删项目名映射块**

替换：
```python
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
```
为：
```python
            target.update({r[0]: r[1] for r in rows})

    items = []
```

- [ ] **Step 9：`list_doctypes` DocTypeResponse 去掉项目字段**

替换：
```python
                    is_template=t.is_template,
                    parent_type_id=t.parent_type_id,
                    project_id=t.project_id,
                    project_name=proj_name_map.get(t.project_id),
                    created_at=t.created_at,
```
为：
```python
                    is_template=t.is_template,
                    parent_type_id=t.parent_type_id,
                    created_at=t.created_at,
```

- [ ] **Step 10：`copy_from` 删项目继承（保留 parent_type_id 血缘）**

替换：
```python
    # 记录血缘：目标由源复制而来（默认类型不 reparent）
    if target.is_default != 1:
        target.parent_type_id = req.source_type_id
        # 目标未分组时继承源项目；已分组不覆盖
        if target.project_id is None and source.project_id is not None:
            target.project_id = source.project_id

    await db.commit()
```
为：
```python
    # 记录血缘：目标由源复制而来（默认类型不 reparent）
    if target.is_default != 1:
        target.parent_type_id = req.source_type_id

    await db.commit()
```

- [ ] **Step 11：删除整个「项目」接口区块**

删除从注释分隔线到 `batch_assign_project` 结束的整段（即 `list_projects` / `upsert_project` / `delete_project` / `batch_assign_project` 四个函数及其上方注释块），保留其后的 `promote_type` / `demote_type`。删除区间是：
```python
# ─────────────────────────────────────────────────────────────
# 项目（纯算法端分类，一个 type 只属一个项目）
# ─────────────────────────────────────────────────────────────


@router.get("/projects", response_model=ResponseWrapper)
async def list_projects(db: AsyncSession = Depends(get_db)):
    ...
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
> 注：上面用省略号示意起止；执行时删掉这两条分隔注释之间、直到 `batch_assign_project` 函数 `return` 结束为止的**全部**内容（4 个函数）。`promote_type`（`@router.post("/{type_id}/promote"...)`）及其后保持不动。

- [ ] **Step 12：确认 `app` 可 import（不跑全套测试）**

Run: `uv run python -c "import app; print('ok')"`
Expected: 打印 `ok`，无 `ImportError` / `NameError`。

- [ ] **Step 13：Commit**

```bash
git add model/tables.py model/schemas.py blue_print/doctype_router.py
git commit -m "refactor: 移除文档类型的项目维度后端代码(ORM/Schema/路由)"
```

---

## Task 2: 启动迁移——回收 `project_id` 列与 `project` 表

**Files:**
- Modify: `service/init_service.py`

- [ ] **Step 1：从「补列」迁移清单删掉 project_id**

替换：
```python
            ("doc_type", "is_template", "TINYINT NOT NULL DEFAULT 0"),
            ("doc_type", "parent_type_id", "VARCHAR(64) NULL"),
            ("doc_type", "project_id", "VARCHAR(64) NULL"),
        ]
```
为：
```python
            ("doc_type", "is_template", "TINYINT NOT NULL DEFAULT 0"),
            ("doc_type", "parent_type_id", "VARCHAR(64) NULL"),
        ]
```

- [ ] **Step 2：从索引迁移清单删掉 project_id 索引**

替换：
```python
            ("doc_type", "ix_doc_type_parent_type_id", "parent_type_id"),
            ("doc_type", "ix_doc_type_project_id", "project_id"),
        ]
```
为：
```python
            ("doc_type", "ix_doc_type_parent_type_id", "parent_type_id"),
        ]
```

- [ ] **Step 3：新增幂等 DROP 块**

在「回填 default 类型」注释之前插入（即替换下面的锚点）：

替换：
```python
        # 回填 default 类型（兼容老数据：旧列可能仍为 NULL/空串）
        await conn.execute(
            text("UPDATE files SET type_id = 'default' WHERE type_id IS NULL OR type_id = ''")
        )
```
为：
```python
        # 「项目」维度已废弃：彻底回收残留的 doc_type.project_id 列与 project 表
        # （新库因 ORM 已无对应模型，本就不会创建；此处兜底删除存量库的遗留对象）。
        result = await conn.execute(
            text("SELECT COUNT(*) FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                 "AND COLUMN_NAME = 'project_id'")
        )
        if result.scalar() == 1:
            # 删除列会一并删除其上的 ix_doc_type_project_id 索引
            await conn.execute(text("ALTER TABLE `doc_type` DROP COLUMN `project_id`"))
            logger.info("已删除 doc_type.project_id 列（项目维度废弃）")

        result = await conn.execute(
            text("SELECT COUNT(*) FROM information_schema.TABLES "
                 "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'")
        )
        if result.scalar() == 1:
            await conn.execute(text("DROP TABLE `project`"))
            logger.info("已删除 project 表（项目维度废弃）")

        # 回填 default 类型（兼容老数据：旧列可能仍为 NULL/空串）
        await conn.execute(
            text("UPDATE files SET type_id = 'default' WHERE type_id IS NULL OR type_id = ''")
        )
```

- [ ] **Step 4：Commit**

```bash
git add service/init_service.py
git commit -m "feat: 启动迁移幂等删除 doc_type.project_id 列与 project 表"
```

---

## Task 3: 重写后端测试并跑绿

**Files:**
- Rewrite: `tests/test_doctype_migration.py`
- Rewrite: `tests/test_doctype_management.py`

- [ ] **Step 1：重写 `tests/test_doctype_migration.py`（整文件覆盖）**

```python
"""doc_type 血缘列保留、项目维度（project_id 列 + project 表）已彻底移除的迁移验证。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from model.database import get_engine
from service.init_service import init_database


@pytest.mark.anyio
async def test_lineage_columns_kept_and_project_dropped():
    await init_database()  # 幂等：建表 + 补列 + 回收项目维度
    engine = get_engine()
    async with engine.connect() as conn:
        # 血缘维度保留
        for col in ("is_template", "parent_type_id"):
            r = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                    "AND COLUMN_NAME = :c"
                ),
                {"c": col},
            )
            assert r.scalar() == 1, f"doc_type 应保留列 {col}"

        # 项目维度移除：列
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                "AND COLUMN_NAME = 'project_id'"
            )
        )
        assert r.scalar() == 0, "doc_type.project_id 应已删除"

        # 项目维度移除：表
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            )
        )
        assert r.scalar() == 0, "project 表应已删除"
```

- [ ] **Step 2：重写 `tests/test_doctype_management.py`（整文件覆盖）**

> 删除 `test_project_crud_and_unbind`、`test_batch_assign_and_project_delete_unbinds`；裁剪 `test_list_paginated_shape_and_filters`（去掉 `project_id` 断言）；把 `test_copy_from_records_parent_and_inherits_project` 改为只验血缘。其余保留。

```python
"""文档类型管理：list 分页/筛选 / 批量删除 / 血缘（copy_from 记录来源、promote/demote）。

项目维度已彻底移除，相关测试一并删除。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_list_backward_compatible_array(client: AsyncClient):
    """不传参数：返回数组（向后兼容）。"""
    r = await client.get("/doctype/list")
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)


@pytest.mark.anyio
async def test_list_paginated_shape_and_filters(client: AsyncClient):
    # 造一个普通类型（is_template=0, is_default=0），代表"副本/普通"
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
        assert "project_id" not in items[0]

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


@pytest.mark.anyio
async def test_copy_from_records_parent(client: AsyncClient):
    await client.post("/doctype", json={"type_id": "dm_src", "type_name": "源"})
    await client.post("/doctype", json={"type_id": "dm_tgt", "type_name": "目标"})
    try:
        r = await client.post("/doctype/dm_tgt/copy_from", json={"source_type_id": "dm_src"})
        assert r.status_code == 200
        r = await client.get("/doctype/list?q=dm_tgt&page=1&page_size=10")
        item = r.json()["data"]["items"][0]
        assert item["parent_type_id"] == "dm_src"
    finally:
        await client.delete("/doctype/dm_src?force=true")
        await client.delete("/doctype/dm_tgt?force=true")


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

- [ ] **Step 3：跑全套测试（需真实 MySQL 连接）**

Run: `uv run pytest -q`
Expected: 全绿。重点确认 `tests/test_doctype_migration.py` 与 `tests/test_doctype_management.py` 通过，且无残留对 `/doctype/projects` / `batch_assign_project` 的引用导致的失败。

> 若本机无 MySQL，至少 Run: `uv run pytest tests/test_doctype_management.py tests/test_doctype_migration.py -q` 并在有数据库的环境复跑。

- [ ] **Step 4：Commit**

```bash
git add tests/test_doctype_migration.py tests/test_doctype_management.py
git commit -m "test: 文档类型测试改为只覆盖血缘/分页,移除项目维度用例"
```

---

## Task 4: 前端 API 封装去项目

**Files:**
- Modify: `ui/js/api.js`

- [ ] **Step 1：`listDocTypes` 去掉 `projectId` 参数**

替换：
```javascript
        if (params.scope && params.scope !== 'all') qs.set('scope', params.scope);
        if (params.projectId) qs.set('project_id', params.projectId);
        if (params.page) qs.set('page', params.page);
```
为：
```javascript
        if (params.scope && params.scope !== 'all') qs.set('scope', params.scope);
        if (params.page) qs.set('page', params.page);
```

- [ ] **Step 2：删 4 个项目 API 方法**

删除整段（保留其前的 `batchDeleteTypes` 与其后的 `saveDocType`）：
```javascript
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

- [ ] **Step 3：Commit**

```bash
git add ui/js/api.js
git commit -m "refactor: api.js 移除项目相关方法与 projectId 参数"
```

---

## Task 5: 重写 `ui/js/doctype.js`

**Files:**
- Rewrite: `ui/js/doctype.js`（整文件覆盖）

- [ ] **Step 1：整文件替换为以下内容**

```javascript
/**
 * 文档类型管理模块
 *
 * - 顶部下拉切换当前类型，localStorage 持久化
 * - 管理弹窗：搜索 / 范围筛选 / 分页；选用；新建（空白/派生/导入）；改名/导出/删除/模板标记
 *
 * 切换/选用类型后，刷新文件列表与字段/规则列表。
 */
const DocTypeManager = {
    selectorTypes: [],     // 选择器用（模板 + 默认）
    _currentName: '',      // 当前类型显示名（选用时记录，供选择器回显非模板类型）
    manage: {
        items: [], total: 0, page: 1, pageSize: 20,
        q: '', scope: 'all', selected: new Set(), menuOpenId: null,
    },
    typeForm: { mode: 'create' },
    _importPayload: null,

    async init() {
        await this.refresh();
        const sel = document.getElementById('doctype-selector');
        if (sel) sel.addEventListener('change', () => this.onSelectorChange());
        // 点击空白处关闭行内 ⋯ 菜单
        document.addEventListener('click', (e) => {
            if (this.manage.menuOpenId && !e.target.closest('.dt-menu')) this.closeRowMenu();
        });
    },

    async refresh() {
        try {
            this.selectorTypes = await API.listDocTypes({ scope: 'template' });
        } catch (e) {
            console.error('加载文档类型失败:', e);
            this.selectorTypes = [{ type_id: 'default', type_name: '默认类型', is_default: 1, file_count: 0 }];
        }
        this.renderSelector();
    },

    renderSelector() {
        const sel = document.getElementById('doctype-selector');
        if (!sel) return;
        const current = API.getCurrentTypeId();
        const list = (this.selectorTypes || []).slice();
        if (current && !list.some(t => t.type_id === current)) {
            list.push({ type_id: current, type_name: this._currentName || current, file_count: 0 });
        }
        sel.innerHTML = list.map(t =>
            `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (${t.file_count || 0})</option>`
        ).join('');
        const exists = list.some(t => t.type_id === current);
        sel.value = exists ? current : 'default';
        if (!exists) API.setCurrentTypeId('default');
    },

    async onSelectorChange() {
        const sel = document.getElementById('doctype-selector');
        if (!sel) return;
        API.setCurrentTypeId(sel.value);
        this._currentName = sel.options[sel.selectedIndex]?.text || sel.value;
        this._reloadCurrentType();
        Toast.success('已切换文档类型: ' + sel.options[sel.selectedIndex].text);
    },

    // 选用/切换当前类型后，刷新依赖「当前类型」的页面区域
    _reloadCurrentType() {
        if (typeof App !== 'undefined' && App.loadFileList) {
            try { App.loadFileList(); } catch (e) { console.warn(e); }
        }
        if (typeof RuleConfig !== 'undefined') {
            try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) { console.warn(e); }
            try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) { console.warn(e); }
        }
    },

    // ─── 管理弹窗 ───

    openManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.add('active');
        this.manage.page = 1;
        this.manage.selected = new Set();
        this.manage.menuOpenId = null;
        this.loadManage();
    },
    closeManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.remove('active');
    },

    async loadManage() {
        const m = this.manage;
        let data;
        try {
            data = await API.listDocTypes({ q: m.q, scope: m.scope, page: m.page, pageSize: m.pageSize });
        } catch (e) {
            Toast.error('加载失败: ' + e.message);
            return;
        }
        m.items = data.items || [];
        m.total = data.total || 0;
        this.renderManageTable();
    },

    // type_id -> type_name（当前页 + 选择器里的模板/默认），用于「来源」列显示名称
    _nameMap() {
        const map = {};
        (this.selectorTypes || []).forEach(t => { map[t.type_id] = t.type_name; });
        (this.manage.items || []).forEach(t => { map[t.type_id] = t.type_name; });
        return map;
    },

    renderManageTable() {
        const m = this.manage;
        const tbody = document.getElementById('doctype-table-body');
        if (!tbody) return;
        const nameMap = this._nameMap();
        tbody.innerHTML = m.items.map(t => {
            const isDef = t.is_default === 1;
            const isTpl = t.is_template === 1;
            const checked = m.selected.has(t.type_id) ? 'checked' : '';
            let tag;
            if (isDef) tag = '<span class="dt-tag dt-tag-default">默认</span>';
            else if (isTpl) tag = '<span class="dt-tag dt-tag-template">模板</span>';
            else if (t.parent_type_id) tag = '<span class="dt-tag dt-tag-copy">副本</span>';
            else tag = '<span class="dt-tag dt-tag-plain">普通</span>';
            const src = t.parent_type_id
                ? '←' + escapeHtml(nameMap[t.parent_type_id] || t.parent_type_id)
                : '—';
            const checkbox = isDef ? '' :
                `<input type="checkbox" ${checked} onclick="DocTypeManager.toggleSelect('${escapeAttr(t.type_id)}', this.checked)">`;
            const tplItem = isDef ? '' : (isTpl
                ? `<button onclick="DocTypeManager.demote('${escapeAttr(t.type_id)}')">取消模板</button>`
                : `<button onclick="DocTypeManager.promote('${escapeAttr(t.type_id)}')">设为模板</button>`);
            const renameItem = isDef ? '' :
                `<button onclick="DocTypeManager.openRenameForm('${escapeAttr(t.type_id)}')">改名</button>`;
            const delItem = isDef
                ? `<button disabled title="默认类型不可删除">删除</button>`
                : `<button class="danger" onclick="DocTypeManager.deleteType('${escapeAttr(t.type_id)}')">删除</button>`;
            const menuOpen = m.menuOpenId === t.type_id;
            return `<tr>
                <td>${checkbox}</td>
                <td>
                    <div class="dt-name">${escapeHtml(t.type_name)}</div>
                    <div class="dt-id-sub"><code>${escapeHtml(t.type_id)}</code></div>
                </td>
                <td>${tag}</td>
                <td>${src}</td>
                <td>${t.file_count || 0}</td>
                <td>${t.field_count || 0}</td>
                <td>${t.rule_count || 0}</td>
                <td class="dt-actions">
                    <button class="btn btn-secondary btn-sm" onclick="DocTypeManager.selectType('${escapeAttr(t.type_id)}')">选用</button>
                    <span class="dt-menu">
                        <button class="btn btn-ghost btn-sm" onclick="DocTypeManager.toggleRowMenu('${escapeAttr(t.type_id)}')">⋯</button>
                        <div class="dt-menu-pop" style="display:${menuOpen ? 'block' : 'none'};">
                            <button onclick="DocTypeManager.viewType('${escapeAttr(t.type_id)}')">查看配置</button>
                            <button onclick="DocTypeManager.openDeriveForm('${escapeAttr(t.type_id)}')">复制为新类型</button>
                            ${renameItem}
                            ${tplItem}
                            <button onclick="DocTypeManager.exportType('${escapeAttr(t.type_id)}')">导出</button>
                            ${delItem}
                        </div>
                    </span>
                </td>
            </tr>`;
        }).join('');

        const totalPages = Math.max(1, Math.ceil(m.total / m.pageSize));
        document.getElementById('dt-page-info').textContent = `${m.page} / ${totalPages}`;
        document.getElementById('dt-total').textContent = `共 ${m.total} 个`;
        this._renderBatchBar();
        const allBox = document.getElementById('dt-check-all');
        if (allBox) allBox.checked = m.items.length > 0 &&
            m.items.filter(t => t.is_default !== 1).every(t => m.selected.has(t.type_id));
    },

    _renderBatchBar() {
        const n = this.manage.selected.size;
        const bar = document.getElementById('dt-batch-bar');
        if (bar) bar.style.display = n > 0 ? 'flex' : 'none';
        const cnt = document.getElementById('dt-selected-count');
        if (cnt) cnt.textContent = `已选 ${n} 项`;
    },

    onSearchInput() {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            this.manage.q = document.getElementById('dt-search').value.trim();
            this.manage.page = 1;
            this.loadManage();
        }, 300);
    },
    onScopeChange() {
        this.manage.scope = document.getElementById('dt-scope').value;
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
        this._renderBatchBar();
    },
    toggleSelectAll(checked) {
        this.manage.items.filter(t => t.is_default !== 1).forEach(t => {
            if (checked) this.manage.selected.add(t.type_id); else this.manage.selected.delete(t.type_id);
        });
        this.renderManageTable();
    },

    toggleRowMenu(typeId) {
        this.manage.menuOpenId = (this.manage.menuOpenId === typeId) ? null : typeId;
        this.renderManageTable();
    },
    closeRowMenu() {
        if (this.manage.menuOpenId !== null) {
            this.manage.menuOpenId = null;
            this.renderManageTable();
        }
    },

    // 选用 = 设为当前类型（任何类型都可，打破"只有模板能用"的死循环）
    async selectType(typeId) {
        const t = this.manage.items.find(x => x.type_id === typeId);
        this._currentName = t ? t.type_name : typeId;
        API.setCurrentTypeId(typeId);
        await this.refresh();
        this._reloadCurrentType();
        Toast.success('已选用文档类型：' + this._currentName);
        this.closeManageDialog();
    },

    async promote(typeId) {
        this.closeRowMenu();
        try { await API.promoteType(typeId); Toast.success('已设为模板'); await this.loadManage(); await this.refresh(); }
        catch (e) { Toast.error('操作失败: ' + e.message); }
    },
    async demote(typeId) {
        this.closeRowMenu();
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

    async deleteType(typeId) {
        this.closeRowMenu();
        const t = this.manage.items.find(x => x.type_id === typeId);
        if (!t) return;
        const hasData = (t.file_count || 0) + (t.field_count || 0) + (t.rule_count || 0) > 0;
        let force = false;
        if (hasData) {
            if (!confirm(`类型 "${t.type_name}" 下有 ${t.file_count} 个文件、${t.field_count} 个字段、${t.rule_count} 条规则。\n\n确认级联删除全部数据？此操作不可撤销！`)) return;
            force = true;
        } else {
            if (!confirm(`确认删除类型 "${t.type_name}"？`)) return;
        }
        try {
            await API.deleteDocType(typeId, force);
            Toast.success('类型已删除');
            if (API.getCurrentTypeId() === typeId) API.setCurrentTypeId('default');
            this.manage.selected.delete(typeId);
            await this.loadManage();
            await this.refresh();
        } catch (e) { Toast.error('删除失败: ' + e.message); }
    },

    async viewType(typeId) {
        this.closeRowMenu();
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

    async exportType(typeId) {
        this.closeRowMenu();
        try {
            const payload = await API.exportDocType(typeId);
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `doctype_${typeId}_${new Date().toISOString().slice(0,10)}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            Toast.success(`已导出 ${payload.fields.length} 个字段、${payload.rules.length} 条规则`);
        } catch (e) { Toast.error('导出失败: ' + e.message); }
    },

    // ─── 从其他类型复制配置（灌入当前类型；与「派生新类型」方向相反，二者并存） ───

    openCopyDialog() {
        const current = API.getCurrentTypeId();
        const pool = this.selectorTypes || [];
        const currentType = pool.find(t => t.type_id === current);
        document.getElementById('copy-target-display').value =
            currentType ? `${currentType.type_name} (${currentType.type_id})` : current;

        const candidates = pool.filter(t => t.type_id !== current);
        if (candidates.length === 0) { Toast.error('没有其他文档类型可供复制'); return; }
        const sourceSel = document.getElementById('copy-source-select');
        sourceSel.innerHTML = candidates.map(t =>
            `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (字段 ${t.field_count || 0}, 规则 ${t.rule_count || 0})</option>`
        ).join('');

        document.getElementById('copy-fields-flag').checked = true;
        document.getElementById('copy-rules-flag').checked = true;
        document.getElementById('copy-conflict').value = 'rename';
        this.onSourceTypeChange();
        document.getElementById('copy-modal-overlay').classList.add('active');
    },
    closeCopyDialog() { document.getElementById('copy-modal-overlay').classList.remove('active'); },

    onSourceTypeChange() {
        const tid = document.getElementById('copy-source-select').value;
        const t = (this.selectorTypes || []).find(x => x.type_id === tid);
        const summary = document.getElementById('copy-source-summary');
        summary.textContent = t
            ? `源类型 "${t.type_name}" 当前有 ${t.field_count || 0} 个字段、${t.rule_count || 0} 条规则。复制后两份完全独立，目标类型修改不会影响源。`
            : '';
    },

    async executeCopy() {
        const target = API.getCurrentTypeId();
        const source = document.getElementById('copy-source-select').value;
        const copyFields = document.getElementById('copy-fields-flag').checked;
        const copyRules = document.getElementById('copy-rules-flag').checked;
        const onConflict = document.getElementById('copy-conflict').value;

        if (!source) { Toast.error('请选择源类型'); return; }
        if (!copyFields && !copyRules) { Toast.error('请至少勾选一种内容'); return; }

        const payload = { source_type_id: source, on_conflict: onConflict };
        if (!copyFields) payload.field_ids = [];   // 留空=全部；显式 [] = 一个不选
        if (!copyRules) payload.rule_ids = [];

        try {
            const result = await API.copyConfigs(target, payload);
            Toast.success(`复制完成：字段 ${result.copied_fields} 复制 / ${result.skipped_fields} 跳过；规则 ${result.copied_rules} 复制 / ${result.skipped_rules} 跳过`);
            if (result.missing_dependencies && result.missing_dependencies.length > 0) {
                alert('部分规则的依赖字段在目标类型中不存在，已自动剔除：\n' + result.missing_dependencies.join('\n'));
            }
            this.closeCopyDialog();
            await this.refresh();
            if (typeof RuleConfig !== 'undefined') {
                try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) {}
                try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) {}
            }
        } catch (e) { Toast.error('复制失败: ' + e.message); }
    },

    // ─── 类型表单：新建（空白/派生/导入）/ 改名 ───

    openCreateForm() { this._openTypeForm({ mode: 'create', sourceMode: 'blank' }); },
    openDeriveForm(sourceId) { this._openTypeForm({ mode: 'create', sourceMode: 'derive', sourceId }); },
    openRenameForm(typeId) {
        const t = this.manage.items.find(x => x.type_id === typeId);
        this._openTypeForm({ mode: 'edit', typeId, typeName: t ? t.type_name : '' });
    },

    _openTypeForm({ mode, sourceMode, sourceId, typeId, typeName }) {
        this.closeRowMenu();
        this.typeForm = { mode };
        this._importPayload = null;
        const isEdit = mode === 'edit';
        document.getElementById('typeform-title').textContent = isEdit ? '重命名类型' : '新建文档类型';
        const idEl = document.getElementById('typeform-id');
        const nameEl = document.getElementById('typeform-name');
        idEl.value = isEdit ? typeId : '';
        idEl.disabled = isEdit;
        nameEl.value = isEdit ? (typeName || '') : '';

        // 来源区仅新建可见
        document.getElementById('typeform-source-block').style.display = isEdit ? 'none' : '';
        document.getElementById('typeform-derive-block').style.display = 'none';
        document.getElementById('typeform-import-block').style.display = 'none';
        document.getElementById('typeform-conflict-block').style.display = 'none';

        if (!isEdit) {
            // 源类型下拉：当前页全部类型（含默认/模板），排除将要新建的（提交时再校验不等于自身）
            const pool = this.manage.items.length ? this.manage.items : this.selectorTypes;
            const srcSel = document.getElementById('typeform-source-type');
            srcSel.innerHTML = pool.map(t =>
                `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (${escapeHtml(t.type_id)})</option>`
            ).join('');
            document.getElementById('typeform-source-mode').value = sourceMode || 'blank';
            if (sourceId) srcSel.value = sourceId;
            document.getElementById('typeform-copy-fields').checked = true;
            document.getElementById('typeform-copy-rules').checked = true;
            document.getElementById('typeform-conflict').value = 'rename';
            document.getElementById('typeform-import-file').value = '';
            document.getElementById('typeform-import-hint').textContent = '';
            this.onCreateSourceModeChange();
        }
        document.getElementById('typeform-modal-overlay').classList.add('active');
    },
    closeTypeForm() { document.getElementById('typeform-modal-overlay').classList.remove('active'); },

    onCreateSourceModeChange() {
        const mode = document.getElementById('typeform-source-mode').value;
        document.getElementById('typeform-derive-block').style.display = mode === 'derive' ? '' : 'none';
        document.getElementById('typeform-import-block').style.display = mode === 'import' ? '' : 'none';
        document.getElementById('typeform-conflict-block').style.display = (mode === 'derive' || mode === 'import') ? '' : 'none';
    },

    async submitTypeForm() {
        const mode = this.typeForm.mode;
        const id = (document.getElementById('typeform-id').value || '').trim();
        const name = (document.getElementById('typeform-name').value || '').trim();
        if (!name) { Toast.error('类型名称必填'); return; }

        // 改名：仅 upsert 名称
        if (mode === 'edit') {
            try {
                await API.saveDocType({ type_id: id, type_name: name, enabled: 1 });
                Toast.success('已重命名');
                this.closeTypeForm();
                await this.loadManage();
                await this.refresh();
            } catch (e) { Toast.error('保存失败: ' + e.message); }
            return;
        }

        // 新建
        if (!/^[a-zA-Z0-9_-]+$/.test(id)) { Toast.error('类型 ID 只能包含英文/数字/_/-'); return; }
        const sourceMode = document.getElementById('typeform-source-mode').value;
        const onConflict = document.getElementById('typeform-conflict').value;

        let importPayload = null;
        if (sourceMode === 'import') {
            const file = document.getElementById('typeform-import-file').files[0];
            if (!file) { Toast.error('请选择 JSON 文件'); return; }
            try {
                importPayload = JSON.parse(await file.text());
            } catch (e) { Toast.error('JSON 解析失败: ' + e.message); return; }
            if (!importPayload || !importPayload.type_id) { Toast.error('无效的导出文件：缺少 type_id'); return; }
        }
        let source = null;
        if (sourceMode === 'derive') {
            source = document.getElementById('typeform-source-type').value;
            if (!source) { Toast.error('请选择源类型'); return; }
            if (source === id) { Toast.error('源类型不能是自己'); return; }
        }

        try {
            // 1. 建空白类型（带目标名称；upsert 语义）
            await API.saveDocType({ type_id: id, type_name: name, enabled: 1 });

            // 2. 按来源灌配置
            if (sourceMode === 'derive') {
                const payload = { source_type_id: source, on_conflict: onConflict };
                if (!document.getElementById('typeform-copy-fields').checked) payload.field_ids = [];
                if (!document.getElementById('typeform-copy-rules').checked) payload.rule_ids = [];
                const r = await API.copyConfigs(id, payload);
                Toast.success(`已派生：字段 ${r.copied_fields} / 规则 ${r.copied_rules}`);
                if (r.missing_dependencies && r.missing_dependencies.length)
                    alert('部分规则依赖字段缺失，已剔除：\n' + r.missing_dependencies.join('\n'));
            } else if (sourceMode === 'import') {
                const r = await API.importDocType(importPayload, { targetTypeId: id, createTypeIfMissing: false, onConflict });
                Toast.success(`已导入：字段 ${r.copied_fields} / 规则 ${r.copied_rules}`);
                if (r.missing_dependencies && r.missing_dependencies.length)
                    alert('部分规则依赖字段缺失，已剔除：\n' + r.missing_dependencies.join('\n'));
            } else {
                Toast.success('类型已创建');
            }

            this.closeTypeForm();
            await this.loadManage();
            await this.refresh();
        } catch (e) { Toast.error('创建失败: ' + e.message); }
    },
};

// HTML 转义辅助
function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) {
    return escapeHtml(s).replace(/`/g, '&#96;');
}
```

- [ ] **Step 2：Commit**

```bash
git add ui/js/doctype.js
git commit -m "feat: 重写 doctype.js — 选用/统一新建/行内菜单,移除项目逻辑"
```

---

## Task 6: 重构 `ui/index.html` 弹窗与按钮

**Files:**
- Modify: `ui/index.html`

- [ ] **Step 1：整体替换「管理弹窗」（去项目筛选/项目列/项目管理/旧新增表单/底部移动；规则页复制按钮保留不动）**

替换从 `<!-- Doc Type Manage Modal -->` 到该弹窗 `</div>` 结束（原 519–593 行整块）为：
```html
    <!-- Doc Type Manage Modal -->
    <div id="doctype-modal-overlay" class="rule-modal-overlay">
        <div class="rule-modal">
            <div class="rule-modal-header">
                <h3>文档类型管理</h3>
                <button class="rule-modal-close" onclick="DocTypeManager.closeManageDialog()">&times;</button>
            </div>
            <div class="rule-modal-body">
                <!-- 工具条：搜索 + 范围 + 新建 -->
                <div style="display:flex; gap:8px; align-items:center; margin-bottom:12px;">
                    <input id="dt-search" type="text" placeholder="搜索 类型ID / 名称" class="form-input" style="width:240px;"
                           oninput="DocTypeManager.onSearchInput()">
                    <select id="dt-scope" class="form-select" style="width:120px;" onchange="DocTypeManager.onScopeChange()">
                        <option value="all">全部</option>
                        <option value="template">★ 模板</option>
                        <option value="copy">副本</option>
                    </select>
                    <div style="flex:1;"></div>
                    <button class="btn btn-primary" onclick="DocTypeManager.openCreateForm()">+ 新建类型</button>
                </div>
                <!-- 表格 -->
                <table class="rule-table dt-table">
                    <thead>
                        <tr>
                            <th style="width:32px;"><input type="checkbox" id="dt-check-all" onclick="DocTypeManager.toggleSelectAll(this.checked)"></th>
                            <th>名称 / ID</th>
                            <th style="width:72px;">标签</th>
                            <th style="width:160px;">来源</th>
                            <th style="width:56px;">文件</th>
                            <th style="width:56px;">字段</th>
                            <th style="width:56px;">规则</th>
                            <th style="width:170px;">操作</th>
                        </tr>
                    </thead>
                    <tbody id="doctype-table-body"></tbody>
                </table>
            </div>
            <div class="rule-modal-footer" style="display:flex; align-items:center; gap:12px;">
                <div id="dt-batch-bar" style="display:none; align-items:center; gap:12px;">
                    <span id="dt-selected-count" style="font-size:12px; color:#4F775D;">已选 0 项</span>
                    <button class="btn btn-danger" onclick="DocTypeManager.batchDelete()">批量删除</button>
                </div>
                <div style="flex:1;"></div>
                <button class="btn btn-ghost" onclick="DocTypeManager.prevPage()">◀</button>
                <span id="dt-page-info" style="font-size:12px; color:#4F775D;">1 / 1</span>
                <button class="btn btn-ghost" onclick="DocTypeManager.nextPage()">▶</button>
                <span id="dt-total" style="font-size:12px; color:#4F775D;">共 0 个</span>
            </div>
        </div>
    </div>
```

- [ ] **Step 2：用「类型表单弹窗」替换「项目弹窗」（复制弹窗保留不动）**

替换从 `<!-- Project Manage Modal -->` 到该项目弹窗 `</div>` 结束（原 634–662 行单块）为：
```html
    <!-- Doc Type Form Modal (create: blank/derive/import; edit: rename) -->
    <div id="typeform-modal-overlay" class="rule-modal-overlay">
        <div class="rule-modal" style="max-width:640px;">
            <div class="rule-modal-header">
                <h3 id="typeform-title">新建文档类型</h3>
                <button class="rule-modal-close" onclick="DocTypeManager.closeTypeForm()">&times;</button>
            </div>
            <div class="rule-modal-body">
                <div style="margin-bottom:12px;">
                    <label class="dt-form-label">类型 ID</label>
                    <input id="typeform-id" type="text" placeholder="如：contract" class="form-input" style="width:100%;">
                </div>
                <div style="margin-bottom:12px;">
                    <label class="dt-form-label">类型名称</label>
                    <input id="typeform-name" type="text" placeholder="如：采购合同" class="form-input" style="width:100%;">
                </div>
                <!-- 来源（仅新建可见） -->
                <div id="typeform-source-block" style="margin-bottom:12px;">
                    <label class="dt-form-label">初始配置来源</label>
                    <select id="typeform-source-mode" class="form-select" style="width:100%;" onchange="DocTypeManager.onCreateSourceModeChange()">
                        <option value="blank">空白（不带任何字段/规则）</option>
                        <option value="derive">从已有类型派生（复制字段+规则）</option>
                        <option value="import">从 JSON 文件导入</option>
                    </select>
                </div>
                <!-- 派生选项 -->
                <div id="typeform-derive-block" style="display:none;">
                    <div style="margin-bottom:12px;">
                        <label class="dt-form-label">源类型</label>
                        <select id="typeform-source-type" class="form-select" style="width:100%;"></select>
                    </div>
                    <div style="margin-bottom:12px;">
                        <label class="dt-form-label">复制内容</label>
                        <div style="display:flex; gap:16px; padding:6px 0;">
                            <label><input type="checkbox" id="typeform-copy-fields" checked> 全部字段</label>
                            <label><input type="checkbox" id="typeform-copy-rules" checked> 全部规则</label>
                        </div>
                    </div>
                </div>
                <!-- 导入选项 -->
                <div id="typeform-import-block" style="display:none;">
                    <div style="margin-bottom:12px;">
                        <label class="dt-form-label">JSON 文件</label>
                        <input id="typeform-import-file" type="file" accept=".json,application/json" class="form-input" style="width:100%;">
                        <div id="typeform-import-hint" style="font-size:12px; color:#4F775D; margin-top:4px;"></div>
                    </div>
                </div>
                <!-- 冲突策略（派生 / 导入） -->
                <div id="typeform-conflict-block" style="display:none; margin-bottom:12px;">
                    <label class="dt-form-label">同名冲突处理</label>
                    <select id="typeform-conflict" class="form-select" style="width:100%;">
                        <option value="rename">自动改名（追加" (副本)"）</option>
                        <option value="skip">跳过同名</option>
                    </select>
                </div>
            </div>
            <div class="rule-modal-footer">
                <button class="btn btn-ghost" onclick="DocTypeManager.closeTypeForm()">取消</button>
                <button class="btn btn-primary" onclick="DocTypeManager.submitTypeForm()">保存</button>
            </div>
        </div>
    </div>
```

> 注：保留前面的 `<!-- Copy Configs Modal -->`（`#copy-modal-overlay`）与其后的 `<!-- Type Config Readonly Modal -->`（`#typeview-modal-overlay`）均不动。

- [ ] **Step 3：确认项目相关标记已移除，复制相关仍保留**

Run: `grep -nE "openProjectDialog|createTypeFromForm|triggerImport|dt-project|dt-batch-project|doctype-new-id|doctype-new-project|project-modal|project-table-body" ui/index.html`
Expected: 无输出（项目相关全部已移除）。

Run: `grep -nE "openCopyDialog|copy-modal-overlay|copy-source-select" ui/index.html`
Expected: **仍有输出**（复制功能保留：规则页 2 处「从其他类型复制」按钮 + 复制弹窗）。

- [ ] **Step 4：Commit**

```bash
git add ui/index.html
git commit -m "feat: 重构文档类型管理弹窗,合并新建/派生/导入,移除项目维度(保留复制)"
```

---

## Task 7: `ui/css/style.css` 新增样式

**Files:**
- Modify: `ui/css/style.css`

- [ ] **Step 1：在全屏弹窗规则之后追加管理表格/菜单样式**

在以下锚点之后插入新块：

替换：
```css
/* ── 文档类型管理弹窗：全屏（铺满，无圆角） ── */
#doctype-modal-overlay .rule-modal {
    width: 100vw;
    height: 100vh;
    max-width: 100vw;
    max-height: 100vh;
    border-radius: 0;
}
```
为：
```css
/* ── 文档类型管理弹窗：全屏（铺满，无圆角） ── */
#doctype-modal-overlay .rule-modal {
    width: 100vw;
    height: 100vh;
    max-width: 100vw;
    max-height: 100vh;
    border-radius: 0;
}

/* ── 文档类型管理：表格、标签、行内操作、⋯ 菜单 ── */
.dt-table .dt-name { font-weight: 600; }
.dt-table .dt-id-sub { font-size: 11px; color: #8a9a8e; margin-top: 2px; }
.dt-table .dt-id-sub code { background: transparent; padding: 0; font-size: 11px; }

.dt-tag { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 10px; }
.dt-tag-default { background: #E8EFE6; color: #2C4C3B; }
.dt-tag-template { background: #DCEBDD; color: #4F775D; }
.dt-tag-copy { background: #F3EEE4; color: #8B735B; }
.dt-tag-plain { color: #9aa39c; }

.btn-sm { padding: 3px 10px; font-size: 12px; }

.dt-actions { white-space: nowrap; }
.dt-menu { position: relative; display: inline-block; margin-left: 4px; }
.dt-menu-pop {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 30;
    min-width: 132px;
    background: #fff;
    border: 1px solid rgba(44, 76, 59, 0.15);
    border-radius: 10px;
    box-shadow: 0 8px 24px rgba(44, 76, 59, 0.18);
    padding: 4px;
}
.dt-menu-pop button {
    display: block;
    width: 100%;
    text-align: left;
    border: 0;
    background: transparent;
    padding: 7px 10px;
    font-size: 13px;
    color: #2C4C3B;
    border-radius: 6px;
    cursor: pointer;
}
.dt-menu-pop button:hover { background: #E8EFE6; }
.dt-menu-pop button.danger { color: #b3261e; }
.dt-menu-pop button:disabled { color: #bbb; cursor: not-allowed; }

.dt-form-label { display: block; font-size: 12px; color: #4F775D; margin-bottom: 4px; }
```

- [ ] **Step 2：Commit**

```bash
git add ui/css/style.css
git commit -m "style: 文档类型管理表格/标签/行内菜单样式"
```

---

## Task 8: 同步文档（gen_openapi + openapi.json + CLAUDE.md + API_DOCUMENTATION.md）

**Files:**
- Modify: `scripts/gen_openapi.py`
- Regenerate: `docs/openapi.json`
- Modify: `CLAUDE.md`
- Modify: `docs/API_DOCUMENTATION.md`

- [ ] **Step 1：`gen_openapi.py` 顶层描述去掉「项目」维度**

替换：
```python
- **类型的两个正交维度**
  - **血缘**：`is_template`（模板标记，`promote` / `demote` 切换）+ `parent_type_id`
    （复制来源，`copy_from` / `import` 自动记录）。
  - **项目**：`doc_type.project_id → project` 表，纯算法端分类；一个类型只属一个项目，
    不影响文件处理 / 流水线。
```
为：
```python
- **类型的血缘维度** —— `is_template`（模板标记，`promote` / `demote` 切换）+
  `parent_type_id`（复制来源，`copy_from` / `import` 自动记录）。
```

- [ ] **Step 2：`TAGS` doctype 描述去掉项目分组**

替换：
```python
            "文档类型管理：CRUD、跨类型复制、导出/导入、模板血缘（promote/demote）、"
            "项目分组（projects + batch_assign_project）、批量删除。"
```
为：
```python
            "文档类型管理：CRUD、跨类型复制、导出/导入、模板血缘（promote/demote）、批量删除。"
```

- [ ] **Step 3：删 `ENRICHMENTS` 里 3 个项目接口条目**

删除 `"/doctype/projects": {...}`、`"/doctype/projects/{project_id}": {...}`、`"/doctype/batch_assign_project": {...}` 三个整段（位于 `"/doctype/import"` 之后、`"/doctype/{type_id}/promote"` 之前）。

- [ ] **Step 4：`/doctype/list` 描述去掉 project_id 行与 project_name**

替换：
```python
                "- `scope`：`all`（全部）/ `template`（`is_template=1` 或默认类型）/ "
                "`copy`（既非模板也非默认的副本）\n"
                "- `project_id`：精确匹配所属项目；特殊值 `__ungrouped__` 表示 `project_id IS NULL`（未分组）\n\n"
```
为：
```python
                "- `scope`：`all`（全部）/ `template`（`is_template=1` 或默认类型）/ "
                "`copy`（既非模板也非默认的副本）\n\n"
```

替换：
```python
                "**每项字段**：`type_id` / `type_name` / `description` / `is_default` / `enabled` / "
                "`is_template` / `parent_type_id` / `project_id` / `project_name` / `created_at` / "
                "`updated_at` / `file_count` / `field_count` / `rule_count`。\n\n"
```
为：
```python
                "**每项字段**：`type_id` / `type_name` / `description` / `is_default` / `enabled` / "
                "`is_template` / `parent_type_id` / `created_at` / "
                "`updated_at` / `file_count` / `field_count` / `rule_count`。\n\n"
```

替换 `/doctype` post 描述里的：
```python
                "**不经由本接口设置**的字段：`is_template`（用 `promote`/`demote`）、"
                "`parent_type_id`（由 `copy_from`/`import` 自动记录）、`project_id`"
                "（用 `batch_assign_project`）。\n\n"
```
为：
```python
                "**不经由本接口设置**的字段：`is_template`（用 `promote`/`demote`）、"
                "`parent_type_id`（由 `copy_from`/`import` 自动记录）。\n\n"
```

替换 `copy_from` 描述里的：
```python
                "**血缘副作用**（目标非默认类型时）：把目标 `parent_type_id` 记为 `source_type_id`；"
                "若目标当前未分组而源有项目，则继承源的 `project_id`（目标已分组则不覆盖）。\n\n"
```
为：
```python
                "**血缘副作用**（目标非默认类型时）：把目标 `parent_type_id` 记为 `source_type_id`。\n\n"
```

- [ ] **Step 5：删参数文档与 schema 文档里的项目项**

删 `GLOBAL_PARAM_DOCS` 里的：
```python
    "project_id": "项目 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。",
```

删 `PARAM_OVERRIDES` `("/doctype/list", "get")` 里的：
```python
        "project_id": "项目过滤；精确匹配 `project_id`，特殊值 `__ungrouped__` 表示未分组（`project_id IS NULL`）；省略不过滤。",
```

删 `SCHEMA_DOCS` 里 `"ProjectCreate": {...}` 与 `"BatchAssignProjectRequest": {...}` 两整段（位于 `"DocTypeCreate"` 之后、`"DocTypeBatchDeleteRequest"` 之前）。

- [ ] **Step 6：重新生成 openapi.json**

Run: `uv run python scripts/gen_openapi.py`
Expected: 打印 `done. size = ...`，且**不再出现** `[warn] 路径不在 schema 中: /doctype/projects` 之类警告以外的异常；`docs/openapi.json` 写入成功。检查无 `project` 残留：
Run: `grep -c "batch_assign_project\|/doctype/projects\|ProjectCreate" docs/openapi.json`
Expected: `0`。

> 若仓库实际生成目标是 `docs/_openapi_generated.json`（见 git 未跟踪文件），以 `gen_openapi.py` 中 `main()` 的 `out_path` 为准；当前脚本写 `docs/openapi.json`。两者按脚本实际产物提交。

- [ ] **Step 7：更新 `CLAUDE.md` 的 Document Type Isolation 段**

替换：
```
- 类型有两个正交维度:**血缘**(`is_template` 标记 + `parent_type_id` 复制来源,`copy_from` 自动记录、`POST /doctype/{id}/promote|demote` 切换模板标记)与**项目**(`doc_type.project_id` → `project` 表,纯算法端分类,一个 type 只属一个项目,不影响文件处理/流水线)。
- `GET /doctype/list` 支持 `q/scope(all|template|copy)/project_id(含 __ungrouped__)/page/page_size/sort`;**传齐 page+page_size 返回 `{items,total}`,否则原样返回数组**(向后兼容)。计数用 3 条 GROUP BY 避免 N+1。
- 批量接口:`POST /doctype/batch_delete`(`{type_ids,force}`)、`POST /doctype/batch_assign_project`(`{type_ids,project_id|null}`)。项目 CRUD:`GET/POST /doctype/projects`、`DELETE /doctype/projects/{id}`(删项目仅解绑成员,不删 type)。
- 顶部选择器只展示模板 + 默认 + 当前选中;副本的搜索/查看/清理在「管理」弹窗内完成。「只读查看配置」复用 `GET /doctype/{id}/export`。
- 存量(本次改动前)副本无 `parent_type_id`/`project_id`,需初期手工标模板、归项目;此后经 `copy_from` 新建的副本自动继承。
```
为：
```
- 类型只有**血缘**这一个附加维度:`is_template`(模板标记) + `parent_type_id`(复制来源,`copy_from`/`import` 自动记录),`POST /doctype/{id}/promote|demote` 切换模板标记。**「项目」维度已彻底移除**(无 `project_id` 列、无 `project` 表、无相关接口)。
- `GET /doctype/list` 支持 `q/scope(all|template|copy)/page/page_size/sort`;**传齐 page+page_size 返回 `{items,total}`,否则原样返回数组**(向后兼容)。计数用 3 条 GROUP BY 避免 N+1。
- 批量接口:`POST /doctype/batch_delete`(`{type_ids,force}`)。
- 管理弹窗(全屏单栏):每行「选用」=设为当前类型(任意类型皆可,不限模板);「+ 新建类型」统一三条造类型路径(空白/从类型派生/导入 JSON);行内 ⋯ 菜单含查看配置/复制为新类型/改名/模板标记/导出/删除。顶部选择器只展示模板 + 默认 + 当前选中。「只读查看配置」复用 `GET /doctype/{id}/export`。
- 存量副本若无 `parent_type_id`,初期需手工标模板;此后经 `copy_from`/派生新建的类型自动记录来源。
```

- [ ] **Step 8：清理 `docs/API_DOCUMENTATION.md` 的项目段落**

Run: `grep -nE "项目|/doctype/projects|batch_assign_project|project_id|project_name" docs/API_DOCUMENTATION.md`
逐条处理：删除「项目 CRUD / 批量归类」相关的接口小节、`project_id`/`project_name` 字段说明、以及把「血缘 + 项目两个维度」的描述改为只剩血缘。仅删项目内容，保留其余文档结构。

- [ ] **Step 9：Commit**

```bash
git add scripts/gen_openapi.py docs/openapi.json docs/API_DOCUMENTATION.md CLAUDE.md
git commit -m "docs: 同步移除项目维度(openapi/CLAUDE/API 文档)"
```

---

## Task 9: 验证

**Files:** 无（验证任务）

- [ ] **Step 1：全套后端测试**

Run: `uv run pytest -q`
Expected: 全绿。

- [ ] **Step 2：全仓无残留项目引用（代码层）**

Run: `grep -rnE "project_id|project_name|batch_assign_project|getProjects|openProjectDialog|loadProjectsIntoFilters" blue_print/ model/ service/ ui/js/ scripts/`
Expected: 无输出（`docs/` 与示例表格内容除外，可忽略 `DATABASE_SCHEMA.md` 里的「项目」表头示例）。

- [ ] **Step 3：启动应用做人工冒烟（建议用 `/run` 技能或手动）**

Run: `python app.py`（或 `uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload`），浏览器开 `http://localhost:5019/ui`。
逐项确认（打开浏览器控制台，确保**无 JS 报错**）：
  1. 顶部「管理」→ 弹窗为全屏单栏，表头为 `名称/ID · 标签 · 来源 · 文件 · 字段 · 规则 · 操作`，**无项目列、无项目筛选、无项目管理按钮**。
  2. 任选一行点「选用」→ 弹窗关闭，顶部选择器变为该类型，文件列表/规则配置随之刷新（打破死循环）。
  3. 「+ 新建类型」→ 三种来源各试一次：空白建成；派生选源类型后字段/规则被复制且新类型「来源」列显示源名称；导入一个由「导出」得到的 JSON 文件，字段/规则导入成功。全程无原生 `prompt/confirm` 弹窗（删除/批量删除的确认 `confirm` 保留属正常）。
  4. 行内 ⋯ 菜单：查看配置 / 复制为新类型 / 改名 / 设为模板·取消 / 导出 / 删除 均可用；默认类型行无「改名」、删除禁用。
  5. 勾选若干行 → 底部出现「已选 N · 批量删除」，未勾选时隐藏；分页 ◀▶ 与「共 N 个」正常；范围下拉 全部/★模板/副本 过滤生效；搜索生效。
  6. 规则配置页「从其他类型复制」按钮**仍在且可用**：选源类型 → 字段/规则被复制进当前类型，列表刷新；新增字段/规则正常。

- [ ] **Step 4：在有 MySQL 的环境确认迁移真的删表删列**

启动一次后，Run（用项目的 mysql 连接）：
```sql
SHOW COLUMNS FROM doc_type LIKE 'project_id';   -- 期望空
SHOW TABLES LIKE 'project';                      -- 期望空
SHOW COLUMNS FROM doc_type LIKE 'is_template';   -- 期望 1 行（保留）
SHOW COLUMNS FROM doc_type LIKE 'parent_type_id';-- 期望 1 行（保留）
```

- [ ] **Step 5：最终提交（若冒烟中有微调）**

```bash
git add -A
git commit -m "chore: 项目维度移除与文档类型管理重做收尾验证"
```

---

## Self-Review（计划自检结论）

- **Spec 覆盖**：死循环→`selectType`(Task5/6)；统一造类型→类型表单(Task5/6)；原生 prompt→改为弹窗+Toast(Task5)；项目维度→后端/DB/前端/文档全删(Task1-4,8)；瘦身表格/底栏→新表头+条件批量栏(Task6/7)；**复制保留**(规则页灌入当前)+**新增派生**(行内/新建弹窗)(Task5/6)；来源显示名称(Task5)。均有对应任务。
- **类型一致性**：`onScopeChange`(JS)↔`dt-scope onchange`(HTML) 一致；`selectType/openCreateForm/openDeriveForm/openRenameForm/toggleRowMenu/onCreateSourceModeChange/submitTypeForm/closeTypeForm` 在 JS 定义且仅被新 HTML 引用；`#dt-batch-bar/#dt-selected-count/#typeform-*` 在 HTML 定义且被 JS 读写，逐一核对一致。
- **占位符**：无 TODO/“类似上文”等；所有代码步骤均给出完整代码或精确 old→new。
- **已知限制**：行内 ⋯ 菜单底部裁切（已在开头声明，不处理）；`saveDocType` upsert 语义下「新建已存在 ID」会更新而非报错（沿用原行为）。
```