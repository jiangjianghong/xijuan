"""程序入口：FastAPI + uvicorn。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from blue_print import register_routers
from service.init_service import run_init
from service.retention_service import retention_loop
from service.runtime_monitor_service import sampler_loop
from utils.config import get_config
from utils.auth_middleware import AuthMiddleware
from service.access_control_service import AccessControlService
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
    await asyncio.to_thread(AccessControlService().migrate_admin_password)
    with log_context(type_id="system", file_id="startup"):
        _log_startup_banner()
        await run_init()
    # 后台 PDF 保留清理:周期扫描 uploads,按总量/时间清理(配置全关时循环内快速空转)
    cleanup_task = asyncio.create_task(retention_loop())
    # 并发水位按秒采样,供运行台展示历史曲线(纯内存,重启清零)
    sampler_task = asyncio.create_task(sampler_loop())
    background_tasks = (cleanup_task, sampler_task)
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="析卷 AI", version=get_version(), lifespan=lifespan)

app.add_middleware(AuthMiddleware)

register_routers(app)


def _custom_openapi():
    """活的 /docs 与导出的 docs/openapi.json 同源：原生 schema + 共享 enrich 富化，消除版本/内容三方漂移。"""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    app.openapi_schema = enrich(schema)
    return app.openapi_schema


app.openapi = _custom_openapi

# 登录入口必须先于静态目录注册，公开地址不暴露文件扩展名。
@app.api_route("/ui/login", methods=["GET", "HEAD"], include_in_schema=False)
async def login_page():
    return FileResponse(Path(__file__).resolve().parent / "ui" / "login.html")


@app.api_route("/ui/login/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/ui/login.html", methods=["GET", "HEAD"], include_in_schema=False)
async def legacy_login_page():
    return RedirectResponse("/ui/login", status_code=303)


# 挂载静态文件服务
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index():
    return RedirectResponse("/ui/")


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon():
    return RedirectResponse(url="/ui/favicon.svg")


if __name__ == "__main__":
    cfg = get_config().server
    # 原始对端地址由鉴权中间件按 trusted_proxies 自行处理。
    uvicorn.run("app:app", host=cfg.host, port=cfg.port, proxy_headers=False)
