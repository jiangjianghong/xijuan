"""失败状态落库的独立会话重试测试。"""

from __future__ import annotations

import pytest

import service.stage_status as stage_status


class _RetrySession:
    def __init__(self, attempt: int, fail_attempts: int):
        self.attempt = attempt
        self.fail_attempts = fail_attempts

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        if self.attempt <= self.fail_attempts:
            raise ConnectionError("database unavailable")

    async def commit(self):
        pass


class _SessionFactory:
    def __init__(self, fail_attempts: int):
        self.fail_attempts = fail_attempts
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        return _RetrySession(self.attempts, self.fail_attempts)


@pytest.mark.asyncio
async def test_mark_file_failed_retries_with_backoff_then_succeeds(monkeypatch):
    factory = _SessionFactory(fail_attempts=2)
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(stage_status, "get_session_factory", lambda: factory)
    monkeypatch.setattr(stage_status.asyncio, "sleep", record_sleep)

    marked = await stage_status.mark_file_failed_with_retry(
        None, "file-1", "parsing_failed", RuntimeError("write failed")
    )

    assert marked is True
    assert factory.attempts == 3
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_mark_file_failed_stops_after_three_retries(monkeypatch):
    factory = _SessionFactory(fail_attempts=4)
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(stage_status, "get_session_factory", lambda: factory)
    monkeypatch.setattr(stage_status.asyncio, "sleep", record_sleep)

    marked = await stage_status.mark_file_failed_with_retry(
        None, "file-1", "parsing_failed", RuntimeError("write failed")
    )

    assert marked is False
    assert factory.attempts == 4
    assert delays == [1, 2, 4]
