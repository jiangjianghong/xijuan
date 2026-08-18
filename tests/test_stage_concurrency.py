from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from service import table_service
from service import analysis_run_service
from utils import concurrency
from utils.config import AppConfig, get_config, replace_config


@pytest.fixture
def stage_config():
    previous = get_config()
    replace_config(
        AppConfig(
            extraction={"base_url": "http://llm.test/v1", "model": "llm"},
            concurrency={
                "global_table_validation": 1,
                "task_table_validation": 3,
                "global_analysis": 2,
                "task_file_analysis": 4,
                "independent_analysis": 4,
            },
            analysis={"calc_precision": 2},
        )
    )
    concurrency.clear_limiters()
    yield
    replace_config(previous)
    concurrency.clear_limiters()


@pytest.mark.asyncio
async def test_table_validation_global_limit_spans_files(monkeypatch, stage_config):
    active = 0
    peak = 0

    async def fake_validate(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "表格"

    monkeypatch.setattr(table_service, "_extract_table_name_with_llm", fake_validate)
    content = "\n".join(f"标题{i}\n<table><tr><td>{i}</td></tr></table>" for i in range(3))
    await asyncio.gather(
        table_service.parse_tables(content, "f1"),
        table_service.parse_tables(content, "f2"),
    )

    assert peak == 1


@pytest.mark.asyncio
async def test_analysis_global_limit_spans_batch_items(monkeypatch, stage_config):
    active = 0
    peak = 0

    async def fake_execute(rule, field_values, *, require_coverage=False):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "rule_id": rule.rule_id,
            "rule_name": rule.rule_name,
            "rule_type": rule.rule_type,
            "result": "true",
            "reason": "ok",
            "input_values": {},
            "source_refs": None,
            "success": True,
        }

    monkeypatch.setattr(analysis_run_service, "execute_rule", fake_execute)
    rules = [
        SimpleNamespace(
            rule_id=f"r{i}",
            rule_name=f"r{i}",
            rule_type="judge",
            depend_fields=[],
            priority=i,
            enabled=True,
            expression="",
            web_search=None,
            system_prompt="",
            is_formatted=False,
            output_schema=None,
        )
        for i in range(2)
    ]
    async def fake_load_rules(type_ids, session):
        return _resolved_rules(type_ids, rules)

    monkeypatch.setattr(analysis_run_service, "_load_rules_by_type", fake_load_rules)

    items = [
        {"type_id": "default", "biz_id": f"b{i}", "field_values": {}}
        for i in range(4)
    ]
    await analysis_run_service.run_analysis_batch(items, object())

    assert peak == 2


def _resolved_rules(type_ids, rules):
    return {type_id: rules for type_id in type_ids}
