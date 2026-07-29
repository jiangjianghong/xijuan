"""异常相关共享工具。"""

from __future__ import annotations

# 异常文案落库长度上限。files.error 与 extraction_result.reason 都是 MySQL TEXT
# （65535 字节，utf8mb4 中文最坏 3 字节/字）。DataError 这类异常的文案会带上完整
# SQL 与参数预览，轻易上万字符；一旦越界，「标记失败态」这个动作自身又会触发
# 1406，形成与线上 2026-07-28 事故相同的连锁。4000 字符最坏 12KB，远低于上限
# 且足够定位问题。
MAX_MESSAGE_LENGTH = 4000

_TRUNCATED_SUFFIX = "...(已截断)"


def _truncate(text: str) -> str:
    """超长文案尾部截断并标记，短文案原样返回。"""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return text
    return text[: MAX_MESSAGE_LENGTH - len(_TRUNCATED_SUFFIX)] + _TRUNCATED_SUFFIX


def format_exception(exc: BaseException) -> str:
    """统一异常文案，避免 str(exc) 为空导致丢失关键信息。

    结果截断到 MAX_MESSAGE_LENGTH，保证能安全写进 TEXT 列。
    """
    msg = str(exc).strip()
    if msg:
        return _truncate(f"{type(exc).__name__}: {msg}")
    return _truncate(f"{type(exc).__name__}: {repr(exc)}")
