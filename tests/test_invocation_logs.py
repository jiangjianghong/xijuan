"""调用日志使用真实 SQLite 表验证三模式持久化、查询和启动恢复。"""

import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from model.database import get_db
from model.tables import InvocationLog
from service import invocation_log_service

analysis_module = importlib.import_module("blue_print.analysis_router")
invocation_module = importlib.import_module("blue_print.invocation_router")


@pytest.fixture
async def log_client(analysis_task_db):
    app = FastAPI()
    app.include_router(analysis_module.router)
    app.include_router(invocation_module.router)

    async def db_override():
        async with analysis_task_db() as session:
            yield session

    app.dependency_overrides[get_db] = db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
async def test_analysis_modes_persist_raw_input_and_download(log_client, monkeypatch, mode):
    expected = {"total_items": 1, "items": [{
        "item_index": 0, "biz_id": "业务-001", "type_id": "contract", "total": 1,
        "succeeded": 1, "failed": 0, "results": [{
            "rule_id": "amount", "rule_name": "金额", "rule_type": "calc",
            "result": "120", "reason": "计算结果", "input_values": {"amount": "120"},
            "success": True, "index": 1, "total": 1,
        }],
    }]}

    async def run(*args, **kwargs):
        return expected

    monkeypatch.setattr(analysis_module, "_run_analysis_with_session", run)
    payload = {"mode": mode, "items": [{"type_id": "contract", "biz_id": "业务-001", "field_values": {"amount": "120"}}]}
    response = await log_client.post("/analysis/run", json=payload)
    assert response.status_code == 200
    listing = (await log_client.get("/invocations")).json()["data"]
    assert listing["total"] == 1
    summary = listing["items"][0]
    assert summary["status"] == "complete"
    assert "result" not in summary
    assert "request_input" not in summary
    detail = (await log_client.get(f"/invocations/{summary['log_id']}")).json()["data"]
    assert detail["request_input"]["body"] == payload
    assert detail["started_at"] and detail["finished_at"]
    assert detail["result"] == {"analysis": expected}
    download = await log_client.get(f"/invocations/{summary['log_id']}/result.json")
    assert download.status_code == 200
    assert download.json() == {"analysis": expected}
    assert "attachment" in download.headers["content-disposition"]


async def test_analysis_failure_is_retained(log_client, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("分析异常")

    monkeypatch.setattr(analysis_module, "_run_analysis_with_session", fail)
    response = await log_client.post("/analysis/run", json={"mode": "sync", "items": [{
        "type_id": "contract", "biz_id": "业务-002", "field_values": {"amount": "120"},
    }]})
    assert response.status_code == 500
    data = (await log_client.get("/invocations", params={"status": "failed"})).json()["data"]
    assert data["total"] == 1
    assert "分析异常" in data["items"][0]["error"]
    assert data["items"][0]["finished_at"]


async def test_recovery_preserves_finished_logs(analysis_task_db):
    for status in ["queued", "running", "complete", "partial_failed", "failed"]:
        await invocation_log_service.create_log(log_id=status, kind="independent_analysis", status=status)
    async with analysis_task_db() as session:
        await invocation_log_service.recover_interrupted_logs(session)
    async with analysis_task_db() as session:
        for status in ["queued", "running"]:
            row = await session.get(InvocationLog, status)
            assert row.status == "failed" and row.finished_at
            assert "服务重启" in row.error
        for status in ["complete", "partial_failed", "failed"]:
            assert (await session.get(InvocationLog, status)).status == status
