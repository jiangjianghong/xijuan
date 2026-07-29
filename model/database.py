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
            max_overflow=get_config().mysql.max_overflow,
            pool_timeout=get_config().mysql.pool_timeout,
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


async def rollback_if_broken(session: AsyncSession) -> bool:
    """会话因写入失败进入 DEACTIVE 时回滚，使其重新可用。

    写入失败（如 DataError）后 SQLAlchemy 把会话置为 DEACTIVE，此后任何
    execute 都抛 PendingRollbackError；异常处理里若直接写库，就会用次生异常
    掩盖真正的失败原因。

    只在真坏掉时回滚：rollback 会 expire 会话中所有 ORM 实例，对没碰过 DB 的
    业务异常做无谓回滚，会让调用方后续访问 field/rule 等对象属性触发 lazy
    refresh —— 多一次 IO，非 greenlet 上下文还会直接抛 MissingGreenlet。

    Returns:
        是否执行了回滚。
    """
    if session.is_active:
        return False
    await session.rollback()
    return True
