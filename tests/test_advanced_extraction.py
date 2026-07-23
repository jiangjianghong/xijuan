"""进阶字段提取单元测试。"""
from __future__ import annotations

from model.tables import ExtractionField
from model.schemas import ExtractionFieldCreate


def test_extraction_field_has_advanced_columns():
    cols = set(ExtractionField.__table__.columns.keys())
    assert "is_advanced" in cols
    assert "depend_fields" in cols


def test_field_create_accepts_advanced_fields():
    m = ExtractionFieldCreate(
        field_id="adv1", field_name="进阶", source_type="text",
        search_type="context",
        search_config={"keywords": ["<field_result>a</field_result>"]},
        text_extract_prompt="从 <search_result>x</search_result> 提取",
        is_advanced=1, depend_fields=["a"],
    )
    assert m.is_advanced == 1
    assert m.depend_fields == ["a"]


def test_field_create_defaults_basic():
    m = ExtractionFieldCreate(
        field_id="b1", field_name="普通", source_type="text",
        search_type="context", search_config={"keywords": ["x"]},
        text_extract_prompt="从 <search_result>x</search_result> 提取",
    )
    assert m.is_advanced == 0
    assert m.depend_fields is None


# ── Task 3: 占位符解析 ──────────────────────────────────────

from service.extraction_service import collect_field_refs, resolve_field_refs


def test_collect_field_refs_dedup_order():
    s = "关于<field_result>b</field_result>与<field_result>a</field_result>及<field_result>b</field_result>"
    assert collect_field_refs(s) == ["b", "a"]


def test_collect_field_refs_empty():
    assert collect_field_refs("没有占位符") == []
    assert collect_field_refs("") == []
    assert collect_field_refs(None) == []


def test_resolve_field_refs_replaces_values():
    s = "合同<field_result>a</field_result>"
    assert resolve_field_refs(s, {"a": "甲方协议"}) == "合同甲方协议"


def test_resolve_field_refs_missing_to_empty():
    assert resolve_field_refs("<field_result>x</field_result>尾", {}) == "尾"


# ── Task 4: 页码派生 ──────────────────────────────────────

from service.extraction_service import derive_page_range_from_model_pages
import pytest


def test_derive_range_min_max():
    assert derive_page_range_from_model_pages([7, 3, 5], None) == (3, 7, False)


def test_derive_range_cap():
    assert derive_page_range_from_model_pages([3, 7], 3) == (3, 5, True)


def test_derive_range_cap_not_needed():
    assert derive_page_range_from_model_pages([3, 4], 5) == (3, 4, False)


def test_derive_range_empty_raises():
    with pytest.raises(ValueError):
        derive_page_range_from_model_pages([], 5)


# ── Task 5: 依赖扫描 ──────────────────────────────────────

from types import SimpleNamespace
from service.extraction_service import collect_depend_fields


def _field(**kw):
    base = dict(
        source_type="text", search_type="context", search_config=None,
        table_match_keywords=None, table_extract_prompt=None, table_system_prompt=None,
        text_extract_prompt=None, text_system_prompt=None,
        vl_extract_prompt=None, vl_system_prompt=None, vl_config=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_collect_depend_fields_from_keywords_and_prompt():
    f = _field(
        search_config={"keywords": ["普通词", "<field_result>a</field_result>"]},
        text_extract_prompt="用 <search_result>x</search_result> 和 <field_result>b</field_result>",
    )
    assert set(collect_depend_fields(f)) == {"a", "b"}


def test_collect_depend_fields_page_source():
    f = _field(search_type="page",
               search_config={"page_source_field": "src", "max_pages": 5})
    assert collect_depend_fields(f) == ["src"]


def test_collect_depend_fields_none_when_basic():
    f = _field(search_config={"keywords": ["纯文本"]},
               text_extract_prompt="<search_result>x</search_result>")
    assert collect_depend_fields(f) == []


# ── Task 6: 进阶字段解析 ──────────────────────────────────────

from model.tables import ExtractionField
from service.extraction_service import resolve_advanced_field


def _adv_text_field():
    return ExtractionField(
        field_id="adv", type_id="default", field_name="进阶", source_type="text",
        is_advanced=1, search_type="context",
        search_config={"keywords": ["<field_result>a</field_result>", "<field_result>missing</field_result>", "固定"]},
        text_extract_prompt="从 <search_result>x</search_result> 提取 <field_result>a</field_result>",
    )


def test_resolve_advanced_keywords_and_prompt():
    field = _adv_text_field()
    resolved, prov = resolve_advanced_field(field, {"a": "甲方"}, {})
    assert resolved.search_config["keywords"] == ["甲方", "固定"]
    assert resolved.text_extract_prompt == "从 <search_result>x</search_result> 提取 甲方"
    assert field.search_config["keywords"][0] == "<field_result>a</field_result>"
    assert prov["_resolved_refs"]["a"] == "甲方"


def test_resolve_advanced_page_link():
    field = ExtractionField(
        field_id="advp", type_id="default", field_name="页联动", source_type="text",
        is_advanced=1, search_type="page",
        search_config={"page_source_field": "src", "max_pages": 3, "max_length": 30000},
        text_extract_prompt="<search_result>page_content</search_result>",
    )
    resolved, prov = resolve_advanced_field(field, {}, {"src": [3, 7]})
    assert resolved.search_config["page_range"] == "3-5"
    assert prov["_page_link"] == {
        "source_field": "src", "model_pages": [3, 7],
        "derived_range": [3, 5], "capped": True,
    }


def test_resolve_advanced_page_link_missing_pages_raises():
    field = ExtractionField(
        field_id="advp2", type_id="default", field_name="页联动", source_type="text",
        is_advanced=1, search_type="page",
        search_config={"page_source_field": "src", "max_pages": 3},
        text_extract_prompt="<search_result>page_content</search_result>",
    )
    with pytest.raises(ValueError):
        resolve_advanced_field(field, {}, {"src": []})
