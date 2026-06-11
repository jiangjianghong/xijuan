"""异常相关共享工具。"""

from __future__ import annotations


def format_exception(exc: BaseException) -> str:
    """统一异常文案，避免 str(exc) 为空导致丢失关键信息。"""
    msg = str(exc).strip()
    if msg:
        return f"{type(exc).__name__}: {msg}"
    return f"{type(exc).__name__}: {repr(exc)}"
