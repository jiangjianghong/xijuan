"""启动配置自检：lifespan 末尾对所有外部依赖做只读探活，
输出对齐表格；任何失败仅记录警告，绝不阻塞启动。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CheckResult:
    name: str
    ok: bool
    elapsed_ms: int
    detail: str
    error: Optional[str] = None
