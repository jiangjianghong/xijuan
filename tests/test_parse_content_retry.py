"""解析结果大字段落库的 MySQL 连接断开重试。"""

from __future__ import annotations

import pytest

import service.parse_service as parse_service


class _Result:
    def scalar_one_or_none(self):
        return None


class _Session:
    def __init__(
        self,
        *,
        fail_execute: BaseException | None = None,
        fail_commit: BaseException | None = None,
        fail_rollback: BaseException | None = None,
    ):
        self.fail_execute = fail_execute
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.execute_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.invalidate_calls = 0
        self.added = []

    async def execute(self, _stmt):
        self.execute_calls += 1
        if self.fail_execute is not None:
            exc, self.fail_execute = self.fail_execute, None
            raise exc
        return _Result()

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_calls += 1
        if self.fail_commit is not None:
            exc, self.fail_commit = self.fail_commit, None
            raise exc

    async def rollback(self):
        self.rollback_calls += 1
        if self.fail_rollback is not None:
            raise self.fail_rollback

    async def invalidate(self):
        self.invalidate_calls += 1


class _Factory:
    def __init__(self, session: _Session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_save_file_content_retries_connection_loss_with_fresh_session(monkeypatch):
    first = _Session(fail_execute=ConnectionError(2013, "Lost connection during query"))
    retry = _Session()
    factory = _Factory(retry)
    delays = []

    monkeypatch.setattr(parse_service, "get_session_factory", lambda: factory)
    monkeypatch.setattr(parse_service.asyncio, "sleep", lambda delay: _record_delay(delays, delay))

    await parse_service.save_file_content(
        "file-1", "中文内容", first, middle_json="{}", page_mapping=[]
    )

    assert first.execute_calls == 1
    assert first.rollback_calls == 1
    assert retry.execute_calls == 1
    assert retry.commit_calls == 1
    assert len(retry.added) == 1
    assert delays == [1]


async def _record_delay(delays: list[int], delay: int):
    delays.append(delay)


@pytest.mark.asyncio
async def test_save_file_content_does_not_retry_non_connection_error(monkeypatch):
    first = _Session(fail_execute=ValueError("invalid payload"))
    factory_calls = []

    def _factory():
        factory_calls.append(True)
        return _Factory(_Session())

    monkeypatch.setattr(parse_service, "get_session_factory", _factory)

    with pytest.raises(ValueError, match="invalid payload"):
        await parse_service.save_file_content("file-2", "内容", first)

    assert factory_calls == []
    assert first.rollback_calls == 0


@pytest.mark.asyncio
async def test_save_file_content_retries_when_commit_loses_connection(monkeypatch):
    """提交阶段断连时也必须用新会话重做幂等写入。"""
    first = _Session(
        fail_commit=ConnectionError(2013, "Lost connection during query")
    )
    retry = _Session()
    monkeypatch.setattr(parse_service, "get_session_factory", lambda: _Factory(retry))
    monkeypatch.setattr(parse_service.asyncio, "sleep", lambda _delay: _record_delay([], 0))

    await parse_service.save_file_content("file-commit", "内容", first)

    assert first.commit_calls == 1
    assert first.rollback_calls == 1
    assert retry.commit_calls == 1


@pytest.mark.asyncio
async def test_save_file_content_invalidates_session_when_rollback_is_broken(monkeypatch):
    first = _Session(
        fail_execute=ConnectionError(2013, "lost"),
        fail_rollback=ConnectionError("connection already closed"),
    )
    retry = _Session()
    monkeypatch.setattr(parse_service, "get_session_factory", lambda: _Factory(retry))
    monkeypatch.setattr(parse_service.asyncio, "sleep", lambda _delay: _record_delay([], 0))

    await parse_service.save_file_content("file-3", "内容", first)

    assert first.invalidate_calls == 1
    assert retry.commit_calls == 1


def test_is_mysql_connection_loss_recognizes_wrapped_operational_error():
    class _Wrapped(Exception):
        connection_invalidated = True

    assert parse_service._is_mysql_connection_loss(_Wrapped("lost"))
    assert parse_service._is_mysql_connection_loss(ConnectionError(2013, "lost"))
    assert not parse_service._is_mysql_connection_loss(ConnectionError(1045, "denied"))

