"""路由注册：汇总所有 router 到 FastAPI app。"""

from __future__ import annotations

from fastapi import FastAPI

from blue_print.file_router import router as file_router
from blue_print.extraction_router import router as extraction_router
from blue_print.analysis_router import router as analysis_router
from blue_print.search_router import router as search_router


def register_routers(app: FastAPI) -> None:
    """将所有路由注册到 app。"""
    app.include_router(file_router)
    app.include_router(extraction_router)
    app.include_router(analysis_router)
    app.include_router(search_router)
