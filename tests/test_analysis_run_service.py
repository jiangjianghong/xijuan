"""独立逻辑分析服务测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from service import analysis_run_service
from service.analysis_run_service import (
    AnalysisRuleSnapshot,
    plan_rules,
    select_covered_rules,
)


def _rule(
    rule_id: str,
    depend_fields: list[str],
    priority: int = 0,
    *,
    rule_type: str = "judge",
    expression: str = "<field_result>amount</field_result>",
) -> AnalysisRuleSnapshot:
    return AnalysisRuleSnapshot(
        rule_id=rule_id,
        type_id="contract",
        rule_name=rule_id,
        rule_type=rule_type,
        expression=expression,
        system_prompt="",
        depend_fields=depend_fields,
        web_search=None,
        priority=priority,
    )


def _orm_rule(
    rule_id: str,
    depend_fields: list[str],
    *,
    priority: int,
):
    return SimpleNamespace(
        rule_id=rule_id,
        type_id="contract",
        rule_name=rule_id,
        rule_type="judge",
        expression="<field_result>amount</field_result>",
        system_prompt="",
        depend_fields=depend_fields,
        web_search=None,
        priority=priority,
    )


def _success(rule: AnalysisRuleSnapshot) -> dict:
    return {
        "rule_id": rule.rule_id,
        "rule_name": rule.rule_name,
        "rule_type": rule.rule_type,
        "result": "true",
        "reason": "",
        "input_values": {},
        "source_refs": None,
        "success": True,
    }


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class ReadOnlySession:
    def __init__(self, rows):
        self.rows = rows
        self.execute_count = 0

    async def execute(self, statement):
        self.execute_count += 1
        return _Result(self.rows)

    def add(self, value):
        raise AssertionError("独立分析不得写数据库")

    async def commit(self):
        raise AssertionError("独立分析不得 commit")


def test_select_covered_rules_requires_all_declared_fields():
    rules = [
        _rule("amount_only", ["amount"], 1),
        _rule("amount_and_tax", ["amount", "tax"], 2),
    ]
    matched = select_covered_rules(rules, {"amount": "1200000"})
    assert [rule.rule_id for rule in matched] == ["amount_only"]


def test_empty_depend_fields_are_covered_by_definition():
    matched = select_covered_rules([_rule("global_rule", [])], {})
    assert [rule.rule_id for rule in matched] == ["global_rule"]


@pytest.mark.anyio
async def test_execute_calc_rule_reuses_calc_primitive(monkeypatch):
    async def fake_calc(expression: str, precision: int):
        assert expression == "120 / 30"
        return "4", "计算成功"

    monkeypatch.setattr(analysis_run_service, "execute_calc", fake_calc)
    result = await analysis_run_service.execute_rule(
        _rule(
            "ratio",
            ["amount", "tax"],
            rule_type="calc",
            expression=(
                "<field_result>amount</field_result> / "
                "<field_result>tax</field_result>"
            ),
        ),
        {"amount": "120", "tax": "30"},
    )
    assert result["result"] == "4"
    assert result["success"] is True
    assert result["input_values"] == {"amount": "120", "tax": "30"}


@pytest.mark.anyio
async def test_execute_rule_returns_validation_failure():
    result = await analysis_run_service.execute_rule(
        _rule("amount_only", ["amount"]),
        {"amount": ""},
    )
    assert result["success"] is False
    assert result["result"] == ""
    assert "均为空" in result["reason"]


@pytest.mark.anyio
async def test_execute_rule_converts_exception_to_failed_result(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("LLM 不可用")

    monkeypatch.setattr(analysis_run_service, "execute_judge", boom)
    result = await analysis_run_service.execute_rule(
        _rule("amount_only", ["amount"]),
        {"amount": "120"},
    )
    assert result["success"] is False
    assert result["reason"] == "RuntimeError: LLM 不可用"


@pytest.mark.anyio
async def test_run_analysis_batch_loads_once_and_keeps_item_order(monkeypatch):
    session = ReadOnlySession([_orm_rule("amount_check", ["amount"], priority=1)])
    events = []

    async def fake_execute(rule, field_values, *, require_coverage=False):
        return {
            **_success(rule),
            "result": field_values["amount"],
            "input_values": {"amount": field_values["amount"]},
        }

    async def record(event):
        events.append(event)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    data = await analysis_run_service.run_analysis_batch(
        [
            {"type_id": "contract", "biz_id": "b0", "field_values": {"amount": "120"}},
            {"type_id": "contract", "biz_id": "b1", "field_values": {"amount": "90"}},
        ],
        session,
        on_rule_done=record,
    )

    assert session.execute_count == 1
    assert [item["biz_id"] for item in data["items"]] == ["b0", "b1"]
    assert {event["item_index"] for event in events} == {0, 1}
    assert all(event["index"] == 1 and event["total"] == 1 for event in events)


@pytest.mark.anyio
async def test_run_analysis_batch_orders_and_skips_uncovered(monkeypatch):
    rows = [
        _orm_rule("second", ["amount"], priority=20),
        _orm_rule("uncovered", ["amount", "tax"], priority=5),
        _orm_rule("first", ["amount"], priority=10),
    ]
    called = []

    async def fake_execute(rule, field_values, *, require_coverage=False):
        called.append(rule.rule_id)
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    data = await analysis_run_service.run_analysis_batch(
        [{"type_id": "contract", "biz_id": "b0", "field_values": {"amount": "120"}}],
        ReadOnlySession(rows),
    )

    assert called == ["first", "second"]
    assert data["items"][0]["total"] == 2
    assert [row["index"] for row in data["items"][0]["results"]] == [1, 2]


@pytest.mark.anyio
async def test_run_analysis_batch_returns_empty_item_when_no_rule_is_covered():
    data = await analysis_run_service.run_analysis_batch(
        [{"type_id": "contract", "biz_id": "b0", "field_values": {"name": "A"}}],
        ReadOnlySession([_orm_rule("amount_check", ["amount"], priority=1)]),
    )
    assert data["items"][0] == {
        "item_index": 0,
        "biz_id": "b0",
        "type_id": "contract",
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "results": [],
        "unknown_rule_ids": [],
        "error": None,
    }


def _custom_rule(rule_id, depend_fields, *, is_formatted=0, output_schema=None):
    return AnalysisRuleSnapshot(
        rule_id=rule_id,
        type_id="contract",
        rule_name=rule_id,
        rule_type="custom",
        expression="根据<field_result>amount</field_result>生成",
        system_prompt="",
        depend_fields=depend_fields,
        web_search=None,
        priority=0,
        is_formatted=is_formatted,
        output_schema=output_schema,
    )


@pytest.mark.anyio
async def test_execute_rule_dispatches_custom(monkeypatch):
    captured = {}

    async def fake_custom(resolved, *, is_formatted, output_schema, system_prompt):
        captured["is_formatted"] = is_formatted
        captured["output_schema"] = output_schema
        return "生成值", "理由"

    monkeypatch.setattr(analysis_run_service, "execute_custom", fake_custom)
    schema = [{"key": "k", "type": "string"}]
    result = await analysis_run_service.execute_rule(
        _custom_rule("c1", ["amount"], is_formatted=1, output_schema=schema),
        {"amount": "120"},
    )
    assert result["success"] is True
    assert result["result"] == "生成值"
    assert captured["is_formatted"] is True
    assert captured["output_schema"] == schema


@pytest.mark.anyio
async def test_snapshot_from_orm_reads_custom_fields():
    orm = SimpleNamespace(
        rule_id="c1", type_id="contract", rule_name="c1", rule_type="custom",
        expression="<field_result>amount</field_result>", system_prompt="",
        depend_fields=["amount"], web_search=None, priority=0,
        is_formatted=1, output_schema=[{"key": "k", "type": "string"}],
    )
    snap = AnalysisRuleSnapshot.from_orm(orm)
    assert snap.is_formatted == 1
    assert snap.output_schema == [{"key": "k", "type": "string"}]


# ── item 级 rule_ids 筛选 ────────────────────────────────────


def test_plan_rules_without_rule_ids_keeps_implicit_coverage_filter():
    """不传 rule_ids 时沿用旧行为：依赖未被覆盖的规则静默跳过。"""
    rules = [
        _rule("amount_only", ["amount"], 1),
        _rule("amount_and_tax", ["amount", "tax"], 2),
    ]
    plan = plan_rules(rules, {"amount": "1200000"}, None)
    assert [rule.rule_id for rule in plan.rules] == ["amount_only"]
    assert plan.unknown_rule_ids == []
    assert plan.require_coverage is False


def test_plan_rules_empty_rule_ids_runs_nothing():
    """空数组表示显式不跑任何规则，与 CopyConfigsRequest 约定一致。"""
    plan = plan_rules([_rule("amount_only", ["amount"], 1)], {"amount": "1"}, [])
    assert plan.rules == []
    assert plan.unknown_rule_ids == []


def test_plan_rules_selects_only_named_rules_in_priority_order():
    rules = [
        _rule("third", ["amount"], 30),
        _rule("first", ["amount"], 10),
        _rule("second", ["amount"], 20),
    ]
    plan = plan_rules(rules, {"amount": "1"}, ["third", "first"])
    assert [rule.rule_id for rule in plan.rules] == ["first", "third"]
    assert plan.require_coverage is True


def test_plan_rules_reports_unknown_rule_ids_in_caller_order():
    plan = plan_rules(
        [_rule("known", ["amount"], 1)],
        {"amount": "1"},
        ["ghost_b", "known", "ghost_a", "ghost_b"],
    )
    assert [rule.rule_id for rule in plan.rules] == ["known"]
    assert plan.unknown_rule_ids == ["ghost_b", "ghost_a"]


def test_plan_rules_keeps_named_rule_whose_depend_fields_are_missing():
    """显式点名的规则不做覆盖过滤，交由 execute_rule 产出 failed 结果。"""
    plan = plan_rules(
        [_rule("amount_and_tax", ["amount", "tax"], 1)],
        {"amount": "1200000"},
        ["amount_and_tax"],
    )
    assert [rule.rule_id for rule in plan.rules] == ["amount_and_tax"]


@pytest.mark.anyio
async def test_execute_rule_reports_missing_depend_fields_when_coverage_required():
    result = await analysis_run_service.execute_rule(
        _rule("amount_and_tax", ["amount", "tax"]),
        {"amount": "1200000"},
        require_coverage=True,
    )
    assert result["success"] is False
    assert result["result"] == ""
    assert "tax" in result["reason"]
    assert "缺少依赖字段" in result["reason"]


@pytest.mark.anyio
async def test_execute_rule_skips_coverage_check_by_default(monkeypatch):
    """不要求覆盖时，缺键仍走既有 validate_field_values 逻辑。"""
    async def fake_judge(resolved, *, system_prompt):
        return "true", "命中"

    monkeypatch.setattr(analysis_run_service, "execute_judge", fake_judge)
    result = await analysis_run_service.execute_rule(
        _rule("amount_and_tax", ["amount", "tax"]),
        {"amount": "1200000"},
    )
    assert result["success"] is True


@pytest.mark.anyio
async def test_run_analysis_batch_honours_item_rule_ids(monkeypatch):
    rows = [
        _orm_rule("wanted", ["amount"], priority=20),
        _orm_rule("ignored", ["amount"], priority=10),
    ]
    called = []

    async def fake_execute(rule, field_values, *, require_coverage=False):
        called.append(rule.rule_id)
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    data = await analysis_run_service.run_analysis_batch(
        [{
            "type_id": "contract",
            "biz_id": "b0",
            "field_values": {"amount": "120"},
            "rule_ids": ["wanted"],
        }],
        ReadOnlySession(rows),
    )

    assert called == ["wanted"]
    assert data["items"][0]["total"] == 1
    assert data["items"][0]["unknown_rule_ids"] == []


@pytest.mark.anyio
async def test_run_analysis_batch_returns_unknown_rule_ids():
    data = await analysis_run_service.run_analysis_batch(
        [{
            "type_id": "contract",
            "biz_id": "b0",
            "field_values": {"amount": "120"},
            "rule_ids": ["ghost"],
        }],
        ReadOnlySession([_orm_rule("amount_check", ["amount"], priority=1)]),
    )
    item = data["items"][0]
    assert item["total"] == 0
    assert item["results"] == []
    assert item["unknown_rule_ids"] == ["ghost"]


@pytest.mark.anyio
async def test_run_analysis_batch_counts_named_rule_missing_fields_as_failed():
    """显式点名但缺依赖字段：计入 total/failed，而不是从 results 里消失。"""
    data = await analysis_run_service.run_analysis_batch(
        [{
            "type_id": "contract",
            "biz_id": "b0",
            "field_values": {"amount": "120"},
            "rule_ids": ["amount_and_tax"],
        }],
        ReadOnlySession([_orm_rule("amount_and_tax", ["amount", "tax"], priority=1)]),
    )
    item = data["items"][0]
    assert item["total"] == 1
    assert item["failed"] == 1
    assert item["results"][0]["rule_id"] == "amount_and_tax"
    assert "缺少依赖字段" in item["results"][0]["reason"]


@pytest.mark.anyio
async def test_run_analysis_batch_defaults_unknown_rule_ids_without_rule_ids():
    data = await analysis_run_service.run_analysis_batch(
        [{"type_id": "contract", "biz_id": "b0", "field_values": {"name": "A"}}],
        ReadOnlySession([_orm_rule("amount_check", ["amount"], priority=1)]),
    )
    assert data["items"][0]["unknown_rule_ids"] == []


# ── file 模式：并发前批量加载 ─────────────────────────────────


def _orm_file(file_id: str, type_id: str = "contract"):
    return SimpleNamespace(file_id=file_id, type_id=type_id)


def _orm_extraction(file_id: str, field_id: str, value: str, source_refs=None):
    return SimpleNamespace(
        file_id=file_id,
        field_id=field_id,
        extracted_value=value,
        source_refs=source_refs,
    )


class MultiQuerySession:
    """按调用顺序返回预置结果集，用于断言「只查了 N 次」。"""

    def __init__(self, batches):
        self._batches = list(batches)
        self.execute_count = 0

    async def execute(self, statement):
        self.execute_count += 1
        rows = self._batches.pop(0) if self._batches else []
        return _Result(rows)

    def add(self, value):
        raise AssertionError("加载阶段不得写数据库")

    async def commit(self):
        raise AssertionError("加载阶段不得 commit")


@pytest.mark.anyio
async def test_load_file_snapshots_reads_files_and_extractions_in_two_queries():
    session = MultiQuerySession([
        [_orm_file("f1", "contract"), _orm_file("f2", "invoice")],
        [
            _orm_extraction("f1", "amount", "120", {"金额": [{"page_num": "1"}]}),
            _orm_extraction("f1", "tax", "30"),
            _orm_extraction("f2", "amount", "999"),
        ],
    ])

    snapshots = await analysis_run_service.load_file_snapshots({"f1", "f2"}, session)

    assert session.execute_count == 2
    assert snapshots["f1"].type_id == "contract"
    assert snapshots["f1"].field_values == {"amount": "120", "tax": "30"}
    # 键是 field_id，值是该字段完整的 source_refs（键为检索 label）
    assert snapshots["f1"].field_source_refs == {"amount": {"金额": [{"page_num": "1"}]}}
    assert snapshots["f2"].field_values == {"amount": "999"}
    assert snapshots["f2"].field_source_refs == {}


@pytest.mark.anyio
async def test_load_file_snapshots_omits_missing_files():
    session = MultiQuerySession([[], []])
    snapshots = await analysis_run_service.load_file_snapshots({"ghost"}, session)
    assert snapshots == {}


@pytest.mark.anyio
async def test_load_file_snapshots_defaults_null_type_id():
    session = MultiQuerySession([[_orm_file("f1", None)], []])
    snapshots = await analysis_run_service.load_file_snapshots({"f1"}, session)
    assert snapshots["f1"].type_id == "default"
    assert snapshots["f1"].field_values == {}


@pytest.mark.anyio
async def test_load_file_snapshots_skips_query_when_no_file_ids():
    session = MultiQuerySession([])
    assert await analysis_run_service.load_file_snapshots(set(), session) == {}
    assert session.execute_count == 0


# ── file 模式：端到端 ────────────────────────────────────────


class FileModeSession:
    """按 run_analysis_batch 的实际查询顺序返回结果集。

    file 模式下查询顺序是 files -> extraction_results -> rules
    （load_file_snapshots 先跑，_load_rules_by_type 后跑），
    persist 阶段的「查已有结果」是第 4 次及以后。
    """

    def __init__(self, rules, files, extractions):
        self._batches = [files, extractions, rules]
        self.execute_count = 0
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        self.execute_count += 1
        rows = self._batches.pop(0) if self._batches else []
        return _Result(rows)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


@pytest.mark.anyio
async def test_run_analysis_batch_file_source_uses_db_values(monkeypatch):
    captured = {}

    async def fake_execute(rule, field_values, *, require_coverage=False):
        captured["field_values"] = dict(field_values)
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[_orm_file("f1", "contract")],
        extractions=[_orm_extraction("f1", "amount", "120")],
    )

    data = await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1"}],
        session,
        source="file",
    )

    assert captured["field_values"] == {"amount": "120"}
    item = data["items"][0]
    assert item["type_id"] == "contract"
    assert item["total"] == 1
    assert item["error"] is None


@pytest.mark.anyio
async def test_run_analysis_batch_file_source_merges_field_source_refs(monkeypatch):
    async def fake_execute(rule, field_values, *, require_coverage=False):
        return {**_success(rule), "source_refs": {"_web_search": {"query": "q"}}}

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[_orm_file("f1")],
        extractions=[
            _orm_extraction("f1", "amount", "120", {"bboxes": [{"page_num": "2"}]}),
        ],
    )

    data = await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1"}], session, source="file",
    )

    refs = data["items"][0]["results"][0]["source_refs"]
    assert refs["amount"] == {"bboxes": [{"page_num": "2"}]}
    assert refs["_web_search"] == {"query": "q"}


@pytest.mark.anyio
async def test_run_analysis_batch_file_source_reports_missing_file():
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[],
        extractions=[],
    )
    data = await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "ghost"}], session, source="file",
    )
    item = data["items"][0]
    assert item["total"] == 0
    assert item["results"] == []
    assert "文件不存在" in item["error"]


@pytest.mark.anyio
async def test_run_analysis_batch_file_source_rejects_type_id_mismatch():
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[_orm_file("f1", "contract")],
        extractions=[_orm_extraction("f1", "amount", "120")],
    )
    data = await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1", "type_id": "invoice"}],
        session,
        source="file",
    )
    item = data["items"][0]
    assert item["total"] == 0
    assert "type_id 与文件不一致" in item["error"]


@pytest.mark.anyio
async def test_run_analysis_batch_file_source_reports_empty_extraction():
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[_orm_file("f1")],
        extractions=[],
    )
    data = await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1"}], session, source="file",
    )
    item = data["items"][0]
    assert item["total"] == 0
    assert "无提取结果" in item["error"]


@pytest.mark.anyio
async def test_run_analysis_batch_file_source_honours_rule_ids(monkeypatch):
    """file 模式与 rule_ids 点名可组合：只重跑指定规则。"""
    called = []

    async def fake_execute(rule, field_values, *, require_coverage=False):
        called.append(rule.rule_id)
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    session = FileModeSession(
        rules=[
            _orm_rule("wanted", ["amount"], priority=10),
            _orm_rule("ignored", ["amount"], priority=20),
        ],
        files=[_orm_file("f1")],
        extractions=[_orm_extraction("f1", "amount", "120")],
    )

    data = await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1", "rule_ids": ["wanted"]}],
        session,
        source="file",
    )

    assert called == ["wanted"]
    assert data["items"][0]["total"] == 1


@pytest.mark.anyio
async def test_values_source_keeps_error_none_and_skips_file_queries(monkeypatch):
    async def fake_execute(rule, field_values, *, require_coverage=False):
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    session = ReadOnlySession([_orm_rule("amount_check", ["amount"], priority=1)])

    data = await analysis_run_service.run_analysis_batch(
        [{"type_id": "contract", "biz_id": "b0", "field_values": {"amount": "1"}}],
        session,
    )

    assert session.execute_count == 1  # 只查规则，不查 files/extraction
    assert data["items"][0]["error"] is None


# ── persist 落库 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_persist_inserts_new_analysis_results(monkeypatch):
    async def fake_execute(rule, field_values, *, require_coverage=False):
        return {**_success(rule), "result": "true", "reason": "命中"}

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[_orm_file("f1")],
        extractions=[_orm_extraction("f1", "amount", "120")],
    )
    # persist 阶段会再查一次已有结果：返回空表示需要 insert
    session._batches.append([])

    await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1"}],
        session,
        source="file",
        persist=True,
    )

    assert len(session.added) == 1
    row = session.added[0]
    assert row.file_id == "f1"
    assert row.rule_id == "amount_check"
    assert row.result_value == "true"
    assert session.commits >= 1


@pytest.mark.anyio
async def test_persist_updates_existing_analysis_result(monkeypatch):
    async def fake_execute(rule, field_values, *, require_coverage=False):
        return {**_success(rule), "result": "false", "reason": "新理由"}

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    existing = SimpleNamespace(
        file_id="f1", rule_id="amount_check", result_value="true",
        input_values={}, reason="老理由", source_refs=None,
    )
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[_orm_file("f1")],
        extractions=[_orm_extraction("f1", "amount", "120")],
    )
    session._batches.append([existing])

    await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "f1"}],
        session,
        source="file",
        persist=True,
    )

    assert session.added == []
    assert existing.result_value == "false"
    assert existing.reason == "新理由"


@pytest.mark.anyio
async def test_persist_skips_errored_items():
    session = FileModeSession(
        rules=[_orm_rule("amount_check", ["amount"], priority=1)],
        files=[],
        extractions=[],
    )
    await analysis_run_service.run_analysis_batch(
        [{"biz_id": "b0", "file_id": "ghost"}],
        session,
        source="file",
        persist=True,
    )
    assert session.added == []


@pytest.mark.anyio
async def test_persist_ignored_when_source_is_values(monkeypatch):
    """values 模式没有 file_id，不可能落库；即便传了 persist 也不写。"""
    async def fake_execute(rule, field_values, *, require_coverage=False):
        return _success(rule)

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    session = ReadOnlySession([_orm_rule("amount_check", ["amount"], priority=1)])

    # ReadOnlySession.add / commit 会 raise，故此处不抛异常即证明没写库
    await analysis_run_service.run_analysis_batch(
        [{"type_id": "contract", "biz_id": "b0", "field_values": {"amount": "1"}}],
        session,
        persist=True,
    )
