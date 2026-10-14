"""正则关键词与空白容忍的检索测试。"""

from types import SimpleNamespace

import pytest

from service import extraction_service as es
from service.text_matching import TextProjection, matches_text


def test_projection_matches_across_spaces_and_maps_to_original_offsets():
    content = "项目 名称是甲方"
    projection = TextProjection.build(content, ignore_whitespace=True)
    start, end = next(projection.spans("项目名称"))
    assert content[start:end] == "项目 名称"


def test_projection_regex_captures_variable_length_and_preserves_newlines():
    content = "年限 为 2.5 年\n年限为\n20年"
    projection = TextProjection.build(content, ignore_whitespace=True)
    spans = list(projection.spans(r"年限为\s*[0-9]+(?:\.[0-9]+)?\s*年"))
    assert [content[start:end] for start, end in spans] == ["年限 为 2.5 年", "年限为\n20年"]
    assert list(projection.spans("年限为20年")) == []


def test_default_projection_does_not_tolerate_whitespace():
    assert list(TextProjection.build("项目 名称").spans("项目名称")) == []
    assert list(TextProjection.build("project  name", True).spans("project name"))


def test_matches_text_supports_regex_for_section_and_table_name_modes():
    assert matches_text("第10 年", r"第\d+年", ignore_whitespace=True)
    assert matches_text("project  name", "projectname", ignore_whitespace=True)
    assert not matches_text("project name", "projectname")


async def test_context_search_returns_original_regex_hit_and_offsets():
    content = "合同约定年限 为 2.5 年。"
    results = await es.search_context(content, {
        "keywords": [r"年限为\s*[0-9]+(?:\.[0-9]+)?\s*年"],
        "ignore_whitespace": True,
        "context_before": 0,
        "context_after": 0,
        "max_total_results": 0,
    })
    assert len(results) == 1
    result = results[0]
    assert result["context"] == "年限 为 2.5 年"
    assert content[result["start_pos"]:result["end_pos"]] == result["context"]
    assert content[result["position"]:].startswith("年限")


async def test_section_search_uses_whitespace_tolerant_regex():
    results = await es.search_section(
        "# 第一章 项目 名称\n正文\n",
        {"section_pattern": "项目名称", "ignore_whitespace": True},
    )
    assert [result["section_title"] for result in results] == ["项目 名称"]


def test_invalid_regex_reports_the_keyword():
    with pytest.raises(ValueError, match="关键词正则无效"):
        list(TextProjection.build("内容").spans("["))


def test_table_search_config_is_carried_into_hybrid_table_field():
    field = SimpleNamespace(
        source_type="text", table_name_pattern=None, table_match_prompt=None,
        vl_system_prompt=None, vl_extract_prompt=None,
    )
    run_field = es._hybrid_item_field(field, {
        "source_type": "table", "method": "table_match",
        "config": {"ignore_whitespace": True, "table_match_keywords": ["项目名称"]},
    })
    assert run_field.search_config["ignore_whitespace"] is True
