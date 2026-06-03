"""日志配置模块：使用 loguru 配置控制台 + 文件日志输出。"""

from __future__ import annotations

import logging
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger


_EMPTY_LOG_VALUE = "-"
_FILE_ID_RE = re.compile(r"(?:^|[\s,，(（])file_id=([^\s,，:：)）]+)")
_TYPE_ID_RE = re.compile(r"(?:^|[\s,，(（])type_id=([^\s,，:：)）]+)")


# ---------------------------------------------------------------------------
# 屏蔽前端高频轮询接口的 uvicorn access log，避免日志刷屏
# ---------------------------------------------------------------------------
class _PollingFilter(logging.Filter):
    """过滤掉前端轮询产生的 access log。"""

    _QUIET_PATHS = ("/file/list", "/status")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._QUIET_PATHS)


logging.getLogger("uvicorn.access").addFilter(_PollingFilter())

_LOGS_DIR = Path(__file__).resolve().parent


def _normalize_log_value(value: object) -> str:
    """统一日志上下文字段的空值展示。"""
    if value is None:
        return _EMPTY_LOG_VALUE
    text = str(value).strip()
    return text or _EMPTY_LOG_VALUE


def _infer_from_message(pattern: re.Pattern[str], message: str) -> str:
    match = pattern.search(message)
    if not match:
        return _EMPTY_LOG_VALUE
    return _normalize_log_value(match.group(1))


def _ensure_log_context(record: dict) -> None:
    """补齐 type_id/file_id，未绑定时从消息中的显式字段兜底提取。"""
    extra = record["extra"]
    message = record["message"]

    type_id = _normalize_log_value(extra.get("type_id"))
    file_id = _normalize_log_value(extra.get("file_id"))

    if type_id == _EMPTY_LOG_VALUE:
        type_id = _infer_from_message(_TYPE_ID_RE, message)
    if file_id == _EMPTY_LOG_VALUE:
        file_id = _infer_from_message(_FILE_ID_RE, message)

    extra["type_id"] = type_id
    extra["file_id"] = file_id


def _console_format(record: dict) -> str:
    _ensure_log_context(record)
    location = ""
    if record["level"].no >= logging.ERROR:
        location = "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[type_id]}</cyan> | "
        "<cyan>{extra[file_id]}</cyan> | "
        f"{location}<level>{{message}}</level>\n{{exception}}"
    )


def _file_format(record: dict) -> str:
    _ensure_log_context(record)
    location = ""
    if record["level"].no >= logging.ERROR:
        location = "{name}:{function}:{line} - "
    return (
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{extra[type_id]} | "
        "{extra[file_id]} | "
        f"{location}{{message}}\n{{exception}}"
    )


@contextmanager
def log_context(file_id: object = None, type_id: object = None) -> Iterator[None]:
    """在当前异步任务/线程内绑定日志业务上下文。"""
    extra = {}
    if file_id is not None:
        extra["file_id"] = _normalize_log_value(file_id)
    if type_id is not None:
        extra["type_id"] = _normalize_log_value(type_id)

    with logger.contextualize(**extra):
        yield


# 移除默认 handler
logger.remove()

# 控制台输出
logger.add(
    sys.stderr,
    level="INFO",
    format=_console_format,
)

# 文件输出（按天轮转，保留 30 天）
logger.add(
    _LOGS_DIR / "app_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="00:00",
    retention="30 days",
    compression="zip",
    encoding="utf-8",
    format=_file_format,
)
