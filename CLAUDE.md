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
- **`ui/`** - Static HTML/JS/CSS frontend, served at `/ui`. File detail view uses a centered modal with timeline, error display, and tabbed data views. Tables tab has a left sidebar (table names with page numbers) + right content (table preview) split layout. Header has a doctype selector that scopes file list / field config / rule config to the current type.

### Pipeline Flow (`service/pipeline_service.py`)
The core orchestrator. Six stages: parsing → tableing → chunking → embedding → extracting → analyzing. Three execution modes:
- **async** - `run_pipeline()` in background task, optional `callback_url` for stage notifications
- **sync** - `run_pipeline()` awaited directly
- **stream** - `run_pipeline_stream()` yields SSE events per stage

Both `run_pipeline` and `run_from_stage` (retry from any stage) exist in sync/stream variants. Failed stages are retried by cleaning downstream data and re-running from that point.

### Async Callback Contract (`utils/callback.py`)
When `callback_url` is supplied to `run_pipeline` / `run_from_stage`, the orchestrator and the per-item services (`run_extraction`, `run_analysis`) POST status updates to that URL. Timeout is **2.5s** per call; failures are logged and swallowed (never affect the main flow).

**Payload shape:**
```json
// 阶段入口（每个阶段开始时各 1 次）
{"file_id": "...", "status": "extracting"}

// 单字段 / 单规则完成（仅 extracting 与 analyzing 阶段产生）
{"file_id": "...", "status": "extracting", "event": "field_done",
 "data": {"field_id", "field_name", "value", "reason", "source_refs",
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
- File progress states: `parsing` -> `tableing` -> `chunking` -> `embedding` -> `extracting` -> `analyzing` -> `complete`. Each can fail to `*_failed`.
- `files` table tracks timestamps per stage: `start_parsing_time`/`end_parsing_time`, `start_tableing_time`/`end_tableing_time`, `start_chunking_time`/`end_chunking_time`, `start_embedding_time`/`end_embedding_time`, `end_extracting_time`, `end_analyzing_time`.

### Document Type Isolation (`blue_print/doctype_router.py`)
Multi-type configuration support: each file is bound to one `type_id` (default `'default'`). Extraction fields and analysis rules are isolated per type — `extraction_service` / `analysis_service` look up `file.type_id` and filter `extraction_field` / `analysis_rule` by it. Configurations are NOT shared across types; sharing is done via explicit copy:
- `POST /doctype/{type_id}/copy_from` clones fields/rules from a source type into the target type. New `field_id`/`rule_id` are source-ID-based copy IDs (`A -> A_0002 -> A_0003`); `field_name`/`rule_name` stay unchanged. Rule `depend_fields` are remapped directly by source `field_id` to the new copied `field_id`; dependencies whose fields were not copied are returned to the caller (not silently dropped).
- After copy, the two copies are fully independent — editing one does not affect the other.
- Default type (`is_default=1`) cannot be deleted. Deleting a non-default type with files/configs requires `force=true` (cascades file content + Milvus + configs).
- 类型只有**血缘**这一个附加维度:`is_template`(模板标记) + `parent_type_id`(复制来源,`copy_from`/`import` 自动记录),`POST /doctype/{id}/promote|demote` 切换模板标记。**「项目」维度已彻底移除**(无 `project_id` 列、无 `project` 表、无相关接口)。
- `GET /doctype/list` 支持 `q/scope(all|template|copy)/page/page_size/sort`;**传齐 page+page_size 返回 `{items,total}`,否则原样返回数组**(向后兼容)。计数用 3 条 GROUP BY 避免 N+1。
- 批量接口:`POST /doctype/batch_delete`(`{type_ids,force}`)。
- 管理弹窗(全屏单栏):每行「选用」=设为当前类型(任意类型皆可,不限模板);「+ 新建类型」统一三条造类型路径(空白/从类型派生/导入 JSON);行内 ⋯ 菜单含查看配置/复制为新类型/改名/模板标记/导出/删除。顶部选择器只展示模板 + 默认 + 当前选中。「只读查看配置」复用 `GET /doctype/{id}/export`。
- 存量副本若无 `parent_type_id`,初期需手工标模板;此后经 `copy_from`/派生新建的类型自动记录来源。

### Extraction System (`service/extraction_service.py`)
Three source types:
- **table** - Matches tables by name (exact/fuzzy/contains/llm), extracts via LLM with `<search_result>label</search_result>` placeholder system in prompts.
- **text** - 6 search methods: `context` (keyword+surrounding text), `section` (chapter matching), `rule` (keyword+stopword boundary), `chunk_db` (MySQL chunk search), `vector_db` (Milvus semantic search), `page` (按 `page_range` 直接切 markdown 喂 LLM；占位符固定为 `<search_result>page_content</search_result>`，可配 `max_length` 末尾截断). Results injected into prompt via same placeholder system.
- **vl** - 三种基于 VL 视觉模型的端到端 PDF 抽取。直接读 `uploads/{file_id}.pdf`，跳过 MinerU 解析的 Markdown：
  - `vl_model`：指定页全部塞 VL 一次出 JSON。配置 `page_range`。
  - `vl_progressive`：分批扫描 + 伪历史累积 + 最后文本聚合。配置 `field_hints`、`batch_size`，可自定义 `batch_prompt_template`。
  - `vl_locate`：缩略图网格并行定位 + 关键页高清提取。配置 `field_hints`、`grid_pages`、`max_concurrent`，可自定义 `locate_prompt_template`。
  - VL 直接产出 `{value, reason}` JSON，**不**走文本 LLM 二次抽取；`source_refs` 存为 `{"_vl": {method, total_pages, key_pages, vl_total_tokens, ...}}`。
  - 全局并发 `vl_model.global_max_concurrency`（默认 8）通过 `utils/vl_client.py` 的 asyncio.Semaphore 治理。
  - PDF 字节由 `blue_print/file_router.py` 在上传时持久化到 `uploads/{file_id}.pdf`，由 DELETE / 批量删除 / 文档类型级联删除联动清理；启动时 `cleanup_orphan_pdfs` 兜底。
- `source_refs` 落库时携带检索原文：每条 ref 含 `text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀），顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`。vl 类（`_vl`）无检索文本不受影响。`GET /file/{id}/extraction` 与回调 `field_done`/`stage_done` 均透出完整 `source_refs`。存量老数据无 `text`/`_texts`，消费方需容错。

### Analysis System (`service/analysis_service.py`)
Two rule types:
- **judge** - LLM-based true/false determination. Uses `<field_result>field_id</field_result>` placeholders resolved with extraction results.
- **calc** - Mathematical expressions evaluated with `numexpr`. Same placeholder resolution.

### External Dependencies
- **MinerU** (`service/mineru_client.py`) - External PDF parsing service. Polled async via httpx.
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

Config file: `configs/config.yaml`. Key sections: `server`, `mineru`, `chunking`, `embedding`, `milvus`, `mysql`, `extraction`, `table_name_validation`, `analysis`, `vl_model`. Each maps to a Pydantic model in `utils/config.py`.
