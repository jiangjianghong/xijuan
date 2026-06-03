"""pytest fixtures：测试用 app client、mock db。"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# 延迟导入 app 以避免初始化时数据库连接问题
# from app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """每个测试结束后释放数据库引擎连接池，隔离事件循环。

    引擎是模块级单例，其连接绑定到首次创建时的事件循环；而每个测试用例
    使用独立的事件循环（function scope）。若不释放，后续测试会复用绑定到
    已关闭循环的连接，触发 'Event loop is closed'。测试结束后 dispose 并清空
    单例，下个测试即在自己的循环上重建干净的连接池。
    """
    yield
    from model import database

    if database._engine is not None:
        await database._engine.dispose()
        database._engine = None
        database._session_factory = None



@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 测试客户端。"""
    from app import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
