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
- **`blue_print/`** - FastAPI routers (registered in `__init__.py:register_routers`). Prefix: `/file`, `/extraction`, `/analysis`, `/search`.
- **`service/`** - Business logic. Each service module corresponds to a pipeline stage.
- **`model/`** - SQLAlchemy async ORM (`tables.py`), Pydantic response schemas (`schemas.py`), database session management (`database.py`).
- **`utils/`** - Shared clients: `llm_client.py` (OpenAI-compatible chat/embeddings), `milvus_client.py` (Milvus vector DB), `config.py`, `file_utils.py`, `callback.py`, `page_mapping.py`.
- **`ui/`** - Static HTML/JS/CSS frontend, served at `/ui`. File detail view uses a centered modal with timeline, error display, and tabbed data views. Tables tab has a left sidebar (table names with page numbers) + right content (table preview) split layout.

### Pipeline Flow (`service/pipeline_service.py`)
The core orchestrator. Six stages: parsing → tableing → chunking → embedding → extracting → analyzing. Three execution modes:
- **async** - `run_pipeline()` in background task, optional `callback_url` for stage notifications
- **sync** - `run_pipeline()` awaited directly
- **stream** - `run_pipeline_stream()` yields SSE events per stage

Both `run_pipeline` and `run_from_stage` (retry from any stage) exist in sync/stream variants. Failed stages are retried by cleaning downstream data and re-running from that point.

### Table Name Validation (`service/parse_service.py`)
The **tableing** stage runs after parsing. `parse_tables()` extracts all `<table>` HTML blocks from the Markdown content, then concurrently calls LLM (`_extract_table_name_with_llm`) to identify each table's name from preceding context. Falls back to the last line before the table if LLM fails. Table names are truncated to 30 characters. Concurrency controlled by `table_name_validation.max_concurrency` config. Results stored in `file_table` with position and page info.

### Database (MySQL + async SQLAlchemy)
- `database.py` uses `aiomysql` async driver. `get_db()` is the FastAPI dependency for sessions.
- Key tables in `model/tables.py`: `files` (progress tracking with stage timestamps), `file_content` (raw MD + middle_json + page_mapping), `file_table` (extracted HTML tables), `file_chunk` (text chunks with positions), `extraction_field` (configurable field definitions), `analysis_rule` (judge/calc rule definitions), `extraction_result`, `analysis_result`.
- File progress states: `parsing` -> `tableing` -> `chunking` -> `embedding` -> `extracting` -> `analyzing` -> `complete`. Each can fail to `*_failed`.
- `files` table tracks timestamps per stage: `start_parsing_time`/`end_parsing_time`, `start_tableing_time`/`end_tableing_time`, `start_chunking_time`/`end_chunking_time`, `start_embedding_time`/`end_embedding_time`, `end_extracting_time`, `end_analyzing_time`.

### Extraction System (`service/extraction_service.py`)
Two source types:
- **table** - Matches tables by name (exact/fuzzy/contains/llm), extracts via LLM with `<search_result>label</search_result>` placeholder system in prompts.
- **text** - 5 search methods: `context` (keyword+surrounding text), `section` (chapter matching), `rule` (keyword+stopword boundary), `chunk_db` (MySQL chunk search), `vector_db` (Milvus semantic search). Results injected into prompt via same placeholder system.

### Analysis System (`service/analysis_service.py`)
Two rule types:
- **judge** - LLM-based true/false determination. Uses `<field_result>field_id</field_result>` placeholders resolved with extraction results.
- **calc** - Mathematical expressions evaluated with `numexpr`. Same placeholder resolution.

### External Dependencies
- **MinerU** (`service/mineru_client.py`) - External PDF parsing service. Polled async via httpx.
- **LLM** (`utils/llm_client.py`) - OpenAI-compatible API (default: Qwen via DashScope).
- **Embedding** - OpenAI-compatible embedding API (default: text-embedding-v4 via DashScope).
- **Milvus** (`utils/milvus_client.py`) - Vector database for semantic search. Collection auto-created on startup.

## Key Patterns

- All services are async. Database operations use `AsyncSession` throughout.
- Pipeline tracks progress in `files.progress` column with timestamps per stage. On failure, progress is set to `*_failed` with error message.
- `init_service.py` handles crash recovery on startup - any `*ing` state is reset to `*_failed` and orphan data cleaned. Also normalizes legacy status names (`table_name_validating` → `tableing`).
- Prompt templates use XML-style placeholders: `<search_result>label</search_result>` for extraction, `<field_result>field_id</field_result>` for analysis.
- LLM responses are parsed as JSON with fallback to regex extraction (`parse_llm_json_response`).
- `file_id` is deterministically generated from filename (via `utils/file_utils.py`), so re-uploading the same file hits the existing record.

## Testing

- Tests in `tests/` use pytest-asyncio with `asyncio_mode = "auto"`.
- Test client fixture in `conftest.py` uses httpx `AsyncClient` with `ASGITransport`.
- Test database connectivity is required (no mocking of DB by default).

## Configuration

Config file: `configs/config.yaml`. Key sections: `server`, `mineru`, `chunking`, `embedding`, `milvus`, `mysql`, `extraction`, `table_name_validation`, `analysis`. Each maps to a Pydantic model in `utils/config.py`.
