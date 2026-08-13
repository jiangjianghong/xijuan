import asyncio

import pytest

from service import retention_service


async def test_retention_loop_reloads_interval_each_round(monkeypatch):
    intervals = iter([2, 7])
    sleeps = []

    class Storage:
        @property
        def cleanup_interval_minutes(self):
            return next(intervals)

    class Config:
        storage = Storage()

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    class FakeSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    async def fake_enforce(_session):
        return None

    monkeypatch.setattr(retention_service, "get_config", lambda: Config())
    monkeypatch.setattr(retention_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(retention_service, "get_session_factory", FakeSessionFactory())
    monkeypatch.setattr(retention_service, "enforce_pdf_retention", fake_enforce)

    with pytest.raises(asyncio.CancelledError):
        await retention_service.retention_loop()

    assert sleeps == [120, 420]
