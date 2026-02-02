"""日志配置模块：使用 loguru 配置控制台 + 文件日志输出。"""

import sys
from pathlib import Path

from loguru import logger

_LOGS_DIR = Path(__file__).resolve().parent

# 移除默认 handler
logger.remove()

# 控制台输出
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)

# 文件输出（按天轮转，保留 30 天）
logger.add(
    _LOGS_DIR / "app_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="00:00",
    retention="30 days",
    compression="zip",
    encoding="utf-8",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
)
