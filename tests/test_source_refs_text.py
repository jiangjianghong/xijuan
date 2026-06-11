"""source_refs 携带检索原文（text/_texts）测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from service.extraction_service import (
    _build_table_source_refs,
    _build_text_source_refs,
)

_MAPPING = [
    {"page_num": "1", "start_pos": 0},
    {"page_num": "2", "start_pos": 100},
]


def test_context_refs_carry_text_and_joined():
    search_results = [
        {"keyword": "金额", "position": 10, "context": "合同金额为100万元",
         "start_pos": 5, "end_pos": 25},
        {"keyword": "金额", "position": 120, "context": "总金额含税",
         "start_pos": 110, "end_pos": 130},
    ]
    refs, texts = _build_text_source_refs("context", search_results, _MAPPING)

    assert [r["text"] for r in refs["金额"]] == ["合同金额为100万元", "总金额含税"]
    assert refs["金额"][0]["page_num"] == "1"
    assert refs["金额"][1]["page_num"] == "2"
    assert texts == {"金额": "合同金额为100万元\n---\n总金额含税"}
    assert refs["_texts"] == texts


def test_chunk_db_refs_carry_text():
    search_results = [
        {"keyword": "乙方", "chunk_id": "c1", "chunk_index": 0,
         "chunk_content": "乙方为某某公司", "start_pos": 0, "end_pos": 20,
         "page_num": "2"},
    ]
    refs, texts = _build_text_source_refs("chunk_db", search_results, [])

    ref = refs["乙方"][0]
    assert ref["text"] == "乙方为某某公司"
    assert ref["chunk_id"] == "c1"
    assert ref["page_num"] == "2"
    assert texts == {"乙方": "乙方为某某公司"}


def test_rule_refs_carry_text():
    search_results = [
        {"keyword": "工期", "position": 3, "extracted_text": "工期为90天",
         "start_pos": 3, "end_pos": 12},
    ]
    refs, texts = _build_text_source_refs("rule", search_results, [])
    assert refs["工期"][0]["text"] == "工期为90天"
    assert texts == {"工期": "工期为90天"}


def test_section_without_keyword_keeps_legacy_behavior():
    """section 结果无 keyword：refs 按 section_title 分组、_texts 为空（与现状一致）。"""
    search_results = [
        {"section_number": "3", "section_title": "付款方式", "section_index": 2,
         "content": "按月支付", "start_pos": 0, "end_pos": 10},
    ]
    refs, texts = _build_text_source_refs("section", search_results, [])
    assert refs["付款方式"][0]["text"] == "按月支付"
    assert texts == {}
    assert refs["_texts"] == {}


def _make_table(index, name, content, start=0, end=10, page="1"):
    t = MagicMock()
    t.table_index = index
    t.table_name = name
    t.table_content = content
    t.start_pos = start
    t.end_pos = end
    t.page_num = page
    return t


def test_table_refs_carry_text_and_joined():
    tables = [
        _make_table(0, "报价表", "<table>A</table>", 0, 20, "2"),
        _make_table(1, "明细表", "<table>B</table>", 30, 60, "3"),
    ]
    refs, texts = _build_table_source_refs(tables, "报价", [])

    assert refs["_tables"][0]["text"] == "表格名称: 报价表\n<table>A</table>"
    assert refs["_tables"][1]["text"] == "表格名称: 明细表\n<table>B</table>"
    assert refs["_tables"][0]["table_name"] == "报价表"
    assert refs["_tables"][0]["page_num"] == "2"
    assert texts == {
        "报价": "表格名称: 报价表\n<table>A</table>\n---\n表格名称: 明细表\n<table>B</table>"
    }
    assert refs["_texts"] == texts


def test_table_refs_unnamed_table_fallback():
    tables = [_make_table(2, "", "<table>C</table>", page="")]
    refs, texts = _build_table_source_refs(tables, "表格", [])

    assert refs["_tables"][0]["text"] == "表格名称: 表格2\n<table>C</table>"
    assert refs["_tables"][0]["page_num"] == ""
    assert texts == {"表格": "表格名称: 表格2\n<table>C</table>"}
