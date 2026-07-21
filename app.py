"""程序入口：FastAPI + uvicorn。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from blue_print import register_routers
from service.init_service import run_init
from service.retention_service import retention_loop
from utils.config import get_config
from utils.openapi_enrich import enrich, get_version

import logs  # noqa: F401  初始化日志配置
from logs import log_context


def _log_startup_banner() -> None:
    """打印启动标识，方便从终端和日志页确认当前服务。"""
    cfg = get_config().server
    lines = (
        "============================================================",
        "  XIJUAN AI / 析卷 AI",
        "  PDF 文档解析、字段抽取与逻辑分析平台",
        "  pipeline: parsing -> tableing -> chunking -> embedding -> extracting -> analyzing",
        f"  ui: http://{cfg.host}:{cfg.port}/ui    api docs: http://{cfg.host}:{cfg.port}/docs",
        "============================================================",
    )
    for line in lines:
        logger.info(line)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理。"""
    with log_context(type_id="system", file_id="startup"):
        _log_startup_banner()
        await run_init()
    # 后台 PDF 保留清理:周期扫描 uploads,按总量/时间清理(配置全关时循环内快速空转)
    cleanup_task = asyncio.create_task(retention_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="析卷 AI", version=get_version(), lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routers(app)


def _custom_openapi():
    """活的 /docs 与导出的 docs/openapi.json 同源：原生 schema + 共享 enrich 富化，消除版本/内容三方漂移。"""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    app.openapi_schema = enrich(schema)
    return app.openapi_schema


app.openapi = _custom_openapi

# 挂载静态文件服务
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon():
    return RedirectResponse(url="/ui/favicon.svg")


if __name__ == "__main__":
    cfg = get_config().server
    uvicorn.run("app:app", host=cfg.host, port=cfg.port)
