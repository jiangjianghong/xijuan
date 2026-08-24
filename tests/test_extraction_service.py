"""字段提取服务测试。"""

from __future__ import annotations

from service.extraction_service import parse_sections, SectionInfo
from service.extraction_service import _classify_heading, _PLAIN_LEVEL
from service.extraction_service import (
    parse_llm_json_response,
    _normalize_pages,
    _sort_source_refs_by_page_containment,
)


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
    """vector_db 检索结果每条挂 keyword=query_text，作为占位符标签。

    改造后走内存全量打分（不再 monkeypatch MilvusClient），命中子块
    映射回父块；详尽用例见 tests/test_search_vector_db.py。
    """
    import numpy as np

    from service import extraction_service
    from service.file_vector_index import FileVectorIndex, l2_normalize

    class Parent:
        def __init__(self, chunk_id, content, page_num, index):
            self.chunk_id = chunk_id
            self.chunk_content = content
            self.chunk_index = index
            self.start_pos = index * 5
            self.end_pos = index * 5 + 2
            self.page_num = page_num

    async def fake_get_embeddings(texts, **kwargs):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(extraction_service, "get_embeddings", fake_get_embeddings)

    parents = [Parent("c1", "块1", "1", 0), Parent("c2", "块2", "2", 1)]
    index = FileVectorIndex(
        file_id="f1",
        sub_ids=("c1_s0", "c2_s0"),
        parent_ids=("c1", "c2"),
        matrix=l2_normalize(np.array([[1.0, 0.0], [0.99, 0.1]], dtype=np.float32)),
        parents={p.chunk_id: p for p in parents},
        degraded=False,
    )

    results = await extraction_service.search_vector_db(
        "f1", {"query_text": " 合同总金额 ", "top_k": 5}, index
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


# ── 模型自报参考页码（pages） ────────────────────────────────


def test_parse_llm_json_response_with_pages():
    value, reason, pages = parse_llm_json_response(
        '{"value": "甲公司", "reason": "见首页", "pages": [1, 3, 1]}'
    )
    assert value == "甲公司"
    assert reason == "见首页"
    assert pages == [1, 3]  # 去重升序


def test_parse_llm_json_response_without_pages():
    value, reason, pages = parse_llm_json_response('{"value": "x", "reason": "y"}')
    assert (value, reason, pages) == ("x", "y", [])


def test_parse_llm_json_response_array_merges_not_drops():
    """模型违规返回对象数组时：不静默只取第一条，逐条拼接 value、合并去重 pages。"""
    resp = (
        '[{"value": "甲村", "reason": "r1", "pages": [215]},'
        ' {"value": "乙村", "reason": "r2", "pages": [216, 217]},'
        ' {"value": "丙村", "reason": "r3", "pages": [217]}]'
    )
    value, reason, pages = parse_llm_json_response(resp)
    assert value == "甲村\n乙村\n丙村"
    assert reason == "r1\nr2\nr3"
    assert pages == [215, 216, 217]  # 合并去重升序


def test_parse_llm_json_response_plain_text_fallback_no_pages():
    value, reason, pages = parse_llm_json_response("这是一段纯文本")
    assert value == "这是一段纯文本"
    assert pages == []


def test_parse_llm_json_response_invalid_backslash_escape_keeps_pages():
    """MinerU 把百分号解析成 LaTeX `$5\\%$`，模型照抄进 value 导致非法 JSON 转义。
    清洗非法反斜杠后应正常解析，pages 不丢。"""
    resp = (
        '{"value": "出资占股 $5\\%$ 实行同股同权。",'
        ' "reason": "标题完全匹配，提取正文。", "pages": [289, 290]}'
    )
    value, reason, pages = parse_llm_json_response(resp)
    assert value == "出资占股 $5\\%$ 实行同股同权。"
    assert reason == "标题完全匹配，提取正文。"
    assert pages == [289, 290]


def test_parse_llm_json_response_valid_escapes_preserved():
    """合法转义 \\n \\t \\\" 以及成对反斜杠 \\\\ 不能被清洗破坏。"""
    resp = '{"value": "第一行\\n第二行\\t制表\\\\反斜杠", "reason": "r", "pages": [1]}'
    value, reason, pages = parse_llm_json_response(resp)
    assert value == "第一行\n第二行\t制表\\反斜杠"
    assert pages == [1]


def test_parse_llm_json_response_backslash_before_valid_char_literal():
    """`\\%` 是非法转义（补成 \\\\%），但 `\\\\%`（转义反斜杠+%）必须原样保留，
    不能把合法的 \\\\ 拆成 \\\\\\%。"""
    resp = '{"value": "a\\\\%b", "reason": "r", "pages": [2]}'
    value, reason, pages = parse_llm_json_response(resp)
    assert value == "a\\%b"
    assert pages == [2]


def test_normalize_pages_variants():
    assert _normalize_pages([2, 1, "第3页", "5"]) == [1, 2, 3, 5]
    assert _normalize_pages("3, 1、2") == [1, 2, 3]
    assert _normalize_pages(4) == [4]
    assert _normalize_pages(["", "abc", 0, -1, True]) == []
    assert _normalize_pages(None) == []


def test_sort_source_refs_skips_model_pages_int_list():
    """回归：_model_pages 是 int 数组，排序时必须跳过，否则对 int 调 .get() 崩溃。"""
    refs = {
        "村庄": [
            {"page_num": "216", "text": "a"},
            {"page_num": "218", "text": "b"},
        ],
        "_model_pages": [216, 217, 218],  # 2 个以上 int，曾触发 'int' object has no attribute 'get'
    }
    page_contents = {"216": "命中值在这一页", "218": "无关内容"}
    # 不抛异常即通过（就地排序）
    _sort_source_refs_by_page_containment(refs, "命中值", page_contents)
    assert refs["_model_pages"] == [216, 217, 218]  # 原样保留，未被当成 ref


# ---------------------------------------------------------------------------
# 关键词检索的相关度排序与两层截断
# ---------------------------------------------------------------------------

def _rank_chunk(index: int, text: str):
    """构造分块快照，供检索方法算 IDF。"""
    from service.extraction_snapshot import ChunkRow

    return ChunkRow(
        chunk_id=f"rk{index}",
        chunk_index=index,
        chunk_content=text,
        start_pos=index * 100,
        end_pos=index * 100 + len(text),
        page_num="1",
    )


async def test_search_context_relevance_prefers_co_occurrence():
    """relevance 排序把共现罕见关键词的命中顶到文档更靠前的孤立命中之前。"""
    from service.extraction_service import search_context

    content = "项目名称" + "填" * 500 + "项目名称与工程名称在此"
    chunks = tuple(
        _rank_chunk(i, text)
        for i, text in enumerate(["项目名称"] * 9 + ["工程名称 项目名称"])
    )
    results = await search_context(
        content,
        {"keywords": ["项目名称", "工程名称"], "context_before": 20,
         "context_after": 20, "max_results": 5},
        chunks,
    )
    assert results[0]["position"] > 400


async def test_search_context_quota_is_per_keyword():
    """低频关键词不再被高频关键词挤空（回归保护：backlog L21）。"""
    from service.extraction_service import search_context

    content = ("甲方" + "填" * 30) * 10 + "乙方"
    results = await search_context(
        content,
        {"keywords": ["甲方", "乙方"], "context_before": 10,
         "context_after": 10, "max_results": 3, "max_total_results": 0},
        (),
    )
    assert sum(1 for r in results if r["keyword"] == "甲方") == 3
    assert sum(1 for r in results if r["keyword"] == "乙方") == 1


async def test_search_context_max_total_results_caps_output():
    """max_total_results 生效，且轮转保证每个关键词都有份。"""
    from service.extraction_service import search_context

    content = ("甲方" + "填" * 30) * 5 + ("乙方" + "填" * 30) * 5
    results = await search_context(
        content,
        {"keywords": ["甲方", "乙方"], "context_before": 5, "context_after": 5,
         "max_results": 5, "max_total_results": 4},
        (),
    )
    assert len(results) == 4
    assert {r["keyword"] for r in results} == {"甲方", "乙方"}


async def test_search_context_asc_preserves_legacy_order():
    """显式 sort_order=asc 时仍按位置升序（存量配置行为不变）。"""
    from service.extraction_service import search_context

    content = "甲方" + "填" * 50 + "甲方" + "填" * 50 + "甲方"
    results = await search_context(
        content,
        {"keywords": ["甲方"], "context_before": 5, "context_after": 5,
         "max_results": 5, "sort_order": "asc"},
        (),
    )
    assert [r["position"] for r in results] == [0, 52, 104]


async def test_search_rule_quota_is_per_keyword():
    """rule 的截断同样改成每关键词限额。"""
    from service.extraction_service import search_rule

    content = "".join(f"甲方是A{i}。" for i in range(10)) + "乙方是B。"
    results = await search_rule(
        content,
        {"keywords": ["甲方", "乙方"], "stop_words": ["。"],
         "direction": "forward", "max_length": 50, "max_results": 3,
         "max_total_results": 0},
        (),
    )
    assert sum(1 for r in results if r["keyword"] == "甲方") == 3
    assert sum(1 for r in results if r["keyword"] == "乙方") == 1


async def test_search_rule_relevance_ranks_by_extracted_text():
    """rule 的相关度打分作用在 extracted_text 上，而非整个窗口。"""
    from service.extraction_service import search_rule

    content = "甲方是张三。" + "填" * 100 + "甲方是乙方指定的李四。"
    results = await search_rule(
        content,
        {"keywords": ["甲方", "乙方"], "stop_words": ["。"],
         "direction": "forward", "max_length": 50, "max_results": 5},
        (),
    )
    # 抽取片段里同时含「甲方」「乙方」的那条覆盖度最高，排第一
    assert "乙方" in results[0]["extracted_text"]


async def test_search_rule_asc_preserves_legacy_order():
    """显式 sort_order=asc 时仍按位置升序（存量配置行为不变）。"""
    from service.extraction_service import search_rule

    content = "金额是1。金额是2。金额是3。"
    results = await search_rule(
        content,
        {"keywords": ["金额"], "stop_words": ["。"], "direction": "forward",
         "max_length": 50, "max_results": 5, "sort_order": "asc"},
        (),
    )
    assert [r["extracted_text"] for r in results] == ["金额是1", "金额是2", "金额是3"]


async def test_search_chunk_db_relevance_beats_chunk_index_order():
    """相关度排序要看全部命中，而不是只在前 N 条里排。

    显式 max_total_results=0（不限总量）把前提固定住：本例要验的是「早停被
    移除后，排在最后的 chunk 8 因共现两个关键词而排第一」，不是总量截断行为。
    缺了这个键，总量会被默认值压到 max_results=2，断言虽仍成立但覆盖面变窄。
    """
    from service.extraction_service import search_chunk_db

    chunks = tuple(
        [_rank_chunk(i, "项目名称") for i in range(8)]
        + [_rank_chunk(8, "项目名称 工程名称")]
    )
    results = await search_chunk_db(
        "f1",
        {"keywords": ["项目名称", "工程名称"], "max_results": 2,
         "max_total_results": 0},
        chunks,
    )
    assert len(results) == 3
    assert results[0]["chunk_index"] == 8


async def test_search_chunk_db_max_total_results_caps_output():
    """chunk_db 也支持总量上限，且轮转保证每个关键词都有份。"""
    from service.extraction_service import search_chunk_db

    chunks = tuple(
        [_rank_chunk(i, "甲方") for i in range(5)]
        + [_rank_chunk(5 + i, "乙方") for i in range(5)]
    )
    results = await search_chunk_db(
        "f1",
        {"keywords": ["甲方", "乙方"], "max_results": 5, "max_total_results": 4},
        chunks,
    )
    assert len(results) == 4
    assert {r["keyword"] for r in results} == {"甲方", "乙方"}


async def test_search_chunk_db_no_keywords_returns_leading_chunks():
    """无关键词时返回前 max_results 个分块（行为不变）。"""
    from service.extraction_service import search_chunk_db

    chunks = tuple(_rank_chunk(i, f"块{i}") for i in range(10))
    results = await search_chunk_db("f1", {"max_results": 3}, chunks)
    assert [r["chunk_index"] for r in results] == [0, 1, 2]


async def test_search_context_max_total_defaults_to_max_results():
    """缺省 max_total_results 时总量等于 max_results（不涨量），但轮转分配、不饿死低频词。"""
    from service.extraction_service import search_context

    content = ("甲方" + "填" * 30) * 10 + "乙方"
    results = await search_context(
        content,
        {"keywords": ["甲方", "乙方"], "context_before": 10,
         "context_after": 10, "max_results": 3},
        (),
    )
    # 总量压回 max_results，与「全局截断」时代一致
    assert len(results) == 3
    # 但低频关键词仍保住一条——这正是 L21 要修的
    assert sum(1 for r in results if r["keyword"] == "乙方") == 1


async def test_search_rule_max_total_defaults_to_max_results():
    """rule 同样：缺省时总量等于 max_results。"""
    from service.extraction_service import search_rule

    content = "".join(f"甲方是A{i}。" for i in range(10)) + "乙方是B。"
    results = await search_rule(
        content,
        {"keywords": ["甲方", "乙方"], "stop_words": ["。"],
         "direction": "forward", "max_length": 50, "max_results": 3},
        (),
    )
    assert len(results) == 3
    assert sum(1 for r in results if r["keyword"] == "乙方") == 1


async def test_search_chunk_db_max_total_defaults_to_max_results():
    """chunk_db 同样：缺省时总量等于 max_results（它的 max_results 可写成 top_k）。"""
    from service.extraction_service import search_chunk_db

    chunks = tuple(
        [_rank_chunk(i, "甲方") for i in range(8)]
        + [_rank_chunk(8, "乙方")]
    )
    results = await search_chunk_db(
        "f1", {"keywords": ["甲方", "乙方"], "max_results": 3}, chunks,
    )
    assert len(results) == 3
    assert sum(1 for r in results if r["keyword"] == "乙方") == 1


async def test_search_context_explicit_zero_means_unlimited():
    """显式 max_total_results=0 表示不限总量，与缺省区分开。"""
    from service.extraction_service import search_context

    content = ("甲方" + "填" * 30) * 10 + "乙方"
    results = await search_context(
        content,
        {"keywords": ["甲方", "乙方"], "context_before": 10,
         "context_after": 10, "max_results": 3, "max_total_results": 0},
        (),
    )
    assert len(results) == 4  # 甲方 3 + 乙方 1，不被总量压制


async def test_search_section_not_wired_into_relevance_ranking():
    """section 检索不接入相关度排序：传 relevance 也按章节顺序返回。

    「section 不受影响」是本次改动的设计不变式之一（它按章节匹配，候选是整段
    章节，相关度打分对它没有意义）。这条测试是护栏——将来若有人顺手把 section
    也接进 rank_and_truncate，它会失败。
    """
    from service.extraction_service import search_section

    content = "# 一、付款方式\n\nA\n\n# 二、付款期限\n\nB\n\n# 三、付款条件\n\nC\n"
    results = await search_section(
        content,
        {"section_pattern": "付款", "section_match_type": "contains",
         "max_results": 5, "sort_order": "relevance"},
    )
    # 恒按 section_index 升序，不因 sort_order=relevance 重排
    assert [r["section_index"] for r in results] == [0, 1, 2]
