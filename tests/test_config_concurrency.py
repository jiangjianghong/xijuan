from __future__ import annotations

import asyncio
from pathlib import Path

from utils.config import load_config
from utils.concurrency import get_limiter, replace_limiters


def test_legacy_model_connection_keys_are_normalized(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
extraction:
  llm_base_url: http://llm.test/v1
  llm_model: qwen
  llm_api_key: key
  llm_timeout: 12
  llm_retry_count: 2
  llm_extra_body: {foo: bar}
embedding:
  base_url: http://embedding.test/v1
  model_name: embed-old
table_name_validation:
  llm_base_url: http://table.test/v1
  llm_model: table-model
  llm_timeout: 9
concurrency:
  global_llm: 3
  global_embedding: 2
  global_vl: 1
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.extraction.base_url == "http://llm.test/v1"
    assert cfg.extraction.model == "qwen"
    assert cfg.extraction.api_key == "key"
    assert cfg.extraction.timeout == 12
    assert cfg.extraction.retry_count == 2
    assert cfg.extraction.extra_body == {"foo": "bar"}
    assert cfg.embedding.model == "embed-old"
    assert cfg.table_name_validation.base_url == "http://table.test/v1"
    assert cfg.table_name_validation.model == "table-model"
    assert cfg.concurrency.global_llm == 3
    assert cfg.concurrency.global_embedding == 2
    assert cfg.concurrency.global_vl == 1


def test_limiters_are_reused_and_can_be_replaced():
    async def scenario():
        replace_limiters({"global_llm": 2})
        first = get_limiter("global_llm", 2)
        assert get_limiter("global_llm", 2) is first
        assert first._value == 2

        replace_limiters({"global_llm": 5})
        second = get_limiter("global_llm", 5)
        assert second is first
        assert second._value == 5

    asyncio.run(scenario())


def test_analysis_concurrency_uses_only_canonical_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
analysis:
  max_concurrency: 19
concurrency:
  task_analysis: 23
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.concurrency.task_file_analysis == 4
    assert cfg.concurrency.independent_analysis == 4
    assert cfg.concurrency.global_analysis == 8
    assert not hasattr(cfg.concurrency, "task_analysis")
    assert not hasattr(cfg.analysis, "max_concurrency")


def test_canonical_analysis_concurrency_values_are_loaded(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
concurrency:
  task_file_analysis: 2
  independent_analysis: 3
  global_analysis: 5
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.concurrency.task_file_analysis == 2
    assert cfg.concurrency.independent_analysis == 3
    assert cfg.concurrency.global_analysis == 5


def test_example_config_documents_only_canonical_analysis_concurrency():
    example = (Path(__file__).parents[1] / "configs" / "config.yaml.example").read_text(
        encoding="utf-8"
    )

    assert "task_analysis:" not in example
    assert "task_file_analysis: 4" in example
    assert "independent_analysis: 4" in example
