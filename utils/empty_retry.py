"""字段空值重试；任务局部上下文防止并发字段互相影响。"""
from contextlib import contextmanager, aclosing
from contextvars import ContextVar
from functools import wraps

from loguru import logger
from pydantic import BaseModel, Field


class EmptyRetryConfig(BaseModel):
    empty_retry_enabled: bool = False
    empty_retry_count: int = Field(2, ge=1, le=10)


_attempt = ContextVar("empty_retry_attempt", default=None)
_failure = ContextVar("empty_retry_failure", default=None)


def mark_retry_failure():
    """现有执行器把异常转为空值时，仍区分异常与正常空结果。"""
    state = _failure.get()
    if state is not None:
        state["failed"] = True


def is_empty_value(value):
    """数值 0 和布尔 false 是有效值。"""
    return value is None or (isinstance(value, str) and not value.strip())


def retry_temperature(original):
    """所有追加尝试固定加 0.1，最高为 1，不随尝试次数累加。"""
    if not _attempt.get():
        return original
    return min(1.0, round(float(original) + 0.1, 10))


def is_empty_retry_active():
    """仅开启空值重试的字段有明确的首次温度基准。"""
    return _attempt.get() is not None


@contextmanager
def retry_failure_boundary():
    """混合检索已处理的通道失败，不应污染字段最终抽取状态。"""
    state = {"failed": False}
    token = _failure.set(state)
    try:
        yield state
    finally:
        _failure.reset(token)


def _budget(field):
    config = EmptyRetryConfig(
        empty_retry_enabled=getattr(field, "empty_retry_enabled", None) or False,
        empty_retry_count=getattr(field, "empty_retry_count", None) or 2,
    )
    return config.empty_retry_count if config.empty_retry_enabled else 0


@contextmanager
def _scope(attempt, state=None):
    token = _attempt.set(attempt)
    state = state if state is not None else {"failed": False}
    failure_token = _failure.set(state)
    try:
        yield state
    finally:
        _failure.reset(failure_token)
        _attempt.reset(token)


def retry_empty(func):
    """包装完整字段执行器；混合通道嵌套调用不叠加重试预算。"""
    @wraps(func)
    async def wrapped(file_id, field, *args, **kwargs):
        if _attempt.get() is not None:
            return await func(file_id, field, *args, **kwargs)
        budget = _budget(field)
        if not budget:
            return await func(file_id, field, *args, **kwargs)
        for attempt in range(budget + 1):
            with _scope(attempt) as state:
                result = await func(file_id, field, *args, **kwargs)
            if state["failed"] or not is_empty_value(result[0]) or attempt == budget:
                return result
            logger.info("字段空值重试: field_id={}, retry={}/{}", field.field_id, attempt + 1, budget)
            # 调试仅展示最终一次尝试的证据和提示词。
            if kwargs.get("debug_events") is not None:
                kwargs["debug_events"].clear()
    return wrapped


def retry_empty_stream(func):
    """保留中间调试事件，只发布最终一次 result/done。"""
    @wraps(func)
    async def wrapped(file_id, field, *args, **kwargs):
        budget = _budget(field)
        if not budget:
            async with aclosing(func(file_id, field, *args, **kwargs)) as stream:
                async for event in stream:
                    yield event
            return
        for attempt in range(budget + 1):
            result = None
            done = None
            failed = False
            state = {"failed": False}
            stream = func(file_id, field, *args, **kwargs)
            async with aclosing(stream):
                while True:
                    # 不跨 yield 持有 ContextVar，避免泄漏到消费者或异任务关闭。
                    with _scope(attempt, state):
                        try:
                            event = await anext(stream)
                        except StopAsyncIteration:
                            break
                    if event["event"] == "result":
                        result = event
                    elif event["event"] == "done":
                        done = event
                    else:
                        failed = failed or event["event"] == "error"
                        yield event
            if not failed and not state["failed"] and result is not None and is_empty_value(result["data"].get("extracted_value")) and attempt < budget:
                yield {"event": "retry", "data": {"attempt": attempt + 1, "total": budget}}
                continue
            if result is not None:
                yield result
            if done is not None:
                yield done
            return
    return wrapped
