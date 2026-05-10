"""service/vl_service 包测试。"""

from __future__ import annotations

import fitz
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


# ── vl_model_extract 测试 ──────────────────────────────────────


def _make_pdf_bytes(num_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), f"Page {i + 1}", fontsize=24)
    return doc.tobytes()


async def test_vl_model_extract_success(monkeypatch):
    from service.vl_service import model as vl_model_module

    captured = {}

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        captured["messages"] = messages
        return {
            "choices": [{"message": {"content": '{"value": "abc", "reason": "ok"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(3)
    value, reason, refs = await vl_model_module.vl_model_extract(
        pdf,
        vl_extract_prompt="提取金额，输出 JSON {value, reason}",
        vl_system_prompt=None,
        page_range="1-2",
        max_pixels=200_000,
    )

    assert value == "abc"
    assert reason == "ok"
    assert refs["method"] == "vl_model"
    assert refs["total_pages"] == 3
    assert refs["key_pages"] == [1, 2]  # 1-indexed
    assert refs["vl_total_tokens"] == 15
    user_msg = captured["messages"][0]
    assert user_msg["role"] == "user"
    image_blocks = [c for c in user_msg["content"] if c["type"] == "image_url"]
    text_blocks = [c for c in user_msg["content"] if c["type"] == "text"]
    assert len(image_blocks) == 2
    assert len(text_blocks) == 1


async def test_vl_model_extract_empty_pages(monkeypatch):
    """page_range 解析为空 → 不调 vl_chat、返回空。"""
    from service.vl_service import model as vl_model_module

    called = False

    async def fake_vl_chat(*a, **kw):
        nonlocal called
        called = True
        raise AssertionError("不应当调用 vl_chat")

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(1)
    value, reason, refs = await vl_model_module.vl_model_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        page_range="",  # 解析为空
    )
    assert value == ""
    assert reason == ""
    assert refs["method"] == "vl_model"
    assert refs["key_pages"] == []
    assert called is False

