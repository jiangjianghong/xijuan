"""pytest fixtures：测试用 app client、mock db。"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# 延迟导入 app 以避免初始化时数据库连接问题
# from app import app


@pytest.fixture
def analysis_task_db(monkeypatch):
    """任务存储使用真实 SQLite 表；只适配同步驱动，不模拟 SQL 行为。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from model.tables import AnalysisTask
    from service import analysis_task_store

    engine = create_engine("sqlite://")
    AnalysisTask.__table__.create(engine)

    class SessionAdapter:
        async def __aenter__(self):
            self.session = Session(engine)
            return self

        async def __aexit__(self, *args):
            self.session.close()

        def add(self, obj):
            self.session.add(obj)

        async def execute(self, statement):
            return self.session.execute(statement)

        async def get(self, model, key):
            return self.session.get(model, key)

        async def commit(self):
            self.session.commit()

    monkeypatch.setattr(analysis_task_store, "get_session_factory", lambda: SessionAdapter)
    yield SessionAdapter
    engine.dispose()


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
