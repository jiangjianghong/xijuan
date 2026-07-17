"""独立逻辑分析请求/响应模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from model.schemas import (
    AnalysisRunModeEnum,
    AnalysisRunRequest,
    AnalysisRunResponse,
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
