import pytest
from pydantic import ValidationError

from model.schemas import ExtractionFieldCreate


def _field(config):
    return {
        "field_id": "amount",
        "field_name": "金额",
        "source_type": "text",
        "search_type": "hybrid",
        "search_config": config,
        "use_llm": 0,
    }


def test_hybrid_union_accepts_text_and_table():
    field = ExtractionFieldCreate(**_field({
        "strategy": "union",
        "items": [
            {"id": "a", "source_type": "text", "method": "chunk_db", "config": {}},
            {"id": "b", "source_type": "table", "method": "table_match", "config": {}},
        ],
    }))
    assert field.search_config["items"][1]["id"] == "b"


def test_hybrid_fallback_accepts_one_vl():
    field = ExtractionFieldCreate(**_field({
        "strategy": "fallback",
        "items": [
            {"id": "a", "source_type": "text", "method": "context", "config": {}},
            {"id": "b", "source_type": "vl", "method": "vl_locate", "config": {}},
        ],
    }))
    assert field.search_type.value == "hybrid"


@pytest.mark.parametrize("config", [
    {"strategy": "union", "items": [{"id": "v", "source_type": "vl", "method": "vl_model", "config": {}}]},
    {"strategy": "fallback", "items": [
        {"id": "v1", "source_type": "vl", "method": "vl_model", "config": {}},
        {"id": "v2", "source_type": "vl", "method": "vl_locate", "config": {}},
    ]},
])
def test_hybrid_rejects_invalid_vl(config):
    with pytest.raises(ValidationError):
        ExtractionFieldCreate(**_field(config))


def test_legacy_field_still_accepts_vector_config():
    field = ExtractionFieldCreate(
        field_id="amount",
        field_name="金额",
        source_type="text",
        search_type="vector_db",
        search_config={"query_text": "总投资"},
        text_extract_prompt="<search_result>总投资</search_result>",
    )
    assert field.search_type.value == "vector_db"


def test_hybrid_llm_requires_fixed_placeholder():
    payload = _field({
        "strategy": "union",
        "items": [{"id": "a", "source_type": "text", "method": "context", "config": {}}],
    })
    payload["use_llm"] = 1
    payload["text_extract_prompt"] = "<search_result>其他标签</search_result>"
    with pytest.raises(ValidationError):
        ExtractionFieldCreate(**payload)
