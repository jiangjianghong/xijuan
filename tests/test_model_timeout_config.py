"""模型调用超时的配置化与透传。

覆盖三件事：
1. 分析阶段（judge + custom，正式与调试）用 analysis.judge_timeout，不再落到
   extraction.timeout；
2. 抽取阶段（含调试抽取）继续用 extraction.timeout，即不显式传 timeout；
3. 异步回调 POST 超时改由 callback.timeout 配置驱动，可热更新。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from service import analysis_service, settings_service
from utils import callback
from utils.config import AnalysisConfig, AppConfig, CallbackConfig, get_config, replace_config, reset_config


@pytest.fixture
def override_config():
    """按组覆盖进程内配置快照，测试结束后还原为磁盘配置。"""
    original = get_config()

    def _apply(**groups):
        data = original.model_dump()
        for group, fields in groups.items():
            data[group].update(fields)
        replace_config(AppConfig(**data))

    yield _apply
    reset_config()


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """只支撑调试流读取 extraction_result 的最小替身。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, statement):
        return _FakeScalarResult(self._rows)


def _record_chat_completion(monkeypatch, response: str):
    """把 chat_completion 换成记录 kwargs 的替身，返回记录列表。"""
    calls = []

    async def fake_chat_completion(prompt, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(analysis_service, "chat_completion", fake_chat_completion)
    return calls


# ── 配置默认值 ────────────────────────────────────────────────


def test_judge_timeout_default_matches_previous_effective_value():
    """默认值必须是 60，与生效前实际使用的 extraction.timeout 一致。"""
    assert AnalysisConfig().judge_timeout == 60


def test_callback_config_default_timeout_is_previous_hardcoded_value():
    assert CallbackConfig().timeout == 2.5


def test_callback_group_is_hot_configurable():
    """回调超时要能在设置页改，故必须属于开放配置组。"""
    assert "callback" in settings_service.OPEN_GROUPS
    assert "callback" in AppConfig.model_fields


# ── 分析：正式路径 ────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("system_prompt", ["", "你是审核助手"])
async def test_execute_judge_uses_judge_timeout(monkeypatch, override_config, system_prompt):
    override_config(analysis={"judge_timeout": 7}, extraction={"timeout": 99})
    calls = _record_chat_completion(monkeypatch, '{"result": "true", "reason": "ok"}')

    result, _ = await analysis_service.execute_judge(
        "净利润是否为正？", system_prompt=system_prompt
    )

    assert result == "true"
    assert calls[0]["timeout"] == 7


@pytest.mark.anyio
@pytest.mark.parametrize("system_prompt", ["", "你是写作助手"])
async def test_execute_custom_uses_judge_timeout(monkeypatch, override_config, system_prompt):
    override_config(analysis={"judge_timeout": 11}, extraction={"timeout": 99})
    calls = _record_chat_completion(monkeypatch, '{"value": "摘要", "reason": "ok"}')

    value, _ = await analysis_service.execute_custom(
        "总结要点", system_prompt=system_prompt
    )

    assert value == "摘要"
    assert calls[0]["timeout"] == 11


# ── 分析：调试路径 ────────────────────────────────────────────


async def _drive_debug_stream(rule_type: str, response_json: str, monkeypatch):
    """驱动调试流到底，返回 (chat_completion kwargs 列表, 事件列表)。"""
    calls = _record_chat_completion(monkeypatch, response_json)
    session = _FakeSession(
        [SimpleNamespace(field_id="f1", extracted_value="1200")]
    )
    events = []
    async for event in analysis_service.test_rule_analysis_stream(
        file_id="file-1",
        rule_type=rule_type,
        expression="净利润 <field_result>f1</field_result> 是否为正？",
        depend_fields=["f1"],
        system_prompt="",
        session=session,
    ):
        events.append(event)
    return calls, events


@pytest.mark.anyio
async def test_debug_judge_stream_uses_judge_timeout(monkeypatch, override_config):
    override_config(analysis={"judge_timeout": 13}, extraction={"timeout": 99})

    calls, events = await _drive_debug_stream(
        "judge", '{"result": "true", "reason": "ok"}', monkeypatch
    )

    assert [e for e in events if e["event"] == "error"] == []
    assert calls[0]["timeout"] == 13


@pytest.mark.anyio
async def test_debug_custom_stream_uses_judge_timeout(monkeypatch, override_config):
    override_config(analysis={"judge_timeout": 17}, extraction={"timeout": 99})

    calls, events = await _drive_debug_stream(
        "custom", '{"value": "摘要", "reason": "ok"}', monkeypatch
    )

    assert [e for e in events if e["event"] == "error"] == []
    assert calls[0]["timeout"] == 17


# ── 抽取：保持走 extraction.timeout ──────────────────────────


def _chat_completion_calls(module) -> list:
    """取出模块里全部 chat_completion 调用节点。"""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "chat_completion"
    ]


def _kwarg_names(call) -> set:
    return {kw.arg for kw in call.keywords}


def test_extraction_llm_calls_never_pin_timeout():
    """抽取与调试抽取都不得显式传 timeout，才能继续吃 extraction.timeout。"""
    from service import extraction_service

    calls = _chat_completion_calls(extraction_service)
    assert calls, "没找到 chat_completion 调用，契约测试失去意义"
    for call in calls:
        assert "timeout" not in _kwarg_names(call), (
            f"extraction_service.py:{call.lineno} 显式传了 timeout，"
            "抽取应继续使用 extraction.timeout 默认值"
        )


def test_every_analysis_llm_call_pins_a_timeout():
    """分析侧每个 LLM 调用点都必须显式传超时，避免漏改某一处。"""
    calls = _chat_completion_calls(analysis_service)
    assert calls, "没找到 chat_completion 调用，契约测试失去意义"
    for call in calls:
        assert "timeout" in _kwarg_names(call), (
            f"analysis_service.py:{call.lineno} 未传 timeout，"
            "会静默回落到 extraction.timeout"
        )


# ── 异步回调 ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_notify_callback_uses_configured_timeout(monkeypatch, override_config):
    override_config(callback={"timeout": 9.5})
    seen = []

    async def fake_post(url, payload, *, timeout):
        seen.append(timeout)

    monkeypatch.setattr(callback, "_post_callback_payload", fake_post)
    await callback.notify_callback("http://cb.local", "file-1", "analyzing")

    assert seen == [9.5]


@pytest.mark.anyio
async def test_notify_analysis_task_callback_uses_configured_timeout(
    monkeypatch, override_config
):
    override_config(callback={"timeout": 4.25})
    seen = []

    async def fake_post(url, payload, *, timeout):
        seen.append(timeout)

    monkeypatch.setattr(callback, "_post_callback_payload", fake_post)
    await callback.notify_analysis_task_callback("http://cb.local", "task-1", "complete")

    assert seen == [4.25]


@pytest.mark.anyio
async def test_explicit_callback_timeout_overrides_config(monkeypatch, override_config):
    """显式传参仍然优先，便于个别调用点单独收紧。"""
    override_config(callback={"timeout": 9.5})
    seen = []

    async def fake_post(url, payload, *, timeout):
        seen.append(timeout)

    monkeypatch.setattr(callback, "_post_callback_payload", fake_post)
    await callback.notify_callback(
        "http://cb.local", "file-1", "analyzing", timeout=1.0
    )

    assert seen == [1.0]


@pytest.mark.anyio
async def test_callback_timeout_change_takes_effect_without_restart(
    monkeypatch, override_config
):
    """热配置：同一进程内改配置后，下一次回调即用新值。"""
    seen = []

    async def fake_post(url, payload, *, timeout):
        seen.append(timeout)

    monkeypatch.setattr(callback, "_post_callback_payload", fake_post)

    override_config(callback={"timeout": 2.5})
    await callback.notify_callback("http://cb.local", "file-1", "parsing")
    override_config(callback={"timeout": 8.0})
    await callback.notify_callback("http://cb.local", "file-1", "complete")

    assert seen == [2.5, 8.0]


def test_callback_timeout_is_editable_through_settings_service(tmp_path, override_config):
    """设置页保存路径：能写入磁盘并同步进程内快照。"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "analysis:\n  judge_timeout: 60\ncallback:\n  timeout: 2.5\n",
        encoding="utf-8",
    )
    service = settings_service.SettingsService(config_file)
    before = service.read_public_config()

    result = service.update_config(
        base_version=before["version"],
        changes={"callback": {"timeout": 6.5}},
        secrets={},
    )

    assert result["config"]["callback"]["timeout"] == 6.5
    assert "timeout: 6.5" in config_file.read_text(encoding="utf-8")
    assert get_config().callback.timeout == 6.5
