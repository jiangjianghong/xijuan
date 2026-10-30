"""管理员维护调用凭证与白名单，共用设置文件的版本锁和原子写入。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from argon2 import PasswordHasher

from service.settings_service import (
    ConfigConflictError, ConfigFieldError, SettingsService, _validate_document, _version,
)
from utils.config import replace_config
from utils.concurrency import replace_limiters


class AccessControlService(SettingsService):
    @staticmethod
    def _public(document) -> dict[str, Any]:
        cfg = _validate_document(document).access_control
        return {
            "version": _version(document),
            "api_keys": [entry.model_dump(mode="json", exclude={"key_hash"}) for entry in cfg.api_keys],
            "ip_allowlist": [entry.model_dump(mode="json") for entry in cfg.ip_allowlist],
        }

    def read(self) -> dict[str, Any]:
        with self._write_lock:
            return self._public(self._load_document())

    def _commit(self, document) -> None:
        validated = _validate_document(document)
        self._write_atomic(document)
        limits = validated.concurrency.model_dump(mode="python")
        limits["mineru_parse"] = validated.mineru.queue_width
        replace_limiters(limits)
        replace_config(validated)

    def mutate(self, *, version: str, kind: str, operation: str,
               entry_id: str | None = None, values: dict | None = None) -> dict[str, Any]:
        if kind not in {"api_keys", "ip_allowlist"}:
            raise ConfigFieldError("未知访问控制类型")
        with self._write_lock:
            document = self._load_document()
            if _version(document) != version:
                raise ConfigConflictError("配置已变化，请重新加载后重试")
            group = document.setdefault("access_control", {})
            entries = group.setdefault(kind, [])
            values = dict(values or {})
            allowed = {"name", "expires_at"} if kind == "api_keys" else {"name", "network"}
            if set(values) - (allowed | {"enabled"}):
                raise ConfigFieldError("包含不可修改的访问控制字段")
            raw_key = None
            if operation == "create":
                entry = {"id": secrets.token_hex(8), "enabled": True, **values}
                entries.append(entry)
            else:
                entry = next((item for item in entries if item["id"] == entry_id), None)
                if entry is None:
                    raise ConfigFieldError("条目不存在，请刷新后重试")
                if operation == "delete":
                    entries.remove(entry)
                elif operation == "update":
                    entry.update(values)
                elif operation != "rotate" or kind != "api_keys":
                    raise ConfigFieldError("不支持的操作")
            if kind == "api_keys" and operation in {"create", "rotate"}:
                raw_key = "xj_" + secrets.token_urlsafe(32)
                entry.update(key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                             hint="xj_…" + raw_key[-4:],
                             created_at=datetime.now(timezone.utc).isoformat())
            if operation in {"create", "update"} and values.get("expires_at"):
                expires = datetime.fromisoformat(values["expires_at"].replace("Z", "+00:00"))
                if expires <= datetime.now(timezone.utc):
                    raise ConfigFieldError("有效期必须晚于当前时间")
            self._commit(document)
            result = self._public(document)
            if raw_key:
                result["key"] = raw_key
            return result

    def migrate_admin_password(self) -> None:
        """保留既有登录密码，只把存储形式转换为 Argon2id。"""
        with self._write_lock:
            document = self._load_document()
            settings = document.setdefault("settings", {})
            if not settings.get("password"):
                return
            if not settings.get("password_hash"):
                settings["password_hash"] = PasswordHasher().hash(settings["password"])
            settings.pop("password", None)
            settings.setdefault("username", "admin")
            document.setdefault("access_control", {"api_keys": [], "ip_allowlist": [], "trusted_proxies": []})
            self._commit(document)
