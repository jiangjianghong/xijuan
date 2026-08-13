"""系统设置读取、校验与原子持久化。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from pydantic import ValidationError
from ruamel.yaml import YAML

from utils.config import AppConfig, get_config_path, replace_config


OPEN_GROUPS = (
    "mineru",
    "chunking",
    "embedding",
    "extraction",
    "table_name_validation",
    "analysis",
    "vl_model",
    "web_search",
    "storage",
)

EMBEDDING_EDITABLE = {"base_url", "api_key", "model_name"}
EMBEDDING_READONLY = ["embedding_dim", "batch_size", "timeout", "retry_count"]

SECRET_PATHS = {
    "embedding.api_key",
    "extraction.llm_api_key",
    "table_name_validation.llm_api_key",
    "vl_model.api_key",
    "web_search.api_key",
}


class SettingsError(RuntimeError):
    """设置服务基础异常。"""


class ConfigConflictError(SettingsError):
    """提交基于过期配置版本。"""


class ConfigFieldError(SettingsError):
    """提交包含未开放或格式错误的字段。"""


class ConfigWriteError(SettingsError):
    """配置无法安全写入磁盘。"""


def _to_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _version(data: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _to_plain(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _secret_configured(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


class SettingsService:
    """管理一个 YAML 配置文件，并同步当前进程的配置快照。"""

    _write_lock = RLock()

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or get_config_path()
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.width = 4096

    def _load_document(self):
        if not self.config_path.exists():
            raise ConfigWriteError(f"配置文件不存在: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as stream:
            document = self.yaml.load(stream)
        if not isinstance(document, Mapping):
            raise ConfigWriteError("配置文件根节点必须是对象")
        return document

    def _public_payload(self, document: Mapping[str, Any]) -> dict[str, Any]:
        validated = AppConfig(**_to_plain(document))
        public: dict[str, Any] = {}
        for group in OPEN_GROUPS:
            public[group] = getattr(validated, group).model_dump(mode="python")
        for path in SECRET_PATHS:
            group, field = path.split(".", 1)
            raw_group = document.get(group) or {}
            public[group][field] = {
                "configured": _secret_configured(raw_group.get(field))
            }
        return {
            "version": _version(document),
            "config": public,
            "readonly": {"embedding": list(EMBEDDING_READONLY)},
        }

    def read_public_config(self) -> dict[str, Any]:
        with self._write_lock:
            return self._public_payload(self._load_document())

    def _validate_changes(
        self, document: Mapping[str, Any], changes: Mapping[str, Any]
    ) -> None:
        for group, fields in changes.items():
            if group not in OPEN_GROUPS:
                raise ConfigFieldError(f"不允许修改配置组: {group}")
            if not isinstance(fields, Mapping):
                raise ConfigFieldError(f"配置组必须是对象: {group}")
            model = AppConfig.model_fields[group].default
            allowed = set(model.__class__.model_fields)
            if group == "embedding":
                allowed = EMBEDDING_EDITABLE
            for field in fields:
                path = f"{group}.{field}"
                if field not in allowed:
                    raise ConfigFieldError(f"不允许修改配置字段: {path}")
                if path in SECRET_PATHS:
                    raise ConfigFieldError(f"密钥必须通过 secrets 操作修改: {path}")
                if group not in document:
                    raise ConfigFieldError(f"配置文件缺少分组: {group}")

    def _apply_secret_operations(
        self, document: Mapping[str, Any], secrets: Mapping[str, Any]
    ) -> None:
        for path, operation in secrets.items():
            if path not in SECRET_PATHS:
                raise ConfigFieldError(f"不允许修改密钥: {path}")
            if not isinstance(operation, Mapping):
                raise ConfigFieldError(f"密钥操作必须是对象: {path}")
            action = operation.get("action")
            group, field = path.split(".", 1)
            if action == "keep":
                continue
            if action == "clear":
                document[group][field] = None if group == "table_name_validation" else ""
                continue
            if action == "replace":
                value = operation.get("value")
                if not isinstance(value, str) or not value.strip():
                    raise ConfigFieldError(f"新密钥不能为空: {path}")
                document[group][field] = value
                continue
            raise ConfigFieldError(f"未知密钥操作: {path}")

    def _write_atomic(self, document: Mapping[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self.config_path.parent,
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                self.yaml.dump(document, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.config_path)
        except OSError as exc:
            raise ConfigWriteError(f"写入配置文件失败: {exc}") from exc
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def update_config(
        self,
        *,
        base_version: str,
        changes: Mapping[str, Any],
        secrets: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._write_lock:
            document = self._load_document()
            if _version(document) != base_version:
                raise ConfigConflictError("配置已被其他管理员修改，请重新加载")
            self._validate_changes(document, changes)
            for group, fields in changes.items():
                for field, value in fields.items():
                    document[group][field] = value
            self._apply_secret_operations(document, secrets)
            try:
                validated = AppConfig(**_to_plain(document))
            except ValidationError as exc:
                raise ConfigFieldError(str(exc)) from exc

            # 新信号量在落盘前构造；后续仅做不会失败的引用切换。
            new_vl_semaphore = asyncio.Semaphore(
                max(1, validated.vl_model.global_max_concurrency)
            )
            self._write_atomic(document)
            replace_config(validated)

            from utils import vl_client

            vl_client._global_sem = new_vl_semaphore
            return self._public_payload(document)
