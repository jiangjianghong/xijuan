# 析卷 AI · 接口文档

> 对应服务版本 0.3.0

析卷 AI 是基于 **FastAPI + MinerU + LLM** 的 PDF 文档智能处理平台，把 PDF 经六阶段管线
（parsing → tableing → chunking → embedding → extracting → analyzing）沉淀为结构化的
「字段提取结果」与「逻辑分析结论」。

本目录是接口文档的**导航枢纽**。文档分四层，`docs/openapi.json`（及活的 `/docs` Swagger UI）
是精确 schema 的唯一权威，本手写文档是语义 / 示例 / 流程的权威，两者由
`scripts/check_docs_sync.py` 机器校验保持一致。

## 文档地图

### 参考层 · 接口参考（`api/`）
- [overview](api/overview.md) — 基础信息 · 通用响应信封 · 认证 · 分页约定 · 错误码总表 · 版本
- [doctype](api/doctype.md) — `/doctype` 文档类型 / 项目 / 血缘 / 模板
- [file](api/file.md) — `/file` 解析 / 列表 / 详情 / 重试 / 内容 / 结果 / PDF
- [extraction](api/extraction.md) — `/extraction` 字段提取配置与调试
- [analysis](api/analysis.md) — `/analysis` 逻辑分析配置 / 调试 / 独立执行
- [search](api/search.md) — `/search` 向量检索
- [logs](api/logs.md) — `/log` 应用日志查看 / SSE
- [callbacks](api/callbacks.md) — 异步回调契约（`callback_url`）
- [sse](api/sse.md) — SSE 流式事件清单

### 指南层 · 任务导向（`guides/`）
- [extraction-config](guides/extraction-config.md) — 字段提取配置手册（table / text / vl）
- [analysis-config](guides/analysis-config.md) — 逻辑分析配置手册（judge / calc / web_search）
- [source-refs](guides/source-refs.md) — `source_refs` 溯源结构 + 页码定位
- [configuration](guides/configuration.md) — `config.yaml` 全量配置

### 数据参考层（`reference/`）
- [data-model](reference/data-model.md) — 数据库表结构 / JSON 子结构（schema 唯一权威）
- [enums](reference/enums.md) — 枚举值 / progress 状态机（枚举唯一权威）

### 架构层（`architecture/`）
- [mineru-integration](architecture/mineru-integration.md) — MinerU 外部依赖集成

## 接口总览

共 50 个接口。下表由 `scripts/gen_doc_tables.py` 从 `docs/openapi.json` 生成，请勿手改。

<!-- AUTOGEN:endpoint-index -->
| 方法 | 路径 | 分组 | 摘要 | 文档 |
|---|---|---|---|---|
| GET | `/analysis/rules` | 逻辑分析 | 列出分析规则 | [api/analysis.md](api/analysis.md) |
| POST | `/analysis/rules` | 逻辑分析 | 新增/更新分析规则（upsert） | [api/analysis.md](api/analysis.md) |
| DELETE | `/analysis/rules/{rule_id}` | 逻辑分析 | 删除分析规则 | [api/analysis.md](api/analysis.md) |
| GET | `/analysis/rules/{rule_id}/check` | 逻辑分析 | 检查规则 ID 是否存在 | [api/analysis.md](api/analysis.md) |
| POST | `/analysis/run` | 逻辑分析 | 独立逻辑分析执行 | [api/analysis.md](api/analysis.md) |
| POST | `/analysis/test` | 逻辑分析 | 逻辑分析调试（同步） | [api/analysis.md](api/analysis.md) |
| POST | `/analysis/test/stream` | 逻辑分析 | 逻辑分析流式调试（SSE） | [api/analysis.md](api/analysis.md) |
| POST | `/doctype` | 文档类型 | 新增/更新文档类型（upsert） | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/batch_assign_project` | 文档类型 | 批量归类到项目（级联血缘） | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/batch_delete` | 文档类型 | 批量删除文档类型 | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/import` | 文档类型 | 从 JSON 载荷导入配置 | [api/doctype.md](api/doctype.md) |
| GET | `/doctype/list` | 文档类型 | 列出文档类型（可搜索/过滤/分页） | [api/doctype.md](api/doctype.md) |
| GET | `/doctype/projects` | 文档类型 | 列出项目 | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/projects` | 文档类型 | 新增/改名项目（upsert） | [api/doctype.md](api/doctype.md) |
| DELETE | `/doctype/projects/{project_id}` | 文档类型 | 删除项目 | [api/doctype.md](api/doctype.md) |
| DELETE | `/doctype/{type_id}` | 文档类型 | 删除文档类型（单个） | [api/doctype.md](api/doctype.md) |
| PUT | `/doctype/{type_id}` | 文档类型 | 更新文档类型（可改 type_id） | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/{type_id}/copy_from` | 文档类型 | 同实例跨类型复制配置 | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/{type_id}/demote` | 文档类型 | 取消模板标记 | [api/doctype.md](api/doctype.md) |
| GET | `/doctype/{type_id}/export` | 文档类型 | 导出配置为 JSON 载荷 | [api/doctype.md](api/doctype.md) |
| POST | `/doctype/{type_id}/promote` | 文档类型 | 标记为模板 | [api/doctype.md](api/doctype.md) |
| GET | `/extraction/fields` | 字段提取 | 列出字段配置 | [api/extraction.md](api/extraction.md) |
| POST | `/extraction/fields` | 字段提取 | 新增/更新字段配置（upsert） | [api/extraction.md](api/extraction.md) |
| DELETE | `/extraction/fields/{field_id}` | 字段提取 | 删除字段配置 | [api/extraction.md](api/extraction.md) |
| GET | `/extraction/fields/{field_id}/check` | 字段提取 | 检查字段 ID 是否存在 | [api/extraction.md](api/extraction.md) |
| POST | `/extraction/test` | 字段提取 | 字段提取调试（同步） | [api/extraction.md](api/extraction.md) |
| POST | `/extraction/test/stream` | 字段提取 | 字段提取流式调试（SSE） | [api/extraction.md](api/extraction.md) |
| DELETE | `/file/batch` | 文件处理 | 批量删除文件 | [api/file.md](api/file.md) |
| POST | `/file/context_query` | 文件处理 | 文件片段上下文查询 | [api/file.md](api/file.md) |
| GET | `/file/list` | 文件处理 | 分页查询文件列表 | [api/file.md](api/file.md) |
| POST | `/file/parse` | 文件处理 | 提交文件解析 | [api/file.md](api/file.md) |
| GET | `/file/processing` | 文件处理 | 处理中文件队列 | [api/file.md](api/file.md) |
| DELETE | `/file/{file_id}` | 文件处理 | 删除文件 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/analysis` | 文件处理 | 逻辑分析结果 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/chunks` | 文件处理 | 文件分块列表 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/content` | 文件处理 | 按页返回 Markdown 内容 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/detail` | 文件处理 | 文件完整详情 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/extraction` | 文件处理 | 字段提取结果 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/outline` | 文件处理 | 文件章节大纲 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/pdf` | 文件处理 | 下载原始 PDF | [api/file.md](api/file.md) |
| POST | `/file/{file_id}/recompute_page_mapping` | 文件处理 | 重算页码映射 | [api/file.md](api/file.md) |
| POST | `/file/{file_id}/retry/analyzing` | 文件处理 | 快捷重试：逻辑分析 | [api/file.md](api/file.md) |
| POST | `/file/{file_id}/retry/extracting` | 文件处理 | 快捷重试：字段提取 | [api/file.md](api/file.md) |
| POST | `/file/{file_id}/retry/{stage}` | 文件处理 | 从指定阶段重试 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/status` | 文件处理 | 查询文件处理进度 | [api/file.md](api/file.md) |
| GET | `/file/{file_id}/tables` | 文件处理 | 文件表格列表 | [api/file.md](api/file.md) |
| GET | `/log/files` | 日志 | 应用日志文件列表 | [api/logs.md](api/logs.md) |
| GET | `/log/recent` | 日志 | 读取最近日志 | [api/logs.md](api/logs.md) |
| GET | `/log/stream` | 日志 | 实时日志流（SSE） | [api/logs.md](api/logs.md) |
| POST | `/search` | 向量检索 | 向量相似度检索 | [api/search.md](api/search.md) |
<!-- /AUTOGEN:endpoint-index -->

## 维护约定

- **改了接口**：跑 `uv run python scripts/gen_openapi.py` 重生成 `openapi.json`，再跑
  `uv run python scripts/gen_doc_tables.py` 刷新所有 AUTOGEN 表格。
- **校验一致性**：`uv run python scripts/check_docs_sync.py`（接口全集 / 版本 / AUTOGEN 新鲜度）。
- AUTOGEN 区块（`<!-- AUTOGEN:... -->`）内容由脚本生成，标记之外的一句话 / 示例 / curl / 备注由人手写。
