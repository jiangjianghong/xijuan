# 文档类型管理增强(面向海量 type) — 设计方案

**日期：** 2026-06-02
**作者：** brainstorming session
**状态：** 待评审

## 1. 背景与目标

后端为"模板 + 每用户复制一份预设"的模式服务:用户看到的预设其实是复制模板后的独立 `type`。用户一多,`doc_type` 行数随之膨胀(规模不确定且快速增长)。算法端用现有界面查看/管理这些 type 很吃力:

- 顶部选择器是原生 `<select>`,选项一多就成了不可搜索的超长下拉
- 管理弹窗(`ui/index.html` 第 520-557 行)的表格**无搜索、无筛选、无分页、无排序**
- `GET /doctype/list`(`blue_print/doctype_router.py:59`)一次性全量返回,且每个 type 跑 3 条 count 子查询(N+1),type 一多就慢
- 目前**无法从数据上区分**「模板」与「用户副本」,也无任何分类维度

**目标(算法端勾选的四项 + 追加一项):**
1. 快速搜索定位某个 type
2. 过滤/隐藏用户副本(默认只看模板)
3. 批量清理废弃 type
4. 只读查看某 type 的字段/规则配置
5. (追加)把 type 归入「项目」分类,按项目组织/筛选

## 2. 核心设计决策(brainstorming 结论)

| 议题 | 决策 |
|---|---|
| 选择器如何扛海量 type | 顶部选择器**只列模板 + 默认 + 当前选中**;全量搜索收口到管理弹窗 |
| 模板/副本如何区分 | 新增 `is_template` 标记 + `parent_type_id` 血缘;`copy_from` 自动记父级,前端支持「副本→模板」提升 |
| 「副本被再复制」 | `parent_type_id` 存**直接父级**;找根模板时向上走父链(带防环上限) |
| 项目分类的归属 | 一个 type 只属一个项目(一对一) |
| 项目是什么 | **独立 `project` 表**(可建/改名/删) |
| 项目作用范围 | **纯算法端管理用**:仅做组织/筛选,不影响文件处理、不进 `type_id` 语义、不透出后端 |
| 对现有接口 | **向后兼容、后端零改造**(详见 §7) |

血缘(模板/副本)与项目(分类)是两个**正交**维度。

## 3. 数据模型

### 3.1 新表 `project`

`Base.metadata.create_all` 在已有库会自动建缺失表,无需手写建表迁移。

| 列 | 类型 | 说明 |
|---|---|---|
| `project_id` | `VARCHAR(64)` PK | 项目 ID |
| `project_name` | `VARCHAR(200)` NOT NULL | 项目名 |
| `description` | `TEXT` NULL | 说明 |
| `created_at` | `DateTime` server_default now | |
| `updated_at` | `DateTime` now/onupdate | |

### 3.2 `doc_type` 新增 3 列

模型(`model/tables.py` 的 `DocType`)加列;存量库通过 `service/init_service.py` 的 `migrations` 列表 `ALTER TABLE ADD COLUMN` 补列(沿用现有写法,仅当列不存在时添加)。

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `is_template` | `TINYINT` | `0` | 是否模板 |
| `parent_type_id` | `VARCHAR(64)` | `NULL` | 复制来源(直接父级) |
| `project_id` | `VARCHAR(64)` | `NULL` | 归属项目;NULL=未分组 |

新增索引(沿用 `index_migrations` 写法):`ix_doc_type_parent_type_id`、`ix_doc_type_project_id`。

> 三列均可空/有默认,`ALTER` 对小表(`doc_type` 每 type 一行)无风险。

## 4. 后端接口

文件:`blue_print/doctype_router.py`、schema 在 `model/schemas.py`。

### 4.1 改造 `GET /doctype/list`

新增**全部选填**的 query 参数,保持向后兼容:

| 参数 | 含义 |
|---|---|
| `q` | 模糊搜 `type_id` / `type_name`(LIKE) |
| `scope` | `all`(默认) / `template`(`is_template=1 OR is_default=1`) / `copy`(`is_template=0 AND is_default=0`) |
| `project_id` | 按项目过滤;特殊值 `__ungrouped__` 表示 `project_id IS NULL` |
| `page` / `page_size` | 1-indexed 分页;**两者都不传 = 不分页(原样返回全部)** |
| `sort` | 选填,仅允许 doc_type 自身列 `created_at`(默认 desc) / `type_name`(可 SQL 内排序+分页);按 file_count 排序需全局计数,与"只算当页计数"冲突,暂不支持 |

**响应形态(关键)**:传齐 `page`+`page_size` 时返回 `{items, total}`;否则**原样返回数组**(与现状一致,向后兼容)。`q/scope/project_id/sort` 仅在传入时生效;分页仅在 `page`+`page_size` 都传入时切片。**不传任何参数即旧行为**(全量数组)。前端选择器用 `?scope=template`(无分页→数组),管理弹窗用分页(→ `{items,total}`)。

**修 N+1 计数**:对**当前页**(或全量时的结果集)的 `type_id` 集合,用 3 条 `GROUP BY ... WHERE type_id IN (...)` 一次性取 files/extraction_field/analysis_rule 计数,再回填。每次请求计数固定 3 条查询。

**响应每项**在现有字段基础上**追加** `is_template / parent_type_id / project_id / project_name`(additive)。

### 4.2 `copy_from` 增加副作用(`POST /doctype/{type_id}/copy_from`)

入参/返回结构**不变**。复制成功、commit 前:
- `target.parent_type_id = source_type_id`(目标为默认类型则跳过;`source==target` 已被现有校验拦截)
- 若 `target.project_id IS NULL` 则继承 `source.project_id`(已分组**不覆盖**)
- 目标被多次从不同源复制时,`parent_type_id` 指向**最近一次**来源(可接受)

### 4.3 新增接口(全部 additive)

| 接口 | 作用 |
|---|---|
| `POST /doctype/{type_id}/promote` | 副本→模板:置 `is_template=1`,保留 `parent_type_id` 作血缘;默认类型不可操作 |
| `POST /doctype/{type_id}/demote` | 模板→普通:置 `is_template=0`;默认类型不可操作 |
| `POST /doctype/batch_delete` | `{type_ids[], force}` 批量删除;复用单删的级联逻辑(抽 `_delete_one_type` helper);跳过默认类型并在结果中标注;非空类型未带 `force` 时按单删规则拒绝 |
| `POST /doctype/batch_assign_project` | `{type_ids[], project_id\|null}` 批量归类/移出;`project_id` 非空时校验项目存在 |
| `GET /doctype/projects` | 列项目,含每项目 `type_count`(`GROUP BY doc_type.project_id`) |
| `POST /doctype/projects` | upsert `{project_id, project_name, description}` |
| `DELETE /doctype/projects/{project_id}` | 删项目:先把成员 type 的 `project_id` 置空,再删项目;**不删 type** |

「只读查看配置」**复用现有** `GET /doctype/{type_id}/export`(已返回字段+规则),前端只读渲染,无需新接口。

### 4.4 新增/扩展 schema(`model/schemas.py`)

- 扩展 `DocTypeResponse`:加 `is_template / parent_type_id / project_id / project_name`(可选,默认 None/0)
- 扩展 `DocTypeCreate`:加**选填** `project_id`(不传=未分组),不破坏旧请求体
- 新增 `ProjectCreate / ProjectResponse`、`BatchDeleteRequest`、`BatchAssignProjectRequest`

## 5. 前端

文件:`ui/index.html`、`ui/js/doctype.js`、`ui/js/api.js`。

### 5.1 顶部选择器(`renderSelector`)

只渲染 `is_template=1 || is_default=1 || type_id===当前选中` 的类型(可选用 `<optgroup>` 按项目分组)。需要切到某个副本时,在管理弹窗里搜索并「切换到此类型」(临时把它补进选择器)。

### 5.2 管理弹窗(重构)

```
┌─ 文档类型管理 ──────────────────────────────────────────────┐
│ [🔍 搜 id/名称] (全部│模板│副本) [项目▾全部] [+新增][导入][项目管理] │
├──────────────────────────────────────────────────────────────┤
│ ☐ 类型ID    名称     标签  项目    来源       文件 字段 规则  操作        │
│ ☐ contract 采购合同 [模板] 合同组   —         12  8  3  查看|导出|删除   │
│ ☐ ct_u123  张三副本   —   合同组  ←contract  2  8  3  查看|设为模板|删除 │
├──────────────────────────────────────────────────────────────┤
│ ☑已选3项 [批量删除] [移动到项目▾]      ◀ 1/27 ▶  共 533 个          │
└──────────────────────────────────────────────────────────────┘
```

- 搜索框 debounce 后调 list(带 `q`);scope 分段(全部/模板/副本)与项目下拉作为筛选;底部分页器
- 复选框列 + 批量工具条(批量删除 / 移动到项目)
- 行内操作:查看(只读层,复用 export) / 设为模板·取消模板(promote·demote) / 导出 / 删除
- 新增表单加选填「归属项目」下拉
- 「项目管理」子弹窗:列项目 + 新建/改名/删除

### 5.3 `api.js`

新增/调整方法:`listDocTypes(params)`(带分页搜索;选择器仍可用轻量 `getDocTypes()` 取模板)、`promoteType` / `demoteType`、`batchDeleteTypes`、`batchAssignProject`、`getProjects` / `saveProject` / `deleteProject`。

## 6. 分批落地

- **P1 治理**:doc_type 两列(`is_template/parent_type_id`)+ migration;`list` 改造(搜索/scope/分页/计数优化)+ 选择器只显模板 + 弹窗搜索/筛选/分页
- **P2 血缘与清理**:`copy_from` 记 `parent_type_id` + `promote/demote` + `batch_delete` + 只读查看(复用 export)
- **P3 项目分组**:`project` 表 + CRUD + `doc_type.project_id` + 弹窗项目筛选/分配/批量移动 + `copy_from` 继承项目

## 7. 向后兼容性(后端零改造)

| 接口 | 影响 |
|---|---|
| `POST /doctype`(新增) | 不变;新列取默认。仅**追加**选填 `project_id` |
| `POST /doctype/{id}/copy_from`(复制) | 入参/返回不变;仅多写 `parent_type_id`(+ 继承 `project_id`)副作用 |
| 文件上传 / pipeline / extraction / analysis | 完全不变(项目不进 `type_id` 语义、不碰流水线) |
| `POST /doctype/import` | 不变 |
| `GET /doctype/list` | 向后兼容:不传分页参数=原样返回全部,每项**追加**字段、计数更快;传参才启用分页搜索 |
| `promote/demote`、`batch_*`、`projects` CRUD | 全新增接口,与现有调用互不干扰 |

## 8. 已知限制

- **存量副本无 `parent_type_id` / `project_id`**(本次改动前创建的):无法自动识别,初期需算法端**手工标模板、归项目**;此后经 `copy_from` 新建的副本自动继承。因无命名规律,**不做**历史血缘回填。
- 找根模板/展示血缘时向上走父链,**带防环上限**(如最多 32 层),避免脏数据成环导致死循环。

## 9. 测试计划

后端新增 `tests/test_doctype_management.py`(pytest-asyncio,沿用 `conftest.py` 的 AsyncClient):

- **list**:不传参=全量(向后兼容);`q` 命中 id/名称;`scope=template/copy` 过滤;`project_id` 与 `__ungrouped__` 过滤;分页 `total` 正确;计数(file/field/rule)数值正确
- **copy_from 副作用**:复制后 `parent_type_id==source`;目标未分组时继承 `project_id`;目标已分组时**不**覆盖;默认类型不被 reparent
- **promote/demote**:切换 `is_template`;默认类型被拒
- **batch_delete**:级联删除;跳过默认类型;非空未带 `force` 被拒,带 `force` 成功
- **batch_assign_project**:批量归类/移出(null);项目不存在被拒
- **projects CRUD**:建/改名;删项目后成员 type 的 `project_id` 被置空且 type 仍在

前端无 JS 测试夹具,**手动在浏览器验证**:选择器只显模板、搜索/筛选/分页、批量删除与移动、只读查看、项目管理增删改。

## 10. 影响范围

### 需要改动

| 文件 | 改动 |
|---|---|
| `model/tables.py` | 新增 `Project` 模型;`DocType` 加 3 列 |
| `model/schemas.py` | 扩展 `DocTypeResponse`/`DocTypeCreate`;新增 Project / Batch* schema |
| `service/init_service.py` | `migrations` 列表加 3 条 `ALTER`;`index_migrations` 加 2 条索引 |
| `blue_print/doctype_router.py` | 改造 `list_doctypes`、`copy_configs`;新增 promote/demote/batch_delete/batch_assign_project/projects CRUD;抽 `_delete_one_type` helper |
| `ui/index.html` | 重构 doctype 管理弹窗;新增项目管理子弹窗、只读查看层 |
| `ui/js/doctype.js` | 选择器只显模板;弹窗搜索/筛选/分页/批量/查看/项目管理逻辑 |
| `ui/js/api.js` | 新增 list/promote/demote/batch/projects 等方法 |
| `CLAUDE.md` | Document Type Isolation 一节补充三列、新接口、选择器行为 |

### 不改动

- 文件上传 / pipeline / extraction / analysis / import / callback 协议 / Milvus / MinerU / VL 系列
- `type_id` 语义与多类型隔离机制
