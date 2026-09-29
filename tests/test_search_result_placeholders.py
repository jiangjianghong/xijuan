"""检索占位符兼容进阶字段引用展开后的多行标签。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.tables import ExtractionField
from service import extraction_service as svc
from service.extraction_snapshot import FileExtractionSnapshot


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_validate_multiline_search_label(newline):
    prompt = f"检索内容：<search_result>项目名称{newline}项目描述</search_result>"
    assert svc.validate_prompt_has_placeholder(prompt)


def test_replace_multiline_labels_independently():
    prompt = "<search_result>项目\n描述</search_result>；<search_result>组织方式</search_result>"
    assert svc.replace_search_result_placeholders(
        prompt, {"项目\n描述": "项目原文", "组织方式": "组织原文"},
    ) == "项目原文；组织原文"


def test_multiline_label_without_results_uses_hint():
    assert svc.replace_search_result_placeholders(
        "<search_result>项目\n描述</search_result>", {},
    ) == "（未找到 '项目\n描述' 的相关内容）"


@pytest.mark.parametrize("prompt", ["普通文本", "<search_result></search_result>"])
def test_missing_or_empty_placeholder_still_rejected(prompt):
    assert not svc.validate_prompt_has_placeholder(prompt)


@pytest.mark.parametrize("mode", ["stream", "formal"])
async def test_advanced_vector_multiline_reference_reaches_llm(monkeypatch, mode):
    """复现 query_text 与检索标签同时引用多行普通字段结果的路径。"""
    reference = "<field_result>ygdz_xmms_all</field_result>"
    upstream = "项目名称：道路建设\n项目描述：以工代赈"
    content = "项目由村集体组织实施"
    field = ExtractionField(
        field_id="ygdz_zzfs_jj", field_name="可研报告-组织方式",
        source_type="text", is_advanced=1, use_llm=1,
        empty_retry_enabled=False, search_type="vector_db",
        search_config={"query_text": reference, "top_k": 50, "score_threshold": 0.5},
        text_extract_prompt=f"检索到的内容：<search_result>{reference}</search_result>",
    )
    snapshot = FileExtractionSnapshot(
        file_id="f1", type_id="default", content=content, page_mapping=[],
        page_contents={}, tables=(), chunks=(),
    )
    monkeypatch.setattr(svc, "load_basic_field_results", AsyncMock(
        return_value=({"ygdz_xmms_all": upstream}, {}, {}),
    ))
    monkeypatch.setattr(svc, "load_extraction_snapshot", AsyncMock(return_value=snapshot))

    async def search(file_id, config, vector_index):
        assert config["query_text"] == upstream
        return [{"keyword": upstream, "chunk_content": content, "page_num": "3",
                 "start_pos": 0, "end_pos": len(content), "chunk_id": "c1", "chunk_index": 0}]

    monkeypatch.setattr(svc, "search_vector_db", search)
    llm = AsyncMock(return_value='{"value":"村集体组织实施","reason":"原文","pages":[3]}')
    monkeypatch.setattr(svc, "chat_completion", llm)

    if mode == "stream":
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            scalar_one_or_none=lambda: SimpleNamespace(file_content=content, page_mapping=[]),
        )))
        events = [event async for event in svc.test_field_extraction_stream("f1", field, session)]
        assert not [event for event in events if event["event"] == "error"]
        assert events[-1]["event"] == "done"
        result = next(event["data"] for event in events if event["event"] == "result")
        assert result["extracted_value"] == "村集体组织实施"
    else:
        resolved, _ = svc.resolve_advanced_field(field, {"ygdz_xmms_all": upstream}, {})
        value, _, _, pages = await svc.extract_text_field("f1", resolved, snapshot)
        assert value == "村集体组织实施"
        assert pages == [3]

    llm.assert_awaited_once()
    prompt = llm.call_args.args[0]
    assert f"【第3页】\n{content}" in prompt
    assert "<search_result>" not in prompt
    assert "<field_result>" not in prompt
