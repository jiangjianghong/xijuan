"""service/vl_service 包测试。"""

from __future__ import annotations

import pytest

from service.vl_service import _common


def test_parse_vl_json_response_clean_json():
    s = '{"value": "5000万", "reason": "见第3页"}'
    v, r = _common.parse_vl_json_response(s)
    assert v == "5000万"
    assert r == "见第3页"


def test_parse_vl_json_response_markdown_fence():
    s = '```json\n{"value": "abc", "reason": "ok"}\n```'
    v, r = _common.parse_vl_json_response(s)
    assert v == "abc"
    assert r == "ok"


def test_parse_vl_json_response_with_think_tag():
    s = '<think>let me think</think>\n{"value": "X", "reason": "Y"}'
    v, r = _common.parse_vl_json_response(s)
    assert v == "X"
    assert r == "Y"


def test_parse_vl_json_response_value_is_list():
    s = '{"value": ["a", "b"], "reason": "two items"}'
    v, r = _common.parse_vl_json_response(s)
    # list 转 JSON 字符串
    assert v == '["a", "b"]'
    assert r == "two items"


def test_parse_vl_json_response_fallback_to_raw():
    s = "纯文本，无法解析为 JSON"
    v, r = _common.parse_vl_json_response(s)
    assert v == s
    assert r == ""


def test_strip_think_tags():
    s = "before<think>noise</think>after<think>more</think>end"
    assert _common.strip_think_tags(s) == "beforeafterend"


def test_build_image_messages_text_only():
    msgs = _common.build_image_messages(prompt="hello", b64_images=[], system_prompt=None)
    assert msgs == [{"role": "user", "content": "hello"}]


def test_build_image_messages_with_images_and_system():
    msgs = _common.build_image_messages(
        prompt="describe", b64_images=["B64A", "B64B"], system_prompt="be precise"
    )
    assert msgs[0] == {"role": "system", "content": "be precise"}
    assert msgs[1]["role"] == "user"
    content = msgs[1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert "data:image/png;base64,B64A" in content[0]["image_url"]["url"]
    assert content[1]["type"] == "image_url"
    assert content[2] == {"type": "text", "text": "describe"}
