"""reason-first JSON 输出契约与调试提示词回归测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from blue_print.extraction_router import _render_debug_llm_input
from service.analysis_service import (
    CUSTOM_JSON_INSTRUCTION_PLAIN,
    _build_custom_prompt,
    _salvage_judge_result,
    build_judge_prompt,
)
from service.extraction_service import JSON_OUTPUT_INSTRUCTION
from service.vl_service._common import (
    append_reason_first_output_instruction,
    parse_vl_json_response,
)
from utils.prompt_contract import build_reason_first_value_suffix
from utils.text_utils import salvage_value_reason


def test_parse_llm_json_response_accepts_reason_first():
    from service.extraction_service import parse_llm_json_response

    value, reason, pages = parse_llm_json_response(
        '{"reason":"先算 40+60","value":"100","pages":[2]}'
    )
    assert (value, reason, pages) == ("100", "先算 40+60", [2])


def test_salvage_accepts_reason_first_invalid_json():
    value, reason = salvage_value_reason(
        '{"reason":"先算 40+60，结果为"100", "value":"100", "pages":[2]}'
    )
    assert value == "100"
    assert reason == "先算 40+60，结果为“100”"


def test_salvage_reason_first_ignores_value_key_mentioned_inside_reason():
    value, reason = salvage_value_reason(
        '{"reason":"依据中提到, "value": "40", 但最终应为100", "value":"100"}'
    )
    assert value == "100"
    assert reason == "依据中提到, “value”: “40”, 但最终应为100"


def test_parse_custom_and_vl_accept_reason_first():
    from service.analysis_service import parse_custom_json_response

    assert parse_custom_json_response(
        '{"reason":"依据","value":"100"}'
    ) == ("100", "依据")


def test_judge_salvage_uses_result_key_when_reason_contains_boolean_word():
    result, reason = _salvage_judge_result(
        '{"reason":"说明中提到 true，但实际判定为"否"", "result":"false"}'
    )
    assert result == "false"
    assert reason == "说明中提到 true，但实际判定为“否”"
    assert parse_vl_json_response(
        '{"reason":"依据","value":"100"}'
    ) == ("100", "依据")


def test_judge_salvage_ignores_result_key_mentioned_inside_reason():
    result, reason = _salvage_judge_result(
        '{"reason":"说明中提到, "result": "true", 但最终应为否", "result":"false"}'
    )
    assert result == "false"
    assert reason == "说明中提到, “result”: “true”, 但最终应为否"


def test_fixed_prompts_require_reason_before_final_value():
    assert JSON_OUTPUT_INSTRUCTION.index('"reason"') < JSON_OUTPUT_INSTRUCTION.index('"value"')
    assert "请务必先输出 reason" in JSON_OUTPUT_INSTRUCTION
    assert "reason（分步、可核验的分析和判定依据），再输出 value（最终要求输出的结果）" in JSON_OUTPUT_INSTRUCTION
    assert "不得在分析尚未完成时提前猜测最终结果" in JSON_OUTPUT_INSTRUCTION

    assert CUSTOM_JSON_INSTRUCTION_PLAIN.index('"reason"') < CUSTOM_JSON_INSTRUCTION_PLAIN.index('"value"')
    formatted = _build_custom_prompt(
        "输入", True, [{"key": "名称", "type": "string"}]
    )
    assert formatted.index('"reason"') < formatted.index('"value"')

    judge = build_judge_prompt("输入", "")
    assert judge.index('"reason"') < judge.index('"result"')
    assert "请务必先输出 reason" in judge
    assert "reason（分步、可核验的分析和判定依据），再输出 result" in judge
    assert "不得在分析尚未完成时提前猜测最终结果" in judge


def test_ui_vl_default_is_reason_first():
    source = Path("ui/js/ruleConfig.js").read_text(encoding="utf-8")
    start = source.index("EXTRACT_PROMPT")
    end = source.index("},", start)
    example = source[start:end]
    assert example.index('"reason"') < example.index('"value"')
    assert "请务必先输出 reason" in example


def test_vl_output_suffix_is_reason_first_and_idempotent():
    prompt = append_reason_first_output_instruction("用户自定义提取要求")
    assert prompt.startswith("用户自定义提取要求")
    assert "请务必先输出 reason" in prompt
    assert "reason（分步、可核验的分析和判定依据），再输出 value（最终要求输出的结果）" in prompt
    assert prompt.index('"reason"') < prompt.index('"value"')
    assert append_reason_first_output_instruction(prompt) == prompt


def test_vl_output_suffix_is_not_suppressed_by_user_marker_text():
    prompt = append_reason_first_output_instruction("用户正文包含【固定输出约束】字样")
    assert prompt.count("【固定输出约束】") == 2
    assert prompt.index('"reason"') < prompt.index('"value"')


def test_vl_output_suffix_only_deduplicates_at_prompt_end():
    suffix = build_reason_first_value_suffix()
    prompt = append_reason_first_output_instruction(
        f"用户前文误引了完整约束：{suffix}\n后面还有冲突要求"
    )
    assert prompt.endswith(suffix)
    assert prompt.count(suffix) == 2


def test_sync_debug_prompt_includes_fixed_contract():
    text_field = SimpleNamespace(
        source_type="text",
        use_llm=1,
        text_extract_prompt="从 <search_result>命中</search_result> 提取结果",
        table_extract_prompt="",
        vl_extract_prompt="",
    )
    text_prompt = _render_debug_llm_input(text_field, {"_texts": {"命中": "100"}})
    assert "100" in text_prompt
    assert text_prompt.index('"reason"') < text_prompt.index('"value"')

    vl_field = SimpleNamespace(
        source_type="vl",
        use_llm=1,
        text_extract_prompt="",
        table_extract_prompt="",
        vl_extract_prompt="用户自定义 VL 提取要求",
    )
    # refs=None means no model call (for example, a missing PDF), so there is
    # no actual prompt to report.
    assert _render_debug_llm_input(vl_field, None) == ""
    assert _render_debug_llm_input(vl_field, {"_vl": {"final_prompt": ""}}) == ""


def test_vl_validation_error_describes_reason_first_json():
    from model.schemas import ExtractionFieldCreate

    with pytest.raises(ValueError, match=r"\{reason, value\}"):
        ExtractionFieldCreate(
            field_id="invalid_vl_prompt",
            field_name="测试",
            source_type="vl",
            vl_method="vl_model",
            vl_extract_prompt="只输出 value，不要说明",
        )
