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


def _format_table(results: list[CheckResult]) -> str:
    """渲染 CheckResult 列表为对齐 ASCII 表格 + 汇总行。"""
    headers = ("Check", "OK", "ms", "Detail")
    if not results:
        return "(no checks ran)"

    rows = []
    for r in results:
        symbol = "✓" if r.ok else "✗"
        detail = r.detail if r.ok else (r.error or r.detail or "")
        rows.append((r.name, symbol, str(r.elapsed_ms), detail))

    col_widths = [
        max(len(headers[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(4)
    ]

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "│ " + " │ ".join(
            cells[i].ljust(col_widths[i]) if i != 1 else cells[i].center(col_widths[i])
            for i in range(4)
        ) + " │"

    sep_top = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    sep_mid = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    sep_bot = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    lines = [sep_top, fmt_row(headers), sep_mid]
    lines.extend(fmt_row(row) for row in rows)
    lines.append(sep_bot)

    ok_count = sum(1 for r in results if r.ok)
    total_ms = sum(r.elapsed_ms for r in results)
    lines.append(
        f"启动检查完成: {ok_count}/{len(results)} 通过, 总耗时 ~{total_ms / 1000:.1f}s"
    )
    return "\n".join(lines)
