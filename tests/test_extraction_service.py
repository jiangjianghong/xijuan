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
