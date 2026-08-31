"""入参在抽取 / 分析链路上的接入。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from model.tables import ExtractionField
from service.extraction_service import _NON_REF_KEYS, _has_real_source_refs


def test_params_key_is_not_a_source_ref():
    """_params 是元数据，不能被当成一条命中。

    否则任何引用了参数的字段哪怕一条都没检索到，也会因为带着 _params 被
    _is_extraction_success 误判成功——_resolved_refs 当初就踩过这个坑。
    """
    assert "_params" in _NON_REF_KEYS
    assert _has_real_source_refs({"_params": {"d": "2026-08-31"}}) is False
    assert _has_real_source_refs({"_params": {"d": "x"}, "关键词": [{"text": "命中"}]}) is True


@pytest.mark.anyio
async def test_snapshot_carries_params():
    from service.extraction_snapshot import FileExtractionSnapshot

    snapshot = FileExtractionSnapshot(
        file_id="f", type_id="t", content="", page_mapping=[],
        page_contents={}, tables=(), chunks=(), params={"d": "2026-08-31"},
    )
    assert snapshot.params == {"d": "2026-08-31"}


@pytest.mark.anyio
async def test_extract_field_result_renders_params(monkeypatch):
    """_extract_field_result 渲染参数并把 _params 并进 source_refs。"""
    from service import extraction_service
    from service.extraction_snapshot import FileExtractionSnapshot

    seen: dict = {}

    async def fake_extract_text_field(file_id, field, snapshot):
        seen["prompt"] = field.text_extract_prompt
        return "值", "原因", {"关键词": [{"text": "命中"}]}, []

    monkeypatch.setattr(
        extraction_service, "extract_text_field", fake_extract_text_field
    )

    field = ExtractionField(
        field_id="f1", type_id="t1", field_name="有效期", source_type="text",
        search_type="context", enabled=1, priority=0,
        text_extract_prompt="今天是<param>d</param>",
    )
    snapshot = FileExtractionSnapshot(
        file_id="f", type_id="t1", content="", page_mapping=[],
        page_contents={}, tables=(), chunks=(), params={"d": "2026-08-31"},
    )

    value, reason, source_refs, _ = await extraction_service._extract_field_result(
        "f", field, snapshot, {}, {},
    )

    assert seen["prompt"] == "今天是2026-08-31"
    assert source_refs["_params"] == {"d": "2026-08-31"}
    # 原 ORM 对象未被就地改写
    assert field.text_extract_prompt == "今天是<param>d</param>"
