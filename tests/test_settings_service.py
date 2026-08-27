from __future__ import annotations

import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from utils.config import get_config, replace_config
from service.settings_service import (
    ConfigConflictError,
    ConfigFieldError,
    SettingsService,
)


CONFIG_TEXT = """\
# 保留这条顶部注释
server:
  host: 0.0.0.0
  port: 5019
mineru:
  base_url: http://mineru.test
  backend: vllm-async-engine
  queue_width: 1
  parse_timeout: 1200
  max_file_size: 104857600
chunking:
  chunk_size: 512  # 保留行内注释
  chunk_overlap: 50
  max_chunk_size: 2048
  separators: ["\\n\\n", "\\n", "。", " "]
embedding:
  base_url: http://embedding.test/v1
  model_name: embedding-old
  api_key: embedding-secret
  embedding_dim: 1024
  batch_size: 8
  timeout: 60
  retry_count: 3
milvus:
  host: milvus.internal
  port: 19530
  password: milvus-secret
mysql:
  host: mysql.internal
  port: 3306
  database: parser
  username: root
  password: mysql-secret
extraction:
  llm_base_url: http://llm.test/v1
  llm_model: qwen
  llm_api_key: extraction-secret
  llm_timeout: 60
  llm_retry_count: 3
  max_context_length: 4096
  llm_extra_body: {}
table_name_validation:
  llm_base_url: null
  llm_model: null
  llm_api_key: table-secret
  llm_timeout: null
  llm_retry_count: null
  max_context_length: null
  max_context_lines: 3
  max_concurrency: 20
  llm_extra_body: null
analysis:
  calc_precision: 2
  judge_timeout: 30
  max_concurrency: 10
concurrency:
  task_analysis: 4
vl_model:
  base_url: http://vl.test/v1
  api_key: vl-secret
  model: vl-old
  temperature: 0.1
  max_tokens: 4096
  timeout: 180
  extra_body: {}
  global_max_concurrency: 8
  default_max_pixels: 4000000
  pdf_storage_dir: uploads
web_search:
  base_url: https://api.bochaai.com/v1/web-search
  api_key: bocha-secret
  count: 5
  summary: true
  freshness: noLimit
  timeout: 10
  retry_count: 2
  max_result_length: 4000
storage:
  max_total_bytes: 0
  max_retention_minutes: 0
  cleanup_interval_minutes: 10
settings:
  password: admin-password
  session_minutes: 30
  secure_cookie: false
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def restore_runtime_config():
    original = get_config()
    yield
    replace_config(original)


def test_public_config_only_exposes_allowed_groups_and_secret_status(config_path: Path):
    payload = SettingsService(config_path).read_public_config()

    assert set(payload["config"]) == {
        "mineru",
        "chunking",
        "embedding",
        "extraction",
        "table_name_validation",
        "analysis",
        "vl_model",
        "web_search",
        "callback",
        "storage",
        "concurrency",
    }
    serialized = repr(payload)
    for secret in (
        "embedding-secret",
        "extraction-secret",
        "table-secret",
        "vl-secret",
        "bocha-secret",
        "admin-password",
        "mysql-secret",
        "milvus-secret",
    ):
        assert secret not in serialized
    assert payload["config"]["embedding"]["api_key"] == {"configured": True}
    assert payload["readonly"]["embedding"] == [
        "embedding_dim",
        "batch_size",
        "timeout",
        "retry_count",
    ]


def test_update_changes_editable_values_and_preserves_closed_config(config_path: Path):
    service = SettingsService(config_path)
    before = service.read_public_config()

    result = service.update_config(
        base_version=before["version"],
        changes={
            "mineru": {"max_file_size": 209715200},
            "chunking": {"chunk_size": 768},
            "embedding": {
                "base_url": "http://embedding-new.test/v1",
                "model_name": "embedding-new",
            },
        },
        secrets={"embedding.api_key": {"action": "keep"}},
    )

    assert result["config"]["mineru"]["max_file_size"] == 209715200
    assert result["config"]["chunking"]["chunk_size"] == 768
    assert result["config"]["embedding"]["model_name"] == "embedding-new"
    written = config_path.read_text(encoding="utf-8")
    assert "# 保留这条顶部注释" in written
    assert "# 保留行内注释" in written
    assert "mysql-secret" in written
    assert "milvus-secret" in written
    assert "admin-password" in written
    assert "embedding-secret" in written


def test_update_can_change_unified_concurrency(config_path: Path):
    service = SettingsService(config_path)
    before = service.read_public_config()

    result = service.update_config(
        base_version=before["version"],
        changes={
            "concurrency": {
                "global_llm": 6,
                "global_embedding": 3,
                "task_extraction": 2,
            }
        },
        secrets={},
    )

    assert result["config"]["concurrency"]["global_llm"] == 6
    assert result["config"]["concurrency"]["global_embedding"] == 3
    assert result["config"]["concurrency"]["task_extraction"] == 2


@pytest.mark.parametrize(
    "changes, rejected_path",
    [
        ({"concurrency": {"task_analysis": 7}}, "concurrency.task_analysis"),
        ({"analysis": {"max_concurrency": 7}}, "analysis.max_concurrency"),
    ],
)
def test_update_rejects_removed_analysis_concurrency_fields(
    config_path: Path, changes: dict, rejected_path: str
):
    service = SettingsService(config_path)
    version = service.read_public_config()["version"]

    with pytest.raises(ConfigFieldError, match=re.escape(rejected_path)):
        service.update_config(base_version=version, changes=changes, secrets={})


def test_successful_save_purges_removed_analysis_concurrency_fields(
    config_path: Path,
):
    service = SettingsService(config_path)
    version = service.read_public_config()["version"]

    result = service.update_config(
        base_version=version,
        changes={"concurrency": {"independent_analysis": 6}},
        secrets={},
    )

    document = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
    assert "task_analysis" not in document["concurrency"]
    assert "max_concurrency" not in document["analysis"]
    assert document["table_name_validation"]["max_concurrency"] == 20
    assert result["config"]["concurrency"]["task_file_analysis"] == 4
    assert result["config"]["concurrency"]["independent_analysis"] == 6
    assert "task_analysis" not in result["config"]["concurrency"]
    assert "max_concurrency" not in result["config"]["analysis"]


def test_update_supports_replace_and_clear_secret_actions(config_path: Path):
    service = SettingsService(config_path)
    version = service.read_public_config()["version"]

    replaced = service.update_config(
        base_version=version,
        changes={},
        secrets={
            "embedding.api_key": {"action": "replace", "value": "new-embedding-key"},
            "web_search.api_key": {"action": "clear"},
        },
    )

    assert replaced["config"]["embedding"]["api_key"] == {"configured": True}
    assert replaced["config"]["web_search"]["api_key"] == {"configured": False}
    written = config_path.read_text(encoding="utf-8")
    assert "new-embedding-key" in written
    assert "bocha-secret" not in written


@pytest.mark.parametrize(
    "changes",
    [
        {"mysql": {"host": "attacker"}},
        {"server": {"port": 9999}},
        {"settings": {"password": "changed"}},
        {"embedding": {"embedding_dim": 4096}},
        {"embedding": {"unknown": 1}},
    ],
)
def test_update_rejects_closed_readonly_and_unknown_fields(config_path: Path, changes: dict):
    service = SettingsService(config_path)
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigFieldError):
        service.update_config(
            base_version=service.read_public_config()["version"],
            changes=changes,
            secrets={},
        )

    assert config_path.read_text(encoding="utf-8") == before


def test_update_rejects_stale_version_without_writing(config_path: Path):
    service = SettingsService(config_path)
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigConflictError):
        service.update_config(
            base_version="stale-version",
            changes={"mineru": {"queue_width": 2}},
            secrets={},
        )

    assert config_path.read_text(encoding="utf-8") == before


def test_update_can_create_an_open_group_missing_from_legacy_yaml(config_path: Path):
    text = config_path.read_text(encoding="utf-8")
    start = text.index("storage:\n")
    end = text.index("settings:\n")
    config_path.write_text(text[:start] + text[end:], encoding="utf-8")
    service = SettingsService(config_path)

    result = service.update_config(
        base_version=service.read_public_config()["version"],
        changes={"storage": {"max_total_bytes": 1024}},
        secrets={},
    )

    assert result["config"]["storage"]["max_total_bytes"] == 1024
    assert "storage:" in config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "changes",
    [
        {"mineru": {"queue_width": 0}},
        {"chunking": {"chunk_size": -1}},
        {"chunking": {"chunk_size": 100, "chunk_overlap": 100}},
        {"chunking": {"chunk_size": 2049, "max_chunk_size": 2048}},
        {"vl_model": {"global_max_concurrency": 0}},
        {"storage": {"cleanup_interval_minutes": 0}},
    ],
)
def test_update_rejects_invalid_ranges_and_cross_field_values(
    config_path: Path, changes: dict
):
    service = SettingsService(config_path)
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigFieldError):
        service.update_config(
            base_version=service.read_public_config()["version"],
            changes=changes,
            secrets={},
        )

    assert config_path.read_text(encoding="utf-8") == before


def test_unknown_top_level_nodes_are_preserved_and_never_exposed(config_path: Path):
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\ncustom_plugin:\n  api_key: plugin-top-secret\n  enabled: true\n",
        encoding="utf-8",
    )
    service = SettingsService(config_path)

    public = service.read_public_config()
    assert "custom_plugin" not in public["config"]
    assert "plugin-top-secret" not in repr(public)
    updated = service.update_config(
        base_version=public["version"],
        changes={"mineru": {"queue_width": 3}},
        secrets={},
    )

    assert "plugin-top-secret" not in repr(updated)
    written = config_path.read_text(encoding="utf-8")
    assert "custom_plugin:" in written
    assert "plugin-top-secret" in written


def test_invalid_existing_config_error_does_not_echo_input_value(config_path: Path):
    marker = "sensitive-invalid-value"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("queue_width: 1", f"queue_width: {marker}"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigFieldError) as exc_info:
        SettingsService(config_path).read_public_config()

    assert "mineru.queue_width" in str(exc_info.value)
    assert marker not in str(exc_info.value)


@pytest.mark.parametrize(
    "secret_path,operation",
    [
        ("mysql.password", {"action": "clear"}),
        ("embedding.api_key", {"action": "replace", "value": ""}),
        ("embedding.api_key", {"action": "unknown"}),
    ],
)
def test_update_rejects_invalid_secret_operations(
    config_path: Path, secret_path: str, operation: dict
):
    service = SettingsService(config_path)

    with pytest.raises(ConfigFieldError):
        service.update_config(
            base_version=service.read_public_config()["version"],
            changes={},
            secrets={secret_path: operation},
        )
