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
    assert texts == {"合同总金额": "块1\n---\n块2"}
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
