"""custom 规则调试流事件序列测试。"""

from __future__ import annotations

from types import SimpleNamespace

from service import analysis_service


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


class _Session:
    async def execute(self, statement):
        # 提取结果：字段 a = 200
        return _Result([SimpleNamespace(field_id="a", extracted_value="200")])


async def test_custom_stream_formatted(monkeypatch):
    async def fake_chat(prompt, messages=None, **kwargs):
        return '{"value": {"公司名称": "华为"}, "reason": "ok"}'

    async def fake_chat_stream(prompt, messages=None, **kwargs):
        yield {"type": "reasoning", "content": "先判断依赖字段。"}
        response = await fake_chat(prompt, messages=messages, **kwargs)
        yield {"type": "content", "content": response[:20]}
        yield {"type": "content", "content": response[20:]}

    monkeypatch.setattr(analysis_service, "chat_completion", fake_chat)
    monkeypatch.setattr(analysis_service, "chat_completion_stream_events", fake_chat_stream)

    events = []
    schema = [{"key": "公司名称", "type": "string", "example": "示例", "desc": "全称"}]
    async for evt in analysis_service.test_rule_analysis_stream(
        "f1", "custom", "根据<field_result>a</field_result>生成", ["a"], "",
        _Session(), web_search=None, is_formatted=1, output_schema=schema,
    ):
        events.append(evt)

    names = [e["event"] for e in events]
    assert names[:2] == ["input_values", "resolved_expression"]
    assert "prompt" in names and "llm_reasoning_delta" in names and "llm_delta" in names and "llm_response" in names
    assert names[-1] == "done"
    result_evt = next(e for e in events if e["event"] == "result")
    response_evt = next(e for e in events if e["event"] == "llm_response")
    assert response_evt["data"]["raw_response"] == '{"value": {"公司名称": "华为"}, "reason": "ok"}'
    assert "先判断依赖字段。" not in response_evt["data"]["raw_response"]
    assert '"公司名称"' in result_evt["data"]["result_value"]
    prompt_evt = next(e for e in events if e["event"] == "prompt")
    assert "示例：" in prompt_evt["data"]["user_prompt"]
