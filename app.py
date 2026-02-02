"""程序入口：FastAPI + uvicorn。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from blue_print import register_routers
from service.init_service import run_init
from utils.config import get_config

import logs  # noqa: F401  初始化日志配置


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理。"""
    await run_init()
    yield


app = FastAPI(title="文档解析与逻辑分析系统", version="0.1.0", lifespan=lifespan)
register_routers(app)


if __name__ == "__main__":
    cfg = get_config().server
    uvicorn.run("app:app", host=cfg.host, port=cfg.port, reload=True)
