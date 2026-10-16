"""管线阶段失败回调(stage_failed)测试。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import AsyncMock

import pytest

import service.pipeline_service as pipeline_service


@pytest.fixture(autouse=True)
def _skip_failure_status_database_writes(monkeypatch):
    monkeypatch.setattr(
        pipeline_service,
        "mark_file_failed_with_retry",
        AsyncMock(return_value=True),
    )


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


class FakeSession:
    """异步 session 桩:execute/commit/rollback 全部空操作。

    is_active/rollback 是必需的:失败标记走 stage_status.mark_file_failed,它靠
    is_active 判断会话是否已 DEACTIVE、需要时才 rollback。桩缺这些成员会被
    helper 吞掉并记 error 日志,测试虽仍通过但不再真实走标记路径。
    """

    is_active = True

    async def execute(self, *args, **kwargs):
        return _FakeResult()

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _stage_failed_count(recorder: CallbackRecorder) -> int:
    """统计 stage_failed 事件条数(契约:失败时恰好 1 条)。"""
    return sum(1 for c in recorder.calls if c["event"] == "stage_failed")


@pytest.mark.anyio
async def test_run_pipeline_parsing_failure_pushes_stage_failed(monkeypatch):
    """parsing 阶段抛异常时,推送 parsing_failed + event=stage_failed。"""
    recorder = CallbackRecorder()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)

    async def _boom(*args, **kwargs):
        raise TimeoutError("MinerU 连接超时")

    monkeypatch.setattr(pipeline_service, "parse_file", _boom)

    with pytest.raises(TimeoutError):
        await pipeline_service.run_pipeline(
            "f_parse_fail", "a.pdf", b"%PDF", FakeSession(), callback_url="http://cb"
        )

    # 阶段入口包(形态 A)已发出
    assert recorder.calls[0] == {"status": "parsing", "event": None, "data": None}
    # 最后一条是失败回调(形态 D)
    last = recorder.calls[-1]
    assert last["status"] == "parsing_failed"
    assert last["event"] == "stage_failed"
    assert last["data"]["stage"] == "parsing"
    assert "TimeoutError" in last["data"]["error"]
    assert "MinerU 连接超时" in last["data"]["error"]
    assert _stage_failed_count(recorder) == 1


@pytest.mark.anyio
async def test_run_pipeline_content_save_failure_persists_parsing_failed(monkeypatch):
    """MinerU 返回后解析内容落库失败，也必须记录为 parsing_failed。"""
    recorder = CallbackRecorder()
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "mark_file_failed_with_retry", mark_failed)
    monkeypatch.setattr(
        pipeline_service, "parse_file", AsyncMock(return_value=("# md", "{}"))
    )
    save_error = RuntimeError("file_content 写入失败")
    monkeypatch.setattr(
        pipeline_service, "save_file_content", AsyncMock(side_effect=save_error)
    )

    with pytest.raises(RuntimeError, match="file_content 写入失败"):
        await pipeline_service.run_pipeline(
            "f_content_save_fail", "a.pdf", b"%PDF", FakeSession(), callback_url="http://cb"
        )

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.args[1:3] == ("f_content_save_fail", "parsing_failed")
    assert mark_failed.await_args.args[3] is save_error
    failed = [call for call in recorder.calls if call["event"] == "stage_failed"]
    assert len(failed) == 1
    assert failed[0]["status"] == "parsing_failed"


@pytest.mark.anyio
async def test_run_pipeline_cancellation_marks_current_stage_failed(monkeypatch):
    recorder = CallbackRecorder()
    mark_failed = AsyncMock(return_value=True)
    entered_parse = asyncio.Event()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "mark_file_failed_with_retry", mark_failed)

    async def _wait_for_cancel(*args, **kwargs):
        entered_parse.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline_service, "parse_file", _wait_for_cancel)
    task = asyncio.create_task(
        pipeline_service.run_pipeline("f_cancel", "a.pdf", b"%PDF", FakeSession())
    )
    await entered_parse.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.args[1:3] == ("f_cancel", "parsing_failed")


@pytest.mark.anyio
async def test_background_pipeline_setup_failure_marks_parsing_failed(monkeypatch):
    file_router = import_module("blue_print.file_router")
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(file_router, "mark_file_failed_with_retry", mark_failed)

    @asynccontextmanager
    async def _broken_pipeline_slot(*args, **kwargs):
        raise RuntimeError("pipeline setup failed")
        yield

    monkeypatch.setattr(file_router, "pipeline_slot", _broken_pipeline_slot)
    await file_router._run_pipeline_background(
        "f_setup_fail", "a.pdf", b"%PDF"
    )

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.args[0] is None
    assert mark_failed.await_args.args[1:3] == ("f_setup_fail", "parsing_failed")


@pytest.mark.anyio
async def test_run_pipeline_tableing_failure_pushes_stage_failed(monkeypatch):
    """中段阶段(tableing)抛异常时,stage 跟踪正确。"""
    recorder = CallbackRecorder()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)

    async def _fake_parse_file(*args, **kwargs):
        return "# md 内容", None  # middle_json 空,build_page_mapping 直接返回 []

    async def _fake_save_content(*args, **kwargs):
        pass

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM 表名校验失败")

    monkeypatch.setattr(pipeline_service, "parse_file", _fake_parse_file)
    monkeypatch.setattr(pipeline_service, "save_file_content", _fake_save_content)
    monkeypatch.setattr(pipeline_service, "parse_tables", _boom)

    with pytest.raises(RuntimeError):
        await pipeline_service.run_pipeline(
            "f_table_fail", "a.pdf", b"%PDF", FakeSession(), callback_url="http://cb"
        )

    last = recorder.calls[-1]
    assert last["status"] == "tableing_failed"
    assert last["event"] == "stage_failed"
    assert last["data"]["stage"] == "tableing"
    assert "RuntimeError" in last["data"]["error"]
    # 失败后不应再有 complete
    statuses = [c["status"] for c in recorder.calls]
    assert "complete" not in statuses
    assert _stage_failed_count(recorder) == 1


class FakeMilvusClient:
    """Milvus 桩:connect / delete 空操作。"""

    def connect(self):
        pass

    def delete_by_file_id(self, file_id):
        pass


@pytest.mark.anyio
async def test_run_from_stage_failure_pushes_stage_failed(monkeypatch):
    """retry 路径失败(缺文件内容)时,推送 <stage>_failed + event=stage_failed。"""
    recorder = CallbackRecorder()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "MilvusClient", FakeMilvusClient)

    # FakeSession 的 scalar_one_or_none 返回 None → 触发"缺少文件内容" ValueError
    with pytest.raises(ValueError):
        await pipeline_service.run_from_stage(
            "f_retry_fail", "tableing", FakeSession(), callback_url="http://cb"
        )

    last = recorder.calls[-1]
    assert last["status"] == "tableing_failed"
    assert last["event"] == "stage_failed"
    assert last["data"]["stage"] == "tableing"
    assert "缺少文件内容" in last["data"]["error"]
    assert _stage_failed_count(recorder) == 1


@pytest.mark.anyio
async def test_run_from_stage_parsing_guard_no_callback(monkeypatch):
    """stage=parsing 的入参校验错误直接抛出,不推失败回调。"""
    recorder = CallbackRecorder()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "MilvusClient", FakeMilvusClient)

    with pytest.raises(ValueError):
        await pipeline_service.run_from_stage(
            "f_guard", "parsing", FakeSession(), callback_url="http://cb"
        )

    assert recorder.calls == []


@pytest.mark.anyio
async def test_run_from_stage_mid_stage_failure_attribution(monkeypatch):
    """retry 从 tableing 进入、chunking 阶段失败时,归因到 chunking。"""
    recorder = CallbackRecorder()
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "MilvusClient", FakeMilvusClient)

    class _ContentResult(_FakeResult):
        def scalar_one_or_none(self):
            class _FC:
                file_content = "# md 内容"
                page_mapping = []
            return _FC()

    class _ContentSession(FakeSession):
        async def execute(self, *args, **kwargs):
            return _ContentResult()

    async def _fake_parse_tables(*args, **kwargs):
        return []

    async def _fake_save_tables(*args, **kwargs):
        pass

    async def _boom(*args, **kwargs):
        raise RuntimeError("分块失败")

    monkeypatch.setattr(pipeline_service, "parse_tables", _fake_parse_tables)
    monkeypatch.setattr(pipeline_service, "save_tables", _fake_save_tables)
    monkeypatch.setattr(pipeline_service, "chunk_content", _boom)

    with pytest.raises(RuntimeError):
        await pipeline_service.run_from_stage(
            "f_mid_fail", "tableing", _ContentSession(), callback_url="http://cb"
        )

    last = recorder.calls[-1]
    assert last["status"] == "chunking_failed"
    assert last["data"]["stage"] == "chunking"
    assert _stage_failed_count(recorder) == 1


@pytest.mark.anyio
async def test_run_from_stage_milvus_connect_failure_marks_stage_failed(monkeypatch):
    """重试初始化 Milvus 失败时也必须写失败态并发送 stage_failed。"""
    recorder = CallbackRecorder()
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "mark_file_failed_with_retry", mark_failed)

    class _BrokenMilvusClient:
        def connect(self):
            raise RuntimeError("Milvus 连接失败")

    monkeypatch.setattr(pipeline_service, "MilvusClient", _BrokenMilvusClient)

    with pytest.raises(RuntimeError, match="Milvus 连接失败"):
        await pipeline_service.run_from_stage(
            "f_milvus_connect_fail", "tableing", FakeSession(), callback_url="http://cb"
        )

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.args[1:3] == (
        "f_milvus_connect_fail",
        "tableing_failed",
    )
    last = recorder.calls[-1]
    assert last["status"] == "tableing_failed"
    assert last["event"] == "stage_failed"
    assert last["data"]["stage"] == "tableing"
    assert "Milvus 连接失败" in last["data"]["error"]


@pytest.mark.anyio
async def test_run_from_stage_stream_milvus_connect_failure_marks_stage_failed(monkeypatch):
    """流式重试初始化 Milvus 失败时也必须写失败态并发 error。"""
    recorder = CallbackRecorder()
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(pipeline_service, "notify_callback", recorder)
    monkeypatch.setattr(pipeline_service, "mark_file_failed_with_retry", mark_failed)

    class _BrokenMilvusClient:
        def connect(self):
            raise RuntimeError("Milvus 连接失败")

    monkeypatch.setattr(pipeline_service, "MilvusClient", _BrokenMilvusClient)

    events = []
    async for event in pipeline_service.run_from_stage_stream(
        "f_milvus_connect_fail_stream", "tableing", FakeSession()
    ):
        events.append(event)

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.args[1:3] == (
        "f_milvus_connect_fail_stream",
        "tableing_failed",
    )
    assert any('"stage": "error"' in event for event in events)
    # 流式重试接口没有 callback_url，失败通过 SSE error 事件返回；
    # 失败态仍由 mark_file_failed_with_retry 持久化。
    assert recorder.calls == []
