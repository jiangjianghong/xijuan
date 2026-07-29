"""DB 写失败后的会话回滚测试。

线上事故（2026-07-28）：extraction_result.extracted_value 超长触发 DataError 1406 后，
异常路径直接 execute 去标记失败态，而 SQLAlchemy 的会话此时已是 DEACTIVE，
于是抛 PendingRollbackError 掩盖原始错误，文件 progress 永久卡在 extracting。

这里的桩复刻真实 SQLAlchemy 行为：flush 失败 → DEACTIVE → execute 必抛
PendingRollbackError，直到显式 rollback。没有这组测试，rollback 被误删不会有任何报警。
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DataError, PendingRollbackError

import service.pipeline_service as pipeline_service


class CallbackRecorder:
    """记录 notify_callback 全部调用的桩。"""

    def __init__(self):
        self.calls = []

    async def __call__(self, callback_url, file_id, status, *, event=None, data=None, timeout=2.5):
        self.calls.append({"status": status, "event": event, "data": data})


class _FakeResult:
    """覆盖管线里用到的 execute 返回值形态。"""

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class FakeMilvusClient:
    """Milvus 桩：connect / delete 空操作。"""

    def connect(self):
        pass

    def delete_by_file_id(self, file_id):
        pass


def _stmt_values(stmt) -> dict:
    """从 Update 语句取出 {列名: 值}，用于断言写入的 progress/error。"""
    values = getattr(stmt, "_values", None)
    if not values:
        return {}
    return {col.name: getattr(val, "value", val) for col, val in values.items()}


class DeactivatingSession:
    """复刻 SQLAlchemy 会话在 flush 失败后的 DEACTIVE 行为。

    deactivate() 之后任何 execute/commit 都抛 PendingRollbackError，
    只有 rollback() 能让会话重新可用 —— 与真实 Session 一致。
    """

    def __init__(self):
        self.deactivated = False
        self.rollback_count = 0
        self.updates: list[dict] = []

    @property
    def is_active(self) -> bool:
        """真实 Session 在 flush 失败后转 False，rollback 后回 True。"""
        return not self.deactivated

    def deactivate(self):
        """模拟一次失败的 flush 把事务打成 DEACTIVE。"""
        self.deactivated = True

    def _guard(self):
        if self.deactivated:
            raise PendingRollbackError(
                "This Session's transaction has been rolled back due to a previous "
                "exception during flush. To begin a new transaction with this Session, "
                "first issue Session.rollback()."
            )

    async def execute(self, stmt, *args, **kwargs):
        self._guard()
        vals = _stmt_values(stmt)
        if vals:
            self.updates.append(vals)
        return _FakeResult()

    async def commit(self):
        self._guard()

    async def rollback(self):
        self.rollback_count += 1
        self.deactivated = False

    def progress_writes(self) -> list[str]:
        """按顺序取出所有写入过的 progress 值。"""
        return [u["progress"] for u in self.updates if "progress" in u]


def _make_data_error() -> DataError:
    """构造与线上一致的 1406 DataError。"""
    return DataError(
        "INSERT INTO extraction_result (file_id, field_id, extracted_value) VALUES (%s, %s, %s)",
        {},
        Exception("(1406, \"Data too long for column 'extracted_value' at row 1\")"),
    )


@pytest.fixture
def _patched(monkeypatch):
    """通用桩：Milvus 空操作 + 回调记录器。"""
    recorder = CallbackRecorder()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "MilvusClient", FakeMilvusClient)
    return recorder


@pytest.mark.anyio
async def test_extracting_failure_marks_failed_after_rollback(monkeypatch, _patched):
    """落库失败导致会话 DEACTIVE 时，仍能把文件标记为 extracting_failed。"""
    session = DeactivatingSession()

    async def _boom(file_id, sess, **kwargs):
        # 复刻真实链路：commit 抛 DataError 的同时把会话打成 DEACTIVE
        sess.deactivate()
        raise _make_data_error()

    monkeypatch.setattr(pipeline_service, "run_extraction", _boom)

    with pytest.raises(DataError):
        await pipeline_service.run_from_stage(
            "f_deactivated", "extracting", session, callback_url="http://cb"
        )

    assert session.rollback_count >= 1, "标记失败态前必须 rollback，否则 execute 必抛 PendingRollbackError"
    assert "extracting_failed" in session.progress_writes(), "文件必须被标记为 extracting_failed，不能卡在 extracting"


@pytest.mark.anyio
async def test_original_data_error_not_masked(monkeypatch, _patched):
    """冒到调用方的必须是原始 DataError，而不是次生的 PendingRollbackError。"""
    session = DeactivatingSession()

    async def _boom(file_id, sess, **kwargs):
        sess.deactivate()
        raise _make_data_error()

    monkeypatch.setattr(pipeline_service, "run_extraction", _boom)

    with pytest.raises(DataError) as exc_info:
        await pipeline_service.run_from_stage(
            "f_not_masked", "extracting", session, callback_url="http://cb"
        )

    assert "Data too long" in str(exc_info.value)
    assert not isinstance(exc_info.value, PendingRollbackError)


@pytest.mark.anyio
async def test_stage_failed_callback_carries_original_error(monkeypatch, _patched):
    """stage_failed 回调恰好 1 条，且带的是原始 1406 而非 PendingRollbackError。"""
    session = DeactivatingSession()

    async def _boom(file_id, sess, **kwargs):
        sess.deactivate()
        raise _make_data_error()

    monkeypatch.setattr(pipeline_service, "run_extraction", _boom)

    with pytest.raises(DataError):
        await pipeline_service.run_from_stage(
            "f_cb", "extracting", session, callback_url="http://cb"
        )

    failed = [c for c in _patched.calls if c["event"] == "stage_failed"]
    assert len(failed) == 1
    assert failed[0]["status"] == "extracting_failed"
    assert failed[0]["data"]["stage"] == "extracting"
    assert "Data too long" in failed[0]["data"]["error"]
    assert "PendingRollbackError" not in failed[0]["data"]["error"]


@pytest.mark.anyio
async def test_analyzing_failure_marks_failed_after_rollback(monkeypatch, _patched):
    """analyzing 阶段同样受保护（20 处失败标记共用一条路径）。"""
    session = DeactivatingSession()

    async def _ok_extraction(file_id, sess, **kwargs):
        return None

    async def _boom(file_id, sess, **kwargs):
        sess.deactivate()
        raise _make_data_error()

    monkeypatch.setattr(pipeline_service, "run_extraction", _ok_extraction)
    monkeypatch.setattr(pipeline_service, "run_analysis", _boom)

    with pytest.raises(DataError):
        await pipeline_service.run_from_stage(
            "f_analyzing", "extracting", session, callback_url="http://cb"
        )

    assert session.rollback_count >= 1
    assert "analyzing_failed" in session.progress_writes()


@pytest.mark.anyio
async def test_healthy_session_is_not_rolled_back(monkeypatch, _patched):
    """会话正常时不做无谓 rollback —— rollback 会 expire 全部 ORM 实例。

    过期后调用方再访问 field/rule 的属性会触发 lazy refresh：多一次 IO，
    非 greenlet 上下文直接抛 MissingGreenlet。没碰过 DB 的业务异常
    （如「引用的字段未取到值」）必须原样放过，只标记失败态。
    """
    session = DeactivatingSession()  # 全程不 deactivate，模拟健康会话

    async def _boom(file_id, sess, **kwargs):
        raise ValueError("引用的字段 basic_a 未取到值")

    monkeypatch.setattr(pipeline_service, "run_extraction", _boom)

    with pytest.raises(ValueError):
        await pipeline_service.run_from_stage(
            "f_healthy", "extracting", session, callback_url="http://cb"
        )

    assert session.rollback_count == 0, "会话健康时不该 rollback，否则会 expire ORM 对象"
    assert "extracting_failed" in session.progress_writes(), "仍须正常标记失败态"


@pytest.mark.anyio
async def test_rollback_if_broken_only_acts_when_needed():
    """rollback_if_broken 的判定语义：只在 DEACTIVE 时回滚，并如实回报。"""
    from model.database import rollback_if_broken

    healthy = DeactivatingSession()
    assert await rollback_if_broken(healthy) is False
    assert healthy.rollback_count == 0

    broken = DeactivatingSession()
    broken.deactivate()
    assert await rollback_if_broken(broken) is True
    assert broken.rollback_count == 1
    assert broken.is_active, "回滚后会话须恢复可用"
