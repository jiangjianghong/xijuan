"""字段提取服务测试。"""

from __future__ import annotations

from service.extraction_service import parse_sections, SectionInfo
from service.extraction_service import _classify_heading, _PLAIN_LEVEL


def test_classify_heading_chinese_number_level1():
    assert _classify_heading("一、项目单位的基本情况") == (1, "一、", "项目单位的基本情况", True)


def test_classify_heading_chapter_level1():
    assert _classify_heading("第二章 规划目标及策略") == (1, "第二章", "规划目标及策略", True)


def test_classify_heading_paren_chinese_level2():
    assert _classify_heading("（三）建设规模及内容") == (2, "（三）", "建设规模及内容", True)


def test_classify_heading_article_level2():
    assert _classify_heading("第七条 村庄分类") == (2, "第七条", "村庄分类", True)


def test_classify_heading_arabic_dot_level3():
    assert _classify_heading("1. 经济效益") == (3, "1.", "经济效益", True)


def test_classify_heading_arabic_dotted_level3():
    # 点分十进制不能被 "1." 规则切成 number="7."
    assert _classify_heading("7.1行政村分类") == (3, "7.1", "行政村分类", True)


def test_classify_heading_paren_arabic_level4():
    assert _classify_heading("(1) 农村水生态环境显著修复") == (4, "(1)", "农村水生态环境显著修复", True)


def test_classify_heading_plain_is_leaf():
    lvl, num, title, numbered = _classify_heading("道路提升横断面图")
    assert lvl == _PLAIN_LEVEL
    assert num == ""
    assert title == "道路提升横断面图"
    assert numbered is False


def test_classify_heading_strips_toc_page_number():
    # 目录标题尾部页码剥掉
    assert _classify_heading("二、项目的基本情况 1") == (1, "二、", "项目的基本情况", True)


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
    assert result[0].level == 3          # 纯数字+空格 → level 3
    assert result[1].number == "2"
    assert result[1].title == "详细设计"


def test_parse_sections_all_headings_become_nodes():
    """所有 # 标题都成节点，含无编号标题。"""
    content = "# 一、总则\n\n正文A\n\n# （一）子节\n\n正文B\n\n# 附图\n\n图\n"
    secs = parse_sections(content)
    assert [s.title for s in secs] == ["总则", "子节", "附图"]
    assert [s.level for s in secs] == [1, 2, _PLAIN_LEVEL]
    assert [s.numbered for s in secs] == [True, True, False]


def test_parse_sections_tree_end_covers_children():
    """父级 tree_end_pos 跨越子节，end_pos 仍停在下一个任意标题。"""
    content = "# 一、父章\n\n引言\n\n# （一）子一\n\nA\n\n# （二）子二\n\nB\n\n# 二、下一章\n\nC\n"
    secs = parse_sections(content)
    parent = secs[0]  # 一、父章
    # 平铺 end 停在 （一）
    assert content[parent.start_pos:parent.end_pos].count("#") == 1
    # 层级 tree_end 跨到 二、下一章 之前，含 （一）（二）
    tree = content[parent.start_pos:parent.tree_end_pos]
    assert "子一" in tree and "子二" in tree
    assert "下一章" not in tree


def test_parse_sections_leaf_tree_end_equals_flat_end():
    """无编号叶子的 tree_end 退化为平铺 end（下一个任意标题）。"""
    content = "# 附图\n\n图1\n\n# 说明\n\n注\n"
    secs = parse_sections(content)
    assert secs[0].end_pos == secs[0].tree_end_pos

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


def test_build_table_source_refs_attaches_bboxes():
    from model.tables import FileTable
    from service.extraction_service import _build_table_source_refs

    table = FileTable(
        file_id="f1", table_index=0, total_table=1,
        table_name="资产负债表", table_content="<table><tr><td>1</td></tr></table>",
        start_pos=10, end_pos=50, page_num="2",
    )
    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 2,
         "bbox": [30, 40, 580, 700], "page_size": [612, 792]},
    ]
    refs, _texts = _build_table_source_refs([table], "资产负债表", mapping)
    ref = refs["_tables"][0]
    assert ref["bboxes"] == [
        {"page_num": 2, "bbox": [30, 40, 580, 700], "page_size": [612, 792]},
    ]
    # 原有字段不受影响
    assert ref["table_name"] == "资产负债表"
    assert ref["text"].startswith("表格名称: 资产负债表\n")


def test_build_table_source_refs_legacy_mapping_no_bboxes_key():
    from model.tables import FileTable
    from service.extraction_service import _build_table_source_refs

    table = FileTable(
        file_id="f1", table_index=0, total_table=1,
        table_name="表A", table_content="<table></table>",
        start_pos=10, end_pos=50, page_num="2",
    )
    refs, _texts = _build_table_source_refs(
        [table], "表A", [{"start_pos": 0, "end_pos": 20, "page_num": 2}]
    )
    assert "bboxes" not in refs["_tables"][0]


def test_build_text_source_refs_chunk_result_with_own_page_num_gets_bboxes():
    """chunk_db/vector_db 形态结果自带 page_num，bbox 仍统一查 page_mapping。"""
    from service.extraction_service import _build_text_source_refs

    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 1,
         "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
    ]
    results = [{
        "keyword": "金额", "chunk_content": "命中文本",
        "start_pos": 5, "end_pos": 15,
        "page_num": "1", "chunk_id": "c1", "chunk_index": 0,
    }]
    refs, _texts = _build_text_source_refs("chunk_db", results, mapping)
    ref = refs["金额"][0]
    assert ref["page_num"] == "1"  # 用自带页码，不走 page_mapping 查页
    assert ref["bboxes"] == [
        {"page_num": 1, "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
    ]


async def test_search_vector_db_attaches_query_text_as_keyword(monkeypatch):
    """vector_db 检索结果每条挂 keyword=query_text，作为占位符标签。"""
    from service import extraction_service

    async def fake_get_embeddings(texts):
        return [[0.1, 0.2]]

    class FakeMilvusClient:
        def connect(self):
            pass

        def ensure_collection(self):
            pass

        def search(self, query_vector, top_k, file_id, score_threshold):
            return [
                {
                    "chunk_id": "c1", "file_id": file_id, "chunk_index": 0,
                    "total_chunks": 2, "chunk_content": "块1",
                    "start_pos": 0, "end_pos": 2, "page_num": "1", "score": 0.1,
                },
                {
                    "chunk_id": "c2", "file_id": file_id, "chunk_index": 1,
                    "total_chunks": 2, "chunk_content": "块2",
                    "start_pos": 5, "end_pos": 7, "page_num": "2", "score": 0.2,
                },
            ]

    monkeypatch.setattr(extraction_service, "get_embeddings", fake_get_embeddings)
    monkeypatch.setattr(extraction_service, "MilvusClient", FakeMilvusClient)

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": " 合同总金额 ", "top_k": 5}
    )
    assert len(results) == 2
    assert all(r["keyword"] == "合同总金额" for r in results)


def test_build_text_source_refs_section_enters_texts():
    """section 结果无 keyword，用 section_title 兜底进 _texts（正式路径注入修复）。"""
    from service.extraction_service import _build_text_source_refs

    results = [{
        "section_number": "1", "section_title": "概述", "section_index": 0,
        "content": "概述内容", "start_pos": 0, "end_pos": 10,
    }]
    refs, texts = _build_text_source_refs("section", results, [])
    assert texts == {"概述": "概述内容"}
    assert refs["_texts"] == {"概述": "概述内容"}
    assert refs["概述"][0]["text"] == "概述内容"


def test_build_text_source_refs_vector_db_enters_texts():
    """vector_db 结果带 keyword=query_text 后，按 query_text 分组拼接进 _texts。"""
    from service.extraction_service import _build_text_source_refs

    results = [
        {"keyword": "合同总金额", "chunk_content": "块1", "start_pos": 0,
         "end_pos": 2, "page_num": "1", "chunk_id": "c1", "chunk_index": 0},
        {"keyword": "合同总金额", "chunk_content": "块2", "start_pos": 5,
         "end_pos": 7, "page_num": "2", "chunk_id": "c2", "chunk_index": 1},
    ]
    refs, texts = _build_text_source_refs("vector_db", results, [])
    assert texts == {"合同总金额": "【第1页】\n块1\n---\n【第2页】\n块2"}
    assert refs["合同总金额"][0]["chunk_id"] == "c1"


async def test_search_section_attaches_pattern_as_keyword():
    """section 结果挂 keyword=section_pattern，作为占位符标签（与前端下拉插入的标签一致）。"""
    from service.extraction_service import search_section

    content = """# 1 概述

概述内容。

# 2 付款方式

按月支付。

# 3 付款期限

合同签订后 30 日内。
"""
    results = await search_section(
        content, {"section_pattern": "付款", "match_type": "contains"}
    )
    assert len(results) == 2
    assert all(r["keyword"] == "付款" for r in results)
    assert results[0]["section_title"] == "付款方式"


async def test_search_section_returns_full_subtree():
    """定位父章拿到整章（含子节），而非停在第一个子标题。"""
    from service.extraction_service import search_section

    content = (
        "# 二、项目的基本情况\n\n引言\n\n"
        "# （一）项目名称\n\n某某项目\n\n"
        "# （二）项目代码\n\nABC123\n\n"
        "# 三、下一章\n\n无关\n"
    )
    results = await search_section(
        content, {"section_pattern": "项目的基本情况", "match_type": "contains"}
    )
    assert len(results) == 1
    body = results[0]["content"]
    assert "项目名称" in body and "项目代码" in body  # 含子节
    assert "下一章" not in body
    assert results[0]["level"] == 1


async def test_search_section_dedup_toc_and_body_keep_longest():
    """目录条 + 正文条同名，只保留内容最长（正文）的那条。"""
    from service.extraction_service import search_section

    content = (
        "# 二、项目的基本情况 1\n\n（一）项目名称 1\n\n"                     # 目录条：正文即目录列表（短）
        "# 三、下一章 2\n\n（一）xxx\n\n"
        "# 二、项目的基本情况\n\n"                                          # 正文条（长）
        "这里是真正的正文内容，篇幅明显更长更长更长更长更长更长更长更长更长更长更长更长更长。\n"
    )
    results = await search_section(
        content, {"section_pattern": "项目的基本情况", "match_type": "exact"}
    )
    assert len(results) == 1
    assert "真正的正文内容" in results[0]["content"]


def test_build_text_source_refs_section_groups_by_pattern_keyword():
    """section 结果带 keyword=pattern 时按 pattern 分组（contains/fuzzy/llm 多命中合并到同一标签）。"""
    from service.extraction_service import _build_text_source_refs

    results = [
        {"keyword": "付款", "section_title": "付款方式", "section_index": 0,
         "content": "按月支付", "start_pos": 0, "end_pos": 10},
        {"keyword": "付款", "section_title": "付款期限", "section_index": 1,
         "content": "30 日内", "start_pos": 20, "end_pos": 30},
    ]
    refs, texts = _build_text_source_refs("section", results, [])
    assert texts == {"付款": "按月支付\n---\n30 日内"}
    assert len(refs["付款"]) == 2


# ---------------------------------------------------------------------------
# search_rule 向前/向后扩展方向逻辑
#   direction 语义：forward=向关键词【后文】扩展；backward=向关键词【前文】扩展；
#   both=双向。两个方向应完全对称，各自只在自己方向扩展。
# ---------------------------------------------------------------------------

async def test_search_rule_forward_excludes_preceding_text():
    """forward 只向关键词后文扩展，不应把关键词前面的内容截进来。"""
    from service.extraction_service import search_rule

    content = "无关前置段落XXXX金额是100元。后续段落"
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。"],
         "direction": "forward", "max_length": 200},
    )
    assert results[0]["extracted_text"] == "金额是100元"


async def test_search_rule_forward_stops_at_adjacent_stopword():
    """forward 遇到紧邻关键词右侧的停用词应立即停止，不被更远的停用词覆盖。"""
    from service.extraction_service import search_rule

    content = "金额。一大段本不该被截取的后续内容\n结束"
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。", "\n"],
         "direction": "forward", "max_length": 200},
    )
    assert results[0]["extracted_text"] == "金额"


async def test_search_rule_forward_adjacent_stopword_no_overexpand():
    """forward 紧邻停用词为唯一停用词时，不应被误判为未命中而扩展到 max_length。"""
    from service.extraction_service import search_rule

    content = "金额。" + "尾" * 50
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。"],
         "direction": "forward", "max_length": 200},
    )
    assert results[0]["extracted_text"] == "金额"


async def test_search_rule_forward_no_stopword_expands_to_max_length():
    """forward 无停用词命中时向后文扩展至 max_length，且不含关键词前文。"""
    from service.extraction_service import search_rule

    content = "前缀金额" + "后" * 300
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。"],
         "direction": "forward", "max_length": 50},
    )
    assert results[0]["extracted_text"] == "金额" + "后" * 50


async def test_search_rule_backward_excludes_following_text():
    """backward 只向关键词前文扩展，不应把关键词后面的内容截进来（回归保护）。"""
    from service.extraction_service import search_rule

    content = "前置段落。金额是100元后续无关YYYY"
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。"],
         "direction": "backward", "max_length": 200},
    )
    assert results[0]["extracted_text"] == "金额"


async def test_search_rule_both_expands_both_sides():
    """both 双向扩展，两侧各到最近停用词（回归保护）。"""
    from service.extraction_service import search_rule

    content = "前置段落。金额是100元。后续段落"
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。"],
         "direction": "both", "max_length": 200},
    )
    assert results[0]["extracted_text"] == "金额是100元"


def test_outline_payload_has_level_fields():
    """outline 每项透出 level/numbered/tree_end_pos/tree_content。"""
    content = "# 一、父章\n\n引言\n\n# （一）子节\n\nA\n\n# 二、下一章\n\nB\n"
    secs = parse_sections(content)
    # 复刻 get_file_outline 的构造逻辑做纯函数校验
    payload = [
        {
            "index": s.index, "number": s.number, "title": s.title,
            "level": s.level, "numbered": s.numbered,
            "content": content[s.start_pos:s.end_pos],
            "tree_content": content[s.start_pos:s.tree_end_pos],
            "start_pos": s.start_pos, "end_pos": s.end_pos,
            "tree_end_pos": s.tree_end_pos,
        }
        for s in secs
    ]
    parent = payload[0]
    assert parent["level"] == 1 and parent["numbered"] is True
    assert "子节" in parent["tree_content"]        # 含子树
    assert "子节" not in parent["content"]         # 自身正文不含子树
    assert payload[2]["title"] == "下一章"


async def test_search_section_mixed_document_integration():
    from service.extraction_service import search_section

    content = (
        "# 目录\n\n二、项目的基本情况 1\n\n"                    # 无编号 + 目录列表
        "# 二、项目的基本情况\n\n本章引言。\n\n"                 # L1 正文
        "# （三）建设规模及内容\n\n建设内容正文很详细。\n\n"      # L2
        "# 1. 经济效益\n\n效益明显。\n\n"                        # L3
        "# (1) 子项\n\n子项内容。\n\n"                           # L4
        "# 三、下一章\n\n无关。\n\n"                             # L1
        "# 附图\n\n某图。\n"                                     # 无编号叶子
    )
    # L2 定位拿到含 L3/L4 的整节
    r2 = await search_section(content, {"section_pattern": "建设规模及内容", "match_type": "contains"})
    assert len(r2) == 1
    assert "经济效益" in r2[0]["content"] and "子项" in r2[0]["content"]
    assert "下一章" not in r2[0]["content"]
    # L1 定位去重后只剩正文条（含引言，不是目录条）
    r1 = await search_section(content, {"section_pattern": "项目的基本情况", "match_type": "exact"})
    assert len(r1) == 1
    assert "本章引言" in r1[0]["content"]
