"""进阶字段提取单元测试。"""
from __future__ import annotations

from model.tables import ExtractionField


def test_extraction_field_has_advanced_columns():
    cols = set(ExtractionField.__table__.columns.keys())
    assert "is_advanced" in cols
    assert "depend_fields" in cols
