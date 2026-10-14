"""请求级模型配置：部分覆盖、任务隔离，不修改进程设置或并发池。"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager, aclosing
from contextvars import ContextVar
from functools import wraps
from inspect import isasyncgenfunction
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from utils.config import AppConfig, get_config

GROUPS = ("extraction", "analysis", "table_name_validation", "vl_model", "embedding")
MODEL_CONFIG_DESCRIPTION = (
    "可选的本次请求模型配置，按分组部分覆盖设置；未传字段沿用服务器设置。"
    "model_config 不传、传 null 或 {} 均不覆盖设置，分组值必须是对象。\n\n"
    "extraction：base_url(str)、model(str)、api_key(str)、timeout(int>=1)、retry_count(int>=1)、"
    "max_context_length(int>=1)、temperature(float>=0)、top_p(float 0..1 或 null)、"
    "extra_body(object，整体替换)、enable_thinking(bool 或 null)。\n\n"
    "analysis：base_url(str)、model(str)、api_key(str)、retry_count(int>=1)、temperature(float>=0)、"
    "top_p(float 0..1 或 null)、extra_body(object，整体替换)、enable_thinking(bool 或 null)、"
    "calc_precision(int>=0)、judge_timeout(int>=1)。\n\n"
    "table_name_validation：base_url/model/api_key(str 或 null)、timeout/retry_count/max_context_length/"
    "max_context_lines(int>=1 或 null)、temperature(float>=0 或 null)、top_p(float 0..1 或 null)、"
    "extra_body(object 或 null)；max_concurrency 不允许覆盖。"
    "base_url/model 为空或 null 时继承 extraction；api_key 仅 null 时继承，空字符串不发送鉴权头。"
    "timeout/retry_count/max_context_length/temperature/top_p 为 null 时继承 extraction；"
    "max_context_lines 为 null 时使用 3 行，extra_body 为 null 时不附加额外请求体。\n\n"
    "vl_model：base_url/api_key/model(str)、temperature(float>=0)、max_tokens/timeout/default_max_pixels(int>=1)、"
    "extra_body(object，整体替换)；global_max_concurrency 与 pdf_storage_dir 不允许覆盖。\n\n"
    "embedding 仅支持 base_url/model/api_key(str)，兼容 model_name；"
    "embedding_dim/batch_size/timeout/retry_count 不允许覆盖。\n\n"
    "extraction/table_name_validation 兼容 llm_base_url、llm_model、llm_api_key、llm_timeout、"
    "llm_retry_count、llm_extra_body 别名；别名与规范字段冲突时拒绝请求。"
    "api_key 空字符串表示不发送鉴权头；extra_body 整体替换，{} 可清空。"
    "timeout/judge_timeout 单位为秒；retry_count 是包含首次调用的最大尝试次数。"
    "extraction/analysis 的 top_p 与 enable_thinking 为 null 时不发送相应参数。"
    "不支持 concurrency 或其它并发字段，仍使用服务器并发限制。"
)
_overrides: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar("request_model_config", default=None)
_ALIASES = {
    "llm_base_url": "base_url", "llm_model": "model", "llm_api_key": "api_key",
    "llm_timeout": "timeout", "llm_retry_count": "retry_count", "llm_extra_body": "extra_body",
}
_READONLY = {"table_name_validation": {"max_concurrency"},
             "vl_model": {"global_max_concurrency", "pdf_storage_dir"}}


def validate_model_config(raw: Any) -> dict[str, dict[str, Any]] | None:
    """规范化与设置相同的分组/字段，拒绝未知字段和请求级并发设置。"""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("model_config 必须是 JSON 对象")
    normalized = {}
    for group, fields in raw.items():
        if group not in GROUPS:
            raise ValueError(f"model_config 不支持配置组: {group}")
        if not isinstance(fields, Mapping):
            raise ValueError(f"model_config.{group} 必须是对象")
        model = type(AppConfig.model_fields[group].default)
        allowed = set(model.model_fields) - _READONLY.get(group, set())
        if group == "embedding":
            allowed = {"base_url", "model", "api_key"}
        aliases = (_ALIASES if group in ("extraction", "table_name_validation") else
                   {"model_name": "model"} if group == "embedding" else {})
        values = {}
        for key, value in fields.items():
            canonical = aliases.get(key, key)
            if canonical not in allowed:
                raise ValueError(f"model_config 不支持配置字段: {group}.{key}")
            if canonical in values and values[canonical] != value:
                raise ValueError(f"model_config 配置别名冲突: {group}.{canonical}")
            values[canonical] = copy.deepcopy(value)
        try:
            checked = model.model_validate(values).model_dump()
        except ValidationError as exc:
            # 不包含 input，校验错误中不能回显调用者密钥。
            details = "; ".join(
                f"model_config.{group}.{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors(include_input=False, include_context=False, include_url=False)
            )
            raise ValueError(details) from exc
        normalized[group] = {key: checked[key] for key in values}
    return normalized


def parse_model_config(raw: str | None) -> dict[str, dict[str, Any]] | None:
    """上传表单里的 JSON 字符串与 JSON 请求体共用校验逻辑。"""
    if raw is None:
        return None
    try:
        return validate_model_config(json.loads(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("model_config 不是合法 JSON 对象字符串") from exc


class ModelConfigRequest(BaseModel):
    """对外使用 model_config；内部避开 Pydantic 的同名保留属性。"""
    model_config = ConfigDict(serialize_by_alias=True)
    model_overrides: dict[str, dict[str, Any]] | None = Field(
        None, alias="model_config",
        description=MODEL_CONFIG_DESCRIPTION,
    )

    @field_validator("model_overrides", mode="before")
    @classmethod
    def validate_overrides(cls, value):
        return validate_model_config(value)


@contextmanager
def model_config_context(overrides):
    """子任务自动继承配置；退出、异常与取消均恢复原上下文。"""
    token = _overrides.set(copy.deepcopy(overrides)) if overrides is not None else None
    try:
        yield
    finally:
        if token is not None:
            _overrides.reset(token)


def resolve_model_config(base: AppConfig | None = None) -> AppConfig:
    """只复制被覆盖的模型组，服务端非模型设置（尤其并发）保持现读。"""
    base = base if base is not None else get_config()
    overrides = _overrides.get()
    if not overrides:
        return base
    groups = {}
    for group, fields in overrides.items():
        original = getattr(base, group)
        groups[group] = type(original).model_validate({**original.model_dump(), **fields})
    return base.model_copy(update=groups)


def model_override(group: str, field: str, fallback: Any = None) -> Any:
    """读取字段级覆盖，保留显式空字符串与 None 的语义。"""
    overrides = _overrides.get() or {}
    values = overrides.get(group) or {}
    return values[field] if field in values else fallback


async def model_config_stream(stream, overrides):
    """每次推进生成器时绑定上下文，yield 时不泄漏给流消费者。"""
    try:
        while True:
            with model_config_context(overrides):
                try:
                    item = await anext(stream)
                except StopAsyncIteration:
                    return
            yield item
    finally:
        with model_config_context(overrides):
            await stream.aclose()


def with_model_config(function):
    """正式服务入口可直接接收 model_config，无需逐层传入检索/模型函数。"""
    if isasyncgenfunction(function):
        @wraps(function)
        async def stream_wrapper(*args, **kwargs):
            raw = kwargs.pop("model_config", None)
            if raw is None:
                req = kwargs.get("req") or (args[0] if args else None)
                raw = getattr(req, "model_overrides", None)
            overrides = validate_model_config(raw)
            async with aclosing(model_config_stream(function(*args, **kwargs), overrides)) as stream:
                async for item in stream:
                    yield item
        return stream_wrapper

    @wraps(function)
    async def wrapper(*args, **kwargs):
        raw = kwargs.pop("model_config", None)
        if raw is None:
            req = kwargs.get("req") or (args[0] if args else None)
            raw = getattr(req, "model_overrides", None)
        overrides = validate_model_config(raw)
        with model_config_context(overrides):
            return await function(*args, **kwargs)
    return wrapper


def redact_model_config(value: Any) -> Any:
    """日志保留模型参数与密钥是否传入，但不持久化明文。"""
    if isinstance(value, Mapping):
        return {key: ("***" if key in ("api_key", "llm_api_key") else redact_model_config(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and value[0] == "model_config" and isinstance(value[1], str):
            return [value[0], redact_model_config(value[1])]
        return [redact_model_config(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        if isinstance(parsed, (dict, list)):
            redacted = redact_model_config(parsed)
            if redacted != parsed:
                return json.dumps(redacted, ensure_ascii=False)
    return value
