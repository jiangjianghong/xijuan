"""启动配置自检：lifespan 末尾对所有外部依赖做只读探活，
输出对齐表格；任何失败仅记录警告，绝不阻塞启动。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from loguru import logger


@dataclass
class CheckResult:
    name: str
    ok: bool
    elapsed_ms: int
    detail: str
    error: Optional[str] = None


_CHECK_TIMEOUT: float = 10.0  # 单项硬超时（秒）


async def _run_one(
    name: str,
    fn: Callable[[], Awaitable[CheckResult]],
) -> CheckResult:
    """运行单个 check 的边界包装：超时 + 异常 → 落到 CheckResult，绝不向上抛。"""
    start = time.monotonic()
    try:
        return await asyncio.wait_for(fn(), timeout=_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("启动检查 {} 超时（>{}s）", name, _CHECK_TIMEOUT)
        return CheckResult(
            name=name,
            ok=False,
            elapsed_ms=elapsed,
            detail="",
            error="TimeoutError",
        )
    except Exception as e:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        logger.exception("启动检查 {} 异常", name)
        return CheckResult(
            name=name,
            ok=False,
            elapsed_ms=elapsed,
            detail="",
            error=f"{type(e).__name__}: {e}",
        )
