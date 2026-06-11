"""字段提取服务测试。"""

from __future__ import annotations

from service.extraction_service import parse_sections, SectionInfo


def test_parse_sections_empty():
    """测试空内容解析。"""
    result = parse_sections("")
    assert result == []


def test_parse_sections_basic():
    """测试基本章节解析。"""
    content = """# 1 概述

这是概述内容。

# 2 详细设计

这是详细设计内容。
"""
    result = parse_sections(content)
    assert len(result) == 2
    assert result[0].number == "1"
    assert result[0].title == "概述"
    assert result[1].number == "2"
    assert result[1].title == "详细设计"

def test_build_text_source_refs_attaches_bboxes():
    from service.extraction_service import _build_text_source_refs

    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 1,
         "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"start_pos": 100, "end_pos": 120, "page_num": 2,
         "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
    ]
    results = [{"keyword": "金额", "context": "命中文本", "start_pos": 5, "end_pos": 110}]
    refs, _texts = _build_text_source_refs("context", results, mapping)
    ref = refs["金额"][0]
    assert ref["bboxes"] == [
        {"page_num": 1, "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"page_num": 2, "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
    ]


def test_build_text_source_refs_legacy_mapping_no_bboxes_key():
    """老 mapping 无 bbox → ref 不带 bboxes 键。"""
    from service.extraction_service import _build_text_source_refs

    mapping = [{"start_pos": 0, "end_pos": 20, "page_num": 1}]
    results = [{"keyword": "金额", "context": "命中文本", "start_pos": 5, "end_pos": 15}]
    refs, _texts = _build_text_source_refs("context", results, mapping)
    assert "bboxes" not in refs["金额"][0]
