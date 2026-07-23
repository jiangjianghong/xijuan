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
