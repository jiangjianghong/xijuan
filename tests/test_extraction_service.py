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
        "f1", {"query_text": "合同总金额", "top_k": 5}
    )
    assert len(results) == 2
    assert all(r["keyword"] == "合同总金额" for r in results)
