# Reason-First JSON Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every built-in and debug LLM/VL output prompt require a verifiable `reason` analysis before the final `value`/`result`, while preserving old response compatibility.

**Architecture:** Centralize fixed output instructions in the existing extraction and analysis service modules. Formal and debug flows will call the same builders; VL flows will append one shared reason-first instruction at the final model call. Parsers continue reading JSON by key and salvage both field orders.

**Tech Stack:** Python, FastAPI services, pytest/pytest-asyncio, vanilla JavaScript UI.

## Global Constraints

- Keep public response keys and database columns unchanged.
- Do not rewrite user-supplied prompts; append system constraints at the actual model boundary.
- The exact required order is `reason` before `value`/`result` in all built-in examples.
- The constraint must say: “请务必先输出 reason（分步、可核验的分析和判定依据），再输出 value/result（最终要求输出的结果）；必须先完成分析，再给出结论；不得在分析尚未完成时提前猜测最终结果。”
- Parsers must accept both `reason → value/result` and legacy `value/result → reason`.

---

### Task 1: Add shared prompt-contract constants and parser regression tests

**Files:**
- Modify: `tests/test_extraction_service.py`
- Modify: `tests/test_execute_custom.py`
- Modify: `tests/test_vl_service.py`
- Create: `tests/test_reason_first_prompts.py`

**Interfaces:**
- Tests will import `JSON_OUTPUT_INSTRUCTION` from `service.extraction_service`, `CUSTOM_JSON_INSTRUCTION_PLAIN` and `_build_custom_prompt` from `service.analysis_service`, and `parse_vl_json_response` from `service.vl_service._common`.

- [ ] **Step 1: Write failing parser tests**

```python
def test_parse_llm_json_response_accepts_reason_first():
    value, reason, pages = parse_llm_json_response(
        '{"reason":"先算 40+60","value":"100","pages":[2]}'
    )
    assert (value, reason, pages) == ("100", "先算 40+60", [2])


def test_salvage_accepts_reason_first_invalid_json():
    value, reason = salvage_value_reason(
        '{"reason":"先算 40+60", "value":"100", "pages":[2]}'
    )
    assert value == "100"
    assert reason == "先算 40+60"


def test_parse_custom_and_vl_accept_reason_first():
    assert parse_custom_json_response(
        '{"reason":"依据","value":"100"}'
    ) == ("100", "依据")
    assert parse_vl_json_response(
        '{"reason":"依据","value":"100"}'
    ) == ("100", "依据")
```

- [ ] **Step 2: Run tests to verify the new salvage case fails**

Run: `uv run pytest tests/test_extraction_service.py -k "reason_first or salvage" tests/test_execute_custom.py -k reason_first tests/test_vl_service.py -k reason_first -v`

Expected: the new invalid-JSON salvage assertion fails because the current regex only matches `value` before `reason`.

- [ ] **Step 3: Add prompt-order contract assertions**

```python
def test_fixed_prompts_require_reason_before_final_value_or_result():
    extraction = JSON_OUTPUT_INSTRUCTION
    assert extraction.index('"reason"') < extraction.index('"value"')
    assert "先输出" in extraction and "不得在分析尚未完成时提前猜测" in extraction

    custom = CUSTOM_JSON_INSTRUCTION_PLAIN
    assert custom.index('"reason"') < custom.index('"value"')

    judge = build_judge_prompt("输入", "")
    assert judge.index('"reason"') < judge.index('"result"')

    formatted = _build_custom_prompt("输入", True, [{"key": "名称", "type": "string"}])
    assert formatted.index('"reason"') < formatted.index('"value"')
```

- [ ] **Step 4: Run the prompt assertions to verify they fail before implementation**

Run: `uv run pytest tests/test_reason_first_prompts.py -v`

Expected: FAIL because the current fixed examples place `value`/`result` first and no shared reason-first wording exists.

### Task 2: Make salvage parsing order-independent

**Files:**
- Modify: `utils/text_utils.py:29-69`
- Modify: `service/analysis_service.py:1007-1039`
- Test: `tests/test_extraction_service.py`, `tests/test_execute_custom.py`, `tests/test_reason_first_prompts.py`

**Interfaces:**
- Preserve `salvage_value_reason(response: str) -> tuple[str, str]`.
- Preserve `salvage_reason(response: str) -> str`.
- Preserve `parse_custom_json_response(response: str) -> tuple[str, str]` and judge debug event payloads.

- [ ] **Step 1: Add bidirectional salvage regexes**

Replace the single-order assumptions with separate patterns for string/object values in both orders. `salvage_value_reason` should first try `reason → value`, then legacy `value → reason`, and return the captured values normalized exactly as today. `salvage_reason` should continue truncating at the next `result`, `value`, or `reason` key.

- [ ] **Step 2: Make judge debug fallback read either order**

In `test_rule_analysis_stream`, keep `json.loads` and object extraction key-based. When JSON parsing fails, call a helper that extracts `reason` and then scans the raw response for `result` in either order; preserve the existing true/false normalization and event shape.

- [ ] **Step 3: Run focused parser tests**

Run: `uv run pytest tests/test_extraction_service.py -k "json_response or salvage" tests/test_execute_custom.py tests/test_vl_service.py -v`

Expected: PASS, including all existing legacy-order tests and the new reason-first invalid JSON case.

### Task 3: Centralize reason-first extraction/custom/judge prompts

**Files:**
- Modify: `service/extraction_service.py:1754-1764`
- Modify: `service/analysis_service.py:337-357,477-540`
- Modify: `service/analysis_service.py:929-941`
- Modify: `tests/test_reason_first_prompts.py`

**Interfaces:**
- Add `build_judge_prompt(resolved_expression: str, system_prompt: str = "") -> str` or an equivalent private helper used by both `execute_judge` and `test_rule_analysis_stream`.
- Keep `CUSTOM_JSON_INSTRUCTION_PLAIN`, `_build_custom_prompt`, and `JSON_OUTPUT_INSTRUCTION` importable for existing callers/tests.

- [ ] **Step 1: Update extraction instruction**

Use this example and wording in `JSON_OUTPUT_INSTRUCTION`:

```text
请务必先输出 reason（分步、可核验的分析和判定依据），再输出 value（最终要求输出的结果）。必须先完成分析，再给出结论；不得在分析尚未完成时提前猜测最终结果。
请以 JSON 格式返回结果，字段顺序必须严格保持为 reason、value、pages：
{"reason":"分步、可核验的分析和判定依据","value":"提取的值","pages":[3,5]}
```

Retain the existing single-object, page normalization, quote, and escaping requirements.

- [ ] **Step 2: Update custom prompt builders**

Use reason-first examples for plain and formatted custom output:

```text
{"reason":"分步、可核验的生成依据","value":"生成的结果内容"}
```

and

```text
{"reason":"分步、可核验的生成依据","value":<上面结构的 JSON>}
```

Add the exact “先输出 reason…不得提前猜测” constraint before the example.

- [ ] **Step 3: Extract one judge prompt builder and reuse it**

The judge instruction must show:

```json
{"reason":"分步、可核验的判断依据","result":"true"}
```

`execute_judge` and the debug stream must call the same builder so their emitted `prompt` event equals the prompt sent to the model.

- [ ] **Step 4: Run prompt and existing analysis tests**

Run: `uv run pytest tests/test_reason_first_prompts.py tests/test_execute_custom.py tests/test_analysis_service.py tests/test_custom_debug_stream.py tests/test_analysis_debug_reextract.py -v`

Expected: PASS.

### Task 4: Apply the shared contract to VL formal and debug flows

**Files:**
- Modify: `service/vl_service/_common.py`
- Modify: `service/vl_service/model.py`
- Modify: `service/vl_service/progressive.py`
- Modify: `service/vl_service/locate.py`
- Modify: `service/extraction_service.py:2348-2460`
- Modify: `tests/test_vl_service.py`

**Interfaces:**
- Add `append_reason_first_output_instruction(prompt: str, *, include_pages: bool = False) -> str` in `service.vl_service._common`.
- Existing `vl_*_extract` return values remain `(value, reason, refs)`.

- [ ] **Step 1: Write failing VL prompt assertions**

Capture the final `vl_chat` messages in model, progressive, locate, and VL debug tests. Assert the final prompt contains `先输出 reason`, `不得在分析尚未完成时提前猜测`, and an example with `"reason"` before `"value"`.

- [ ] **Step 2: Run VL prompt tests to verify they fail**

Run: `uv run pytest tests/test_vl_service.py -k "prompt or model or progressive or locate" -v`

Expected: FAIL because user VL prompts are currently sent without a shared reason-first suffix.

- [ ] **Step 3: Implement the append helper and use it at final extraction calls**

The helper must preserve the user prompt and append:

```text

【固定输出约束】请务必先输出 reason（分步、可核验的分析和判定依据），再输出 value（最终要求输出的结果）。必须先完成分析，再给出结论；不得在分析尚未完成时提前猜测最终结果。
只返回一个 JSON 对象，字段顺序必须严格为 reason、value：{"reason":"分步依据","value":"最终结果"}
```

For text-only extraction that already adds `pages`, use the extraction instruction from Task 3 instead of duplicating a conflicting example. Ensure VL debug emits the exact appended prompt in its `prompt` event.

- [ ] **Step 4: Run VL tests**

Run: `uv run pytest tests/test_vl_service.py tests/test_extraction_router_vl.py -v`

Expected: PASS.

### Task 5: Synchronize front-end defaults and API documentation examples

**Files:**
- Modify: `ui/js/ruleConfig.js:31-34`
- Modify: `utils/openapi_enrich.py:1245-1267,4269-4303`
- Modify: `tests/test_settings_ui_contract.py` or create `tests/test_reason_first_prompts.py` assertions for static assets

**Interfaces:**
- Keep the existing UI validation requirement that prompts contain `value` and `reason`; do not require users to order custom prompts because the backend appends the contract.

- [ ] **Step 1: Update VL UI default examples**

Change both found and not-found examples to `{"reason":"...","value":"..."}` and include the reason-first wording in the default prompt text.

- [ ] **Step 2: Update OpenAPI examples**

Change text/table/VL examples and descriptions that show concrete JSON so every concrete example has `reason` before `value`; update custom/judge descriptions to mention the reason-first generation order without changing response schemas.

- [ ] **Step 3: Add static contract checks**

```python
def test_ui_vl_default_is_reason_first():
    source = Path("ui/js/ruleConfig.js").read_text(encoding="utf-8")
    example = source[source.index('EXTRACT_PROMPT'):source.index('},', source.index('EXTRACT_PROMPT'))]
    assert example.index('"reason"') < example.index('"value"')
    assert "先输出 reason" in example
```

- [ ] **Step 4: Run UI/documentation contract tests**

Run: `uv run pytest tests/test_reason_first_prompts.py tests/test_settings_ui_contract.py tests/test_api_docs_parser.py -v`

Expected: PASS.

### Task 6: Full regression verification

**Files:**
- Modify: none
- Test: full `tests/` suite

- [ ] **Step 1: Run focused complete suite**

Run: `uv run pytest tests/test_extraction_service.py tests/test_execute_custom.py tests/test_vl_service.py tests/test_custom_debug_stream.py tests/test_analysis_service.py tests/test_analysis_debug_reextract.py tests/test_extraction_page.py tests/test_extraction_router_vl.py -v`

Expected: exit code 0 with no failures.

- [ ] **Step 2: Run all tests**

Run: `uv run pytest`

Expected: exit code 0; any pre-existing environment-only failures must be recorded explicitly rather than claimed as passing.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; only prompt-contract, parser, tests, UI, docs, and plan/spec files are changed.

