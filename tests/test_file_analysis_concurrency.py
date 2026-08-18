from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from service import analysis_service
from service.analysis_service import FileRuleComputation, FileRuleSnapshot
from utils import concurrency


@pytest.fixture(autouse=True)
def clear_runtime_limiters():
    concurrency.clear_limiters()
    yield
    concurrency.clear_limiters()


def _install_limits(monkeypatch, *, file_limit: int, total_limit: int):
    monkeypatch.setattr(
        analysis_service,
        "get_config",
        lambda: SimpleNamespace(
            analysis=SimpleNamespace(calc_precision=2),
            concurrency=SimpleNamespace(
                task_file_analysis=file_limit,
                global_analysis=total_limit,
            ),
        ),
    )


def _rule(rule_id: str, *, rule_type: str = "judge") -> FileRuleSnapshot:
    return FileRuleSnapshot(
        rule_id=rule_id,
        rule_name=rule_id,
        rule_type=rule_type,
        depend_fields=(),
        expression=rule_id,
        web_search=None,
        system_prompt="",
        is_formatted=False,
        output_schema=None,
    )


def _computed(
    rule: FileRuleSnapshot,
    *,
    result: str = "true",
    success: bool = True,
    reason: str = "",
) -> FileRuleComputation:
    return FileRuleComputation(
        rule_id=rule.rule_id,
        rule_name=rule.rule_name,
        rule_type=rule.rule_type,
        result=result,
        reason=reason,
        input_values={},
        source_refs=None,
        success=success,
    )


class _FinalSession:
    def __init__(self):
        self.execute_calls = 0
        self.commit_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1

    async def commit(self):
        self.commit_calls += 1


@pytest.mark.asyncio
async def test_file_rules_obey_per_file_limit_and_keep_input_order(monkeypatch):
    _install_limits(monkeypatch, file_limit=2, total_limit=10)
    active = 0
    peak = 0

    async def fake_compute(rule, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep({"r1": .03, "r2": .02, "r3": .01}[rule.rule_id])
        active -= 1
        return _computed(rule, result=rule.rule_id)

    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    rules = [_rule("r1"), _rule("r2"), _rule("r3")]

    results = await analysis_service._compute_file_rules("f1", rules, {}, {}, 2)

    assert peak == 2
    assert [result.rule_id for result in results] == ["r1", "r2", "r3"]
    assert "task_file_analysis" not in concurrency.runtime_snapshot()["task_pools"]


@pytest.mark.asyncio
async def test_different_files_have_independent_file_limits(monkeypatch):
    _install_limits(monkeypatch, file_limit=1, total_limit=10)
    active = 0
    peak = 0

    async def fake_compute(rule, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1
        return _computed(rule)

    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    await asyncio.gather(
        analysis_service._compute_file_rules("f1", [_rule("a")], {}, {}, 2),
        analysis_service._compute_file_rules("f2", [_rule("b")], {}, {}, 2),
    )

    assert peak == 2


@pytest.mark.asyncio
async def test_file_rule_failure_does_not_cancel_siblings(monkeypatch):
    _install_limits(monkeypatch, file_limit=3, total_limit=3)

    async def fake_judge(expression, *, system_prompt=""):
        if expression == "r2":
            raise RuntimeError("r2 failed")
        await asyncio.sleep(.01)
        return "true", expression

    monkeypatch.setattr(analysis_service, "execute_judge", fake_judge)
    results = await analysis_service._compute_file_rules(
        "f1", [_rule("r1"), _rule("r2"), _rule("r3")], {}, {}, 2
    )

    assert [row.rule_id for row in results] == ["r1", "r2", "r3"]
    assert [row.success for row in results] == [True, False, True]
    assert results[1].reason == "r2 failed"


@pytest.mark.asyncio
async def test_file_rule_cancellation_awaits_tasks_and_unregisters_pool(monkeypatch):
    _install_limits(monkeypatch, file_limit=2, total_limit=2)
    two_started = asyncio.Event()
    active = 0
    cancelled = 0

    async def blocking_compute(rule, *args, **kwargs):
        nonlocal active, cancelled
        active += 1
        if active == 2:
            two_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise
        finally:
            active -= 1

    monkeypatch.setattr(analysis_service, "_compute_file_rule", blocking_compute)
    running = asyncio.create_task(
        analysis_service._compute_file_rules(
            "f1", [_rule("r1"), _rule("r2"), _rule("r3")], {}, {}, 2
        )
    )
    await asyncio.wait_for(two_started.wait(), timeout=1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert active == 0
    assert cancelled == 2
    assert "task_file_analysis" not in concurrency.runtime_snapshot()["task_pools"]


@pytest.mark.asyncio
async def test_run_analysis_persists_and_callbacks_in_rule_order(monkeypatch):
    _install_limits(monkeypatch, file_limit=3, total_limit=3)
    rules = [_rule("r1"), _rule("r2"), _rule("r3")]
    persisted = []
    callbacks = []
    compute_active = 0

    async def fake_load(file_id, session):
        return rules, {}, {}

    async def fake_compute(rule, *args, **kwargs):
        nonlocal compute_active
        compute_active += 1
        await asyncio.sleep({"r1": .03, "r2": .02, "r3": .01}[rule.rule_id])
        compute_active -= 1
        return _computed(rule, result=rule.rule_id)

    async def fake_persist(file_id, item, session):
        assert compute_active == 0
        persisted.append(item.rule_id)
        await asyncio.sleep(0)

    async def fake_callback(url, file_id, status, *, event=None, data=None):
        if event == "rule_done":
            callbacks.append(data["rule_id"])

    monkeypatch.setattr(
        analysis_service,
        "_load_file_analysis_context",
        fake_load,
        raising=False,
    )
    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    monkeypatch.setattr(
        analysis_service,
        "_persist_file_computation",
        fake_persist,
        raising=False,
    )
    monkeypatch.setattr(analysis_service, "notify_callback", fake_callback)
    session = _FinalSession()

    await analysis_service.run_analysis("f1", session, "http://callback.test")

    assert persisted == ["r1", "r2", "r3"]
    assert callbacks == ["r1", "r2", "r3"]
    assert session.execute_calls == 1
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_run_analysis_stream_yields_in_rule_order(monkeypatch):
    _install_limits(monkeypatch, file_limit=3, total_limit=3)
    rules = [_rule("r1"), _rule("r2"), _rule("r3")]
    persisted = []

    async def fake_load(file_id, session):
        return rules, {}, {}

    async def fake_compute(rule, *args, **kwargs):
        await asyncio.sleep({"r1": .03, "r2": .02, "r3": .01}[rule.rule_id])
        return _computed(rule, result=rule.rule_id)

    async def fake_persist(file_id, item, session):
        persisted.append(item.rule_id)

    monkeypatch.setattr(
        analysis_service,
        "_load_file_analysis_context",
        fake_load,
        raising=False,
    )
    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_compute)
    monkeypatch.setattr(
        analysis_service,
        "_persist_file_computation",
        fake_persist,
        raising=False,
    )
    rows = [
        row
        async for row in analysis_service.run_analysis_stream("f1", _FinalSession())
    ]

    assert [row["rule_id"] for row in rows] == ["r1", "r2", "r3"]
    assert [row["current"] for row in rows] == [1, 2, 3]
    assert persisted == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_file_and_independent_rules_share_global_analysis(monkeypatch):
    from service import analysis_run_service

    limits = SimpleNamespace(
        task_file_analysis=4,
        independent_analysis=4,
        global_analysis=2,
    )
    config = SimpleNamespace(
        analysis=SimpleNamespace(calc_precision=2),
        concurrency=limits,
    )
    monkeypatch.setattr(analysis_service, "get_config", lambda: config)
    monkeypatch.setattr(analysis_run_service, "get_config", lambda: config)
    active = 0
    peak = 0

    async def probe():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1

    async def fake_file_compute(rule, *args, **kwargs):
        await probe()
        return _computed(rule)

    independent_rule = analysis_run_service.AnalysisRuleSnapshot(
        rule_id="independent",
        type_id="contract",
        rule_name="independent",
        rule_type="judge",
        expression="",
        system_prompt="",
        depend_fields=[],
        web_search=None,
        priority=1,
        enabled=1,
    )

    async def fake_load_rules(type_ids, session):
        return {"contract": [independent_rule]}

    async def fake_independent_execute(rule, field_values, *, require_coverage=False):
        await probe()
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

    monkeypatch.setattr(analysis_service, "_compute_file_rule", fake_file_compute)
    monkeypatch.setattr(analysis_run_service, "_load_rules_by_type", fake_load_rules)
    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_independent_execute)
    await asyncio.gather(
        analysis_service._compute_file_rules(
            "f1", [_rule("f1"), _rule("f2"), _rule("f3")], {}, {}, 2
        ),
        analysis_run_service.run_analysis_batch(
            [
                {"type_id": "contract", "biz_id": f"b{i}", "field_values": {}}
                for i in range(3)
            ],
            object(),
        ),
    )

    assert peak == 2
