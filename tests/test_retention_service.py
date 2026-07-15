"""PDF 保留策略清理逻辑测试(service/retention_service.py)。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from model.database import get_session_factory
from model.tables import File
from service import retention_service
from utils import vl_client
from utils.config import get_config

_PREFIX = "rtest_"


@pytest.fixture
def fresh_uploads(tmp_path, monkeypatch):
    """把 PDF 存储目录指向临时目录,隔离真实 uploads。"""
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def storage_cfg(monkeypatch):
    """默认两项策略都关闭,单测各自按需打开。"""
    cfg = get_config().storage
    monkeypatch.setattr(cfg, "max_total_bytes", 0)
    monkeypatch.setattr(cfg, "max_retention_minutes", 0)
    return cfg


@pytest.fixture(autouse=True)
async def _cleanup_rows():
    """每个用例结束后删除本测试插入的 files 记录,避免污染真实库。"""
    yield
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(delete(File).where(File.file_id.like(f"{_PREFIX}%")))
        await session.commit()


async def _seed(file_id: str, create_time: datetime, size: int, storage_dir) -> None:
    """插入一条 files 记录 + 写一个对应大小的假 PDF。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add(
            File(
                file_id=file_id,
                type_id="default",
                file_name=f"{file_id}.pdf",
                file_size=size,
                create_time=create_time,
                progress="complete",
            )
        )
        await session.commit()
    (storage_dir / f"{file_id}.pdf").write_bytes(b"x" * size)


async def _run() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await retention_service.enforce_pdf_retention(session)


async def test_ttl_removes_expired_keeps_recent(fresh_uploads, storage_cfg, monkeypatch):
    """超过 max_retention_minutes 的 PDF 被删,未超过的保留。"""
    monkeypatch.setattr(storage_cfg, "max_retention_minutes", 60)
    await _seed(f"{_PREFIX}old", datetime.now() - timedelta(minutes=120), 10, fresh_uploads)
    await _seed(f"{_PREFIX}new", datetime.now() - timedelta(minutes=10), 10, fresh_uploads)

    await _run()

    assert not (fresh_uploads / f"{_PREFIX}old.pdf").exists()
    assert (fresh_uploads / f"{_PREFIX}new.pdf").exists()


async def test_total_size_evicts_oldest_until_under_limit(fresh_uploads, storage_cfg, monkeypatch):
    """总大小超上限时,按 create_time 从最旧淘汰,直到回落到上限以下。"""
    monkeypatch.setattr(storage_cfg, "max_total_bytes", 150)
    await _seed(f"{_PREFIX}a", datetime.now() - timedelta(minutes=30), 100, fresh_uploads)
    await _seed(f"{_PREFIX}b", datetime.now() - timedelta(minutes=20), 100, fresh_uploads)
    await _seed(f"{_PREFIX}c", datetime.now() - timedelta(minutes=10), 100, fresh_uploads)

    await _run()

    # 总 300 > 150:删最旧 a(→200 仍>150),再删 b(→100 ≤150 停),c(最新)保留
    assert not (fresh_uploads / f"{_PREFIX}a.pdf").exists()
    assert not (fresh_uploads / f"{_PREFIX}b.pdf").exists()
    assert (fresh_uploads / f"{_PREFIX}c.pdf").exists()


async def test_both_disabled_keeps_all(fresh_uploads, storage_cfg):
    """两项配置都为 0(默认)时,再旧的文件也不删。"""
    await _seed(f"{_PREFIX}keep", datetime.now() - timedelta(days=999), 100, fresh_uploads)

    await _run()

    assert (fresh_uploads / f"{_PREFIX}keep.pdf").exists()


async def test_orphan_pdf_untouched(fresh_uploads, storage_cfg, monkeypatch):
    """不在 files 表中的孤儿 PDF 不被保留策略清理(归 cleanup_orphan_pdfs 管)。"""
    monkeypatch.setattr(storage_cfg, "max_retention_minutes", 1)
    (fresh_uploads / f"{_PREFIX}orphan.pdf").write_bytes(b"xxxx")

    await _run()

    assert (fresh_uploads / f"{_PREFIX}orphan.pdf").exists()


async def test_retention_loop_calls_enforce_then_reraises_cancel(monkeypatch):
    """后台循环每轮 sleep 后调用一次 enforce;收到取消时向上抛出以便优雅退出。"""
    import asyncio

    calls = []

    async def fake_enforce(session):
        calls.append(1)
        raise asyncio.CancelledError

    async def fake_sleep(_secs):
        return

    monkeypatch.setattr(retention_service, "enforce_pdf_retention", fake_enforce)
    monkeypatch.setattr(retention_service.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await retention_service.retention_loop()

    assert calls == [1]
