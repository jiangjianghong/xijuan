# Page Extraction Middle JSON Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `search_type=page` obtain page content from MinerU's stored page structure, while retaining a clearly marked mapping fallback for legacy records.

**Architecture:** `utils/page_mapping.py` will project `middle_json.pdf_info` into ordered text segments with direct page provenance. Ordinary blocks use their `page_idx`; a cross-page table is emitted once from its owner block and records every covered page. The extraction snapshot builds this immutable projection once per file. Page extraction uses it whenever valid; otherwise it uses the existing Markdown mapping with a conservative leading-content fallback.

**Tech Stack:** Python 3.12, FastAPI service layer, pytest, SQLAlchemy async snapshots.

## Global Constraints

- Do not reparse PDF files or change persisted production records.
- Preserve one-pass MinerU handling and inject a complete cross-page table when its page range intersects the request.
- Do not claim exact per-page boundaries for legacy Markdown before its first trusted anchor.
- Keep `page_mapping` for existing full-text position and bbox consumers.

---

### Task 1: Define Page Projection Behavior With Pure Tests

**Files:**
- Create: `tests/test_page_projection.py`
- Modify: `tests/test_extraction_page.py`
- Modify: `tests/test_extraction_snapshot.py`

**Interfaces:**
- Consumes: `build_page_projection(middle_json)` and `select_page_projection(projection, start_page, end_page)`.
- Produces: Regression coverage for repeated covers, cross-page tables, image-only pages, and leading mapping fallbacks.

- [x] **Step 1: Write failing tests for projected ordinary blocks and cross-page tables**

```python
projection = build_page_projection(middle)
assert [item["page_num"] for item in select_page_projection(projection, 1, 2)] == [1, 2]
assert table_item["page_num"] == "2-3"
assert table_item["source_pages"] == [2, 3]
```

- [x] **Step 2: Run the projection tests to verify they fail**

Run: `uv run pytest tests/test_page_projection.py -v`

Expected: FAIL because the projection functions do not exist.

- [x] **Step 3: Write failing tests for legacy leading content and extraction provenance**

```python
result = slice_by_page_range("COVER1COVER2PAGE3", mapping, 1, 2, 30000)
assert result["text"] == "COVER1COVER2"
assert result["leading_unmapped"] is True
```

- [x] **Step 4: Run the focused page tests to verify the new cases fail**

Run: `uv run pytest tests/test_extraction_page.py -v`

Expected: FAIL because the current first-anchor logic returns an empty slice.

### Task 2: Build Middle JSON Page Projection

**Files:**
- Modify: `utils/page_mapping.py`
- Test: `tests/test_page_projection.py`

**Interfaces:**
- Consumes: MinerU `middle_json.pdf_info` blocks, block bboxes, and existing continuation-table detection.
- Produces: `build_page_projection(middle_json) -> list[dict] | None` and `select_page_projection(projection, start_page, end_page) -> list[dict]`.

- [x] **Step 1: Add a table-group collector that retains owner block identity**

```python
{
    "first_page": first_page,
    "last_page": last_page,
    "owner_page": page_num,
    "owner_block_index": block_index,
    "blocks": [(page_num, block, page_size), ...],
}
```

- [x] **Step 2: Emit ordered normal-block and table-group projection segments**

```python
{
    "page_num": 5 or "5-7",
    "source_pages": [5] or [5, 6, 7],
    "content": text,
    "bboxes": [{"page_num": 5, "bbox": [...], "page_size": [...]}],
    "mapping_quality": "middle_json",
}
```

- [x] **Step 3: Run projection tests and confirm they pass**

Run: `uv run pytest tests/test_page_projection.py -v`

Expected: PASS.

### Task 3: Route Page Extraction Through Projection With Safe Legacy Fallback

**Files:**
- Modify: `service/extraction_snapshot.py`
- Modify: `service/extraction_service.py`
- Modify: `tests/test_extraction_page.py`
- Modify: `tests/test_extraction_snapshot.py`

**Interfaces:**
- Consumes: `FileExtractionSnapshot.page_projection` and the existing page prompt contract.
- Produces: projection-backed refs with direct bboxes, `source_pages`, and `mapping_quality`; legacy refs with `leading_unmapped` provenance.

- [x] **Step 1: Add `page_projection` to the immutable extraction snapshot**

```python
page_projection = build_page_projection(fc_row.middle_json) if fc_row else None
```

- [x] **Step 2: Update `_extract_page_field` to prefer a valid projection**

```python
segments = select_page_projection(page_projection, start_page, end_page)
if page_projection is not None and not segments:
    return "", f"页码区间 {start_page}-{end_page} 无可提取文本", None, []
```

- [x] **Step 3: Correct legacy leading-anchor behavior**

```python
if start_page < first_anchor_page:
    slice_start = 0
    leading_unmapped = True
else:
    slice_start = first_anchor_in_requested_range["start_pos"]
```

- [x] **Step 4: Keep the debug preview on the same projection-aware path**

```python
segments = select_page_projection(build_page_projection(file_content.middle_json), start_p, end_p)
```

- [x] **Step 5: Run focused test suites**

Run: `uv run pytest tests/test_page_projection.py tests/test_extraction_page.py tests/test_extraction_snapshot.py -v`

Expected: PASS.

### Task 4: Verify Regression Surface

**Files:**
- Modify: `docs/superpowers/plans/2026-08-27-page-extraction-middle-json.md`

**Interfaces:**
- Consumes: focused test results and project test configuration.
- Produces: an accurate record of what passed and the known database-test environment blocker.

- [x] **Step 1: Run page-mapping regression tests**

Run: `uv run pytest tests/test_page_mapping.py tests/test_page_mapping_cross_page_table.py tests/test_page_mapping_table_caption.py tests/test_page_span_split.py -v`

- [x] **Step 2: Run the full suite and record its environmental result**

Run: `uv run pytest`

Expected: the suite remains blocked by the isolated worktree's missing local MySQL credentials unless a test database is configured.

- [ ] **Step 3: Commit the implementation and plan**

```bash
git add utils/page_mapping.py service/extraction_snapshot.py service/extraction_service.py tests/test_page_projection.py tests/test_extraction_page.py tests/test_extraction_snapshot.py docs/superpowers/plans/2026-08-27-page-extraction-middle-json.md
git commit -m "fix: derive page extraction from middle json"
```

## Verification Record

- `uv run pytest tests/test_page_projection.py tests/test_extraction_page.py tests/test_extraction_snapshot.py tests/test_page_mapping.py tests/test_page_mapping_cross_page_table.py tests/test_page_mapping_table_caption.py tests/test_page_span_split.py -q`: **89 passed**.
- `python -m compileall service/extraction_snapshot.py service/extraction_service.py utils/page_mapping.py`: passed.
- `uv run pytest`: **740 passed, 65 failed, 10 errors**. The failures are pre-existing integration tests that require a local MySQL `file_parser` database; this worktree has no usable `root@localhost` credential and pytest reports `OperationalError (1045)`. Page extraction, page mapping, and projection test files all passed during that run.
