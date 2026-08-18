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
