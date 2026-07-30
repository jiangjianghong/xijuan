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
