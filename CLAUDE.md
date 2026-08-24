# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PDF document intelligent processing system built with FastAPI + MinerU + LLM. Processes PDF files through a 6-stage pipeline: **parsing** (MinerU) -> **tableing** (AI table name validation via LLM) -> **chunking** (recursive text splitting) -> **embedding** (vector storage in Milvus) -> **extraction** (LLM-driven field extraction) -> **analysis** (LLM judge / numexpr calc).

The system is written in Chinese (comments, logs, API responses, database fields). All documentation and code comments are in Chinese.

## Common Commands

```bash
# Run the server (development with hot reload)
python app.py
# OR
uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_file_router.py

# Run a single test function
uv run pytest tests/test_file_router.py::test_function_name -v

# Install dependencies
uv sync

# Docker deploy
./deploy.sh              # normal build
./deploy.sh --no-cache   # rebuild without cache
```

## Architecture

### Entry Point & Startup
- `app.py` - FastAPI app with uvicorn. On startup, `lifespan` calls `run_init()` which: creates database/tables if missing, ensures Milvus collection exists, recovers crashed pipeline states (*ing -> *_failed), cleans orphan data.
- Config loaded from `configs/config.yaml` via `utils/config.py` (`get_config()` singleton). Override path with `APP_CONFIG_PATH` env var.
- Logging configured in `logs/__init__.py` using loguru. Filters out polling endpoints from uvicorn access log.

### Layer Structure
- **`blue_print/`** - FastAPI routers (registered in `__init__.py:register_routers`). Prefix: `/file`, `/extraction`, `/analysis`, `/search`, `/doctype`.
- **`service/`** - Business logic. Each service module corresponds to a pipeline stage.
- **`model/`** - SQLAlchemy async ORM (`tables.py`), Pydantic response schemas (`schemas.py`), database session management (`database.py`).
- **`utils/`** - Shared clients: `llm_client.py` (OpenAI-compatible chat/embeddings), `milvus_client.py` (Milvus vector DB), `config.py`, `file_utils.py`, `callback.py`, `page_mapping.py`.
- **`ui/`** - Static HTML/JS/CSS frontend, served at `/ui`. File detail view uses a centered modal with timeline, error display, and tabbed data views. Tables tab has a left sidebar (table names with page numbers) + right content (table preview) split layout. Header has a doctype selector that scopes file list / field config / rule config to the current type. Extraction tab is a left/right split: field cards (with 📍 locate buttons) + pdf.js viewer that jumps to hit pages and draws `source_refs.bboxes` highlight boxes (`ui/js/pdfViewer.js`, served via `GET /file/{id}/pdf`). 统计页（`ui/js/statistics.js`，图表用本地内置 ECharts 5.5.1 `ui/vendor/echarts/`）入口是**点击左上角「析卷 AI」标题**，没有导航按钮，故 `App.switchPage` 在找不到对应 `.nav-btn` 时把滑动指示器透明度置 0；数据全部来自 `GET /file/stats` 单接口，**全局口径，不跟随顶部项目/类型选择器**。图表容器 `display:none` 时尺寸为 0，因此 `Statistics.activate()` 必须在 page-container 加上 `active` 之后调用。状态/阶段的中文名复用 `Utils.getStatusText` / `getStageText`，后端只回 key，避免两处中文表漂移。并发运行台（`ui/js/runtime-monitor.js`）入口是**点击左上角叶子图标**，与统计页同为普通内页（保留全站 header，导航指示器收起，无整页接管）；压力曲线**全部来自后端** `GET /runtime/concurrency` 的 `history`（`service/runtime_monitor_service.py` 按 1s 采样、纯内存保留 30 分钟、按窗口降采样成定长 60 桶取峰值），前端不再本地累积，故刷新页面 / 多开标签看到的是同一条曲线。空桶为 `null`（进程刚起时集中在左侧），渲染需容错。顶层只展示全局池（`scope` 恒为 `global`，无 task 作用域记录，故前端无 `isTask` 分支）；点柱子的侧窗含「等待分解」（本闸 / 端到端）与「当前占用任务」（主行文件名、副行 `file_id · stage`，`file_name` 缺失时主行回退 `file_id`）。

### Pipeline Flow (`service/pipeline_service.py`)
The core orchestrator. Six stages: parsing → tableing → chunking → embedding → extracting → analyzing. Three execution modes:
- **async** - `run_pipeline()` in background task, optional `callback_url` for stage notifications
- **sync** - `run_pipeline()` awaited directly
- **stream** - `run_pipeline_stream()` yields SSE events per stage

Both `run_pipeline` and `run_from_stage` (retry from any stage) exist in sync/stream variants. Failed stages are retried by cleaning downstream data and re-running from that point.

**并发分层**：`concurrency` 配置分三层且**全部真实生效**——模型通道（`global_llm` / `global_embedding` / `global_vl`，在 client 层）、业务阶段（`global_extraction` / `global_analysis` / `global_table_validation`）、单文件（`task_extraction` / `task_file_analysis` / `task_table_validation` / `task_embedding`）。`global_pipeline` 是最外层闸门，由 `service/pipeline_gate.py:pipeline_slot` 在**六个管线入口**（async/sync/stream 上传 + 三条 retry 路径）全程持有令牌（`parsing` → `complete`），超限文件落 `queued` 状态排队，`sync`/`stream` 模式排队时 HTTP 请求挂起。有空位时不写 `queued`，避免状态闪烁。

**向量化并发**：`get_embeddings` 的批次之间用 `asyncio.gather` 并发，结果**按批次索引回填**——原先的串行 for 循环让单文件在 `global_embedding` 池里永远只占 1 个令牌（300 chunk 实测峰值并发 1，配置值形同虚设），而 `all_embeddings.extend()` 的累积方式依赖串行顺序，改并发后不按索引回填会让向量与 chunk **静默错位**（不报错，只是检索结果全对不上）。`task_id` 传了才注册 `task_embedding` 闸门（`embed_chunks` 传 `chunks[0]["file_id"]`），`/search` 与 vector_db 单条查询不传、只受全局池约束。`get_embeddings` 与 `chat_completion` 同样对 4xx（除 429）直接抛出不重试。

**单文件层仍限流但不在运行台展示**（`service/runtime_monitor_service.py:_TASK_POOL_IDS`，池记录与事件双双滤除）：`task_* == global_*` 时它会常年显示 100% 饱和而全局池远未跑满，图与实情相反。**单文件值必须小于对应全局值才起作用**，相等时单个文件即可占满全局池，一个几百张表的文件会把后到的小文件连续堵在队尾；`utils/config.py:ConcurrencyConfig` 检测到 `task_* >= global_*` 会打 WARNING（仅告警不阻断）。被这层吸收的排队改由 `total_wait_p95_ms` 暴露。

**两个等待口径**（`utils/concurrency.py`）：`gate_wait_p95_ms` 只算在**这一道闸**上排队的时间（局部量），`total_wait_p95_ms` 从工作项起点算起、含上游闸门的全部排队。起点由 `work_item()`（contextvar，`create_task` 时按任务复制）标记，**必须包在最外层闸之前、且只包住等待+单次工作**——放进 `pipeline_slot` 或包住顺序执行的多个工作项（如 `analysis_run_service` 单 item 内的规则循环，故那里刻意不包）会把真实工作耗时算成等待。目前三处：`table_service._process_single_table`、`extraction_service._iter_field_group._worker`、`analysis_service.guarded`。**分位数不可加**，故 total 是每个工作项各自实测总等待后再取分位，不是把上下游 gate 分位相加。`summary` 只保留 `total_wait_p95_ms`（最坏等待），无 `wait_p95_ms`。

**并发上下文透传**：`limiter_context(**fields)` 绑定 ambient context，本任务及其子任务的 limiter 事件自动携带（白名单 `_SAFE_CONTEXT_KEYS`，显式 context 优先）。`pipeline_slot` 用它一次性绑定 `file_id` / `file_name`，覆盖 tableing / extracting / analyzing 全部下游阶段——因此运行台侧窗能显示文件名，而 `parse_tables` 等函数签名无需改动。`independent_analysis`（`/analysis/run`）不走管线闸门，无 file_name。

**字段提取的两阶段屏障**：`run_extraction` / `run_extraction_stream` 并发执行字段，但**普通字段组整体完成后才启动进阶字段组**（`_iter_extraction_results`）——进阶字段引用普通字段结果，没有屏障就会读到空值。并发段禁止任何 `session` 访问：只读数据在并发前经 `service/extraction_snapshot.py:load_extraction_snapshot` 一次性快照（content / tables / chunks，三次查询），写库与回调回到主协程串行执行（`AsyncSession` 非并发安全）。组内用 `_iter_field_group` 的 `as_completed` **完成即产出**，`field_done` 因此按完成序推送、`index` 仍是配置序号；`stage_done.results` 按 `index` 排序回填，聚合口径不变。单字段异常在 worker 内收敛为 `success=False`，不连坐同批；`CancelledError` 必须外抛。

### Async Callback Contract (`utils/callback.py`)
When `callback_url` is supplied to `run_pipeline` / `run_from_stage`, the orchestrator and the per-item services (`run_extraction`, `run_analysis`) POST status updates to that URL. Timeout is **2.5s** per call; failures are logged and swallowed (never affect the main flow).

**Payload shape:**
```json
// 阶段入口（每个阶段开始时各 1 次）
{"file_id": "...", "status": "extracting"}

// 单字段 / 单规则完成（仅 extracting 与 analyzing 阶段产生）
{"file_id": "...", "status": "extracting", "event": "field_done",
 "data": {"field_id", "field_name", "value", "reason",
          "pages": [3], "source_pages": [3], "source_refs",
          "success": true, "index": 5, "total": 12}}

{"file_id": "...", "status": "analyzing", "event": "rule_done",
 "data": {"rule_id", "rule_name", "rule_type", "result", "reason",
          "input_values", "source_refs", "success": true, "index": 3, "total": 8}}

// 阶段完整数据（每个阶段结束时各 1 次）
{"file_id": "...", "status": "<stage>", "event": "stage_done", "data": {...}}

// 阶段失败（失败时 1 次，替代 stage_done 与后续事件）
{"file_id": "...", "status": "<stage>_failed", "event": "stage_failed",
 "data": {"stage": "<stage>", "error": "TimeoutError: ..."}}
```

**`stage_done.data` per stage:**

| stage | data |
|---|---|
| parsing | `{content, middle_json, page_mapping}` — 完整 markdown 等价于 `/file/{id}/content` |
| tableing | `{total, tables: [{file_id, table_index, total_table, table_name, table_content, start_pos, end_pos, page_num}]}` |
| chunking | `{total, chunks: [{file_id, chunk_id, chunk_index, total_chunks, chunk_content, start_pos, end_pos, page_num}]}` |
| embedding | **不携带 data**（仅作完成信号；向量数据量过大不下发，需要请走 Milvus 查询） |
| extracting | `{total, succeeded, failed, results: [field_done.data ...]}` |
| analyzing | `{total, succeeded, failed, results: [rule_done.data ...]}` |

**事件序列示例（一次完整管线）：**
```
parsing                    → parsing + stage_done（完整 md）
tableing                   → tableing + stage_done（完整 tables）
chunking                   → chunking + stage_done（完整 chunks）
embedding                  → embedding + stage_done（无 data）
extracting + field_done×N  → extracting + stage_done（完整 results）
analyzing  + rule_done×N   → analyzing  + stage_done（完整 results）
complete
（任一阶段失败 → 该阶段 stage_failed,序列终止,无 complete）
```

**实现位置：** stage_done 事件由 `pipeline_service.run_pipeline` / `run_from_stage` 在每阶段 commit 之后触发；`extracting` / `analyzing` 的 stage_done 与 per-item 事件由 `run_extraction` / `run_analysis` 内部触发，pipeline 层只透传 `callback_url`。老消费者只读 `status` 不受影响（新事件靠 `event` 字段区分）。失败回调（stage_failed）由 `run_pipeline` / `run_from_stage` 最外层 except 统一触发，用 `current_stage` 局部变量跟踪当前阶段，覆盖含 parsing 在内的全部阶段；stream 模式不受影响（SSE 已有 error 事件）。

### Table Name Validation (`service/table_service.py`)
The **tableing** stage runs after parsing. `parse_tables()` extracts all `<table>` HTML blocks from the Markdown content, then concurrently calls LLM (`_extract_table_name_with_llm`) to identify each table's name from preceding context. Falls back to the last line before the table if LLM fails. Table names are truncated to 30 characters. Concurrency controlled by `table_name_validation.max_concurrency` config. Results stored in `file_table` with position and page info.

### Database (MySQL + async SQLAlchemy)
- `database.py` uses `aiomysql` async driver. `get_db()` is the FastAPI dependency for sessions.
- Key tables in `model/tables.py`: `doc_type` (file type definitions), `files` (progress tracking with stage timestamps; has `type_id`), `file_content` (raw MD + middle_json + page_mapping), `file_table` (extracted HTML tables), `file_chunk` (text chunks with positions), `extraction_field` (configurable field definitions; has `type_id`), `analysis_rule` (judge/calc rule definitions; has `type_id`), `extraction_result`, `analysis_result`.
- File progress states: `queued` -> `parsing` -> `tableing` -> `chunking` -> `embedding` -> `extracting` -> `analyzing` -> `complete`. Each `*ing` can fail to `*_failed`. `queued` 是等 `global_pipeline` 令牌的排队态（上传即写入，拿到令牌后由 `parse_service` 改成 `parsing`，故排队时长不计入解析耗时）；它算「处理中」（`PROCESSING_STATES` / 前端 `Utils.isProcessing` 都含它），崩溃恢复时归为 `parsing_failed`——重启后后台任务已丢失，不会自行启动。
- `files` table tracks timestamps per stage: `start_parsing_time`/`end_parsing_time`, `start_tableing_time`/`end_tableing_time`, `start_chunking_time`/`end_chunking_time`, `start_embedding_time`/`end_embedding_time`, `start_extracting_time`/`end_extracting_time`, `start_analyzing_time`/`end_analyzing_time`. Retry (`run_from_stage`) resets the start/end timestamps of the target stage and all downstream stages to NULL before re-running (frontend timeline duration = end − start; extracting/analyzing fall back to previous stage's end time only for legacy rows without start values).
- **大文本列一律 LONGTEXT**：`extraction_result.extracted_value` 与 `analysis_result.result_value` 用 LONGTEXT 而非 TEXT。TEXT 上限 65535 **字节**，utf8mb4 中文只能存约 21845 字，而 `search_type=page` + `use_llm=0` 会把整段原文当字段值落库（`_DEFAULT_PAGE_MAX_LENGTH` 默认 30000 **字符** ≈ 90000 字节，默认配置即超限）。字符/字节单位错配曾导致线上 `DataError 1406`（2026-07-28）。旧库靠 `init_service.py` 的 `longtext_migrations` 自动 `MODIFY COLUMN` 扩容（幂等；**新增此类扩容时务必改这一处，不要另起一段目标类型不同的迁移，否则每次启动来回 ALTER**）。
- **异常路径必须先 rollback**：DB 写失败后 SQLAlchemy 会话进入 DEACTIVE，此后任何 `execute` 都抛 `PendingRollbackError`。标记失败态统一走 `service/stage_status.py:mark_file_failed`（先 rollback 再写 progress/error，**自身异常一律吞掉**以免掩盖原始异常）；`run_extraction` / `run_analysis` 里「保存空值继续下一项」的 except 分支同样要先 `await session.rollback()`。漏了会把单字段失败放大成文件永久卡在 `*ing`。回归保护见 `tests/test_session_rollback_on_failure.py`。

### Document Type Isolation (`blue_print/doctype_router.py`)
Multi-type configuration support: each file is bound to one `type_id` (default `'default'`). Extraction fields and analysis rules are isolated per type — `extraction_service` / `analysis_service` look up `file.type_id` and filter `extraction_field` / `analysis_rule` by it. Configurations are NOT shared across types; sharing is done via explicit copy:
- `POST /doctype/{type_id}/copy_from` clones fields/rules from a source type into the target type. New `field_id`/`rule_id` are source-ID-based copy IDs (`A -> A_0002 -> A_0003`); `field_name`/`rule_name` stay unchanged. Rule `depend_fields` are remapped directly by source `field_id` to the new copied `field_id`; dependencies whose fields were not copied are returned to the caller (not silently dropped).
- After copy, the two copies are fully independent — editing one does not affect the other.
- Default type (`is_default=1`) cannot be deleted. Deleting a non-default type with files/configs requires `force=true` (cascades file content + Milvus + configs).
- 类型有**血缘 + 项目**两个附加维度。血缘:`is_template`(模板标记) + `parent_type_id`(复制来源,`copy_from`/`import` 自动记录),`POST /doctype/{id}/promote|demote` 切换模板标记。项目:`project_id`(可空=未分组) + `project` 表,对「模板 + 其血缘下游」分类,一个 type 属 ≤1 个项目,`default` 恒未分组。
- 项目接口:`GET/POST /doctype/projects`(列出带 `type_count` / 按 `project_id` upsert)、`DELETE /doctype/projects/{id}`(成员 `project_id` 置空、**不删 type**)、`POST /doctype/batch_assign_project`(`{type_ids, project_id}`,`project_id=null` 移出未分组)。**归类级联血缘**:入参每个 type 的所有 `parent_type_id` 传递后代(`_lineage_closure`)一并归入同一项目,`default` 跳过,返回 `{requested, affected, project_id}`(`affected` 含级联带入数)。`copy_from`/派生的新 type 未分组时**继承源项目**;`POST /doctype` 建档时写 `project_id`(仅新建生效,PUT 更新忽略)。
- `GET /doctype/list` 支持 `q/scope(all|template|copy)/project_id(`__ungrouped__`=未分组)/page/page_size/sort`;**传齐 page+page_size 返回 `{items,total}`,否则原样返回数组**(向后兼容)。计数用 3 条 GROUP BY 避免 N+1;响应含 `project_id/project_name`。
- 批量接口:`POST /doctype/batch_delete`(`{type_ids,force}`)、`POST /doctype/batch_assign_project`(见上)。
- 顶部导航**两级**:项目下拉(未分组 + 各项目) → **文档类型下拉只显示当前项目的 type**;切项目会把当前 type 重置为该项目首个(未分组回退 `default`)并联动刷新文件列表 + 字段/规则配置。管理弹窗(全屏单栏)每行「选用」=设为当前类型(同步当前项目);含**项目列 + 项目筛选下拉 + 批量「归入项目」+「管理项目」子弹窗**(建/改名/删项目);「+ 新建类型」统一三条造类型路径(空白/从类型派生/导入 JSON),新建默认落在当前项目(表单内「所属项目」下拉可改选,仅新建可见);行内 ⋯ 菜单含查看配置/复制为新类型/改名/模板标记/导出/删除。「只读查看配置」复用 `GET /doctype/{id}/export`。
- 存量副本若无 `parent_type_id`,初期需手工标模板;此后经 `copy_from`/派生新建的类型自动记录来源。

### Extraction System (`service/extraction_service.py`)
Three source types:
- **table** - Matches tables by name (exact/fuzzy/contains/llm), extracts via LLM with `<search_result>label</search_result>` placeholder system in prompts.
- **text** - 6 search methods: `context` (keyword+surrounding text), `section` (chapter matching), `rule` (keyword+stopword boundary), `chunk_db` (MySQL chunk search), `vector_db` (Milvus semantic search；`query_text` 同时作为占位符标签，结果以 `<search_result>query_text</search_result>` 注入), `page` (按 `page_range` 直接切 markdown 喂 LLM；占位符固定为 `<search_result>page_content</search_result>`，可配 `max_length` 末尾截断). Results injected into prompt via same placeholder system.
- **vl** - 三种基于 VL 视觉模型的端到端 PDF 抽取。直接读 `uploads/{file_id}.pdf`，跳过 MinerU 解析的 Markdown：
  - `vl_model`：指定页全部塞 VL 一次出 JSON。配置 `page_range`。
  - `vl_progressive`：分批扫描 + 伪历史累积 + 最后文本聚合。配置 `field_hints`、`batch_size`，可自定义 `batch_prompt_template`。
  - `vl_locate`：缩略图网格并行定位 + 关键页高清提取。配置 `field_hints`、`grid_pages`、`max_concurrent`，可自定义 `locate_prompt_template`。
  - **三种方法共用页码限定**：`vl_config.page_range`（`"all"` / `"1-3,5"`）+ `max_pages`（候选页上限，超出取前 N 页）。统一经 `utils/vl_client.py:resolve_target_pages` 收敛（解析 → 去重升序 → cap），三方法各自消费同一份目标页。`vl_locate` 的 `key_pages_limit` 是定位**后**的高清页上限，与定位**前**的 `max_pages` 是两个阶段的约束；`fallback_pages` 取候选页前 N 个（非文档前 N 页）。`vl_progressive` 的 `{total_pages}` 恒为文档总页数，另有可选占位符 `{scan_scope}`（老模板不含它也不报错）。`source_refs._vl` 新增 `target_pages` / `pages_capped`（存量数据无，需容错）。
  - VL 直接产出 `{value, reason}` JSON，**不**走文本 LLM 二次抽取；`source_refs` 存为 `{"_vl": {method, total_pages, key_pages, vl_total_tokens, ...}}`。
  - 全局并发 `vl_model.global_max_concurrency`（默认 8）通过 `utils/vl_client.py` 的 asyncio.Semaphore 治理。
  - PDF 字节由 `blue_print/file_router.py` 在上传时持久化到 `uploads/{file_id}.pdf`，由 DELETE / 批量删除 / 文档类型级联删除联动清理；启动时 `cleanup_orphan_pdfs` 兜底；另有 `storage` 保留策略按总量/时长滚动清理（见 Configuration 节）。
- **LLM 匹配提示词可配置**（`service/match_prompts.py`）：`table_match_type`/`section_match_type=llm` 时的匹配 prompt 分两段 —— 用户可编辑段存字段配置（**存储位置不对称**：表格在 `extraction_field.table_match_prompt` 列，章节在 `search_config.section_match_prompt`，**空值=用系统默认模板**，渲染结果与硬编码时代逐字一致），系统固定段 `MATCH_INDEX_OUTPUT_INSTRUCTION`（输出格式指令）由 `render_match_prompt` 恒定追加、用户改不到 —— 匹配结果靠 `re.findall(r"\d+")` 抓整数序号，用户改成「返回 JSON」解析器会把 JSON 里的数字一并当序号。占位符 `{table_list}`/`{section_list}` **必填**（缺了 422，schema 层校验），另有可选 `{query}`/`{quantity_hint}`；渲染用 `str.replace` 故字面 `{` 无需转义（区别于 VL 模板的 `str.format`）。`GET /extraction/match-prompt-defaults` 下发 `section`/`table`/`output_instruction`/`vl_batch`/`vl_locate` 五个默认模板供前端渲染「LLM 匹配高级设置」折叠面板（默认收起）并做「是否改过」比对，消除前端硬编码副本漂移。`table_match_prompt` 已纳入 `collect_depend_fields` 与 `resolve_advanced_field`（模板内可写 `<field_result>`），复制/导出/导入均携带并重映射。
- `source_refs` 落库时携带检索原文：每条 ref 含 `text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀），顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`。text/table 类 ref 另携带 `bboxes: [{page_num, bbox: [x0,y0,x1,y1], page_size: [w,h]}]`（MinerU 块级框，来自 page_mapping，供前端 PDF 高亮定位；page 类整页切片不挂）。vl 类（`_vl`）无检索文本/bbox 不受影响。`GET /file/{id}/extraction` 与回调 `field_done`/`stage_done` 均透出完整 `source_refs`。存量老数据无 `text`/`_texts`/`bboxes`（老文件 page_mapping 无 bbox，重新解析后才有），消费方需容错。
- **页码顶层化（`pages` / `source_pages`）**：页码是与 `value`/`reason` 平级的**顶层字段**，不在 `source_refs` 里。`pages` = 模型自报（text/table 的 LLM 输出除 `value`/`reason` 外可带 `pages`，取自注入文本的【第X页】标记，经 `parse_llm_json_response` 归一化：去重升序正整数，容错 `"第X页"`/逗号串/单值/范围串，解析不出为 `[]`），落库到 **`extraction_result.model_pages` 列**；VL 类 / `use_llm=0` / 模型未返回时为 `[]`。`source_pages` = **可用页码**，`pages` 优先、程序从 `source_refs` 算出的命中页兜底，由 `derive_source_pages()` 在**输出时现算**（纯派生值，**不落库**，避免与 `source_refs` 脱节），**键恒存在但可能为 `[]`**（失败字段 / 无命中 / `vl_progressive`）。
  - 三个纯函数在 `extraction_service.py`：`parse_page_num_str`（`ref.page_num` 归一，同时吃 int 与 str，**区间展开为逐页**，单区间上限 `_MAX_SOURCE_PAGE_SPAN=5` 取前 5 页 —— 区别于模型自报页防胡说的 `_MAX_PAGE_RANGE_SPAN=50`「退回起始页」）、`collect_ref_pages`（每条 ref 按精度降序取 `bboxes[].page_num` > `page_nums` > `page_num`，vl 读 `_vl.key_pages`）、`derive_source_pages`。
  - 三个 `extract_*_field` 与 `_extract_field_result` 返回**四元组** `(value, reason, source_refs, model_pages)`。
  - **存量兼容**：老数据的模型自报页在 `source_refs._model_pages`，`model_pages` 列为 NULL —— 读取一律走 `read_model_pages()`（两处都读），对外输出经 `strip_legacy_model_pages()` 剔除该键。
  - 前端 `renderModelPages`/`preferredLocatePage`/`collectLocateHits` 均改接**整个 item**（非 `source_refs`），直接读顶层两字段;原 `actualRefPages` 已删除（它用 `match(/^(\d+)/)` 只取首个数字，跨页 `"12-15"` 会丢 13/14/15）。
- **LLM 开关**：`extraction_field.use_llm`（TINYINT，默认 1，NULL/1 均视为启用）仅对 **text / table** 生效，**vl 恒需模型不读该开关**。置 0 时检索/表格匹配照常跑、`source_refs`（含 bbox）照常构建，但**跳过占位符校验与 LLM 调用**，直接把各 label 检索原文用 `\n---\n` 拼成 `value`（`_join_retrieved_text`），`reason` 固定为「未启用 LLM，直接返回检索原文」（常量 `NO_LLM_REASON`）。判定函数 `_is_llm_disabled`。use_llm=0 时 schema 层（`ExtractionFieldCreate` 校验器）与 `import` 均放宽提取提示词必填/占位符要求。`copy_from`/导出(`ExportFieldItem`)/导入原样携带。前端字段表单有「使用 LLM 提取」勾选框（VL 时隐藏）。
- **进阶字段（两层字段模型）**：`extraction_field.is_advanced`（TINYINT，默认 0，NULL/0=普通）把字段分两层，同表存储。`run_extraction` / `run_extraction_stream` **两阶段执行**：先按 priority 跑全部普通字段（`is_advanced=0`），把每个字段的 `extracted_value` 与 `source_refs._model_pages` 收进 `field_values` / `field_model_pages` 映射；再按 priority 跑进阶字段。**进阶字段只能引用普通字段**（不支持进阶引用进阶），保存时由 `blue_print/extraction_router.py:upsert_field` 校验引用目标同类型存在且 `is_advanced=0`，否则 400。
  - **字段引用**：进阶字段配置里可写 `<field_result>字段ID</field_result>` 占位符（关键词、表格匹配词、`query_text`/`section_pattern` 等 search_config 字符串、各类提示词、`vl_config.field_hints`/模板）。抽取前经 `resolve_advanced_field` 解析成「等价普通字段」（游离克隆 `_clone_field_transient`，**不改会话内 ORM 对象**）再交给现有抽取核心，核心函数零改动。缺失/空引用替换为空串并从列表类配置中剔除（避免空关键词全文命中）。相关纯函数：`collect_field_refs` / `resolve_field_refs` / `collect_depend_fields`。
  - **页码联动**：`search_type=page` 的进阶字段可配 `search_config.page_source_field`（来源普通字段 ID）+ 可选 `max_pages`。抽取时取来源字段的 **`source_pages`**（可用页码：模型自报优先、程序命中页兜底）经 `derive_page_range_from_model_pages` 派生 `page_range=[min,max]`（超 `max_pages` 则从最小页起收敛），**覆盖**手填 `page_range`；来源字段**无任何可用页码**（模型没自报且检索也没命中）才失败 —— 老逻辑只认模型自报页，模型不返回 `pages` 是常态，会让这类字段永久失败。
  - **VL 页码联动**：`source_type=vl` 的进阶字段可配 `vl_config.page_source_field`（+ 可选 `max_pages`），三种 `vl_method` 都支持。取来源普通字段的 `source_pages` 经 `pick_model_pages` 派生**离散**目标页（VL 按页出图，跳页零代价，不取中间页 —— 区别于 text `page` 检索的连续区间），改写成 `vl_config.page_range` 逗号串复用现有通路，`vl_service` 三方法无需感知联动。覆盖手填 `page_range`；来源字段无任何可用页码才失败。provenance 复用 `_page_link`，靠 `mode` 键区分：text 为 `"range"`（`derived_range`），VL 为 `"discrete"`（`derived_pages`）；存量数据无 `mode`，按 `"range"` 容错。
  - **provenance**：解析结果并入 `source_refs`，键为 `_resolved_refs`（`{field_id: 实际填入值}`）与 `_page_link`（`{source_field, source_pages, pages_from, mode, derived_range|derived_pages, capped}`，`pages_from` ∈ `"model"`/`"refs"` 标记页码来源），已加入 `_NON_REF_KEYS` 不被当作 ref 列表。
  - **依赖列**：`depend_fields`（JSON）由服务端 `collect_depend_fields` 扫描配置算出，非调用方指定；`list_fields` 回传。`copy_from` / 导出 / 导入均按新 field_id 重映射占位符与 `page_source_field`（`_remap_advanced_field_config`，在全部字段 id 映射建好后回填）。调试流 `test_field_extraction_stream` 对进阶字段先载入同类型普通字段的已有结果解析引用，并先推一个 `resolved_refs` 事件。
  - **成败判定**：`_is_extraction_success` 只认非元数据键（`_has_real_source_refs` 跳过 `_NON_REF_KEYS`）。否则进阶字段只要解析过引用就带 `_resolved_refs`，`bool(source_refs)` 恒真，「什么都没抽到」会被误记成成功。空引用记 `_empty_refs` + `logger.warning`，失败 reason 会点名是哪个上游字段没取到值。
  - **引用方保护**：`DELETE /extraction/fields/{id}` 在该字段被同类型进阶字段引用时返回 **409**（可 `force=true` 强删）；被引用的普通字段**不能**改成进阶字段（400）；禁用被引用字段放行但回 warning 文案。反查走 `_referencing_advanced_fields`（读 `depend_fields`）。
  - **调试接口**：`/extraction/test` 与 `/test/stream` 共用 `_build_temp_field`（透传 `is_advanced`），进阶字段先经 `resolve_advanced_field_from_db`（读该文件**已落库**的普通字段结果）解析再调试；非流式在响应里多回 `resolved_refs`，流式先推 `resolved_refs` 事件。
  - **复制/导入**：`_remap_advanced_field_config` 返回 `(attrs, missing)`，未被一起复制的引用记入 `missing_dependencies`（格式 `字段名::源field_id`），不静默留悬空引用。
  - 前端：字段配置页拆「普通字段」白框 + 「进阶字段」浅绿框两区，进阶表单用 K 按钮插入字段引用（chip 显示被引字段中文名，原始占位符存 `data-value`），`page` 检索多出「页码来源字段 + 最大页数」。

### Analysis System (`service/analysis_service.py`)
Two rule types:
- **judge** - LLM-based true/false determination. Uses `<field_result>field_id</field_result>` placeholders resolved with extraction results.
- **calc** - Mathematical expressions evaluated with `numexpr`. Same placeholder resolution.
- **独立分析接口** - `POST /analysis/run` 支持 sync/async/stream 与批量 items，实现位于 `service/analysis_run_service.py`。**items 与规则双层并发，闸门（`independent_analysis`）在规则层**——与 `task_file_analysis`（单文件规则并发）对称、共用 `global_analysis` 总池；闸门装在 item 层时 item 内规则串行，在途模型调用被卡死在 item 并发数上，总池吃不满。规则间无依赖（`execute_rule` 只读 `field_values`，异常已在内部收敛为失败结果），组内 `as_completed` **完成即推** `rule_done`、`index` 恒为 `priority, rule_id` 排序后的配置序，`results` 按 `index` 排序回填，聚合口径不变。async 用 `task_id` 推送 `rule_done/task_done/task_failed`。规则范围由 item 级 `rule_ids` 经 `plan_rules` 决定：**不传/null** = 全部启用规则且只跑 `depend_fields` 被输入键完整覆盖的（其余静默跳过，旧行为）；**`[]`** = 不跑任何规则；**显式点名** = 只跑指定规则且**跳过覆盖门控**，缺键的规则由 `execute_rule(require_coverage=True)` 产出 `success=false` 结果（reason 列出缺失字段）并计入 total/failed，避免点名的规则从 results 里凭空消失。点名了不存在/未启用/跨 type_id 的 rule_id **不报错**，收进 `AnalysisRunItemResult.unknown_rule_ids` 回传（sync 走 schema 序列化，故新增此类响应字段**必须同步加进 `AnalysisRunItemResult`**，否则被 pydantic 静默丢弃；async/stream 的 `task_done` 直接透传服务层 dict 不受影响）。
- **`/analysis/run` 的取值来源（`source`）**：`values`（默认）接收外部 `field_values`，仅按 `type_id` 读启用规则，不读文件提取结果、不写库；`file` 改按 item 的 `file_id` 读该文件已落库的 `extraction_result` 当字段值，`type_id` 省略则取 `files.type_id`。`persist=true`（仅 `source=file` 合法）把结果 upsert 进 `analysis_result`，但**从不修改 `files.progress`** —— 管线状态机只归 pipeline/retry 管。**并发安全约束**：`AsyncSession` 非并发安全，故所有读库集中在 `asyncio.gather` **之前**（`load_file_snapshots` 一次 2 条查询转成 `FileFieldSnapshot` 只读快照），写库集中在 gather **之后**（`persist_analysis_results`），并发段只碰快照与纯计算。`source=file` 的结果 `source_refs` 经 `merge_field_source_refs` 并入依赖字段的提取溯源（键为 `field_id`，与 `_web_search` 同级，与管线版一致）。**校验分层**：能从请求体判断的 → 422（Pydantic `model_validator`）；查库才知道的（文件不存在/type_id 不一致/无提取结果）→ 该 item 的 `AnalysisRunItemResult.error` 字段 + HTTP 200，不让一个坏 item 拖垮整批。
- **judge 网络搜索**：规则可配置 `web_search` JSON（`{"enabled", "query", "count", "freshness"}`，仅 judge 类型）。启用时执行判断前先调博查 Bocha AI 搜索（`utils/web_search.py`），搜索词支持 `<field_result>field_id</field_result>` 占位符拼接提取结果，搜索文本替换 expression 中的 `<web_search_result/>` 占位符（schema 层强制要求存在）。搜索失败不致命（占位符替换为失败提示继续判断）。溯源数据存 `source_refs._web_search`（`{query, results: [{name,url,siteName,datePublished,summary}], error?}`），`GET /file/{id}/analysis` 与回调 `rule_done` 透出。调试流新增 `web_search` 事件。`copy_from` 复制时 expression 与 `web_search.query` 中的 `<field_result>` 占位符随 depend_fields 重映射；导出/导入原样携带。全局参数在 `configs/config.yaml` 的 `web_search` 节。

### External Dependencies
- **MinerU** (`service/mineru_client.py`) - External PDF parsing service. Polled async via httpx. Returns md_content + middle_json; page_mapping is built via `build_page_mapping`（全局唯一锚 + LIS 单调清洗，数据源 middle_json，bbox 为原生页坐标）。存量文件可经 `POST /file/{id}/recompute_page_mapping` 用落库 md+middle_json 重算刷新（无需重传）。
- **LLM** (`utils/llm_client.py`) - OpenAI-compatible API (default: Qwen via DashScope). Retry with exponential backoff; skips 4xx errors except 429.
- **Embedding** - OpenAI-compatible embedding API (default: text-embedding-v4 via DashScope). Batches requests, truncates to 8192 chars.
- **Milvus** (`utils/milvus_client.py`) - Vector database for semantic search. Collection auto-created on startup with IVF_FLAT index.

## Key Patterns

- All services are async. Database operations use `AsyncSession` throughout.
- Pipeline tracks progress in `files.progress` column with timestamps per stage. On failure, progress is set to `*_failed` with error message.
- `init_service.py` handles crash recovery on startup - any `*ing` state is reset to `*_failed` and orphan data cleaned. Orphan cleanup scope varies by failure stage (e.g., `parsing_failed` cleans file_content + file_table + file_chunk + Milvus; `extracting_failed` cleans only extraction_result). Also normalizes legacy status names (`table_name_validating` -> `tableing`).
- Prompt templates use XML-style placeholders: `<search_result>label</search_result>` for extraction, `<field_result>field_id</field_result>` for analysis.
- LLM responses are parsed as JSON with fallback to regex extraction (`parse_llm_json_response`).
- `file_id` is generated from `(type_id, file_name, time.time_ns(), secrets.token_hex(8))` via SHA256[:32] (`utils/file_utils.py`). **Every upload produces a new `file_id`** — same filename re-uploaded always creates a fresh record and re-runs the full pipeline. There is no upload-side dedup / "retry from failed stage" path; failed files must be retried explicitly via `POST /file/{file_id}/retry/{stage}` (or deleted then re-uploaded).
- Tables from parsing are preserved as independent chunks (not split). Table names are prepended as context. Super-long tables (>8192 chars) are split on `</tr>`, `</td>`, or `\n` boundaries.

## Testing

- Tests in `tests/` use pytest-asyncio with `asyncio_mode = "auto"`.
- Test client fixture in `conftest.py` uses httpx `AsyncClient` with `ASGITransport`.
- Test database connectivity is required (no mocking of DB by default).
- Tests for extraction/analysis services use `monkeypatch` to mock LLM responses.

## Configuration

Config file: `configs/config.yaml`. Key sections: `server`, `mineru`, `chunking`, `embedding`, `milvus`, `mysql`, `extraction`, `table_name_validation`, `analysis`, `vl_model`, `web_search`, `storage`. Each maps to a Pydantic model in `utils/config.py`. `storage`（`max_total_bytes` / `max_retention_minutes` / `cleanup_interval_minutes`，默认 `0/0/10`，0=关闭）治理 `uploads` 下 PDF：启动时 + 每 `cleanup_interval_minutes` 分钟 + 每次上传后触发 `service/retention_service.py:enforce_pdf_retention`，只删物理 PDF（按 `create_time` 最旧优先淘汰 / 超时删除），不动数据库；被清文件的 PDF 预览与 VL 抽取返回 404。

## API Documentation

接口文档在 `docs/`，采用「README 枢纽 + 四层多文件」结构，配套「手写 Markdown + 生成 OpenAPI」双权威 + 机器化一致性校验：

- **`docs/README.md`** — 导航枢纽 + AUTOGEN 生成的 50 接口总览表。
- **`docs/api/`** — 接口参考，按资源前缀分文件（`overview`/`doctype`/`file`/`extraction`/`analysis`/`search`/`logs`）+ `callbacks.md`（异步回调契约）+ `sse.md`（流式事件）。每个 REST 接口套统一 9 段排布模板；参数/请求体/响应表由 AUTOGEN 从 openapi 生成（`<!-- AUTOGEN:<kind> METHOD /path -->` 区块，kind ∈ path-params/query-params/request-body/response/endpoint-index），回调/SSE 载荷表人写（openapi 不含）。
- **`docs/guides/`** — 任务导向手册（`extraction-config`/`analysis-config`/`source-refs`/`configuration`）。
- **`docs/reference/`** — `data-model.md`（DB schema 唯一权威）、`enums.md`（枚举 + progress 状态机）。
- **`docs/architecture/`** — `mineru-integration.md`。

**OpenAPI 单一来源**：富化逻辑集中在 `utils/openapi_enrich.py`（`ENRICHMENTS`/`PARAM_OVERRIDES`/`SCHEMA_DOCS`/`RESPONSE_DATA` 四类字典 + `enrich()`）。`app.py` 覆盖 `app.openapi` 复用它，使**活的 `/docs` Swagger 与导出的 `docs/openapi.json` 同源**；版本单源于 `pyproject.toml`（`get_version()`）。响应统一包在 `ResponseWrapper{data:Any}`，故各接口响应形态经 `RESPONSE_DATA` 映射把响应模型注入 `components`+`responses.200`（纯文档增强，不改运行时）。

**改了接口后的维护流程（三步）：**

```bash
uv run python scripts/gen_openapi.py        # 从 app 重生成 docs/openapi.json
uv run python scripts/gen_doc_tables.py      # 用 openapi 刷新所有 AUTOGEN 表格
uv run python scripts/check_docs_sync.py     # 校验:接口全集/版本/AUTOGEN 新鲜度,全绿才算同步
```

`check_docs_sync` 保证 md 与 openapi 一致（每个 openapi 接口都被 AUTOGEN 引用、无幽灵接口、pyproject/openapi/md 三处版本一致）；`gen_doc_tables`/`check_docs_sync` 均有单测（`tests/test_gen_doc_tables.py` / `tests/test_check_docs_sync.py`，fixture 在 `tests/fixtures/doc_tools/`）。
