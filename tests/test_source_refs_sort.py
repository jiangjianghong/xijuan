"""source_refs 按整页包含相似度排序的测试。"""

from __future__ import annotations

from service.extraction_service import _sort_source_refs_by_page_containment

# page_contents 的 key 来自 split_md_by_pages，生产数据是 int
_PAGE_CONTENTS = {1: "无关内容", 2: "合同总金额为一百万元", 3: "其他条款"}


def test_str_page_num_still_matches_int_keyed_page_contents():
    """ref.page_num 是 str、page_contents 的 key 是 int，仍须命中。"""
    refs = {
        "金额": [
            {"page_num": "1", "text": "无关"},
            {"page_num": "2", "text": "命中"},
        ]
    }
    _sort_source_refs_by_page_containment(refs, "一百万元", _PAGE_CONTENTS)
    assert refs["金额"][0]["page_num"] == "2"


def test_cross_page_ref_scores_by_best_page():
    """跨页 ref 用 page_nums 里得分最高的页参与排序，不再恒沉底。"""
    refs = {
        "金额": [
            {"page_num": "3", "text": "无关"},
            {"page_num": "1-2", "page_nums": [1, 2], "text": "跨页命中"},
        ]
    }
    _sort_source_refs_by_page_containment(refs, "一百万元", _PAGE_CONTENTS)
    assert refs["金额"][0]["page_nums"] == [1, 2]


def test_non_ref_keys_and_short_lists_untouched():
    refs = {"_texts": {"金额": "x"}, "金额": [{"page_num": "1"}]}
    _sort_source_refs_by_page_containment(refs, "一百万元", _PAGE_CONTENTS)
    assert refs["_texts"] == {"金额": "x"}
    assert len(refs["金额"]) == 1
