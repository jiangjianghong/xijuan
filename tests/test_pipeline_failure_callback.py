"""管线阶段失败回调(stage_failed)测试。"""

from __future__ import annotations

import pytest

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


class FakeSession:
    """异步 session 桩:execute/commit 全部空操作。"""

    async def execute(self, *args, **kwargs):
        return _FakeResult()

    async def commit(self):
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
