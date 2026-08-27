"""逻辑分析调试临时重新抽取测试。"""

from types import SimpleNamespace

from model.schemas import AnalysisTestRequest
from model.tables import ExtractionField, File
from service import extraction_service


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
