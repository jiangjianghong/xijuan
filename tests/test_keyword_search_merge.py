"""关键词上下文合并、逐页注入与表格完整性回归测试。"""

from types import SimpleNamespace

import pytest

from model.tables import ExtractionField
from service import extraction_service as es
from service.extraction_snapshot import FileExtractionSnapshot


def _config(**overrides):
    return {
        "keywords": ["你好"], "context_before": 3, "context_after": 3,
        "max_results": 10, "max_total_results": 0, "sort_order": "asc",
        **overrides,
    }


async def test_context_merges_user_example():
    """按坐标取并集，不能直接拼接两个包含重复文字的窗口。"""
    content = "我我我你好你好他他他"
    results = await es.search_context(content, _config())
    assert len(results) == 1
    assert results[0]["context"] == content
    assert (results[0]["start_pos"], results[0]["end_pos"]) == (0, len(content))


async def test_dense_chain_merges_before_result_quota():
    """密集命中只占一个名额，合并末尾不受原始命中条数上限截断。"""
    dense = "我我我" + "你好" * 8 + "他他他"
    content = dense + "隔" * 30 + "甲甲甲你好乙乙乙"
    results = await es.search_context(content, _config(max_results=2))
    assert [r["context"] for r in results] == [dense, "甲甲甲你好乙乙乙"]


@pytest.mark.parametrize("order", ["asc", "desc", "relevance"])
async def test_merged_result_limit_keeps_entire_window(order):
    content = "我我我你好你好你好他他他"
    results = await es.search_context(content, _config(max_results=1, sort_order=order))
    assert len(results) == 1
    assert results[0]["context"] == content
    assert content[results[0]["position"]:].startswith("你好")


async def test_identical_text_at_different_positions_is_not_deduplicated():
    content = "甲你好乙" + "隔" * 20 + "甲你好乙"
    results = await es.search_context(content, _config(context_before=1, context_after=1))
    assert [r["context"] for r in results] == ["甲你好乙", "甲你好乙"]
    assert results[0]["end_pos"] < results[1]["start_pos"]


async def test_adjacent_windows_merge_without_inventing_gap_text():
    results = await es.search_context("你好你好", _config(context_before=0, context_after=0))
    assert [r["context"] for r in results] == ["你好你好"]


async def test_keywords_keep_independent_placeholder_labels():
    content = "甲方乙方甲方"
    results = await es.search_context(content, _config(
        keywords=["甲方", "乙方"], context_before=10, context_after=10,
    ))
    refs, texts = es._build_text_source_refs("context", results, [{
        "start_pos": 0, "end_pos": len(content), "page_num": 1,
    }])
    assert texts == {"甲方": "【第1页】\n" + content, "乙方": "【第1页】\n" + content}
    assert len(refs["甲方"]) == len(refs["乙方"]) == 1


@pytest.mark.parametrize("direction", ["forward", "backward", "both"])
async def test_rule_overlaps_merge_and_keep_original_coordinates(direction):
    content = "前置。 \t甲你好乙你好丙 \t。后置"
    results = await es.search_rule(content, _config(
        direction=direction, stop_words=["。"], max_length=200,
    ))
    assert len(results) == 1
    result = results[0]
    expected = {"forward": "你好乙你好丙", "backward": "甲你好乙你好", "both": "甲你好乙你好丙"}
    assert result["extracted_text"] == expected[direction]
    assert content[result["start_pos"]:result["end_pos"]] == expected[direction]


@pytest.mark.parametrize("method,text_key", [(es.search_context, "context"), (es.search_rule, "extracted_text")])
async def test_table_hits_return_one_complete_table(method, text_key):
    table = '<TABLE class="数据"><tr><td>你好</td><td>你好</td></tr></TABLE>'
    content = "无关前文\n" + table + "\n无关后文"
    results = await method(content, _config(
        context_before=0, context_after=0, max_length=1,
        stop_words=["</td>"], max_results=1,
    ))
    assert len(results) == 1
    assert results[0][text_key] == table
    assert content[results[0]["start_pos"]:results[0]["end_pos"]] == table


@pytest.mark.parametrize("hit_before", [True, False])
async def test_window_boundary_inside_table_expands_to_whole_table(hit_before):
    table = "<table><tr><td>数据</td></tr></table>"
    content = "你好" + table if hit_before else table + "你好"
    results = await es.search_context(content, _config(
        context_before=0 if hit_before else 5,
        context_after=5 if hit_before else 0,
    ))
    assert results[0]["context"] == content


async def test_unrelated_table_outside_window_is_not_added():
    results = await es.search_context("你好无关<table><tr><td>数据</td></tr></table>", _config(
        context_before=0, context_after=1,
    ))
    assert results[0]["context"] == "你好无"


async def test_merged_cross_page_context_keeps_page_markers_and_bboxes():
    content = "我我我你好你好他他他"
    mapping = [
        {"start_pos": 0, "end_pos": 5, "page_num": 1, "bbox": [1, 2, 3, 4]},
        {"start_pos": 5, "end_pos": len(content), "page_num": 2, "bbox": [5, 6, 7, 8]},
    ]
    results = await es.search_context(content, _config())
    refs, texts = es._build_text_source_refs("context", results, mapping)
    assert texts["你好"] == "【第1页】\n我我我你好\n【第2页】\n你好他他他"
    assert refs["_texts"] == texts
    assert len(refs["你好"]) == 1
    assert refs["你好"][0]["page_nums"] == [1, 2]
    assert [bbox["page_num"] for bbox in refs["你好"][0]["bboxes"]] == [1, 2]


async def test_table_and_following_page_keep_html_and_page_boundaries():
    table = "<table><tr><td>你好</td><td>你好</td></tr></table>"
    content = table + "你好后文"
    mapping = [
        {"start_pos": 0, "end_pos": len(table), "page_num": 1},
        {"start_pos": len(table), "end_pos": len(content), "page_num": 2},
    ]
    results = await es.search_context(content, _config(context_before=3, context_after=5))
    refs, texts = es._build_text_source_refs("context", results, mapping)
    assert texts["你好"] == "【第1页】\n" + table + "\n【第2页】\n你好后文"
    assert refs["你好"][0]["text"] == content


def test_table_chunks_with_name_prefix_keep_existing_content():
    """带表名前缀的块不能按原文坐标重切，避免损坏标签或丢掉表名。"""
    text = "表格名称: 金额\n<table><tr><td>你好</td></tr></table>"
    results = [{"keyword": "你好", "chunk_id": "c1", "chunk_index": 0,
                "chunk_content": text, "start_pos": 0, "end_pos": 43, "page_num": "1-2"}]
    refs, texts = es._build_text_source_refs("chunk_db", results, [
        {"start_pos": 0, "page_num": 1}, {"start_pos": 10, "page_num": 2},
    ])
    assert texts["你好"] == "【第1-2页】\n" + text
    assert refs["你好"][0]["text"] == text


@pytest.mark.parametrize("use_llm", [0, 1])
async def test_official_and_debug_extraction_use_same_merged_pages(monkeypatch, use_llm):
    """实际模型输入、关闭模型的原文输出与调试流保持一致。"""
    content = "我我我你好你好他他他"
    mapping = [{"start_pos": 0, "end_pos": 5, "page_num": 1},
               {"start_pos": 5, "end_pos": len(content), "page_num": 2}]
    snapshot = FileExtractionSnapshot(
        file_id="f1", type_id="default", content=content, page_mapping=mapping,
        page_contents={}, tables=(), chunks=(),
    )
    field = ExtractionField(field_id="f1", field_name="问候", source_type="text",
                            search_type="context", search_config=_config(), use_llm=use_llm,
                            text_extract_prompt="提取 <search_result>你好</search_result>")
    expected = "【第1页】\n我我我你好\n【第2页】\n你好他他他"
    prompts = []

    async def fake_chat(prompt, **kwargs):
        prompts.append(prompt)
        return '{"reason": "命中", "value": "问候", "pages": [1, 2]}'

    async def fake_snapshot(*args, **kwargs):
        return snapshot

    class Session:
        async def execute(self, stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
                file_content=content, page_mapping=mapping,
            ))

    monkeypatch.setattr(es, "chat_completion", fake_chat)
    monkeypatch.setattr(es, "load_extraction_snapshot", fake_snapshot)
    value, _, refs, _ = await es.extract_text_field("f1", field, snapshot)
    events = [event async for event in es.test_field_extraction_stream("f1", field, Session())]
    assert not [event for event in events if event["event"] == "error"]
    search_event = next(event for event in events if event["event"] == "search_results")
    assert len(search_event["data"]["results"]) == 1
    assert search_event["data"]["results_by_label"] == refs["_texts"] == {"你好": expected}
    if use_llm:
        assert len(prompts) == 2
        assert prompts[0] == prompts[1]
        assert expected in prompts[0]
    else:
        assert not prompts
        assert value == expected
        assert next(event for event in events if event["event"] == "result")["data"]["extracted_value"] == expected
