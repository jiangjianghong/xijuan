"""独立逻辑分析请求/响应模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from model.schemas import (
    AnalysisRunModeEnum,
    AnalysisRunRequest,
    AnalysisRunResponse,
    AnalysisRunSourceEnum,
)


def _item(biz_id: str = "order-889") -> dict:
    return {
        "type_id": "contract",
        "biz_id": biz_id,
        "field_values": {"amount": "1200000"},
    }


def test_analysis_run_rejects_empty_items():
    with pytest.raises(ValidationError):
        AnalysisRunRequest(mode="sync", items=[])


def test_analysis_run_async_requires_callback_url():
    with pytest.raises(ValidationError, match="async 模式必须提供 callback_url"):
        AnalysisRunRequest(mode="async", items=[_item()])


def test_analysis_run_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        AnalysisRunRequest(mode="batch", items=[_item()])


def test_analysis_run_accepts_sync_batch():
    request = AnalysisRunRequest(
        mode="sync",
        items=[_item("order-889"), _item("order-890")],
    )
    assert request.mode == AnalysisRunModeEnum.sync
    assert request.items[1].biz_id == "order-890"


def test_analysis_run_response_keeps_item_order():
    response = AnalysisRunResponse(
        total_items=2,
        items=[
            {
                "item_index": 0,
                "biz_id": "a",
                "type_id": "t",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
            },
            {
                "item_index": 1,
                "biz_id": "b",
                "type_id": "t",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
            },
        ],
    )
    assert [item.biz_id for item in response.items] == ["a", "b"]


# ── item 级 rule_ids ────────────────────────────────────────


def test_analysis_run_item_defaults_rule_ids_to_none():
    """不传 rule_ids 必须是 None 而非空列表——两者语义相反。"""
    request = AnalysisRunRequest(mode="sync", items=[_item()])
    assert request.items[0].rule_ids is None


def test_analysis_run_item_accepts_named_rule_ids():
    request = AnalysisRunRequest(
        mode="sync",
        items=[{**_item(), "rule_ids": ["amount_check", "tax_check"]}],
    )
    assert request.items[0].rule_ids == ["amount_check", "tax_check"]


def test_analysis_run_item_keeps_empty_rule_ids_distinct_from_none():
    request = AnalysisRunRequest(
        mode="sync",
        items=[{**_item(), "rule_ids": []}],
    )
    assert request.items[0].rule_ids == []


def test_analysis_run_item_result_defaults_unknown_rule_ids():
    response = AnalysisRunResponse(
        total_items=1,
        items=[{
            "item_index": 0,
            "biz_id": "a",
            "type_id": "t",
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
        }],
    )
    assert response.items[0].unknown_rule_ids == []


def test_analysis_run_response_preserves_unknown_rule_ids():
    """服务层产出的 unknown_rule_ids 不能被 pydantic 静默丢弃。"""
    response = AnalysisRunResponse.model_validate({
        "total_items": 1,
        "items": [{
            "item_index": 0,
            "biz_id": "a",
            "type_id": "t",
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
            "unknown_rule_ids": ["ghost"],
        }],
    })
    assert response.model_dump()["items"][0]["unknown_rule_ids"] == ["ghost"]


# ── source=file 模式 ────────────────────────────────────────


def _file_item(**overrides) -> dict:
    item = {"biz_id": "order-889", "file_id": "f" * 32}
    item.update(overrides)
    return item


def test_source_defaults_to_values():
    request = AnalysisRunRequest(mode="sync", items=[_item()])
    assert request.source == AnalysisRunSourceEnum.values
    assert request.persist is False


def test_file_source_requires_file_id():
    with pytest.raises(ValidationError, match="source=file 时每个 item 必须提供 file_id"):
        AnalysisRunRequest(
            mode="sync",
            source="file",
            items=[{"biz_id": "b0", "type_id": "contract"}],
        )


def test_file_source_rejects_field_values():
    with pytest.raises(ValidationError, match="source=file 时不能传 field_values"):
        AnalysisRunRequest(
            mode="sync",
            source="file",
            items=[_file_item(field_values={"amount": "1"})],
        )


def test_values_source_rejects_file_id():
    with pytest.raises(ValidationError, match="source=values 时不能传 file_id"):
        AnalysisRunRequest(
            mode="sync",
            items=[{**_item(), "file_id": "f" * 32}],
        )


def test_values_source_requires_type_id():
    with pytest.raises(ValidationError, match="source=values 时每个 item 必须提供 type_id"):
        AnalysisRunRequest(
            mode="sync",
            items=[{"biz_id": "b0", "field_values": {"amount": "1"}}],
        )


def test_persist_requires_file_source():
    with pytest.raises(ValidationError, match="persist=true 仅在 source=file 时可用"):
        AnalysisRunRequest(mode="sync", persist=True, items=[_item()])


def test_file_source_accepts_optional_type_id_and_rule_ids():
    request = AnalysisRunRequest(
        mode="sync",
        source="file",
        persist=True,
        items=[_file_item(rule_ids=["amount_check"])],
    )
    assert request.items[0].type_id is None
    assert request.items[0].file_id == "f" * 32
    assert request.items[0].rule_ids == ["amount_check"]
    assert request.persist is True


def test_item_result_defaults_error_to_none():
    response = AnalysisRunResponse(
        total_items=1,
        items=[{
            "item_index": 0, "biz_id": "a", "type_id": "t",
            "total": 0, "succeeded": 0, "failed": 0, "results": [],
        }],
    )
    assert response.items[0].error is None
