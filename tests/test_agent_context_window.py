"""验证后台整理的边界、预算、增量合并和生命周期。"""

import asyncio
import json

import pytest

from xijuan_agent.context import ContextWindow, compact_messages, estimate_tokens


def _group(index, size=600):
    return [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{index}", "function": {"name": "read_section", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": f"call_{index}", "content": f"EVIDENCE_{index} " + "文" * size},
    ]


def _messages(count=5):
    return [{"role": "system", "content": "系统"}, {"role": "user", "content": "当前问题"}] + [
        message for index in range(count) for message in _group(index)
    ]


@pytest.mark.asyncio
async def test_compaction_within_one_question_is_real_and_does_not_repeat():
    calls = []

    async def summarize(text):
        calls.append(text)
        return "旧工具的证据摘要"

    options = dict(threshold_tokens=100, max_tokens=24000, recent_tool_rounds=4, summarize=summarize)
    first, changed, tokens = await compact_messages(_messages(), **options)
    assert changed and len(calls) == 1
    assert tokens < estimate_tokens(_messages())
    assert "EVIDENCE_0" in calls[0] and "EVIDENCE_1" not in calls[0]
    assert any(message.get("content") == "当前问题" for message in first)
    second, changed, _ = await compact_messages(first, **options)
    assert not changed and second == first and len(calls) == 1


@pytest.mark.asyncio
async def test_no_eligible_evidence_does_not_claim_compaction():
    async def summarize(_text):
        pytest.fail("没有旧证据时不应发起摘要请求")

    messages = _messages(4)
    result, changed, _ = await compact_messages(
        messages, threshold_tokens=100, max_tokens=24000, recent_tool_rounds=4, summarize=summarize,
    )
    assert not changed and result == messages


@pytest.mark.asyncio
async def test_background_summary_does_not_block_and_merge_keeps_new_messages():
    started, release = asyncio.Event(), asyncio.Event()

    async def summarize(_text):
        started.set()
        await release.wait()
        return "早期证据"

    messages = _messages()
    window = ContextWindow(threshold_tokens=100, max_tokens=24000, recent_tool_rounds=1, summarize=summarize)
    try:
        events = [event async for event in window.prepare(messages)]
        assert [event["state"] for event in events] == ["started"]
        await asyncio.wait_for(started.wait(), 1)
        task = window.task
        assert task is not None and not task.done()
        messages.extend(_group(99))
        messages.append({"role": "assistant", "content": "摘要期间的新答复"})
        assert [event async for event in window.prepare(messages)] == []
        assert window.task is task
        release.set()
        await task
        window.threshold_tokens = 20000
        events = [event async for event in window.prepare(messages)]
        assert [event["state"] for event in events] == ["completed"]
        assert messages[-3:] == _group(99) + [{"role": "assistant", "content": "摘要期间的新答复"}]
        assert "EVIDENCE_0" not in json.dumps(messages, ensure_ascii=False)
        assert any(message.get("content") == "当前问题" for message in messages)
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_only_over_budget_request_waits_for_pending_summary():
    release, waiting = asyncio.Event(), asyncio.Event()

    async def summarize(_text):
        await release.wait()
        return "旧证据摘要"

    messages = _messages(6)
    window = ContextWindow(threshold_tokens=100, max_tokens=estimate_tokens(messages) + 100,
                           recent_tool_rounds=1, summarize=summarize)
    await anext(window.prepare(messages))
    messages.extend(_group(99, 1800))
    events = []

    async def prepare():
        async for event in window.prepare(messages):
            events.append(event)
            if event["state"] == "waiting":
                waiting.set()

    pending = asyncio.create_task(prepare())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        assert not pending.done()
        release.set()
        await asyncio.wait_for(pending, 1)
        assert estimate_tokens(messages) <= window.max_tokens
        assert any(event["state"] == "completed" for event in events)
        assert messages[-2:] == _group(99, 1800)
    finally:
        pending.cancel()
        await window.close()


@pytest.mark.asyncio
async def test_hard_budget_includes_overhead_and_preserves_tool_pairs():
    async def summarize(_text):
        return "图片及工具证据摘要"

    messages = _messages(1)
    messages.append({"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA"}},
    ]})
    messages.extend(_group(1))
    window = ContextWindow(threshold_tokens=100, max_tokens=1200, recent_tool_rounds=4, summarize=summarize)
    try:
        events = [event async for event in window.prepare(messages, overhead_tokens=400)]
        assert any(event["state"] == "waiting" for event in events)
        assert estimate_tokens(messages) + 400 <= 1200
        assert any(message.get("content") == "当前问题" for message in messages)
        assert [message.get("tool_call_id") for message in messages if message.get("role") == "tool"] == ["call_1"]
        assert [call["id"] for message in messages for call in message.get("tool_calls", [])] == ["call_1"]
        assert not any(isinstance(message.get("content"), list) for message in messages)
    finally:
        await window.close()


@pytest.mark.asyncio
async def test_unread_pdf_images_are_not_replaced_by_text_only_summary():
    async def summarize(_text):
        pytest.fail("尚未被模型读取的页图不能只摘要成文字占位符")

    messages = _messages(1)
    image = {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA"}},
    ]}
    messages.append(image)
    window = ContextWindow(threshold_tokens=100, max_tokens=1000, recent_tool_rounds=4, summarize=summarize)
    with pytest.raises(ValueError, match="超过 Agent 上下文上限"):
        async for _ in window.prepare(messages):
            pass
    assert messages[-1] == image


@pytest.mark.asyncio
async def test_unreducible_question_stops_before_exceeding_budget():
    async def summarize(_text):
        pytest.fail("当前问题不能被摘要替换")

    window = ContextWindow(threshold_tokens=100, max_tokens=200, recent_tool_rounds=4, summarize=summarize)
    with pytest.raises(ValueError, match="超过 Agent 上下文上限"):
        async for _ in window.prepare([{"role": "user", "content": "文" * 1000}]):
            pass


@pytest.mark.asyncio
async def test_close_cancels_pending_summary_without_leaking_task():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def summarize(_text):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    window = ContextWindow(threshold_tokens=100, max_tokens=24000, recent_tool_rounds=1, summarize=summarize)
    messages = _messages()
    async for _ in window.prepare(messages):
        pass
    await asyncio.wait_for(started.wait(), 1)
    event = await window.finish(messages)
    assert event["state"] == "cancelled"
    assert cancelled.is_set() and window.task is None
    await window.close()


@pytest.mark.asyncio
async def test_summary_failure_falls_back_once_instead_of_retry_loop():
    calls = 0

    async def summarize(_text):
        nonlocal calls
        calls += 1
        raise RuntimeError("模型摘要失败")

    messages = _messages()
    window = ContextWindow(threshold_tokens=100, max_tokens=24000, recent_tool_rounds=4, summarize=summarize)
    try:
        async for _ in window.prepare(messages):
            pass
        await window.task
        events = [event async for event in window.prepare(messages)]
        assert [event["state"] for event in events] == ["completed"]
        assert [event async for event in window.prepare(messages)] == []
        assert calls == 1
    finally:
        await window.close()
