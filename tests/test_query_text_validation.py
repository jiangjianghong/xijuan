"""query_text 数组项的占位符齐备校验（422 而非静默丢弃）。"""

import pytest
from pydantic import ValidationError

from model.schemas import ExtractionFieldCreate


def _payload(query_text, prompt):
    return {
        "field_id": "F1",
        "field_name": "项目名称",
        "source_type": "text",
        "search_type": "vector_db",
        "search_config": {"query_text": query_text},
        "text_extract_prompt": prompt,
    }


def test_single_query_with_matching_placeholder_passes():
    field = ExtractionFieldCreate(**_payload(
        "项目名称", "从 <search_result>项目名称</search_result> 中提取"
    ))
    assert field.search_config["query_text"] == "项目名称"


def test_all_array_items_present_passes():
    ExtractionFieldCreate(**_payload(
        ["项目名称", "工程名称"],
        "参考 <search_result>项目名称</search_result> 与 "
        "<search_result>工程名称</search_result>",
    ))


def test_missing_placeholder_for_array_item_rejected():
    """加了同义词却没在 prompt 里加对应占位符 → 那一路会被静默丢弃，故拦下。"""
    with pytest.raises(ValidationError, match="工程名称"):
        ExtractionFieldCreate(**_payload(
            ["项目名称", "工程名称"],
            "只写了 <search_result>项目名称</search_result>",
        ))


def test_whitespace_is_stripped_before_matching():
    """归一化会 strip，校验口径必须一致。"""
    ExtractionFieldCreate(**_payload(
        [" 项目名称 "], "从 <search_result>项目名称</search_result> 提取"
    ))


def test_use_llm_disabled_skips_check():
    """use_llm=0 不调 LLM、不校验占位符，这里也放宽。"""
    ExtractionFieldCreate(**{
        **_payload(["项目名称", "工程名称"], None),
        "use_llm": 0,
    })


def test_non_vector_db_search_type_unaffected():
    """只管 vector_db；chunk_db 等用 keywords，不走这套标签。"""
    ExtractionFieldCreate(**{
        "field_id": "F2",
        "field_name": "金额",
        "source_type": "text",
        "search_type": "chunk_db",
        "search_config": {"keywords": ["金额", "总价"]},
        "text_extract_prompt": "从 <search_result>金额</search_result> 提取",
    })
