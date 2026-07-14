"""page 检索方式测试。"""

from __future__ import annotations

import pytest

from service.extraction_service import _parse_page_range


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5", (5, 5)),
        ("5-7", (5, 7)),
        ("  3 - 9 ", (3, 9)),
        ("1-1", (1, 1)),
        ("100", (100, 100)),
    ],
)
def test_parse_page_range_valid(raw, expected):
    assert _parse_page_range(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "0",
        "0-3",
        "5-3",
        "a",
        "5-a",
        "5-7-9",
        "-",
        "5-",
        "-5",
        None,
        5,
    ],
)
def test_parse_page_range_invalid(raw):
    assert _parse_page_range(raw) is None


from service.extraction_service import slice_by_page_range


def _make_mapping():
    """构造一个 5 页文档的 page_mapping。

    md 内容假设为：
      "PAGE1_AAA" + "PAGE2_BBB" + ... + "PAGE5_EEE"
    每页一个 block，每个 block 长度 9，block 之间无间隔。
    """
    return [
        {"start_pos": 0, "end_pos": 9, "page_num": 1},
        {"start_pos": 9, "end_pos": 18, "page_num": 2},
        {"start_pos": 18, "end_pos": 27, "page_num": 3},
        {"start_pos": 27, "end_pos": 36, "page_num": 4},
        {"start_pos": 36, "end_pos": 45, "page_num": 5},
    ]


_MD_5P = "PAGE1_AAA" + "PAGE2_BBB" + "PAGE3_CCC" + "PAGE4_DDD" + "PAGE5_EEE"


def test_slice_by_page_range_middle():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 2, 4, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE2_BBB" + "PAGE3_CCC" + "PAGE4_DDD"
    assert r["start_pos"] == 9
    assert r["end_pos"] == 36
    assert r["length"] == 27
    assert r["truncated"] is False


def test_slice_by_page_range_single():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 3, 3, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE3_CCC"


def test_slice_by_page_range_to_end():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 4, 5, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE4_DDD" + "PAGE5_EEE"
    assert r["end_pos"] == len(_MD_5P)


def test_slice_by_page_range_overflow_end():
    """end_page 越界 → 切到末尾。"""
    r = slice_by_page_range(_MD_5P, _make_mapping(), 5, 99, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE5_EEE"


def test_slice_by_page_range_start_overflow():
    """start_page 越过末页 → ok=False。"""
    r = slice_by_page_range(_MD_5P, _make_mapping(), 10, 20, 30000)
    assert r["ok"] is False
    assert "10-20" in r["reason"]


def test_slice_by_page_range_empty_mapping():
    r = slice_by_page_range(_MD_5P, [], 1, 3, 30000)
    assert r["ok"] is False
    assert "page_mapping" in r["reason"]


def test_slice_by_page_range_truncate():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 1, 5, 10)
    assert r["ok"] is True
    assert r["text"] == "PAGE1_AAAP"
    assert r["length"] == 10
    assert r["truncated"] is True
    assert r["start_pos"] == 0
    assert r["end_pos"] == 10


from service.extraction_service import split_md_by_pages


def test_split_md_by_pages_basic():
    pages = split_md_by_pages(_MD_5P, _make_mapping())
    assert [p["page_num"] for p in pages] == [1, 2, 3, 4, 5]
    assert pages[0]["content"] == "PAGE1_AAA"
    assert pages[2]["content"] == "PAGE3_CCC"
    assert pages[4]["content"] == "PAGE5_EEE"


def test_split_md_by_pages_first_page_includes_leading_content():
    """首页锚点前的前导内容并入首页。"""
    md = "LEAD_" + _MD_5P
    mapping = [
        {"start_pos": 5, "end_pos": 14, "page_num": 1},
        {"start_pos": 14, "end_pos": 23, "page_num": 2},
    ]
    pages = split_md_by_pages(md, mapping)
    assert pages[0]["page_num"] == 1
    assert pages[0]["content"].startswith("LEAD_PAGE1_AAA")


def test_split_md_by_pages_last_page_to_end():
    mapping = [
        {"start_pos": 0, "end_pos": 9, "page_num": 1},
        {"start_pos": 9, "end_pos": 18, "page_num": 2},
    ]
    pages = split_md_by_pages(_MD_5P, mapping)
    # 末页（第2页）切到文末，包含后续全部内容
    assert pages[-1]["page_num"] == 2
    assert pages[-1]["content"] == _MD_5P[9:]


def test_split_md_by_pages_empty_md():
    assert split_md_by_pages("", _make_mapping()) == []


def test_split_md_by_pages_empty_mapping():
    assert split_md_by_pages(_MD_5P, []) == []


def test_split_md_by_pages_skips_blank_pages():
    """切片为纯空白的页码跳过。"""
    md = "PAGE1_AAA" + "   " + "PAGE3_CCC"
    mapping = [
        {"start_pos": 0, "end_pos": 9, "page_num": 1},
        {"start_pos": 9, "end_pos": 12, "page_num": 2},   # 只有空白
        {"start_pos": 12, "end_pos": 21, "page_num": 3},
    ]
    pages = split_md_by_pages(md, mapping)
    assert [p["page_num"] for p in pages] == [1, 3]



def test_slice_by_page_range_empty_md():
    r = slice_by_page_range("", _make_mapping(), 1, 1, 30000)
    assert r["ok"] is False


from unittest.mock import MagicMock

import service.extraction_service as ext_svc
from service.extraction_service import _ensure_valid_extraction_result, _extract_page_field


def _make_field(prompt='提取问题: <search_result>page_content</search_result>', system=None):
    """构造一个简单的 ExtractionField stub。"""
    field = MagicMock()
    field.field_id = "fld_test"
    field.field_name = "测试字段"
    field.text_extract_prompt = prompt
    field.text_system_prompt = system
    return field


@pytest.mark.asyncio
async def test_extract_page_field_happy(monkeypatch):
    captured = {}

    async def fake_chat(prompt, messages=None):
        captured["prompt"] = prompt
        captured["messages"] = messages
        return '{"value": "答案", "reason": "在第3页"}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field()
    config = {"page_range": "3", "max_length": 30000}
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), config, field)

    assert value == "答案"
    assert reason == "在第3页"
    assert refs == {
        "page_content": [
            {
                "type": "page",
                "page_range": "3",
                "start_pos": 18,
                "end_pos": 27,
                "length": 9,
                "page_num": 3,
                "text": "PAGE3_CCC",
            }
        ],
        "_texts": {"page_content": "【第3页】\nPAGE3_CCC"},
    }
    sent = captured["prompt"] or (captured["messages"] and captured["messages"][-1]["content"])
    assert "PAGE3_CCC" in sent
    assert "【第3页】" in sent
    assert "<search_result>" not in sent


@pytest.mark.asyncio
async def test_extract_page_field_per_page_injection(monkeypatch):
    """区间被拆成逐页，每页各带【第X页】标记，refs 逐页各一条。"""
    captured = {}

    async def fake_chat(prompt, messages=None):
        captured["prompt"] = prompt
        return '{"value": "v", "reason": "r"}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field()
    config = {"page_range": "2-4", "max_length": 30000}
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), config, field)

    assert [r["page_num"] for r in refs["page_content"]] == [2, 3, 4]
    assert [r["text"] for r in refs["page_content"]] == ["PAGE2_BBB", "PAGE3_CCC", "PAGE4_DDD"]
    injected = refs["_texts"]["page_content"]
    assert injected == (
        "【第2页】\nPAGE2_BBB\n---\n【第3页】\nPAGE3_CCC\n---\n【第4页】\nPAGE4_DDD"
    )
    assert captured["prompt"].count("【第") == 3


@pytest.mark.asyncio
async def test_extract_page_field_truncated(monkeypatch):
    async def fake_chat(prompt, messages=None):
        return '{"value": "v", "reason": "r"}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field()
    config = {"page_range": "1-5", "max_length": 10}
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), config, field)

    assert value == "v"
    # 首页 piece = "【第1页】\nPAGE1_AAA"(16 字) 超过 10 → 截断到 10 字并停止
    injected = refs["_texts"]["page_content"]
    assert len(injected) == 10
    assert injected == "【第1页】\nPAGE"
    assert [r["page_num"] for r in refs["page_content"]] == [1]


@pytest.mark.asyncio
async def test_extract_page_field_invalid_range():
    field = _make_field()
    value, reason, refs = await _extract_page_field(
        _MD_5P, _make_mapping(), {"page_range": "5-3"}, field
    )
    assert value == ""
    assert "page_range" in reason
    assert "'5-3'" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_missing_range():
    field = _make_field()
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), {}, field)
    assert value == ""
    assert "page_range" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_empty_mapping():
    field = _make_field()
    value, reason, refs = await _extract_page_field(
        _MD_5P, [], {"page_range": "1-2"}, field
    )
    assert value == ""
    assert "page_mapping" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_out_of_range():
    field = _make_field()
    value, reason, refs = await _extract_page_field(
        _MD_5P, _make_mapping(), {"page_range": "100-200"}, field
    )
    assert value == ""
    assert "100-200" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_missing_placeholder(monkeypatch):
    """prompt 不带占位符时返回空 value，与现有 5 种方法一致。"""
    fake_chat_called = False

    async def fake_chat(prompt, messages=None):
        nonlocal fake_chat_called
        fake_chat_called = True
        return '{"value": "X", "reason": ""}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field(prompt="提取问题（无占位符）")
    value, reason, refs = await _extract_page_field(
        _MD_5P, _make_mapping(), {"page_range": "1"}, field
    )
    assert value == ""
    assert fake_chat_called is False


def test_empty_extraction_without_source_refs_is_failure():
    field = _make_field()

    with pytest.raises(ValueError, match="未提取到有效结果"):
        _ensure_valid_extraction_result(field, "", "", None)

    _ensure_valid_extraction_result(field, "", "未找到明确值", {"page_content": [{"text": "原文"}]})


