"""SQLAlchemy 引擎与会话管理。"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from utils.config import get_config


def _build_database_url() -> str:
    cfg = get_config().mysql
    return (
        f"mysql+aiomysql://{cfg.username}:{cfg.password}"
        f"@{cfg.host}:{cfg.port}/{cfg.database}?charset=utf8mb4"
    )


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """获取数据库引擎（懒加载）。"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _build_database_url(),
            pool_size=get_config().mysql.pool_size,
            pool_recycle=300,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂（懒加载）。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


# 兼容旧代码的别名
@property
def engine() -> AsyncEngine:
    return get_engine()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：获取数据库会话。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
