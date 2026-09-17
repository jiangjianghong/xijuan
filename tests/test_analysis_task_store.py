"""独立分析任务持久化与启动恢复。"""

import pytest


@pytest.mark.anyio
async def test_task_lifecycle_survives_separate_sessions(analysis_task_db):
    from service import analysis_task_store as store

    assert await store.get_task("missing") is None
    await store.create_task("task-1")
    queued = await store.get_task("task-1")
    assert queued["status"] == "queued"
    assert queued["result"] is None
    assert queued["error"] is None
    assert queued["created_at"] is not None
    await store.set_task_state("task-1", "analyzing")
    assert (await store.get_task("task-1"))["status"] == "analyzing"
    result = {"total_items": 1, "items": [{"biz_id": "业务编号", "results": []}]}
    await store.set_task_state("task-1", "complete", result=result)
    assert (await store.get_task("task-1"))["result"] == result


@pytest.mark.anyio
async def test_restart_marks_only_unfinished_tasks_failed(analysis_task_db):
    from service import analysis_task_store as store

    for state in ["queued", "analyzing", "complete", "analysis_failed"]:
        await store.create_task(state)
        await store.set_task_state(state, state)
    async with analysis_task_db() as session:
        await store.recover_interrupted_tasks(session)
    for state in ["queued", "analyzing"]:
        task = await store.get_task(state)
        assert task["status"] == "analysis_failed"
        assert "服务重启" in task["error"]
    assert (await store.get_task("complete"))["status"] == "complete"
    assert (await store.get_task("analysis_failed"))["error"] is None


@pytest.mark.anyio
async def test_startup_invokes_task_recovery(analysis_task_db, monkeypatch):
    from service import analysis_task_store as store, init_service, retention_service
    from types import SimpleNamespace

    async def noop(*args):
        pass

    await store.create_task("interrupted")
    for name in ["init_database", "recover_abnormal_status", "cleanup_garbage_data", "cleanup_orphan_pdfs"]:
        monkeypatch.setattr(init_service, name, noop)
    monkeypatch.setattr(init_service, "get_session_factory", lambda: analysis_task_db)
    monkeypatch.setattr(init_service, "MilvusClient", lambda: SimpleNamespace(
        connect=lambda: None, ensure_collection=lambda: None,
    ))
    monkeypatch.setattr(retention_service, "enforce_pdf_retention", noop)
    await init_service.run_init()
    assert (await store.get_task("interrupted"))["status"] == "analysis_failed"
