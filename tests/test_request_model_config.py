"""请求级模型覆盖：实际模型请求、并发隔离、各 HTTP 入口与清理行为。"""

import asyncio
import importlib
import json
import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from model.schemas import AnalysisRunRequest
from utils import llm_client, vl_client
from utils.config import AppConfig
from utils.model_config import (
    model_config_context, model_config_stream, parse_model_config,
    redact_model_config, resolve_model_config, validate_model_config, with_model_config,
)


@pytest.mark.parametrize("value", [[], "bad", {"mysql": {}}, {"analysis": []},
    {"analysis": {"judge_timeout": 0}}, {"concurrency": {"global_llm": 100}},
    {"vl_model": {"global_max_concurrency": 100}},
    {"embedding": {"embedding_dim": 8}}, {"extraction": {"typo": 1}}])
def test_reject_unknown_or_invalid_model_config(value):
    with pytest.raises(ValueError):
        validate_model_config(value)


def test_aliases_partial_overrides_and_replace_extra_body():
    base = AppConfig()
    base.extraction.extra_body = {"old": 1}
    values = validate_model_config({"extraction": {"llm_model": "new", "extra_body": {}},
                                    "embedding": {"model_name": "embed"}})
    req = AnalysisRunRequest(mode="sync", items=[{"type_id": "t", "biz_id": "b"}], model_config=values)
    assert req.model_dump()["model_config"] == values
    with model_config_context(values):
        cfg = resolve_model_config(base)
        assert cfg.extraction.model == "new"
        assert cfg.extraction.base_url == base.extraction.base_url
        assert cfg.extraction.extra_body == {}
        assert cfg.embedding.model == "embed"
        assert cfg.concurrency is base.concurrency
    assert base.extraction.extra_body == {"old": 1}
    assert resolve_model_config(base) is base
    assert parse_model_config(json.dumps(values)) == values


async def test_concurrent_children_inherit_and_restore_on_error():
    base = AppConfig()
    gate = asyncio.Event()
    ready = 0

    @with_model_config
    async def request():
        nonlocal ready
        ready += 1
        if ready == 2:
            gate.set()
        await gate.wait()
        async def child():
            await asyncio.sleep(0)
            return resolve_model_config(base).extraction.model
        return await asyncio.create_task(child())

    assert await asyncio.gather(
        request(model_config={"extraction": {"model": "a"}}),
        request(model_config={"extraction": {"model": "b"}}),
    ) == ["a", "b"]
    assert resolve_model_config(base) is base
    with pytest.raises(RuntimeError):
        with model_config_context({"extraction": {"model": "x"}}):
            raise RuntimeError()
    assert resolve_model_config(base) is base


async def test_stream_does_not_leak_and_closes_under_own_config():
    base = AppConfig()
    closed = []
    async def source():
        try:
            yield resolve_model_config(base).extraction.model
            yield resolve_model_config(base).extraction.model
        finally:
            closed.append(resolve_model_config(base).extraction.model)
    stream = model_config_stream(source(), {"extraction": {"model": "stream"}})
    assert await anext(stream) == "stream"
    assert resolve_model_config(base) is base
    await asyncio.create_task(stream.aclose())
    assert closed == ["stream"]
    assert resolve_model_config(base) is base


@pytest.mark.parametrize("group", ["extraction", "analysis", "table_name_validation", "embedding", "vl_model"])
async def test_actual_model_requests_use_overrides_and_original_limiters(monkeypatch, group):
    base = AppConfig()
    base.extraction.extra_body = {"old": True}
    calls = []
    limits = []
    class Client:
        def __init__(self, **kwargs):
            self.timeout = kwargs["timeout"]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            calls.append((url, kwargs, self.timeout))
            return httpx.Response(200, request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "ok"}}], "data": [{"embedding": [1.0]}]})
    class Limiter:
        @asynccontextmanager
        async def context(self, *args):
            yield
    def limiter(name, limit):
        limits.append((name, limit))
        return Limiter()
    monkeypatch.setattr(llm_client, "get_config", lambda: base)
    monkeypatch.setattr(vl_client, "get_config", lambda: base)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", Client)
    monkeypatch.setattr(llm_client, "get_limiter", limiter)
    monkeypatch.setattr(vl_client, "get_limiter", limiter)
    fields = {"base_url": "http://request.test/v1", "model": "request-model", "api_key": ""}
    if group != "embedding":
        fields.update({"temperature": 0.7, "extra_body": {"request": True},
                       "judge_timeout" if group == "analysis" else "timeout": 19})
    with model_config_context(validate_model_config({group: fields})):
        if group == "embedding":
            await llm_client.get_embeddings(["test"])
        elif group == "vl_model":
            await vl_client.vl_chat([])
        else:
            await llm_client.chat_completion("test", config_group=group)
    url, kwargs, timeout = calls[0]
    assert url.startswith("http://request.test/v1/")
    assert kwargs["json"]["model"] == "request-model"
    assert "Authorization" not in kwargs["headers"]
    assert "request-secret" not in str(kwargs)
    if group != "embedding":
        assert timeout == 19
        assert kwargs["json"]["temperature"] == 0.7
        assert kwargs["json"]["extra_body"] == {"request": True}
    pool = "global_vl" if group == "vl_model" else "global_embedding" if group == "embedding" else "global_llm"
    assert limits == [(pool, getattr(base.concurrency, pool))]


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
async def test_independent_analysis_three_modes_keep_context(client, monkeypatch, analysis_task_db, mode):
    router = importlib.import_module("blue_print.analysis_router")
    seen = []
    async def run(*args, **kwargs):
        seen.append(resolve_model_config(AppConfig()).analysis.model)
        return {"total_items": 1, "items": []}
    monkeypatch.setattr(router, "_run_analysis_with_session", run)
    resp = await client.post("/analysis/run", json={"mode": mode,
        "model_config": {"analysis": {"model": "request-analysis"}},
        "items": [{"type_id": "t", "biz_id": "b"}]})
    assert resp.status_code == 200
    assert seen == ["request-analysis"]
    assert resolve_model_config(AppConfig()).analysis.model == ""


@pytest.fixture
def file_environment(monkeypatch, tmp_path):
    from app import app
    from model import database
    from model.database import get_db
    from service import invocation_log_service, type_param_store
    router = importlib.import_module("blue_print.file_router")
    class Session:
        async def execute(self, stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
                file_id="f", file_name="test.pdf", type_id="default", file_size=1, input_params={}))
        def add(self, value):
            pass
        async def commit(self):
            pass
    @asynccontextmanager
    async def session_factory():
        yield Session()
    async def dependency():
        yield Session()
    @asynccontextmanager
    async def context(*args, **kwargs):
        yield
    async def noop(*args, **kwargs):
        return None
    async def params(*args, **kwargs):
        return []
    monkeypatch.setattr(database, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(router, "pipeline_slot", context)
    monkeypatch.setattr(router, "track_file_call", context)
    monkeypatch.setattr(router, "_enforce_retention_background", noop)
    monkeypatch.setattr(invocation_log_service, "create_log", noop)
    monkeypatch.setattr(type_param_store, "load_type_param_defs", params)
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    app.dependency_overrides[get_db] = dependency
    yield router
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
@pytest.mark.parametrize("kind", ["parse", "retry"])
async def test_file_three_modes_pass_model_config(client, monkeypatch, file_environment, mode, kind):
    router = file_environment
    seen = []
    @with_model_config
    async def run(*args, **kwargs):
        seen.append(resolve_model_config(AppConfig()).extraction.model)
    @with_model_config
    async def stream(*args, **kwargs):
        await run()
        yield 'event: complete\ndata: {}\n\n'
    monkeypatch.setattr(router, "run_pipeline" if kind == "parse" else "run_from_stage", run)
    monkeypatch.setattr(router, "run_pipeline_stream" if kind == "parse" else "run_from_stage_stream", stream)
    config = {"extraction": {"model": "file-model"}}
    if kind == "parse":
        resp = await client.post('/file/parse', params={"mode": mode},
            files={"file": ("test.pdf", b"pdf", "application/pdf")},
            data={"model_config": json.dumps(config)})
    else:
        resp = await client.post('/file/f/retry/extracting', params={"mode": mode}, json={"model_config": config})
    assert resp.status_code == 200, resp.text
    assert seen == ["file-model"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", ["extraction", "analysis"])
async def test_debug_endpoints_use_request_models(client, monkeypatch, file_environment, kind, streaming):
    router = importlib.import_module(f"blue_print.{kind}_router")
    extraction_router = importlib.import_module("blue_print.extraction_router")
    seen = []

    async def params(*args, **kwargs):
        return {}

    async def snapshot(*args, **kwargs):
        return None

    def record():
        seen.append(getattr(resolve_model_config(AppConfig()), kind).model)

    @with_model_config
    async def events(*args, **kwargs):
        record()
        yield {"event": "result", "data": {"result_value": "ok", "reason": "测试"}}

    async def extract(*args, **kwargs):
        record()
        return "ok", "测试", {}, []

    monkeypatch.setattr(extraction_router, "resolve_debug_params", params)
    if kind == "extraction":
        monkeypatch.setattr(router, "load_extraction_snapshot", snapshot)
        monkeypatch.setattr(router, "extract_vl_field", extract)
        monkeypatch.setattr(router, "test_field_extraction_stream", events)
        config = {"source_type": "vl", "vl_method": "vl_model", "vl_extract_prompt": "测试"}
    else:
        monkeypatch.setattr(router, "test_rule_analysis_stream", events)
        config = {"rule_type": "judge", "expression": "测试"}
    response = await client.post(f"/{kind}/test" + ("/stream" if streaming else ""), json={
        "file_id": "f", "config": config, "model_config": {kind: {"model": "debug-model"}},
    })
    assert response.status_code == 200, response.text
    assert seen == ["debug-model"]
    assert getattr(resolve_model_config(AppConfig()), kind).model != "debug-model"


async def test_invocation_log_persists_only_redacted_credentials(analysis_task_db):
    from model.tables import InvocationLog
    from service.invocation_log_service import create_log

    config = {"analysis": {"api_key": "private-secret", "model": "request-model"}}
    log_id = await create_log(kind="independent_analysis", status="queued",
        request_input={"raw_body": json.dumps({"model_config": config})},
        parameters={"model_config": config})
    async with analysis_task_db() as session:
        row = await session.get(InvocationLog, log_id)
        assert "private-secret" not in str(row.request_input)
        assert row.parameters["model_config"]["analysis"]["api_key"] == "***"
        assert row.parameters["model_config"]["analysis"]["model"] == "request-model"


def test_log_redaction_covers_form_raw_body_and_parameters():
    config = {"extraction": {"api_key": "private-secret", "model": "x"}}
    value = {"request_input": {"form": [("model_config", json.dumps(config))],
              "raw_body": json.dumps({"model_config": config})}, "parameters": {"model_config": config}}
    assert "private-secret" not in str(redact_model_config(value))
    assert "private-secret" in str(value)
    assert redact_model_config('{  "params": {"x": 1} }') == '{  "params": {"x": 1} }'


def test_exception_logs_do_not_dump_request_credentials():
    # 子进程加载真实日志配置，并将两个 sink 重定向到内存，避免影响其他测试。
    script = textwrap.dedent('''
        import io
        from loguru import logger
        output = io.StringIO()
        original_add = logger.add
        diagnostics = []
        def capture_sink(sink, **kwargs):
            diagnostics.append(kwargs.get("diagnose", True))
            for key in ("rotation", "retention", "compression", "encoding"):
                kwargs.pop(key, None)
            return original_add(output, **kwargs)
        logger.add = capture_sink
        import logs
        from model.schemas import AnalysisRunRequest
        def fail(req):
            assert req.model_overrides["analysis"]["api_key"]
            raise RuntimeError("模型调用失败")
        req = AnalysisRunRequest(mode="sync", items=[{"type_id": "t", "biz_id": "b"}],
            model_config={"analysis": {"api_key": "private-request-secret"}})
        try:
            fail(req)
        except RuntimeError:
            logger.exception("独立分析执行失败")
        assert diagnostics == [False, False]
        assert "private-request-secret" not in output.getvalue()
        assert "RuntimeError" in output.getvalue()
    ''')
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
