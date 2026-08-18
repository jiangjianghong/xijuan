# Analysis Concurrency And Runtime Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement distinct per-file analysis, worker-global independent-analysis, and shared total-analysis limits, then expose them in the read-only runtime monitor with a static explanatory modal.

**Architecture:** Configuration becomes a one-way schema change with no legacy fallback. File-pipeline rules are snapshotted before concurrent, session-free computation and then persisted/emitted in rule order; independent-analysis items share one stable worker limiter while every rule from either source also enters `global_analysis`. The runtime API publishes fixed pool semantics, and the frontend imposes its own fixed five-group display order plus a static accessible help dialog.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy async, asyncio, pytest/pytest-asyncio, vanilla JavaScript, ECharts, Lucide, CSS, Node test runner, Playwright (bundled workspace dependency for visual verification).

## Global Constraints

- Canonical keys are exactly `concurrency.task_file_analysis`, `concurrency.independent_analysis`, and `concurrency.global_analysis`, with defaults `4`, `4`, and `8`.
- Delete `ConcurrencyConfig.task_analysis` and `AnalysisConfig.max_concurrency`; never derive a new value from either removed field.
- Runtime settings must reject the removed fields and remove them from YAML on the next successful save.
- `task_file_analysis` counts concurrent rules per `file_id`; `independent_analysis` counts items across all `/analysis/run` requests in the current worker; `global_analysis` counts complete rule executions from both sources.
- Each independent-analysis item keeps its rules sequential in `priority, rule_id` order; there is no per-request limiter.
- File-rule compute tasks must not receive or use an `AsyncSession`; database reads happen before concurrency and database writes happen sequentially afterward.
- File results, callbacks, and stream events remain in configured rule order even when computation finishes out of order.
- A single rule failure does not cancel sibling rules; external cancellation cancels and awaits outstanding file-rule tasks and always unregisters the file limiter.
- `global_analysis` encloses validation, optional web search, calc, judge, and custom execution; actual judge/custom chat calls continue to enter `global_llm` through the existing LLM client.
- The runtime monitor is read-only. Its explanatory numbers are static default examples and never interpolate snapshot or settings values.
- Runtime pool display order is fixed by frontend IDs, not API response order, and the five visual groups use the `3:3:3:1:1` ratio.
- At `1440x900`, `1280x720`, and `500x900`, the runtime page must have no horizontal scrollbar, overlapping labels, or clipped dialog controls.

---

## File Map

- `utils/config.py`: canonical configuration models and removal of the analysis legacy mapping.
- `configs/config.yaml`: deployed canonical keys and comments.
- `service/settings_service.py`: reject removed fields, purge them on save, and hot-update stable global limiters.
- `ui/js/settings.js`: canonical setting labels and helper text.
- `service/analysis_run_service.py`: worker-global independent item limiter and shared per-rule total limiter.
- `service/analysis_service.py`: immutable file-rule snapshots, session-free concurrent compute, ordered persistence/callback/stream output.
- `blue_print/runtime_router.py`: pool definitions, labels, scopes, constraints, and deterministic API records.
- `ui/index.html`: five-group matrix header and static help dialog markup.
- `ui/js/runtime-monitor.js`: fixed pool order, updated metadata, dialog lifecycle, and stable chart history.
- `ui/css/style.css`: rounded dialog/header controls and responsive five-group layout without horizontal scrolling.
- `design-mockups/concurrency-pools.html`: keep the approved visual reference and its contract test aligned with canonical pool IDs.
- `tests/`: focused backend, contract, JavaScript, and regression coverage.

---

### Task 1: Canonical Configuration And Runtime Settings

**Files:**
- Modify: `utils/config.py`
- Modify: `configs/config.yaml`
- Modify: `service/settings_service.py`
- Modify: `ui/js/settings.js`
- Modify: `tests/test_config_concurrency.py`
- Modify: `tests/test_settings_service.py`

**Interfaces:**
- Produces: `ConcurrencyConfig.task_file_analysis: int`, `ConcurrencyConfig.independent_analysis: int`, and existing `ConcurrencyConfig.global_analysis: int`.
- Produces: `SettingsService.update_config(*, base_version: str, changes: Mapping[str, Any], secrets: Mapping[str, Any]) -> dict[str, Any]` that strips `analysis.max_concurrency` and `concurrency.task_analysis` from the YAML document before validation/write.
- Consumes: existing `replace_limiters(validated.concurrency.model_dump(mode="python"))`; both new global keys may be created or resized through this call.

- [ ] **Step 1: Write failing configuration tests**

Add these tests to `tests/test_config_concurrency.py`:

```python
def test_analysis_concurrency_uses_only_canonical_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
analysis:
  max_concurrency: 19
concurrency:
  task_analysis: 23
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.concurrency.task_file_analysis == 4
    assert cfg.concurrency.independent_analysis == 4
    assert cfg.concurrency.global_analysis == 8
    assert not hasattr(cfg.concurrency, "task_analysis")
    assert not hasattr(cfg.analysis, "max_concurrency")


def test_canonical_analysis_concurrency_values_are_loaded(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
concurrency:
  task_file_analysis: 2
  independent_analysis: 3
  global_analysis: 5
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.concurrency.task_file_analysis == 2
    assert cfg.concurrency.independent_analysis == 3
    assert cfg.concurrency.global_analysis == 5
```

Add focused settings tests to `tests/test_settings_service.py`:

```python
@pytest.mark.parametrize(
    "changes, rejected_path",
    [
        ({"concurrency": {"task_analysis": 7}}, "concurrency.task_analysis"),
        ({"analysis": {"max_concurrency": 7}}, "analysis.max_concurrency"),
    ],
)
def test_update_rejects_removed_analysis_concurrency_fields(
    config_path: Path, changes: dict, rejected_path: str
):
    service = SettingsService(config_path)
    version = service.read_public_config()["version"]

    with pytest.raises(ConfigFieldError, match=re.escape(rejected_path)):
        service.update_config(base_version=version, changes=changes, secrets={})


def test_successful_save_purges_removed_analysis_concurrency_fields(config_path: Path):
    service = SettingsService(config_path)
    version = service.read_public_config()["version"]

    result = service.update_config(
        base_version=version,
        changes={"concurrency": {"independent_analysis": 6}},
        secrets={},
    )

    written = config_path.read_text(encoding="utf-8")
    document = YAML(typ="safe").load(written)
    assert "task_analysis" not in document["concurrency"]
    assert "max_concurrency" not in document["analysis"]
    assert document["table_name_validation"]["max_concurrency"] == 20
    assert result["config"]["concurrency"]["task_file_analysis"] == 4
    assert result["config"]["concurrency"]["independent_analysis"] == 6
    assert "task_analysis" not in result["config"]["concurrency"]
    assert "max_concurrency" not in result["config"]["analysis"]
```

Import `re` and `YAML` from `ruamel.yaml` in the test module. Extend its fixture YAML with a `concurrency` block containing `task_analysis: 4`; its existing `analysis.max_concurrency: 10` and `table_name_validation.max_concurrency: 20` then prove the purge is narrowly scoped.

- [ ] **Step 2: Run the focused tests and verify the old schema fails**

Run:

```powershell
uv run pytest tests/test_config_concurrency.py tests/test_settings_service.py -q
```

Expected: FAIL because `task_file_analysis` and `independent_analysis` do not exist, `task_analysis`/`analysis.max_concurrency` still exist, and settings accepts the removed fields.

- [ ] **Step 3: Replace the configuration schema and legacy mapping**

In `utils/config.py`, replace the two models with:

```python
class AnalysisConfig(BaseModel):
    calc_precision: int = Field(2, ge=0)
    judge_timeout: int = Field(30, ge=1)


class ConcurrencyConfig(BaseModel):
    global_llm: int = Field(16, ge=1)
    global_embedding: int = Field(4, ge=1)
    global_vl: int = Field(8, ge=1)
    global_table_validation: int = Field(10, ge=1)
    global_extraction: int = Field(8, ge=1)
    global_analysis: int = Field(8, ge=1)
    task_table_validation: int = Field(4, ge=1)
    task_extraction: int = Field(4, ge=1)
    task_file_analysis: int = Field(4, ge=1)
    independent_analysis: int = Field(4, ge=1)
    global_pipeline: int = Field(4, ge=1)
```

In `AppConfig.normalize_legacy_concurrency`, delete only the `"task_analysis": analysis.get("max_concurrency")` entry and its now-unused `analysis` local. Keep the unrelated table-validation and VL connection migrations intact.

- [ ] **Step 4: Purge removed YAML fields on successful settings saves**

Add this helper in `service/settings_service.py`:

```python
def _remove_retired_analysis_concurrency(document: MutableMapping[str, Any]) -> None:
    analysis = document.get("analysis")
    if isinstance(analysis, MutableMapping):
        analysis.pop("max_concurrency", None)
    concurrency = document.get("concurrency")
    if isinstance(concurrency, MutableMapping):
        concurrency.pop("task_analysis", None)
```

Import `MutableMapping` from `typing`. Call `_remove_retired_analysis_concurrency(document)` in `update_config()` after applying submitted changes/secrets and before `_validate_document(document)`. Do not alias either old path in `FIELD_ALIASES`; `_validate_changes()` must reject them.

- [ ] **Step 5: Update deployed YAML and setting controls**

Change the canonical block in `configs/config.yaml` to:

```yaml
# 模型通道、业务阶段、文件内任务与独立接口并发限制（当前 worker 内生效）
concurrency:
  global_llm: 16
  global_embedding: 4
  global_vl: 8
  global_table_validation: 10
  global_extraction: 8
  global_analysis: 8
  task_table_validation: 4
  task_extraction: 4
  task_file_analysis: 4
  independent_analysis: 4
  global_pipeline: 4
```

In the `concurrency` field list in `ui/js/settings.js`, use exactly:

```javascript
['global_analysis', '全局逻辑分析总并发', 'number', { min: 1 }],
['task_file_analysis', '单文件逻辑分析并发', 'number', { min: 1 }],
['independent_analysis', '独立逻辑分析并发', 'number', { min: 1 }],
```

Set the independent-analysis help text to `当前 worker 内所有独立分析请求合计同时执行的 item 数` and remove all “单请求”/“单任务逻辑分析” wording from this settings group.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
uv run pytest tests/test_config_concurrency.py tests/test_settings_service.py -q
rg -n "task_analysis|max_concurrency" utils/config.py service/settings_service.py ui/js/settings.js configs/config.yaml
```

Expected: all tests PASS; `rg` returns no removed analysis concurrency field (unrelated table/VL `max_concurrency` fields may still exist in their own models).

Commit:

```powershell
git add utils/config.py configs/config.yaml service/settings_service.py ui/js/settings.js tests/test_config_concurrency.py tests/test_settings_service.py
git commit -m "refactor: split analysis concurrency settings"
```

---

### Task 2: Worker-Global Independent Analysis Item Limiter

**Files:**
- Modify: `service/analysis_run_service.py`
- Modify: `tests/test_analysis_run_service.py`
- Modify: `tests/test_stage_concurrency.py`

**Interfaces:**
- Consumes: `get_config().concurrency.independent_analysis` and `get_config().concurrency.global_analysis`.
- Produces: one stable `get_limiter("independent_analysis", limit)` shared across every `run_analysis_batch()` call in the event loop.
- Preserves: `run_analysis_batch(items, session, *, on_rule_done=None, source="values", persist=False) -> Dict[str, Any]`, input result ordering, per-item rule ordering, callbacks, and optional persistence.

- [ ] **Step 1: Replace old per-request tests with a cross-request failing test**

Replace `_patch_max_concurrency` and its two old tests in `tests/test_analysis_run_service.py` with a helper that supplies canonical config and a test that launches two batches concurrently:

```python
def _patch_analysis_concurrency(monkeypatch, *, independent: int, total: int = 20):
    monkeypatch.setattr(
        analysis_run_service,
        "get_config",
        lambda: SimpleNamespace(
            analysis=SimpleNamespace(calc_precision=2),
            concurrency=SimpleNamespace(
                independent_analysis=independent,
                global_analysis=total,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_independent_item_limit_is_shared_across_concurrent_batches(monkeypatch):
    _patch_analysis_concurrency(monkeypatch, independent=2)
    active = 0
    peak = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute(rule, field_values, *, require_coverage=False):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    async def fake_load_rules(type_ids, session):
        return {type_id: [_rule("r1", [])] for type_id in type_ids}

    monkeypatch.setattr(analysis_run_service, "_load_rules_by_type", fake_load_rules)
    first = [{"type_id": "default", "biz_id": f"a{i}", "field_values": {}} for i in range(3)]
    second = [{"type_id": "default", "biz_id": f"b{i}", "field_values": {}} for i in range(3)]

    running = asyncio.gather(
        run_analysis_batch(first, object()),
        run_analysis_batch(second, object()),
    )
    await asyncio.wait_for(two_started.wait(), timeout=1)
    assert active == 2
    assert peak == 2
    release.set()
    await running
```

Add the ordered callback test:

```python
@pytest.mark.asyncio
async def test_independent_item_keeps_rule_and_callback_order(monkeypatch):
    _patch_analysis_concurrency(monkeypatch, independent=4)
    callbacks = []

    async def fake_load_rules(type_ids, session):
        return {"contract": [_rule("r1", [], 1), _rule("r2", [], 2)]}

    async def fake_execute(rule, field_values, *, require_coverage=False):
        return _success(rule)

    async def on_rule_done(item):
        callbacks.append(item["rule_id"])

    monkeypatch.setattr(analysis_run_service, "_load_rules_by_type", fake_load_rules)
    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    response = await run_analysis_batch(
        [{"type_id": "contract", "biz_id": "b1", "field_values": {}}],
        object(),
        on_rule_done=on_rule_done,
    )

    assert [row["rule_id"] for row in response["items"][0]["results"]] == ["r1", "r2"]
    assert callbacks == ["r1", "r2"]
```

- [ ] **Step 2: Run the new test and verify the request-local limiter leaks capacity**

Run:

```powershell
uv run pytest tests/test_analysis_run_service.py -k "independent_item_limit or rule_order" -v
```

Expected: FAIL because two requests each create their own `task_analysis` limiter instance, allowing the combined active count to exceed 2.

- [ ] **Step 3: Install one stable global item limiter**

In `service/analysis_run_service.py`:

1. Remove `register_task_limiter` and `unregister_task_limiter` imports.
2. Read limits without fallback:

```python
concurrency_cfg = get_config().concurrency
stage_limiter = get_limiter("global_analysis", concurrency_cfg.global_analysis)
item_limiter = get_limiter(
    "independent_analysis",
    concurrency_cfg.independent_analysis,
)
```

3. Keep the existing `run_item()` loop unchanged so rules remain sequential and each rule stays inside `stage_limiter.context(context)`.
4. Replace the request-local registration block with:

```python
async def run_item_guarded(
    item_index: int,
    item: Mapping[str, Any],
) -> Dict[str, Any]:
    metadata = {
        "stage": "independent_analysis",
        "task_id": str(item.get("biz_id", item_index)),
        "index": item_index,
    }
    async with item_limiter.context(metadata):
        return await run_item(item_index, item)

ordered_items = await asyncio.gather(*(
    run_item_guarded(index, item)
    for index, item in enumerate(items)
))
```

There is no registration, instance ID, or unregister `finally`: this limiter intentionally lives for the worker/event-loop lifetime.

- [ ] **Step 4: Update shared-stage fixtures and run regression tests**

Replace all `task_analysis`/`analysis.max_concurrency` fixture data in `tests/test_stage_concurrency.py` with:

```python
concurrency={
    "global_table_validation": 1,
    "task_table_validation": 3,
    "global_analysis": 2,
    "task_file_analysis": 4,
    "independent_analysis": 4,
},
analysis={"calc_precision": 2},
```

Run:

```powershell
uv run pytest tests/test_analysis_run_service.py tests/test_stage_concurrency.py -q
```

Expected: PASS, including the two concurrent batches sharing a peak of 2 items and existing `global_analysis` peak coverage.

- [ ] **Step 5: Commit**

```powershell
git add service/analysis_run_service.py tests/test_analysis_run_service.py tests/test_stage_concurrency.py
git commit -m "feat: share independent analysis capacity across requests"
```

---

### Task 3: Session-Free Concurrent File Rule Computation

**Files:**
- Modify: `service/analysis_service.py`
- Create: `tests/test_file_analysis_concurrency.py`

**Interfaces:**
- Produces: frozen `FileRuleSnapshot` and `FileRuleComputation` value objects with no ORM/session references.
- Produces: `_compute_file_rule(rule, field_values, field_source_refs, calc_precision) -> FileRuleComputation`.
- Produces: `_compute_file_rules(file_id, rules, field_values, field_source_refs, calc_precision) -> list[FileRuleComputation]` in input order.
- Consumes: `register_task_limiter("task_file_analysis", file_id, limit, metadata)`, `get_limiter("global_analysis", limit)`, and `unregister_task_limiter("task_file_analysis", file_id)`.

- [ ] **Step 1: Write failing pure-compute concurrency and lifecycle tests**

Create `tests/test_file_analysis_concurrency.py` with these imports, helpers, cleanup fixture, and cases:

```python
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from service import analysis_service
from service.analysis_service import FileRuleComputation, FileRuleSnapshot
from utils import concurrency


@pytest.fixture(autouse=True)
def clear_runtime_limiters():
    concurrency.clear_limiters()
    yield
    concurrency.clear_limiters()


def _install_limits(monkeypatch, *, file_limit: int, total_limit: int):
    monkeypatch.setattr(
        analysis_service,
        "get_config",
        lambda: SimpleNamespace(
            analysis=SimpleNamespace(calc_precision=2),
            concurrency=SimpleNamespace(
                task_file_analysis=file_limit,
                global_analysis=total_limit,
            ),
        ),
    )


def _rule(rule_id: str, *, rule_type: str = "judge") -> FileRuleSnapshot:
    return FileRuleSnapshot(
        rule_id=rule_id,
        rule_name=rule_id,
        rule_type=rule_type,
        depend_fields=(),
        expression=rule_id,
        web_search=None,
        system_prompt="",
        is_formatted=False,
        output_schema=None,
    )


def _computed(
    rule: FileRuleSnapshot,
    *,
    result: str = "true",
    success: bool = True,
    reason: str = "",
) -> FileRuleComputation:
    return FileRuleComputation(
        rule_id=rule.rule_id,
        rule_name=rule.rule_name,
        rule_type=rule.rule_type,
        result=result,
        reason=reason,
        input_values={},
        source_refs=None,
        success=success,
    )


@pytest.mark.asyncio
async def test_file_rules_obey_per_file_limit_and_keep_input_order(monkeypatch):
    _install_limits(monkeypatch, file_limit=2, total_limit=10)
    active = 0
    peak = 0

    async def fake_compute(rule, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep({"r1": .03, "r2": .02, "r3": .01}[rule.rule_id])
        active -= 1
        return _computed(rule, result=rule.rule_id)

    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    rules = [_rule("r1"), _rule("r2"), _rule("r3")]

    results = await analysis_service._compute_file_rules("f1", rules, {}, {}, 2)

    assert peak == 2
    assert [result.rule_id for result in results] == ["r1", "r2", "r3"]
    assert "task_file_analysis" not in concurrency.runtime_snapshot()["task_pools"]


@pytest.mark.asyncio
async def test_different_files_have_independent_file_limits(monkeypatch):
    _install_limits(monkeypatch, file_limit=1, total_limit=10)
    active = 0
    peak = 0

    async def fake_compute(rule, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1
        return _computed(rule)

    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    await asyncio.gather(
        analysis_service._compute_file_rules("f1", [_rule("a")], {}, {}, 2),
        analysis_service._compute_file_rules("f2", [_rule("b")], {}, {}, 2),
    )
    assert peak == 2


@pytest.mark.asyncio
async def test_file_rule_failure_does_not_cancel_siblings(monkeypatch):
    _install_limits(monkeypatch, file_limit=3, total_limit=3)

    async def fake_judge(expression, *, system_prompt=""):
        if expression == "r2":
            raise RuntimeError("r2 failed")
        await asyncio.sleep(.01)
        return "true", expression

    monkeypatch.setattr(analysis_service, "execute_judge", fake_judge)
    results = await analysis_service._compute_file_rules(
        "f1", [_rule("r1"), _rule("r2"), _rule("r3")], {}, {}, 2
    )

    assert [row.rule_id for row in results] == ["r1", "r2", "r3"]
    assert [row.success for row in results] == [True, False, True]
    assert results[1].reason == "r2 failed"


@pytest.mark.asyncio
async def test_file_rule_cancellation_awaits_tasks_and_unregisters_pool(monkeypatch):
    _install_limits(monkeypatch, file_limit=2, total_limit=2)
    two_started = asyncio.Event()
    active = 0
    cancelled = 0

    async def blocking_compute(rule, *args, **kwargs):
        nonlocal active, cancelled
        active += 1
        if active == 2:
            two_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise
        finally:
            active -= 1

    monkeypatch.setattr(analysis_service, "_compute_file_rule", blocking_compute)
    running = asyncio.create_task(
        analysis_service._compute_file_rules(
            "f1", [_rule("r1"), _rule("r2"), _rule("r3")], {}, {}, 2
        )
    )
    await asyncio.wait_for(two_started.wait(), timeout=1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert active == 0
    assert cancelled == 2
    assert "task_file_analysis" not in concurrency.runtime_snapshot()["task_pools"]
```

Do not use an `AsyncSession` or session-shaped fake anywhere in this pure-compute test block.

- [ ] **Step 2: Run the new test module and verify missing interfaces**

Run:

```powershell
uv run pytest tests/test_file_analysis_concurrency.py -v
```

Expected: FAIL during collection because `FileRuleSnapshot`, `FileRuleComputation`, and `_compute_file_rules` do not exist.

- [ ] **Step 3: Add immutable snapshots and pure rule execution**

In `service/analysis_service.py`, add `asyncio`, `copy`, and `dataclass` imports plus task-limiter imports. Define:

```python
@dataclass(frozen=True)
class FileRuleSnapshot:
    rule_id: str
    rule_name: str
    rule_type: str
    depend_fields: tuple[str, ...]
    expression: str
    web_search: dict[str, Any] | None
    system_prompt: str
    is_formatted: bool
    output_schema: Any | None

    @classmethod
    def from_orm(cls, rule: AnalysisRule) -> "FileRuleSnapshot":
        return cls(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            depend_fields=tuple(rule.depend_fields or ()),
            expression=rule.expression or "",
            web_search=copy.deepcopy(rule.web_search),
            system_prompt=rule.system_prompt or "",
            is_formatted=bool(rule.is_formatted),
            output_schema=copy.deepcopy(rule.output_schema),
        )


@dataclass(frozen=True)
class FileRuleComputation:
    rule_id: str
    rule_name: str
    rule_type: str
    result: str
    reason: str
    input_values: dict[str, str]
    source_refs: dict[str, Any] | None
    success: bool
```

Implement `_compute_file_rule(rule, field_values, field_source_refs, calc_precision)` as the single rule dispatcher. It must:

1. Build `input_values` and copied source refs from `depend_fields`.
2. Return `success=False` with the validation reason if `validate_field_values` fails.
3. Resolve fields and apply web search before judge/custom.
4. Call `execute_judge`, `execute_calc`, or `execute_custom` directly; remove `_run_analysis_model` from this path because the outer total-rule limiter now owns the complete execution.
5. Catch `Exception` and return one failed `FileRuleComputation` with `reason=str(exc)`; re-raise `asyncio.CancelledError`.

Use one local constructor so every exit supplies all dataclass fields:

```python
def finish(
    result: str,
    reason: str,
    success: bool,
) -> FileRuleComputation:
    return FileRuleComputation(
        rule_id=rule.rule_id,
        rule_name=rule.rule_name,
        rule_type=rule.rule_type,
        result=result,
        reason=reason,
        input_values=input_values,
        source_refs=source_refs or None,
        success=success,
    )

try:
    valid, reason = validate_field_values(rule.rule_type, list(rule.depend_fields), field_values)
    if not valid:
        return finish("", reason, False)
    resolved = resolve_expression(rule.expression, field_values)
    if rule.rule_type in {"judge", "custom"}:
        resolved, web_ref = await apply_web_search(resolved, rule.web_search, field_values)
        if web_ref:
            source_refs["_web_search"] = web_ref
    if rule.rule_type == "judge":
        value, reason = await execute_judge(resolved, system_prompt=rule.system_prompt)
    elif rule.rule_type == "calc":
        value, reason = await execute_calc(resolved, calc_precision)
    elif rule.rule_type == "custom":
        value, reason = await execute_custom(
            resolved,
            is_formatted=rule.is_formatted,
            output_schema=rule.output_schema,
            system_prompt=rule.system_prompt,
        )
    else:
        return finish("", f"未知规则类型: {rule.rule_type}", False)
    return finish(value, reason, True)
except asyncio.CancelledError:
    raise
except Exception as exc:
    logger.error("规则分析失败: rule_id={}, error={}", rule.rule_id, exc)
    return finish("", str(exc), False)
```

- [ ] **Step 4: Add nested file and total limiter orchestration**

Implement `_compute_file_rules` exactly around pure compute tasks:

```python
async def _compute_file_rules(
    file_id: str,
    rules: list[FileRuleSnapshot],
    field_values: dict[str, str],
    field_source_refs: dict[str, dict],
    calc_precision: int,
) -> list[FileRuleComputation]:
    limits = get_config().concurrency
    file_limiter = register_task_limiter(
        "task_file_analysis",
        file_id,
        limits.task_file_analysis,
        {"file_id": file_id, "stage": "analyzing"},
    )
    total_limiter = get_limiter("global_analysis", limits.global_analysis)

    async def guarded(rule: FileRuleSnapshot) -> FileRuleComputation:
        context = {"file_id": file_id, "stage": "analyzing", "rule_id": rule.rule_id}
        async with file_limiter.context(context):
            async with total_limiter.context(context):
                return await _compute_file_rule(
                    rule, field_values, field_source_refs, calc_precision
                )

    tasks = [asyncio.create_task(guarded(rule)) for rule in rules]
    try:
        return [await task for task in tasks]
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        unregister_task_limiter("task_file_analysis", file_id)
```

Keep the acquisition order file limiter then total limiter to avoid inconsistent nested semantics.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
uv run pytest tests/test_file_analysis_concurrency.py -q
```

Expected: PASS; snapshots disappear after success and cancellation, per-file peak is enforced, different files run independently, and a rule exception is converted to one failed result.

Commit:

```powershell
git add service/analysis_service.py tests/test_file_analysis_concurrency.py
git commit -m "feat: compute file analysis rules concurrently"
```

---

### Task 4: Ordered File Persistence, Callback, And Streaming Integration

**Files:**
- Modify: `service/analysis_service.py`
- Modify: `tests/test_file_analysis_concurrency.py`
- Modify: `tests/test_run_analysis_custom_dispatch.py`
- Modify: `tests/test_stage_concurrency.py`

**Interfaces:**
- Produces: `_load_file_analysis_context(file_id, session) -> tuple[list[FileRuleSnapshot], dict[str, str], dict[str, dict]]`.
- Produces: `_persist_file_computation(file_id, computation, session) -> None` with one sequential commit.
- Preserves: `run_analysis(file_id, session, callback_url=None) -> None` and `run_analysis_stream(file_id, session) -> AsyncIterator[dict]`.
- Consumes: Task 3 `_compute_file_rules(file_id, rules, field_values, field_source_refs, calc_precision)` ordered result list.

- [ ] **Step 1: Write failing integration tests for ordering and session isolation**

Add these integration tests to `tests/test_file_analysis_concurrency.py`. They use the Task 3 helpers and deliberately invert completion order:

```python
class _FinalSession:
    def __init__(self):
        self.execute_calls = 0
        self.commit_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1

    async def commit(self):
        self.commit_calls += 1


@pytest.mark.asyncio
async def test_run_analysis_persists_and_callbacks_in_rule_order(monkeypatch):
    _install_limits(monkeypatch, file_limit=3, total_limit=3)
    rules = [_rule("r1"), _rule("r2"), _rule("r3")]
    persisted = []
    callbacks = []
    compute_active = 0

    async def fake_load(file_id, session):
        return rules, {}, {}

    async def fake_compute(rule, *args, **kwargs):
        nonlocal compute_active
        compute_active += 1
        await asyncio.sleep({"r1": .03, "r2": .02, "r3": .01}[rule.rule_id])
        compute_active -= 1
        return _computed(rule, result=rule.rule_id)

    async def fake_persist(file_id, item, session):
        assert compute_active == 0
        persisted.append(item.rule_id)
        await asyncio.sleep(0)

    async def fake_callback(url, file_id, status, *, event=None, data=None):
        if event == "rule_done":
            callbacks.append(data["rule_id"])

    monkeypatch.setattr(analysis_service, "_load_file_analysis_context", fake_load)
    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    monkeypatch.setattr(analysis_service, "_persist_file_computation", fake_persist)
    monkeypatch.setattr(analysis_service, "notify_callback", fake_callback)
    session = _FinalSession()

    await analysis_service.run_analysis("f1", session, "http://callback.test")

    assert persisted == ["r1", "r2", "r3"]
    assert callbacks == ["r1", "r2", "r3"]
    assert session.execute_calls == 1
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_run_analysis_stream_yields_in_rule_order(monkeypatch):
    _install_limits(monkeypatch, file_limit=3, total_limit=3)
    rules = [_rule("r1"), _rule("r2"), _rule("r3")]
    persisted = []

    async def fake_load(file_id, session):
        return rules, {}, {}

    async def fake_compute(rule, *args, **kwargs):
        await asyncio.sleep({"r1": .03, "r2": .02, "r3": .01}[rule.rule_id])
        return _computed(rule, result=rule.rule_id)

    async def fake_persist(file_id, item, session):
        persisted.append(item.rule_id)

    monkeypatch.setattr(analysis_service, "_load_file_analysis_context", fake_load)
    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    monkeypatch.setattr(analysis_service, "_persist_file_computation", fake_persist)
    rows = [
        row async for row in analysis_service.run_analysis_stream("f1", _FinalSession())
    ]

    assert [row["rule_id"] for row in rows] == ["r1", "r2", "r3"]
    assert [row["current"] for row in rows] == [1, 2, 3]
    assert persisted == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_file_and_independent_rules_share_global_analysis(monkeypatch):
    from service import analysis_run_service

    limits = SimpleNamespace(
        task_file_analysis=4,
        independent_analysis=4,
        global_analysis=2,
    )
    config = SimpleNamespace(
        analysis=SimpleNamespace(calc_precision=2),
        concurrency=limits,
    )
    monkeypatch.setattr(analysis_service, "get_config", lambda: config)
    monkeypatch.setattr(analysis_run_service, "get_config", lambda: config)
    active = 0
    peak = 0

    async def probe():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1

    async def fake_file_compute(rule, *args, **kwargs):
        await probe()
        return _computed(rule)

    independent_rule = analysis_run_service.AnalysisRuleSnapshot(
        rule_id="independent",
        type_id="contract",
        rule_name="independent",
        rule_type="judge",
        expression="",
        system_prompt="",
        depend_fields=[],
        web_search=None,
        priority=1,
        enabled=1,
    )

    async def fake_load_rules(type_ids, session):
        return {"contract": [independent_rule]}

    async def fake_independent_execute(rule, field_values, *, require_coverage=False):
        await probe()
        return {
            "rule_id": rule.rule_id,
            "rule_name": rule.rule_name,
            "rule_type": rule.rule_type,
            "result": "true",
            "reason": "",
            "input_values": {},
            "source_refs": None,
            "success": True,
        }

    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_file_compute)
    monkeypatch.setattr(analysis_run_service, "_load_rules_by_type", fake_load_rules)
    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_independent_execute)
    await asyncio.gather(
        analysis_service._compute_file_rules(
            "f1", [_rule("f1"), _rule("f2"), _rule("f3")], {}, {}, 2
        ),
        analysis_run_service.run_analysis_batch(
            [
                {"type_id": "contract", "biz_id": f"b{i}", "field_values": {}}
                for i in range(3)
            ],
            object(),
        ),
    )

    assert peak == 2
```

The `assert compute_active == 0` persistence guard proves database work begins only after all concurrent compute has settled. The compute helper signature has no session parameter, so a production compute task cannot capture the session without breaking the focused tests.

- [ ] **Step 2: Run focused tests and verify serial legacy paths fail**

Run:

```powershell
uv run pytest tests/test_file_analysis_concurrency.py tests/test_run_analysis_custom_dispatch.py tests/test_stage_concurrency.py -k "file or custom or global_analysis" -v
```

Expected: FAIL because `run_analysis` and `run_analysis_stream` still contain separate serial ORM loops and `_run_analysis_model` does not count calc/web-search work.

- [ ] **Step 3: Extract pre-concurrency loading and sequential persistence**

Implement `_load_file_analysis_context` to execute exactly three sequential reads: file/type, ordered enabled rules, and file extraction results. Order rules by both `AnalysisRule.priority` and `AnalysisRule.rule_id`; convert ORM rows immediately with `FileRuleSnapshot.from_orm` and return plain copied dictionaries.

Implement `_persist_file_computation` with the existing upsert contract:

```python
async def _persist_file_computation(
    file_id: str,
    item: FileRuleComputation,
    session: AsyncSession,
) -> None:
    stmt = select(AnalysisResult).where(
        AnalysisResult.file_id == file_id,
        AnalysisResult.rule_id == item.rule_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    values = {
        "result_value": item.result,
        "input_values": item.input_values,
        "reason": item.reason,
        "source_refs": item.source_refs,
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
    else:
        session.add(AnalysisResult(file_id=file_id, rule_id=item.rule_id, **values))
    await session.commit()
```

Add this sequential recovery wrapper and import `replace` from `dataclasses`:

```python
async def _persist_file_computation_safely(
    file_id: str,
    item: FileRuleComputation,
    session: AsyncSession,
) -> FileRuleComputation:
    try:
        await _persist_file_computation(file_id, item, session)
        return item
    except Exception as exc:
        logger.error("分析结果落库失败: rule_id={}, error={}", item.rule_id, exc)
        await rollback_if_broken(session)
        failed = replace(item, result="", reason=str(exc), source_refs=None, success=False)
        try:
            await _persist_file_computation(file_id, failed, session)
        except Exception as retry_exc:
            logger.error("失败结果落库失败: rule_id={}, error={}", item.rule_id, retry_exc)
            await rollback_if_broken(session)
        return failed
```

Do not put `session` in any task closure passed to `asyncio.create_task`.

- [ ] **Step 4: Rewrite both public file-analysis paths around shared compute**

Add explicit outward-shape helpers:

```python
def _callback_item(item: FileRuleComputation, index: int, total: int) -> dict[str, Any]:
    return {
        "rule_id": item.rule_id,
        "rule_name": item.rule_name,
        "rule_type": item.rule_type,
        "result": item.result,
        "reason": item.reason,
        "input_values": item.input_values,
        "source_refs": item.source_refs,
        "success": item.success,
        "index": index,
        "total": total,
    }


def _stream_item(item: FileRuleComputation, index: int, total: int) -> dict[str, Any]:
    return {
        "rule_id": item.rule_id,
        "rule_name": item.rule_name,
        "rule_type": item.rule_type,
        "result_value": item.result,
        "input_values": item.input_values,
        "reason": item.reason,
        "source_refs": item.source_refs,
        "success": item.success,
        "current": index,
        "total": total,
    }
```

Both public functions must follow this sequence:

```python
rules, field_values, field_source_refs = await _load_file_analysis_context(file_id, session)
computed = await _compute_file_rules(
    file_id,
    rules,
    field_values,
    field_source_refs,
    get_config().analysis.calc_precision,
)
for index, computation in enumerate(computed, start=1):
    settled = await _persist_file_computation_safely(file_id, computation, session)
    outward = _callback_item(settled, index, len(computed))
    aggregated.append(outward)
    await notify_callback(
        callback_url, file_id, "analyzing", event="rule_done", data=outward
    )
```

For the stream function, each iteration assigns `settled = await _persist_file_computation_safely(file_id, computation, session)` and then executes `yield _stream_item(settled, index, len(computed))`. After the ordered loop, keep the existing sequential `files.progress="complete"` update/commit. Normal mode derives `succeeded = sum(row["success"] for row in aggregated)`, `failed = total - succeeded`, and sends the existing ordered `stage_done` aggregate.

Delete `_run_analysis_model`; judge/custom model concurrency is already provided by `chat_completion` entering `global_llm`, while the outer `global_analysis` lease now spans the whole rule.

- [ ] **Step 5: Run file analysis and pipeline regressions**

Run:

```powershell
uv run pytest tests/test_file_analysis_concurrency.py tests/test_run_analysis_custom_dispatch.py tests/test_stage_concurrency.py tests/test_analysis_service.py tests/test_analysis_web_search.py tests/test_analysis_router.py -q
```

Expected: PASS, with ordered emissions and a combined `global_analysis` peak no greater than the configured limit.

- [ ] **Step 6: Commit**

```powershell
git add service/analysis_service.py tests/test_file_analysis_concurrency.py tests/test_run_analysis_custom_dispatch.py tests/test_stage_concurrency.py
git commit -m "refactor: persist concurrent file analysis in order"
```

---

### Task 5: Runtime API Pool Semantics

**Files:**
- Modify: `blue_print/runtime_router.py`
- Modify: `tests/test_runtime_router.py`
- Modify: `tests/test_concurrency_runtime.py`

**Interfaces:**
- Produces global pool `independent_analysis` with `scope="global"`, group `独立接口`, and limit from canonical config.
- Produces task pool `task_file_analysis` with `scope="task"`, group `文件内任务`, and task-instance metrics.
- Removes `task_analysis` from all API responses and events generated by new analysis work.

- [ ] **Step 1: Update API contract tests first**

Change the expected set in `tests/test_runtime_router.py` to the eleven canonical IDs:

```python
{
    "global_llm", "global_embedding", "global_vl",
    "global_table_validation", "global_extraction", "global_analysis",
    "task_table_validation", "task_extraction", "task_file_analysis",
    "independent_analysis", "global_pipeline",
}
```

Add assertions:

```python
independent = next(pool for pool in data["pools"] if pool["id"] == "independent_analysis")
assert independent["scope"] == "global"
assert independent["group"] == "独立接口"
assert independent["constraints"] == ["global_analysis"]

file_analysis = next(pool for pool in data["pools"] if pool["id"] == "task_file_analysis")
assert file_analysis["scope"] == "task"
assert file_analysis["group"] == "文件内任务"
assert file_analysis["constraints"] == ["global_analysis"]
assert all(pool["id"] != "task_analysis" for pool in data["pools"])
```

Add a `tests/test_concurrency_runtime.py` case that registers `task_file_analysis` for `file-1`, acquires it, checks `busiest_active == 1`, and verifies it disappears after `unregister_task_limiter`.

- [ ] **Step 2: Run the contract tests and verify old IDs fail**

Run:

```powershell
uv run pytest tests/test_runtime_router.py tests/test_concurrency_runtime.py -q
```

Expected: FAIL because the router still publishes `task_analysis` and has no independent global record.

- [ ] **Step 3: Replace router pool definitions and constraints**

Use these exact definitions in `blue_print/runtime_router.py`:

```python
_GLOBAL_POOLS = (
    ("global_llm", "文本 LLM", "模型通道", "模型通道"),
    ("global_embedding", "Embedding", "模型通道", "模型通道"),
    ("global_vl", "VL 视觉", "模型通道", "模型通道"),
    ("global_table_validation", "表名校验", "业务阶段", "表名校验"),
    ("global_extraction", "字段抽取", "业务阶段", "字段抽取"),
    ("global_analysis", "逻辑分析总池", "业务阶段", "逻辑分析"),
    ("independent_analysis", "独立分析", "独立接口", "独立分析"),
)
_TASK_POOLS = (
    ("task_table_validation", "文件内表名校验", "文件内任务", "单文件表名"),
    ("task_extraction", "文件内字段抽取", "文件内任务", "单文件抽取"),
    ("task_file_analysis", "文件内逻辑分析", "文件内任务", "单文件分析"),
)
_POOL_CONSTRAINTS = {
    "global_table_validation": ["global_llm"],
    "global_extraction": ["global_llm", "global_embedding", "global_vl"],
    "global_analysis": ["global_llm"],
    "task_table_validation": ["global_table_validation", "global_llm"],
    "task_extraction": ["global_extraction"],
    "task_file_analysis": ["global_analysis"],
    "independent_analysis": ["global_analysis"],
}
```

The `global_analysis -> global_llm` relation is conditional; keep that nuance in explanatory copy rather than inventing a conditional field in the API.

- [ ] **Step 4: Run tests and commit**

Run:

```powershell
uv run pytest tests/test_runtime_router.py tests/test_concurrency_runtime.py -q
```

Expected: PASS and the endpoint contains no `task_analysis`.

Commit:

```powershell
git add blue_print/runtime_router.py tests/test_runtime_router.py tests/test_concurrency_runtime.py
git commit -m "feat: expose layered analysis pools in runtime API"
```

---

### Task 6: Five-Group Runtime UI And Static Help Dialog

**Files:**
- Modify: `ui/index.html`
- Modify: `ui/js/runtime-monitor.js`
- Modify: `ui/css/style.css`
- Modify: `design-mockups/concurrency-pools.html`
- Modify: `tests/js/runtime_monitor.test.js`
- Modify: `tests/test_concurrency_monitor_mockup.py`
- Create: `tests/test_runtime_monitor_ui_contract.py`
- Create: `tests/browser/runtime_monitor_visual.js`

**Interfaces:**
- Produces: `RuntimeMonitor.orderedPools(snapshot)` using fixed ID order.
- Produces: `RuntimeMonitor.openHelp()` and `RuntimeMonitor.closeHelp(restoreFocus=true)`.
- Consumes: runtime API canonical IDs from Task 5; does not consume API numbers for help copy.
- Preserves: `activate()`, `deactivate()`, refresh/error-last-snapshot behavior, details drawer, chart clicks, and ECharts resize/dispose lifecycle.

- [ ] **Step 1: Write failing ordering and static-dialog contract tests**

Add to `tests/js/runtime_monitor.test.js`:

```javascript
test('orderedPools uses canonical five-group order regardless of API order', () => {
    const ids = [
        'independent_analysis', 'task_file_analysis', 'global_vl',
        'global_analysis', 'global_llm', 'global_pipeline',
        'task_extraction', 'global_extraction', 'global_embedding',
        'task_table_validation', 'global_table_validation',
    ];
    const snapshot = RuntimeMonitor.normalizeSnapshot({
        pools: ids.map(id => ({
            id,
            scope: id.startsWith('task_') ? 'task' : 'global',
            limit: 4,
            per_instance_limit: 4,
            active: 0,
            queued: 0,
        })),
    });

    assert.deepEqual(RuntimeMonitor.orderedPools(snapshot).map(pool => pool.id), [
        'global_llm', 'global_embedding', 'global_vl',
        'global_table_validation', 'global_extraction', 'global_analysis',
        'task_table_validation', 'task_extraction', 'task_file_analysis',
        'independent_analysis', 'global_pipeline',
    ]);
});
```

Create `tests/test_runtime_monitor_ui_contract.py` with complete file loading and assertions:

```python
from pathlib import Path


ROOT = Path(__file__).parents[1]
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
javascript = (ROOT / "ui" / "js" / "runtime-monitor.js").read_text(encoding="utf-8")
css = (ROOT / "ui" / "css" / "style.css").read_text(encoding="utf-8")
compact_css = "".join(css.split())


def test_runtime_help_dialog_is_static_accessible_and_read_only():
    assert 'id="runtime-help-open"' in html
    assert 'data-lucide="circle-help"' in html
    assert 'id="runtime-help-dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'id="runtime-help-close"' in html
    assert "默认配置示例 · 当前 worker · 只读监控" in html
    assert "所有独立分析请求合计最多同时处理 4 个 item" in html
    assert "两类来源合计最多同时执行 8 条规则" in html
    assert "openHelp()" in javascript
    assert "closeHelp(" in javascript
    assert "task_analysis" not in html + javascript


def test_runtime_matrix_uses_five_groups_without_horizontal_scroll():
    for label in ("模型通道", "业务阶段", "文件内任务", "独立接口", "管线"):
        assert label in html
    assert "grid-template-columns:3fr 3fr 3fr 1fr 1fr" in compact_css
    assert ".runtime-design-matrix-shell{min-width:0;overflow-x:hidden" in compact_css
```

Update `tests/test_concurrency_monitor_mockup.py` pool IDs from `task_analysis` to both `task_file_analysis` and `independent_analysis`, and add the help control ID/copy to its required contract.

- [ ] **Step 2: Run frontend contracts and verify failures**

Run:

```powershell
node --test tests/js/runtime_monitor.test.js
uv run pytest tests/test_runtime_monitor_ui_contract.py tests/test_concurrency_monitor_mockup.py -q
```

Expected: FAIL because ordering follows API scope/order, the fourth/fifth groups and canonical IDs are absent, and there is no help dialog.

- [ ] **Step 3: Implement fixed canonical ordering and chart groups**

In `ui/js/runtime-monitor.js`, define:

```javascript
const POOL_ORDER = [
    'global_llm', 'global_embedding', 'global_vl',
    'global_table_validation', 'global_extraction', 'global_analysis',
    'task_table_validation', 'task_extraction', 'task_file_analysis',
    'independent_analysis', 'global_pipeline',
];

function orderedPools(snapshot) {
    const byId = new Map([
        ...snapshot.globalPools.map(pool => [pool.id, { ...pool, capacity: pool.limit }]),
        ...snapshot.taskPools.map(pool => [pool.id, { ...pool, limit: pool.capacity }]),
        [snapshot.pipeline.id, { ...snapshot.pipeline, capacity: snapshot.pipeline.limit }],
    ]);
    return POOL_ORDER.map(id => byId.get(id)).filter(Boolean);
}
```

Expose it as `RuntimeMonitor.orderedPools = orderedPools` and replace every `allPools(snapshot)` use with `orderedPools(snapshot)`. Replace `task_analysis` metadata/compact labels with `task_file_analysis`; add `independent_analysis`. Update the ECharts `markArea` ranges to `0-2`, `3-5`, `6-8`, `9-9`, and `10-10`.

Keep `appendFixedHistory` at exactly 60 slots with leading `null` values so the overall line length never changes while the first samples arrive.

- [ ] **Step 4: Add static help markup with all pool explanations**

In `ui/index.html`, place this button immediately before refresh and add the backdrop/dialog after the runtime detail drawer:

```html
<button id="runtime-help-open" class="runtime-design-icon-button" type="button"
        title="查看并发池说明" aria-label="查看并发池说明">
    <i data-lucide="circle-help"></i>
</button>

<div id="runtime-help-backdrop" class="runtime-help-backdrop" aria-hidden="true">
    <section id="runtime-help-dialog" class="runtime-help-dialog" role="dialog"
             aria-modal="true" aria-labelledby="runtime-help-title" aria-hidden="true">
        <header class="runtime-help-header">
            <div>
                <h2 id="runtime-help-title">并发池说明</h2>
                <p>默认配置示例 · 当前 worker · 只读监控</p>
            </div>
            <button id="runtime-help-close" class="runtime-design-icon-button" type="button"
                    title="关闭并发池说明" aria-label="关闭并发池说明">
                <i data-lucide="x"></i>
            </button>
        </header>
        <div class="runtime-help-body">
            <section class="runtime-help-group">
                <h3>模型通道</h3>
                <article class="runtime-help-item"><h4>文本 LLM</h4><code>concurrency.global_llm</code><p>统计当前 worker 内正在执行的文本模型 HTTP 请求。默认容量 16；表名校验、文本字段抽取以及 judge/custom 分析调用共同竞争，calc 不占用。</p></article>
                <article class="runtime-help-item"><h4>Embedding</h4><code>concurrency.global_embedding</code><p>统计当前 worker 内正在执行的向量模型请求。默认容量 4；向量写入和 vector_db 检索共同竞争。</p></article>
                <article class="runtime-help-item"><h4>VL 视觉</h4><code>concurrency.global_vl</code><p>统计当前 worker 内正在执行的 VL 请求。默认容量 8；所有 VL 抽取方式共享，局部 max_concurrent 不能突破该总池。</p></article>
            </section>
            <section class="runtime-help-group">
                <h3>业务阶段</h3>
                <article class="runtime-help-item"><h4>表名校验</h4><code>concurrency.global_table_validation</code><p>统计所有文件正在执行的表名判断。默认容量 10，并继续受文本 LLM 默认容量 16 约束。</p></article>
                <article class="runtime-help-item"><h4>字段抽取</h4><code>concurrency.global_extraction</code><p>统计所有文件正在执行的字段抽取。默认容量 8，实际还可能进入文本 LLM、Embedding 或 VL 模型通道。</p></article>
                <article class="runtime-help-item"><h4>逻辑分析总池</h4><code>concurrency.global_analysis</code><p>文件管线规则与独立分析规则共享同一个总池。以默认配置为例，两类来源合计最多同时执行 8 条规则。judge 和 custom 规则还需要文本 LLM 槽位，calc 规则不占用文本模型通道。</p></article>
            </section>
            <section class="runtime-help-group">
                <h3>文件内任务</h3>
                <article class="runtime-help-item"><h4>文件内表名校验</h4><code>concurrency.task_table_validation</code><p>一个 PDF 里可能有很多张表，需要逐张调用 LLM 判断表名。以默认配置为例，每个文件最多同时校验 4 张表；所有文件合计又受“表名校验”全局上限 10 和“文本 LLM”上限 16 约束。文件 A 运行 4 个、文件 B 运行 4 个、文件 C 运行 2 个时，全局表名校验池已经达到 10，其他表格需要等待。</p></article>
                <article class="runtime-help-item"><h4>文件内字段抽取</h4><code>concurrency.task_extraction</code><p>一个 PDF 可以配置多个抽取字段。默认配置中的文件级上限是 4，但当前字段循环仍按顺序执行，所以单文件真实观测并发通常为 1；多个文件同时抽取时，共同受“字段抽取”全局上限 8 以及文本 LLM、Embedding、VL 模型通道约束。</p></article>
                <article class="runtime-help-item"><h4>文件内逻辑分析</h4><code>concurrency.task_file_analysis</code><p>一个 PDF 可以配置多条判断、计算或自定义规则。以默认配置为例，每个文件最多同时执行 4 条规则；不同文件继续共同竞争“逻辑分析总池”。当两个文件各运行 4 条规则时，默认总上限 8 已被占满，其他文件或独立分析规则需要等待。</p></article>
            </section>
            <section class="runtime-help-group">
                <h3>独立接口</h3>
                <article class="runtime-help-item"><h4>独立分析</h4><code>concurrency.independent_analysis</code><p>独立分析统计所有 /analysis/run 请求正在处理的 item，不限制单个请求。以默认配置为例，当前 worker 内所有独立分析请求合计最多同时处理 4 个 item。每个 item 内的规则仍按顺序执行，每条规则继续受“逻辑分析总池”默认上限 8 约束。</p></article>
            </section>
            <section class="runtime-help-group">
                <h3>管线</h3>
                <article class="runtime-help-item"><h4>文件管线</h4><code>concurrency.global_pipeline</code><p>统计完整文件管线任务。默认配置值 4 目前尚未接入调度，只显示“未接入”，不产生控制效果。</p></article>
            </section>
        </div>
    </section>
</div>
```

Replace the orienting HTML comment with five semantic `<section class="runtime-help-group">` elements for 模型通道、业务阶段、文件内任务、独立接口、管线. Each section contains plain `<article class="runtime-help-item">` rows, not nested cards. Use these exact key paragraphs:

```text
文件内表名校验：一个 PDF 里可能有很多张表，需要逐张调用 LLM 判断表名。以默认配置为例，每个文件最多同时校验 4 张表；所有文件合计又受“表名校验”全局上限 10 和“文本 LLM”上限 16 约束。文件 A 运行 4 个、文件 B 运行 4 个、文件 C 运行 2 个时，全局表名校验池已经达到 10，其他表格需要等待。

文件内字段抽取：一个 PDF 可以配置多个抽取字段。默认配置中的文件级上限是 4，但当前字段循环仍按顺序执行，所以单文件真实观测并发通常为 1；多个文件同时抽取时，共同受“字段抽取”全局上限 8 以及文本 LLM、Embedding、VL 模型通道约束。

文件内逻辑分析：一个 PDF 可以配置多条判断、计算或自定义规则。以默认配置为例，每个文件最多同时执行 4 条规则；不同文件继续共同竞争“逻辑分析总池”。当两个文件各运行 4 条规则时，默认总上限 8 已被占满，其他文件或独立分析规则需要等待。

独立分析：独立分析统计所有 `/analysis/run` 请求正在处理的 item，不限制单个请求。以默认配置为例，当前 worker 内所有独立分析请求合计最多同时处理 4 个 item。每个 item 内的规则仍按顺序执行，每条规则继续受“逻辑分析总池”默认上限 8 约束。

逻辑分析总池：文件管线规则与独立分析规则共享同一个总池。以默认配置为例，两类来源合计最多同时执行 8 条规则。judge 和 custom 规则还需要文本 LLM 槽位，calc 规则不占用文本模型通道。
```

Every row visibly includes its exact key, statistic unit, and constraint path. The exact keys for these five rows are `concurrency.task_table_validation`, `concurrency.task_extraction`, `concurrency.task_file_analysis`, `concurrency.independent_analysis`, and `concurrency.global_analysis`.

For the remaining pools, include these static default examples:

```text
文本 LLM：默认容量 16；表名校验、文本字段抽取以及 judge/custom 分析调用共同竞争；calc 不占用。
Embedding：默认容量 4；向量写入和 vector_db 检索共同竞争。
VL 视觉：默认容量 8；所有 VL 抽取方式共享，局部 max_concurrent 不能突破该总池。
表名校验：默认容量 10；所有文件的表名判断合计受限，并继续进入文本 LLM。
字段抽取：默认容量 8；所有文件合计受限，实际还可能进入文本 LLM、Embedding 或 VL。
文件管线：默认配置值 4 目前尚未接入调度，只显示“未接入”，不产生控制效果。
```

Each row displays the Chinese name, exact config key, statistic unit, constraint path, and example as plain semantic markup. Do not add cards inside section containers.

- [ ] **Step 5: Implement accessible modal lifecycle**

Extend runtime monitor state with `helpOpen`, `helpTriggerElement`, and `helpFocusTimer`. Bind:

```javascript
helpButton.addEventListener('click', () => this.openHelp());
helpClose.addEventListener('click', () => this.closeHelp());
helpBackdrop.addEventListener('click', event => {
    if (event.target === helpBackdrop) this.closeHelp();
});
```

`openHelp()` must save the trigger, add `.open`, set `aria-hidden="false"`, add a body scroll-lock class, and focus the close button. `closeHelp()` reverses all state and restores trigger focus when requested. Escape closes help before the detail drawer. `deactivate()` calls both `closeHelp(false)` and `closePool(false)`, clears pending focus timers, and always removes the body scroll lock.

- [ ] **Step 6: Apply responsive rounded styling**

In `ui/css/style.css`:

- Change the matrix header desktop tracks to `3fr 3fr 3fr 1fr 1fr`.
- Change the mobile header to five `minmax(0, 1fr)` tracks with short visible group names and hidden English labels.
- Ensure `.runtime-design-matrix-shell { min-width: 0; overflow-x: hidden; }` and the chart uses a width constrained to its parent.
- Use rounded icon buttons and dialog corners consistent with the existing `24px` header, with the dialog capped at `min(760px, calc(100vw - 24px))` and `max-height: min(760px, calc(100vh - 32px))`.
- Give only the dialog body `overflow-y:auto`; body scroll is locked while open.
- Add `overflow-wrap:anywhere`, `min-width:0`, and non-overlapping grid rules for the 500px viewport.
- Extend `prefers-reduced-motion` to disable modal transitions.

Mirror the canonical IDs, five group header, static help entry, and rounded responsive behavior in `design-mockups/concurrency-pools.html` so it remains a valid visual reference.

- [ ] **Step 7: Run automated frontend tests**

Run:

```powershell
node --test tests/js/runtime_monitor.test.js
uv run pytest tests/test_runtime_monitor_ui_contract.py tests/test_concurrency_monitor_mockup.py tests/test_runtime_router.py -q
```

Expected: PASS; static help wording remains unchanged when snapshots use non-default limits because no JavaScript writes into the help body.

- [ ] **Step 8: Add and run Playwright visual interaction verification**

Create `tests/browser/runtime_monitor_visual.js` with this complete self-contained verifier:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const uiRoot = path.resolve(__dirname, '../../ui');
const outputDir = path.join(os.tmpdir(), 'wanz-runtime-monitor');
const contentTypes = {
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.woff2': 'font/woff2',
};

const server = http.createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const requested = pathname === '/' ? '/index.html' : pathname;
    const filename = path.resolve(uiRoot, `.${requested}`);
    if (!filename.startsWith(`${uiRoot}${path.sep}`) || !fs.existsSync(filename)) {
        response.writeHead(404).end('not found');
        return;
    }
    response.writeHead(200, {
        'content-type': contentTypes[path.extname(filename)] || 'application/octet-stream',
    });
    fs.createReadStream(filename).pipe(response);
});

const globalPool = (id, label, group, limit, active, queued, constraints = []) => ({
    id, label, group, scope: 'global', limit, active, queued,
    completed: 12, wait_p95_ms: 18, status: queued ? 'pressure' : 'normal',
    constraints, tasks: [{ task_id: `${id}-task`, stage: 'analyzing' }],
});
const taskPool = (id, label, limit, active, queued, constraints) => ({
    id, label, group: '文件内任务', scope: 'task', per_instance_limit: limit,
    instance_count: 1, busiest_active: active, aggregate_active: active,
    aggregate_queued: queued, status: queued ? 'pressure' : 'normal', constraints,
    instances: [{ instance_id: 'file-1', active, queued, limit }],
});
const pools = [
    globalPool('global_llm', '文本 LLM', '模型通道', 16, 7, 0),
    globalPool('global_embedding', 'Embedding', '模型通道', 4, 2, 0),
    globalPool('global_vl', 'VL 视觉', '模型通道', 8, 3, 0),
    globalPool('global_table_validation', '表名校验', '业务阶段', 10, 4, 0, ['global_llm']),
    globalPool('global_extraction', '字段抽取', '业务阶段', 8, 5, 1, ['global_llm', 'global_embedding', 'global_vl']),
    globalPool('global_analysis', '逻辑分析总池', '业务阶段', 8, 6, 1, ['global_llm']),
    taskPool('task_table_validation', '文件内表名校验', 4, 3, 0, ['global_table_validation', 'global_llm']),
    taskPool('task_extraction', '文件内字段抽取', 4, 1, 0, ['global_extraction']),
    taskPool('task_file_analysis', '文件内逻辑分析', 4, 4, 1, ['global_analysis']),
    globalPool('independent_analysis', '独立分析', '独立接口', 4, 2, 1, ['global_analysis']),
    {
        id: 'global_pipeline', label: '文件管线', group: '管线', scope: 'global',
        limit: 4, active: 0, queued: 0, completed: 0, wait_p95_ms: 0,
        status: 'offline', connected: false, constraints: [], tasks: [],
    },
];
const connectedGlobals = pools.filter(pool => pool.scope === 'global' && pool.connected !== false);
const snapshot = {
    updated_at: '2026-08-18T12:00:00+08:00',
    scope: 'single-process',
    summary: {
        active: connectedGlobals.reduce((sum, pool) => sum + pool.active, 0),
        capacity: connectedGlobals.reduce((sum, pool) => sum + pool.limit, 0),
        queued: connectedGlobals.reduce((sum, pool) => sum + pool.queued, 0),
        hot_pools: connectedGlobals.filter(pool => pool.status === 'pressure').length,
        wait_p95_ms: 18,
    },
    pools,
    events: [{
        pool_id: 'global_analysis', type: 'queued', at: 1787025600,
        wait_ms: 18, context: { file_id: 'file-1', rule_id: 'rule-1' },
    }],
};

async function assertNoHorizontalOverflow(page) {
    const metrics = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        document: document.documentElement.scrollWidth,
        matrixClient: document.querySelector('.runtime-design-matrix-shell').clientWidth,
        matrixScroll: document.querySelector('.runtime-design-matrix-shell').scrollWidth,
    }));
    assert.ok(metrics.document <= metrics.viewport + 1, JSON.stringify(metrics));
    assert.ok(metrics.matrixScroll <= metrics.matrixClient + 1, JSON.stringify(metrics));
}

async function main() {
    fs.mkdirSync(outputDir, { recursive: true });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    const baseUrl = `http://127.0.0.1:${port}`;
    const browser = await chromium.launch({ headless: true });
    try {
        for (const viewport of [
            { width: 1440, height: 900 },
            { width: 1280, height: 720 },
            { width: 500, height: 900 },
        ]) {
            const page = await browser.newPage({ viewport });
            await page.route('**/runtime/concurrency', route => route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ code: 200, message: 'success', data: snapshot }),
            }));
            await page.goto(`${baseUrl}/index.html?page=runtime-monitor`);
            await page.waitForSelector('#runtime-pool-chart canvas');
            await assertNoHorizontalOverflow(page);

            await page.click('#runtime-help-open');
            await page.waitForSelector('#runtime-help-dialog[aria-hidden="false"]');
            assert.equal(await page.evaluate(() => document.activeElement.id), 'runtime-help-close');
            await page.keyboard.press('Escape');
            assert.equal(await page.evaluate(() => document.activeElement.id), 'runtime-help-open');

            await page.click('#runtime-help-open');
            await page.click('#runtime-help-backdrop', { position: { x: 4, y: 4 } });
            assert.equal(await page.getAttribute('#runtime-help-dialog', 'aria-hidden'), 'true');

            await page.click('#runtime-help-open');
            await page.evaluate(() => App.switchPage('file-processing'));
            assert.equal(await page.getAttribute('#runtime-help-dialog', 'aria-hidden'), 'true');
            assert.equal(
                await page.evaluate(() => document.body.classList.contains('runtime-modal-open')),
                false,
            );

            await page.evaluate(() => App.switchPage('runtime-monitor'));
            await page.screenshot({
                path: path.join(outputDir, `runtime-${viewport.width}x${viewport.height}.png`),
                fullPage: true,
            });
            await page.close();
        }
    } finally {
        await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
```

The script closes browser/server in `finally` and exits nonzero on any assertion. Visually inspect the three generated PNGs under `%TEMP%\wanz-runtime-monitor` for: fixed-length overall pressure line, no first-panel scrollbar, rounded controls, five aligned group headers, legible mobile labels, and no nested cards.

Run with the bundled dependency:

```powershell
$env:NODE_PATH='C:\Users\19404\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
node tests/browser/runtime_monitor_visual.js
```

Expected: exit code 0 and three nonblank PNGs under `%TEMP%\wanz-runtime-monitor`.

- [ ] **Step 9: Commit**

```powershell
git add ui/index.html ui/js/runtime-monitor.js ui/css/style.css design-mockups/concurrency-pools.html tests/js/runtime_monitor.test.js tests/test_concurrency_monitor_mockup.py tests/test_runtime_monitor_ui_contract.py tests/browser/runtime_monitor_visual.js
git commit -m "feat: explain layered concurrency in runtime monitor"
```

---

### Task 7: Full Regression, Static Audit, And Master Handoff

**Files:**
- Verify: all files changed in Tasks 1-6
- Modify only if a regression requires a scoped fix: the failing file and its direct test

**Interfaces:**
- Verifies the public `/analysis/run`, file pipeline analysis, runtime settings, and `/runtime/concurrency` contracts end to end.
- Produces a clean `master` commit series ready to push/merge according to the repository remote policy.

- [ ] **Step 1: Audit removed names and canonical labels**

Run:

```powershell
rg -n "task_analysis|analysis\.max_concurrency|单请求分析|单任务逻辑分析" utils service blue_print ui tests configs design-mockups
rg -n "task_file_analysis|independent_analysis|global_analysis" utils service blue_print ui tests configs design-mockups
```

Expected: the first command finds removed names only in explicit rejection/no-compatibility tests or historical design documentation; production code, runtime payloads, UI labels, and deployed YAML contain none. The second command covers config, both services, runtime API, settings UI, monitor UI, and tests.

- [ ] **Step 2: Run all JavaScript and Python tests**

Run:

```powershell
node --test tests/js/*.test.js
uv run pytest
```

Expected: all tests PASS. Do not claim completion if database-backed tests are skipped or fail; report the exact external dependency if MySQL/Milvus prevents a full run, while still running all isolated test modules.

- [ ] **Step 3: Repeat visual checks against the final code**

Repeat Task 6 Playwright checks at `1440x900`, `1280x720`, and `500x900` using a snapshot with active/queued values in every canonical pool. Verify canvas bounding boxes are non-zero and sample center/corner pixels are not all identical, proving charts rendered rather than leaving blank canvases.

- [ ] **Step 4: Review diff and runtime semantics**

Run:

```powershell
git diff --check
git status --short
git log --oneline -8
```

Review specifically that:

- no compute task captures `session`;
- nested acquire order is file/item then total, then model where applicable;
- `independent_analysis` is created through `get_limiter`, never `register_task_limiter`;
- file task limiter unregister happens in `finally`;
- ordered results drive persistence, callbacks, and yields;
- help text contains hardcoded default examples only;
- no unrelated user changes are staged.

- [ ] **Step 5: Commit any final scoped corrections and hand off**

If verification required a fix, rerun the directly affected tests and then the full suite before committing:

```powershell
git add utils/config.py service/settings_service.py service/analysis_run_service.py service/analysis_service.py blue_print/runtime_router.py ui/index.html ui/js/settings.js ui/js/runtime-monitor.js ui/css/style.css configs/config.yaml design-mockups/concurrency-pools.html tests
git commit -m "fix: complete analysis concurrency integration"
```

Expected final state: clean worktree on `master`, canonical feature commits present, and no unverified process left running. Push or merge only when explicitly included in the active execution authorization; report the final commit hash and exact verification commands.
