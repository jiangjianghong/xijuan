"""章节树回归：覆盖以工代赈文件中出现的混合编号与目录。"""

import pytest

from service.extraction_service import _classify_heading, parse_sections, search_section


def document(*titles):
    return "".join(f"# {title}\n\n正文{idx}\n\n" for idx, title in enumerate(titles))


def test_arabic_chapter_contains_sections_until_next_chapter():
    content = document("第1章概述", "1.1.项目概况", "1.1.1.项目名称", "第2章 建设背景", "2.1.政策")
    sections = parse_sections(content)
    chapter = sections[0]
    assert chapter.number == "第1章"
    assert chapter.title == "概述"
    assert chapter.tree_end_pos == sections[3].start_pos
    assert sections[1].title == "项目概况"
    assert sections[1].tree_end_pos == sections[3].start_pos


@pytest.mark.parametrize("local", ["1. 分项", "1、分项", "（1）分项", "1）分项", "一、分项", "（一）分项"])
def test_local_numbering_stays_inside_decimal_section(local):
    content = document("第四章 建设方案", "4.3 排水工程", "4.3.4 设计做法", "4.3.4.1 新建排水渠", local, "4.3.4.2 暗排水渠", "4.4 挡土墙")
    sections = parse_sections(content)
    assert sections[4].numbered
    assert sections[4].level > sections[3].level
    assert sections[3].tree_end_pos == sections[5].start_pos
    assert sections[2].tree_end_pos == sections[6].start_pos
    assert sections[1].tree_end_pos == sections[6].start_pos


def test_local_siblings_and_children_reset_at_next_decimal_section():
    content = document("第四章 建设方案", "4.1 道路", "1. 施工", "(1) 材料", "1）规格", "2）型号", "(2) 工艺", "2. 验收", "4.2 供水", "(1) 设计参数", "第五章 投资")
    sections = parse_sections(content)
    assert sections[2].level == sections[7].level
    assert sections[3].level == sections[6].level
    assert sections[4].level == sections[5].level
    assert sections[2].tree_end_pos == sections[7].start_pos
    assert sections[3].tree_end_pos == sections[6].start_pos
    assert sections[1].tree_end_pos == sections[8].start_pos
    assert sections[9].level > sections[8].level
    assert sections[8].tree_end_pos == sections[10].start_pos


def test_chapter_and_chinese_list_are_different_levels():
    content = document("第一章 总则", "一、范围", "（一）子项", "二、对象", "第二章 规定")
    sections = parse_sections(content)
    assert sections[0].level < sections[1].level < sections[2].level
    assert sections[1].level == sections[3].level
    assert sections[0].tree_end_pos == sections[4].start_pos


def test_single_number_root_contains_decimal_children():
    content = document("1 概况", "1.1 位置", "1.1.1 坐标", "2 方案", "2.1 道路")
    sections = parse_sections(content)
    assert sections[0].level < sections[1].level < sections[2].level
    assert sections[0].tree_end_pos == sections[3].start_pos
    assert sections[0].level == sections[3].level


def test_measurement_heading_does_not_close_section():
    content = document("第四章 建设方案", "4.4 挡土墙", "4.4.2 详细做法", "2.5米高毛石挡土墙做法", "第五章 投资")
    sections = parse_sections(content)
    assert not sections[3].numbered
    assert sections[2].tree_end_pos == sections[4].start_pos


def test_explicit_markdown_hierarchy_keeps_plain_parent():
    content = "# 建设方案\n\n## 道路\n\n### 路面\n\n材料\n\n## 供水\n\n参数\n\n# 投资\n"
    sections = parse_sections(content)
    assert [s.level for s in sections] == [1, 2, 3, 2, 1]
    assert sections[0].tree_end_pos == sections[4].start_pos
    assert sections[1].tree_end_pos == sections[3].start_pos


def test_toc_leaders_and_decimal_trailing_dot_are_not_title():
    assert _classify_heading("第7章 群众务工组织 ..... 71")[1:3] == ("第7章", "群众务工组织")
    assert _classify_heading("7.1. 组织架构")[1:3] == ("7.1", "组织架构")


async def test_search_selects_body_when_llm_selects_toc(monkeypatch):
    content = document("第9章 劳务报酬发放 ..... 76", "第10章 技能培训 ..... 78", "第9章 劳务报酬发放", "9.1 发放标准", "9.2 发放方式", "第10章 技能培训")

    async def select_first(*args, **kwargs):
        return "1"

    monkeypatch.setattr("service.extraction_service.chat_completion", select_first)
    results = await search_section(content, {"section_pattern": "劳务报酬发放", "section_match_type": "llm", "max_results": 1})
    assert len(results) == 1
    assert results[0]["section_index"] == 2
    assert "发放标准" in results[0]["content"]
    assert "技能培训" not in results[0]["content"]


async def test_same_title_in_different_body_sections_is_not_dropped():
    content = document("第一章 建设", "1.1 保障措施", "第二章 运营", "2.1 保障措施")
    results = await search_section(content, {"section_pattern": "保障措施", "section_match_type": "exact"})
    assert [r["section_number"] for r in results] == ["1.1", "2.1"]


@pytest.mark.parametrize("match_type", ["contains", "exact", "fuzzy"])
async def test_section_retrieval_includes_local_subsections(match_type):
    content = document("第4章 建设方案", "4.3 道路工程", "4.3.1 施工方案", "1. 材料", "(1) 规格", "4.4 供水工程")
    results = await search_section(content, {"section_pattern": "道路工程", "section_match_type": match_type})
    assert len(results) == 1
    assert "材料" in results[0]["content"] and "规格" in results[0]["content"]
    assert "供水工程" not in results[0]["content"]
