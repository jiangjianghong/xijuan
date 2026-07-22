"""custom 规则执行 execute_custom + parse_custom_json_response 测试。"""

from __future__ import annotations

import json

from service import analysis_service
from service.analysis_service import execute_custom, parse_custom_json_response


def test_parse_plain_value():
    v, r = parse_custom_json_response('{"value": "华为技术有限公司", "reason": "取自抬头"}')
    assert v == "华为技术有限公司"
    assert r == "取自抬头"


def test_parse_object_value_becomes_json_string():
    resp = '{"value": {"公司名称": "华为", "股东": ["张三"]}, "reason": "汇总"}'
    v, r = parse_custom_json_response(resp)
    assert json.loads(v) == {"公司名称": "华为", "股东": ["张三"]}
    assert r == "汇总"


def test_parse_fenced_json():
    v, r = parse_custom_json_response('```json\n{"value": "X", "reason": "Y"}\n```')
    assert v == "X" and r == "Y"


async def test_execute_custom_plain(monkeypatch):
    captured = {}

    async def fake_chat(prompt, messages=None):
        captured["prompt"] = prompt
        captured["messages"] = messages
        return '{"value": "结果文本", "reason": "依据"}'

    monkeypatch.setattr(analysis_service, "chat_completion", fake_chat)
    value, reason = await execute_custom("根据 200 生成摘要", is_formatted=False)
    assert value == "结果文本"
    assert reason == "依据"
    assert "200" in captured["prompt"]


async def test_execute_custom_formatted_injects_schema(monkeypatch):
    captured = {}

    async def fake_chat(prompt, messages=None):
        captured["prompt"] = prompt
        return '{"value": {"公司名称": "华为"}, "reason": "ok"}'

    monkeypatch.setattr(analysis_service, "chat_completion", fake_chat)
    schema = [{"key": "公司名称", "type": "string", "example": "示例", "desc": "全称"}]
    value, reason = await execute_custom(
        "整理公司档案", is_formatted=True, output_schema=schema
    )
    assert json.loads(value) == {"公司名称": "华为"}
    assert "公司名称 (字符串)：全称" in captured["prompt"]
    assert "示例：" in captured["prompt"]


async def test_execute_custom_uses_system_prompt(monkeypatch):
    captured = {}

    async def fake_chat(prompt, messages=None):
        captured["messages"] = messages
        return '{"value": "v", "reason": "r"}'

    monkeypatch.setattr(analysis_service, "chat_completion", fake_chat)
    await execute_custom("x<field_result>", is_formatted=False, system_prompt="你是助手")
    assert captured["messages"][0] == {"role": "system", "content": "你是助手"}
