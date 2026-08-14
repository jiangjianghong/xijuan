"""系统设置读取、校验与原子持久化。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from pydantic import ValidationError
from ruamel.yaml import YAML

from utils.config import AppConfig, get_config_path, replace_config
from utils.concurrency import replace_limiters


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
    "concurrency",
)

EMBEDDING_EDITABLE = {"base_url", "api_key", "model", "model_name"}
EMBEDDING_READONLY = ["embedding_dim", "batch_size", "timeout", "retry_count"]

SECRET_PATHS = {
    "embedding.api_key",
    "extraction.api_key",
    "table_name_validation.api_key",
    "vl_model.api_key",
    "web_search.api_key",
}

SECRET_PATH_ALIASES = {
    "extraction.llm_api_key": "extraction.api_key",
    "table_name_validation.llm_api_key": "table_name_validation.api_key",
}

FIELD_ALIASES = {
    "embedding.model_name": "embedding.model",
    "extraction.llm_base_url": "extraction.base_url",
    "extraction.llm_model": "extraction.model",
    "extraction.llm_api_key": "extraction.api_key",
    "extraction.llm_timeout": "extraction.timeout",
    "extraction.llm_retry_count": "extraction.retry_count",
    "extraction.llm_extra_body": "extraction.extra_body",
    "table_name_validation.llm_base_url": "table_name_validation.base_url",
    "table_name_validation.llm_model": "table_name_validation.model",
    "table_name_validation.llm_api_key": "table_name_validation.api_key",
    "table_name_validation.llm_timeout": "table_name_validation.timeout",
    "table_name_validation.llm_retry_count": "table_name_validation.retry_count",
    "table_name_validation.llm_extra_body": "table_name_validation.extra_body",
}

_VERSION_KEY = secrets.token_bytes(32)


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
    return hmac.new(_VERSION_KEY, payload, hashlib.sha256).hexdigest()


def _secret_configured(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical_path(path: str) -> str:
    return SECRET_PATH_ALIASES.get(path, FIELD_ALIASES.get(path, path))


def _remove_legacy_keys(document: Mapping[str, Any], canonical_path: str) -> None:
    group, field = canonical_path.split(".", 1)
    for alias, canonical in FIELD_ALIASES.items():
        if canonical != canonical_path:
            continue
        alias_group, alias_field = alias.split(".", 1)
        if alias_group == group and alias_field != field:
            document[group].pop(alias_field, None)


def _known_config(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _to_plain(value)
        for key, value in document.items()
        if key in AppConfig.model_fields
    }


def _format_validation_error(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        path = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg", "配置值无效")
        messages.append(f"{path}: {message}" if path else message)
    return "; ".join(messages) or "配置值无效"


def _validate_document(document: Mapping[str, Any]) -> AppConfig:
    try:
        return AppConfig(**_known_config(document))
    except ValidationError as exc:
        raise ConfigFieldError(_format_validation_error(exc)) from exc


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
        validated = _validate_document(document)
        public: dict[str, Any] = {}
        for group in OPEN_GROUPS:
            public[group] = getattr(validated, group).model_dump(mode="python")
        # 旧并发字段只用于输入迁移，不再出现在新设置响应中。
        public.get("table_name_validation", {}).pop("max_concurrency", None)
        public.get("analysis", {}).pop("max_concurrency", None)
        public.get("vl_model", {}).pop("global_max_concurrency", None)
        for path in SECRET_PATHS:
            group, field = path.split(".", 1)
            raw_group = document.get(group) or {}
            raw_value = raw_group.get(field)
            if raw_value is None:
                for alias, canonical in FIELD_ALIASES.items():
                    alias_group, alias_field = alias.split(".", 1)
                    if alias_group == group and canonical == path:
                        raw_value = raw_group.get(alias_field)
                        if raw_value is not None:
                            break
            public[group][field] = {
                "configured": _secret_configured(raw_value)
            }
        # 旧设置页面/客户端仍可读取别名，但值来自规范字段，不暴露明文。
        for alias, canonical in FIELD_ALIASES.items():
            alias_group, alias_field = alias.split(".", 1)
            canonical_group, canonical_field = canonical.split(".", 1)
            if alias_group == canonical_group and canonical_group in public:
                public[alias_group][alias_field] = public[canonical_group][canonical_field]
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
                path = _canonical_path(f"{group}.{field}")
                if field not in allowed:
                    if path.split(".", 1)[1] not in allowed:
                        raise ConfigFieldError(f"不允许修改配置字段: {path}")
                if path in SECRET_PATHS:
                    raise ConfigFieldError(f"密钥必须通过 secrets 操作修改: {path}")

    @staticmethod
    def _ensure_open_group(document: Mapping[str, Any], group: str) -> None:
        if group not in document or not isinstance(document[group], Mapping):
            default = AppConfig.model_fields[group].default
            document[group] = default.model_dump(mode="python")

    def _apply_secret_operations(
        self, document: Mapping[str, Any], secrets: Mapping[str, Any]
    ) -> None:
        for path, operation in secrets.items():
            path = _canonical_path(path)
            if path not in SECRET_PATHS:
                raise ConfigFieldError(f"不允许修改密钥: {path}")
            if not isinstance(operation, Mapping):
                raise ConfigFieldError(f"密钥操作必须是对象: {path}")
            action = operation.get("action")
            group, field = path.split(".", 1)
            if action == "keep":
                continue
            self._ensure_open_group(document, group)
            if action == "clear":
                document[group][field] = None if group == "table_name_validation" else ""
                _remove_legacy_keys(document, path)
                continue
            if action == "replace":
                value = operation.get("value")
                if not isinstance(value, str) or not value.strip():
                    raise ConfigFieldError(f"新密钥不能为空: {path}")
                document[group][field] = value
                _remove_legacy_keys(document, path)
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
                self._ensure_open_group(document, group)
                for field, value in fields.items():
                    canonical = _canonical_path(f"{group}.{field}").split(".", 1)[1]
                    document[group][canonical] = value
                    _remove_legacy_keys(document, f"{group}.{canonical}")
            self._apply_secret_operations(document, secrets)
            validated = _validate_document(document)

            # 新信号量在落盘前构造；后续仅做不会失败的引用切换。
            limits = validated.concurrency.model_dump(mode="python")
            replace_limiters(limits)
            self._write_atomic(document)
            replace_config(validated)
            return self._public_payload(document)
