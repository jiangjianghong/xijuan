"""逻辑分析调试临时重新抽取测试。"""

import importlib
from types import SimpleNamespace

import pytest

from model.schemas import AnalysisTestRequest
from model.tables import ExtractionField, File
from service import analysis_service, extraction_service

analysis_router = importlib.import_module("blue_print.analysis_router")


def test_analysis_test_request_re_extract_defaults_false():
    req = AnalysisTestRequest(file_id="file-1", config={"rule_type": "judge"})

    assert req.re_extract is False


def test_analysis_test_request_accepts_re_extract_true():
    req = AnalysisTestRequest(
        file_id="file-1",
        config={"rule_type": "judge"},
        re_extract=True,
    )

    assert req.re_extract is True


class _ScalarResult:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _QueuedSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _statement):
        return self._results.pop(0)


def _field(
    field_id: str,
    *,
    priority: int,
    is_advanced: int = 0,
    depend_fields=None,
):
    return ExtractionField(
        field_id=field_id,
        type_id="type-a",
        field_name=f"字段 {field_id}",
        source_type="text",
        priority=priority,
        is_advanced=is_advanced,
        depend_fields=depend_fields,
    )


async def test_temporary_plan_selects_only_direct_and_advanced_dependencies(
    monkeypatch,
):
    file_row = File(
        file_id="f1",
        file_name="a.pdf",
        file_size=1,
        type_id="type-a",
    )
    base = _field("base", priority=20)
    advanced = _field(
        "advanced",
        priority=10,
        is_advanced=1,
        depend_fields=["base"],
    )
    other = _field("other", priority=1)
    snapshot = SimpleNamespace(page_contents={})

    async def fake_snapshot(*_args, **_kwargs):
        return snapshot

    monkeypatch.setattr(extraction_service, "load_extraction_snapshot", fake_snapshot)
    session = _QueuedSession(
        [
            _ScalarResult(one=file_row),
            _ScalarResult(rows=[other, advanced, base]),
        ]
    )

    plan = await extraction_service.build_temporary_extraction_plan(
        "f1", ["advanced"], session
    )

    assert [field.field_id for field in plan.ordered_fields] == ["base", "advanced"]
    assert plan.basic_count == 1
    assert plan.direct_field_ids == ("advanced",)
    assert plan.snapshot is snapshot


async def test_temporary_plan_reports_missing_direct_field(monkeypatch):
    file_row = File(
        file_id="f1",
        file_name="a.pdf",
        file_size=1,
        type_id="type-a",
    )
    session = _QueuedSession(
        [_ScalarResult(one=file_row), _ScalarResult(rows=[_field("base", priority=1)])]
    )

    try:
        await extraction_service.build_temporary_extraction_plan(
            "f1", ["missing"], session
        )
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("缺失直接依赖字段时应拒绝构建临时抽取计划")


async def test_temporary_plan_reports_missing_advanced_dependency(monkeypatch):
    file_row = File(
        file_id="f1",
        file_name="a.pdf",
        file_size=1,
        type_id="type-a",
    )
    advanced = _field(
        "advanced",
        priority=1,
        is_advanced=1,
        depend_fields=["missing-base"],
    )
    session = _QueuedSession(
        [_ScalarResult(one=file_row), _ScalarResult(rows=[advanced])]
    )

    try:
        await extraction_service.build_temporary_extraction_plan(
            "f1", ["advanced"], session
        )
    except ValueError as exc:
        assert "missing-base" in str(exc)
    else:
        raise AssertionError("缺失进阶字段前置配置时应拒绝构建临时抽取计划")


async def test_temporary_extraction_uses_same_run_basic_value_without_db_writes(
    monkeypatch,
):
    base = _field("base", priority=1)
    advanced = _field(
        "advanced",
        priority=2,
        is_advanced=1,
        depend_fields=["base"],
    )
    plan = extraction_service.TemporaryExtractionPlan(
        file_id="f1",
        direct_field_ids=("advanced",),
        ordered_fields=(base, advanced),
        basic_count=1,
        snapshot=SimpleNamespace(page_contents={}),
    )
    unregister_calls = []

    async def fake_iter_group(
        _file_id,
        fields,
        _snapshot,
        field_values,
        _field_source_pages,
        _field_pages_from,
        _task_limit,
        _global_limit,
        start_index,
    ):
        field = fields[0]
        if field.field_id == "advanced":
            assert field_values == {"base": "本次基础值"}
            value = "本次进阶值"
        else:
            value = "本次基础值"
        yield extraction_service.FieldComputation(
            index=start_index + 1,
            field=field,
            success=True,
            value=value,
            reason="本次理由",
            source_refs=None,
            model_pages=[2],
        )

    monkeypatch.setattr(extraction_service, "_iter_field_group", fake_iter_group)
    monkeypatch.setattr(
        extraction_service,
        "unregister_task_limiter",
        lambda stage, file_id: unregister_calls.append((stage, file_id)),
    )

    items = [
        item
        async for item in extraction_service.iter_temporary_extraction_results(plan)
    ]

    assert [item["field_id"] for item in items] == ["base", "advanced"]
    assert [item["is_direct_dependency"] for item in items] == [False, True]
    assert items[1]["value"] == "本次进阶值"
    assert items[1]["source_pages"] == [2]
    assert unregister_calls == [("task_extraction", "f1")]


def _temporary_item(*, value="7", success=True):
    return {
        "field_id": "a",
        "field_name": "字段 a",
        "value": value,
        "reason": "本次理由",
        "pages": [2],
        "source_pages": [2],
        "source_refs": None,
        "success": success,
        "index": 1,
        "total": 1,
        "is_direct_dependency": True,
    }


async def _collect_rule_events(session, *, re_extract):
    return [
        event
        async for event in analysis_service.test_rule_analysis_stream(
            "f1",
            "calc",
            "<field_result>a</field_result> + 1",
            ["a"],
            "",
            session,
            re_extract=re_extract,
        )
    ]


async def test_rule_debug_reextract_uses_only_ephemeral_values(monkeypatch):
    plan = SimpleNamespace(
        ordered_fields=(SimpleNamespace(field_id="a"),),
        direct_field_ids=("a",),
    )

    async def fake_build(*_args, **_kwargs):
        return plan

    async def fake_iter(_plan):
        yield _temporary_item()

    monkeypatch.setattr(
        analysis_service, "build_temporary_extraction_plan", fake_build
    )
    monkeypatch.setattr(
        analysis_service, "iter_temporary_extraction_results", fake_iter
    )
    old_session = _QueuedSession(
        [_ScalarResult(rows=[SimpleNamespace(field_id="a", extracted_value="100")])]
    )

    events = await _collect_rule_events(old_session, re_extract=True)

    assert [event["event"] for event in events[:3]] == [
        "extraction_started",
        "extraction_field",
        "extraction_done",
    ]
    input_event = next(event for event in events if event["event"] == "input_values")
    assert input_event["data"]["input_values"] == {"a": "7"}
    resolved = next(
        event for event in events if event["event"] == "resolved_expression"
    )
    assert resolved["data"]["resolved_expression"] == "7 + 1"
    assert old_session._results, "重新抽取开启时不应查询数据库旧结果"


async def test_rule_debug_without_reextract_keeps_existing_event_sequence():
    session = _QueuedSession(
        [_ScalarResult(rows=[SimpleNamespace(field_id="a", extracted_value="5")])]
    )

    events = await _collect_rule_events(session, re_extract=False)

    assert events[0]["event"] == "input_values"
    assert all(not event["event"].startswith("extraction_") for event in events)


@pytest.mark.parametrize(
    ("item", "message_fragment"),
    [
        (_temporary_item(value="", success=False), "a"),
        (_temporary_item(value="", success=True), "a"),
    ],
)
async def test_rule_debug_reextract_stops_when_direct_field_invalid(
    monkeypatch,
    item,
    message_fragment,
):
    plan = SimpleNamespace(
        ordered_fields=(SimpleNamespace(field_id="a"),),
        direct_field_ids=("a",),
    )

    async def fake_build(*_args, **_kwargs):
        return plan

    async def fake_iter(_plan):
        yield item

    monkeypatch.setattr(
        analysis_service, "build_temporary_extraction_plan", fake_build
    )
    monkeypatch.setattr(
        analysis_service, "iter_temporary_extraction_results", fake_iter
    )

    events = await _collect_rule_events(_QueuedSession([]), re_extract=True)

    assert [event["event"] for event in events[:3]] == [
        "extraction_started",
        "extraction_field",
        "extraction_done",
    ]
    error = next(event for event in events if event["event"] == "error")
    assert message_fragment in error["data"]["message"]
    assert all(event["event"] != "resolved_expression" for event in events)


def _debug_session():
    """调试路由用的 session：先查文件行（取 type_id），再查该类型的入参清单。

    路由在调用 test_rule_analysis_stream 之前会解析入参（<param> 渲染 + required
    校验），这两次查询是它带来的；此处给一个无入参定义的文件。
    """
    return _QueuedSession([
        _ScalarResult(one=SimpleNamespace(file_id="f1", type_id="type-a")),
        _ScalarResult(rows=[]),
    ])


def _debug_request(*, re_extract=True):
    return AnalysisTestRequest(
        file_id="f1",
        config={
            "rule_type": "calc",
            "expression": "<field_result>a</field_result> + 1",
            "depend_fields": ["a"],
        },
        re_extract=re_extract,
    )


async def test_analysis_stream_router_passes_reextract_flag(monkeypatch):
    captured = {}

    async def fake_stream(*_args, **kwargs):
        captured["re_extract"] = kwargs["re_extract"]
        yield {"event": "done", "data": {}}

    monkeypatch.setattr(analysis_router, "test_rule_analysis_stream", fake_stream)

    response = await analysis_router.test_analysis_stream(
        _debug_request(), _debug_session()
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode() if isinstance(chunk, str) else chunk

    assert captured["re_extract"] is True
    assert b"event: done" in body


async def test_analysis_nonstream_router_collects_shared_stream(monkeypatch):
    captured = {}

    async def fake_stream(*_args, **kwargs):
        captured["re_extract"] = kwargs["re_extract"]
        yield {
            "event": "input_values",
            "data": {"input_values": {"a": "7"}},
        }
        yield {
            "event": "resolved_expression",
            "data": {"resolved_expression": "7 + 1"},
        }
        yield {
            "event": "result",
            "data": {"result_value": "8", "reason": "计算完成"},
        }
        yield {"event": "done", "data": {}}

    monkeypatch.setattr(analysis_router, "test_rule_analysis_stream", fake_stream)

    response = await analysis_router.test_analysis(_debug_request(), _debug_session())

    assert captured["re_extract"] is True
    assert response.data["input_values"] == {"a": "7"}
    assert response.data["expression_resolved"] == "7 + 1"
    assert response.data["result_value"] == "8"


async def test_analysis_nonstream_router_turns_stream_error_into_422(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield {"event": "error", "data": {"message": "字段 a 抽取失败"}}

    monkeypatch.setattr(analysis_router, "test_rule_analysis_stream", fake_stream)

    with pytest.raises(Exception) as exc_info:
        await analysis_router.test_analysis(_debug_request(), _debug_session())

    assert getattr(exc_info.value, "status_code", None) == 422
    assert "字段 a 抽取失败" in str(getattr(exc_info.value, "detail", ""))
