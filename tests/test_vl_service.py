"""service/vl_service 包测试。"""

from __future__ import annotations

import fitz
import pytest

from service.vl_service import _common
from utils import vl_client


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


# ── resolve_target_pages 测试 ──────────────────────────────────


def test_resolve_target_pages_all():
    pages, capped = vl_client.resolve_target_pages("all", 5)
    assert pages == [0, 1, 2, 3, 4]
    assert capped is False


def test_resolve_target_pages_range_and_single():
    pages, capped = vl_client.resolve_target_pages("2-3,5", 10)
    assert pages == [1, 2, 4]
    assert capped is False


def test_resolve_target_pages_dedups_and_sorts():
    """重复页不重复渲染，乱序输入归一为升序。"""
    pages, _ = vl_client.resolve_target_pages("5-7,6,3", 10)
    assert pages == [2, 4, 5, 6]


def test_resolve_target_pages_filters_out_of_bounds():
    pages, _ = vl_client.resolve_target_pages("1,99", 3)
    assert pages == [0]


def test_resolve_target_pages_empty_string():
    pages, capped = vl_client.resolve_target_pages("", 5)
    assert pages == []
    assert capped is False


def test_resolve_target_pages_caps_to_max_pages():
    pages, capped = vl_client.resolve_target_pages("all", 10, max_pages=3)
    assert pages == [0, 1, 2]
    assert capped is True


def test_resolve_target_pages_cap_not_triggered():
    pages, capped = vl_client.resolve_target_pages("1-2", 10, max_pages=5)
    assert pages == [0, 1]
    assert capped is False


def test_resolve_target_pages_max_pages_falsy_means_unlimited():
    """None / 0 / 负数都视为不限制。"""
    for mp in (None, 0, -1):
        pages, capped = vl_client.resolve_target_pages("all", 4, max_pages=mp)
        assert pages == [0, 1, 2, 3], f"max_pages={mp}"
        assert capped is False


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


async def test_vl_model_extract_caps_by_max_pages(monkeypatch):
    """max_pages 截断候选页，refs 记录 target_pages / pages_capped。"""
    from service.vl_service import model as vl_model_module

    captured = {}

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        captured["messages"] = messages
        return {
            "choices": [{"message": {"content": '{"value": "v", "reason": "r"}'}}],
            "usage": {"total_tokens": 7},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(6)
    _, _, refs = await vl_model_module.vl_model_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        page_range="all",
        max_pages=2,
        max_pixels=200_000,
    )

    assert refs["key_pages"] == [1, 2]
    assert refs["target_pages"] == [1, 2]
    assert refs["pages_capped"] is True
    image_blocks = [c for c in captured["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(image_blocks) == 2


async def test_vl_model_extract_dedups_duplicate_pages(monkeypatch):
    """page_range 里重复的页只渲染一次。"""
    from service.vl_service import model as vl_model_module

    captured = {}

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        captured["messages"] = messages
        return {
            "choices": [{"message": {"content": '{"value": "v", "reason": "r"}'}}],
            "usage": {"total_tokens": 7},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(8)
    _, _, refs = await vl_model_module.vl_model_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        page_range="5-7,6",
        max_pixels=200_000,
    )

    assert refs["key_pages"] == [5, 6, 7]
    assert refs["pages_capped"] is False
    image_blocks = [c for c in captured["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(image_blocks) == 3


# ── vl_progressive_extract 测试 ──────────────────────────────────


async def test_vl_progressive_extract_filters_no_info_batches(monkeypatch):
    """前 20 字符含'无相关信息'的批被丢弃，不进入 history。"""
    from service.vl_service import progressive as vl_progressive_module

    call_log = []

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        call_log.append(messages)
        idx = len(call_log)
        if idx == 1:
            content = "第1页：投资金额 5000 万元"
        elif idx == 2:
            content = "无相关信息"
        elif idx == 3:
            content = "第3页：股东 张三"
        else:  # 最后聚合
            content = '{"value": "5000万", "reason": "第1页 + 第3页累积"}'
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(3)
    value, reason, refs = await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="基于累积信息提取，返回 {value, reason}",
        vl_system_prompt=None,
        field_hints="投资金额、股东",
        batch_size=1,
    )

    # 4 次调用 = 3 批 + 1 次最终聚合
    assert len(call_log) == 4
    assert value == "5000万"
    assert reason == "第1页 + 第3页累积"
    assert refs["method"] == "vl_progressive"
    assert refs["total_pages"] == 3
    assert refs["key_pages"] is None
    assert refs["batches_with_info"] == 2  # 第2页被丢


async def test_vl_progressive_extract_progress_callback(monkeypatch):
    from service.vl_service import progressive as vl_progressive_module

    progress_events = []

    async def fake_vl_chat(messages, **kw):
        idx = len(progress_events) + 1
        if idx <= 2:
            content = f"第{idx}页有信息"
        else:
            content = '{"value": "x", "reason": "y"}'
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 10},
        }

    async def cb(evt):
        progress_events.append(evt)

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(2)
    await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="hint",
        batch_size=1,
        progress_cb=cb,
    )
    # 2 批 → 2 次 batch 进度事件
    assert len(progress_events) == 2
    assert progress_events[0]["page_label"] == "第1页"
    assert progress_events[0]["has_info"] is True


async def test_vl_progressive_extract_custom_batch_template(monkeypatch):
    """自定义模板替代默认。"""
    from service.vl_service import progressive as vl_progressive_module

    captured_prompts = []

    async def fake_vl_chat(messages, **kw):
        for c in (messages[-1]["content"] if isinstance(messages[-1]["content"], list) else []):
            if c.get("type") == "text":
                captured_prompts.append(c["text"])
        idx = len(captured_prompts)
        if idx == 1:
            content = "无相关信息"
        else:
            content = '{"value":"a","reason":"b"}'
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 5},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    custom = "CUSTOM hints={field_hints} label={page_label} total={total_pages} hist={history}"
    pdf = _make_pdf_bytes(1)
    await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="final",
        vl_system_prompt=None,
        field_hints="X",
        batch_size=1,
        batch_prompt_template=custom,
    )
    # 第一条 prompt 应该是渲染后的 custom（不含历史）
    assert captured_prompts[0].startswith("CUSTOM hints=X label=第1页 total=1 hist=")


def test_format_page_label_variants():
    from service.vl_service.progressive import _format_page_label

    assert _format_page_label([0]) == "第1页"
    assert _format_page_label([0, 1]) == "第1-2页"
    assert _format_page_label([2, 8]) == "第3,9页"          # 不连续
    assert _format_page_label([2, 3, 4]) == "第3-5页"


async def test_vl_progressive_extract_respects_page_range(monkeypatch):
    """只扫 page_range 指定的页，批次按目标页列表切片。"""
    from service.vl_service import progressive as vl_progressive_module

    labels = []

    async def fake_vl_chat(messages, **kw):
        content = messages[-1]["content"]
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "text":
                    labels.append(c["text"])
                    break
            return {
                "choices": [{"message": {"content": "有信息"}}],
                "usage": {"total_tokens": 5},
            }
        # 最终聚合是纯文本消息
        return {
            "choices": [{"message": {"content": '{"value":"v","reason":"r"}'}}],
            "usage": {"total_tokens": 5},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(10)
    _, _, refs = await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="final",
        vl_system_prompt=None,
        field_hints="hint",
        batch_size=2,
        page_range="3,4,9",
        max_pixels=200_000,
    )

    # 3 个目标页 / batch_size=2 → 2 批
    assert len(labels) == 2
    assert "第3-4页" in labels[0]
    assert "第9页" in labels[1]
    assert refs["total_pages"] == 10          # 文档真实总页数不变
    assert refs["target_pages"] == [3, 4, 9]
    assert refs["pages_capped"] is False


async def test_vl_progressive_extract_caps_by_max_pages(monkeypatch):
    from service.vl_service import progressive as vl_progressive_module

    async def fake_vl_chat(messages, **kw):
        content = messages[-1]["content"]
        if isinstance(content, list):
            return {
                "choices": [{"message": {"content": "有信息"}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value":"v","reason":"r"}'}}],
            "usage": {"total_tokens": 5},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(10)
    _, _, refs = await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="final",
        vl_system_prompt=None,
        field_hints="hint",
        batch_size=5,
        page_range="all",
        max_pages=2,
        max_pixels=200_000,
    )
    assert refs["target_pages"] == [1, 2]
    assert refs["pages_capped"] is True


async def test_vl_progressive_scan_scope_injected(monkeypatch):
    """限页时 {scan_scope} 有内容；全文时为空串。"""
    from service.vl_service import progressive as vl_progressive_module

    prompts = []

    async def fake_vl_chat(messages, **kw):
        content = messages[-1]["content"]
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "text":
                    prompts.append(c["text"])
                    break
            return {
                "choices": [{"message": {"content": "有信息"}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value":"v","reason":"r"}'}}],
            "usage": {"total_tokens": 5},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    tpl = "SCOPE[{scan_scope}] label={page_label} total={total_pages} hints={field_hints} hist={history}"
    pdf = _make_pdf_bytes(4)

    await vl_progressive_module.vl_progressive_extract(
        pdf, vl_extract_prompt="f", vl_system_prompt=None, field_hints="H",
        batch_size=1, page_range="2", batch_prompt_template=tpl, max_pixels=200_000,
    )
    assert prompts[0].startswith("SCOPE[")
    assert "第 2 页" in prompts[0]
    assert "total=4" in prompts[0]            # 仍是文档总页数

    prompts.clear()
    await vl_progressive_module.vl_progressive_extract(
        pdf, vl_extract_prompt="f", vl_system_prompt=None, field_hints="H",
        batch_size=4, page_range="all", batch_prompt_template=tpl, max_pixels=200_000,
    )
    assert prompts[0].startswith("SCOPE[]")   # 全文 → 空串


async def test_vl_progressive_legacy_template_without_scan_scope(monkeypatch):
    """老的自定义模板没有 {scan_scope} 占位符，必须仍能 format 不报错。"""
    from service.vl_service import progressive as vl_progressive_module

    prompts = []

    async def fake_vl_chat(messages, **kw):
        content = messages[-1]["content"]
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "text":
                    prompts.append(c["text"])
                    break
            return {
                "choices": [{"message": {"content": "有信息"}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value":"v","reason":"r"}'}}],
            "usage": {"total_tokens": 5},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    legacy = "CUSTOM hints={field_hints} label={page_label} total={total_pages} hist={history}"
    pdf = _make_pdf_bytes(1)
    await vl_progressive_module.vl_progressive_extract(
        pdf, vl_extract_prompt="final", vl_system_prompt=None, field_hints="X",
        batch_size=1, batch_prompt_template=legacy, max_pixels=200_000,
    )
    assert prompts[0].startswith("CUSTOM hints=X label=第1页 total=1 hist=")


# ── vl_locate_extract 测试 ─────────────────────────────────────


def _is_locate_call(messages: list) -> bool:
    """判断当前调用是否为 locate 第一轮（消息含 '缩略图网格'）。"""
    if not messages or not isinstance(messages[-1]["content"], list):
        return False
    for c in messages[-1]["content"]:
        if c.get("type") == "text" and "缩略图网格" in c.get("text", ""):
            return True
    return False


async def test_vl_locate_extract_filters_hallucinated_pages(monkeypatch):
    """LLM 返回了不在网格范围里的幻觉页码 → 必须被过滤。"""
    from service.vl_service import locate as vl_locate_module

    pdf = _make_pdf_bytes(6)

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        if _is_locate_call(messages):
            # 故意返回一个超界页码 99
            return {
                "choices": [{"message": {"content": '{"found_pages": [2, 99], "reason": "x"}'}}],
                "usage": {"total_tokens": 10},
            }
        return {
            "choices": [{"message": {"content": '{"value": "FOUND", "reason": "page 2"}'}}],
            "usage": {"total_tokens": 20},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    value, reason, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="基于关键页提取，返回 {value, reason}",
        vl_system_prompt=None,
        field_hints="关键信息",
        grid_pages=6,
    )

    assert value == "FOUND"
    assert refs["method"] == "vl_locate"
    assert refs["total_pages"] == 6
    # 99 被过滤，只剩 2
    assert refs["key_pages"] == [2]


async def test_vl_locate_extract_fallback_when_no_hits(monkeypatch):
    """第一轮一页未命中 → 回退前 fallback_pages 页。"""
    from service.vl_service import locate as vl_locate_module

    pdf = _make_pdf_bytes(5)

    async def fake_vl_chat(messages, **kw):
        if _is_locate_call(messages):
            return {
                "choices": [{"message": {"content": '{"found_pages": [], "reason": "无"}'}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value": "fallback", "reason": "前 N 页"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        grid_pages=6,
        fallback_pages=3,
    )
    assert refs["key_pages"] == [1, 2, 3]


async def test_vl_locate_extract_truncates_to_limit(monkeypatch):
    """命中超 key_pages_limit 截断，未达阈值全保留。"""
    from service.vl_service import locate as vl_locate_module

    pdf = _make_pdf_bytes(12)

    async def fake_vl_chat(messages, **kw):
        if _is_locate_call(messages):
            text = ""
            for c in messages[-1]["content"]:
                if c.get("type") == "text":
                    text = c["text"]
                    break
            # 第一个网格页码 1-6；第二个 7-12
            if "第 1, 2, 3, 4, 5, 6" in text:
                return {
                    "choices": [{"message": {"content": '{"found_pages": [1,2,3,4]}'}}],
                    "usage": {"total_tokens": 5},
                }
            return {
                "choices": [{"message": {"content": '{"found_pages": [7,8,9,10]}'}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value": "ok", "reason": "ok"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        grid_pages=6,
        key_pages_limit=5,
    )
    # 8 命中 → 排序去重后取前 5
    assert refs["key_pages"] == [1, 2, 3, 4, 7]



async def test_vl_locate_extract_respects_page_range(monkeypatch):
    """网格只覆盖 page_range 指定的页，不扫全文。"""
    from service.vl_service import locate as vl_locate_module

    locate_prompts = []

    async def fake_vl_chat(messages, **kw):
        if _is_locate_call(messages):
            for c in messages[-1]["content"]:
                if c.get("type") == "text":
                    locate_prompts.append(c["text"])
                    break
            return {
                "choices": [{"message": {"content": '{"found_pages": [7]}'}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value": "ok", "reason": "r"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(20)
    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        page_range="6-8",
        grid_pages=6,
        max_pixels=200_000,
    )

    # 3 个目标页 → 1 个网格
    assert len(locate_prompts) == 1
    assert "第 6, 7, 8 页" in locate_prompts[0]
    assert "第1行第1列=第6页" in locate_prompts[0]
    assert refs["total_pages"] == 20
    assert refs["target_pages"] == [6, 7, 8]
    assert refs["key_pages"] == [7]


async def test_vl_locate_fallback_uses_target_pages_not_doc_head(monkeypatch):
    """定位全空时兜底取候选页前 N 个，而不是文档前 N 页。"""
    from service.vl_service import locate as vl_locate_module

    async def fake_vl_chat(messages, **kw):
        if _is_locate_call(messages):
            return {
                "choices": [{"message": {"content": '{"found_pages": []}'}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value": "fb", "reason": "r"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(20)
    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        page_range="11-15",
        grid_pages=6,
        fallback_pages=2,
        max_pixels=200_000,
    )
    assert refs["key_pages"] == [11, 12]   # 不是 [1, 2]


async def test_vl_locate_extract_caps_by_max_pages(monkeypatch):
    """max_pages 限定定位前的候选页；与 key_pages_limit 是两个阶段的约束。"""
    from service.vl_service import locate as vl_locate_module

    async def fake_vl_chat(messages, **kw):
        if _is_locate_call(messages):
            return {
                "choices": [{"message": {"content": '{"found_pages": [1, 2, 3]}'}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value": "ok", "reason": "r"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(20)
    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        page_range="all",
        max_pages=3,
        grid_pages=6,
        max_pixels=200_000,
    )
    assert refs["target_pages"] == [1, 2, 3]
    assert refs["pages_capped"] is True
    assert refs["key_pages"] == [1, 2, 3]
