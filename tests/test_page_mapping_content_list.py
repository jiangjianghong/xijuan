"""content_list 顺序重放构建 page_mapping 测试。"""

import pytest

from utils.page_mapping import (
    build_page_mapping_auto,
    build_page_mapping_from_content_list,
)

_MD = (
    "# 标题甲\n\n"
    "第一段正文内容，足够长的一段文字用于定位。\n\n"
    "<table><tr><td>表格单元甲</td></tr></table>\n\n"
    "- 列表项目甲，内容也足够长\n\n"
    "第二页的正文内容，同样足够长的一段文字。\n"
)

# bbox 均为 1000 归一化坐标；page_size [500, 2000] 便于验证反归一化(×0.5 / ×2)
_CL = [
    {"type": "text", "text": "标题甲", "text_level": 1,
     "bbox": [100, 200, 300, 400], "page_idx": 0},
    {"type": "text", "text": "第一段正文内容，足够长的一段文字用于定位。",
     "bbox": [100, 400, 900, 500], "page_idx": 0},
    {"type": "page_number", "text": "1", "bbox": [450, 950, 550, 980], "page_idx": 0},
    {"type": "table", "table_caption": [], "table_footnote": [],
     "table_body": "<table><tr><td>表格单元甲</td></tr></table>",
     "bbox": [100, 500, 900, 700], "page_idx": 0},
    {"type": "list", "sub_type": "text",
     "list_items": ["列表项目甲，内容也足够长"],
     "bbox": [100, 700, 900, 800], "page_idx": 1},
    {"type": "text", "text": "第二页的正文内容，同样足够长的一段文字。",
     "bbox": [100, 100, 900, 200], "page_idx": 1},
]

_MIDDLE = {"pdf_info": [
    {"page_idx": 0, "page_size": [500, 2000], "para_blocks": []},
    {"page_idx": 1, "page_size": [500, 2000], "para_blocks": []},
]}


def test_replay_builds_anchor_per_item_and_skips_page_number():
    mapping = build_page_mapping_from_content_list(_MD, _CL, _MIDDLE)
    # 6 项 - 1 个 page_number = 5 个锚点
    assert len(mapping) == 5
    assert [m["page_num"] for m in mapping] == [1, 1, 1, 2, 2]
    # 顺序重放: start_pos 严格递增
    positions = [m["start_pos"] for m in mapping]
    assert positions == sorted(positions)
    # 标题项锚定在 "# " 之后的正文文本处
    assert mapping[0]["start_pos"] == _MD.find("标题甲")
    # 表格项用 table_body 定位
    assert mapping[2]["start_pos"] == _MD.find("<table")
    # 列表项用 list_items[0] 定位
    assert mapping[3]["start_pos"] == _MD.find("列表项目甲")


def test_bbox_denormalized_from_1000_to_page_size():
    mapping = build_page_mapping_from_content_list(_MD, _CL, _MIDDLE)
    # [100, 200, 300, 400] × [500/1000, 2000/1000] → [50, 400, 150, 800]
    assert mapping[0]["bbox"] == pytest.approx([50.0, 400.0, 150.0, 800.0])
    assert mapping[0]["page_size"] == [500, 2000]


def test_missing_item_skipped_without_cursor_advance():
    cl = list(_CL)
    cl.insert(2, {"type": "text", "text": "这段文字在md中根本不存在哦",
                  "bbox": [0, 0, 10, 10], "page_idx": 0})
    mapping = build_page_mapping_from_content_list(_MD, cl, _MIDDLE)
    # 幽灵项被跳过,其余 5 项定位不受影响
    assert len(mapping) == 5
    assert mapping[2]["start_pos"] == _MD.find("<table")


def test_without_middle_json_no_bbox_but_page_num_kept():
    mapping = build_page_mapping_from_content_list(_MD, _CL, None)
    assert len(mapping) == 5
    assert mapping[0]["page_num"] == 1
    assert "bbox" not in mapping[0]
    assert "page_size" not in mapping[0]


def test_content_list_accepts_json_string():
    import json
    mapping = build_page_mapping_from_content_list(
        _MD, json.dumps(_CL, ensure_ascii=False), _MIDDLE)
    assert len(mapping) == 5


def test_empty_inputs_return_empty():
    assert build_page_mapping_from_content_list("", _CL, _MIDDLE) == []
    assert build_page_mapping_from_content_list(_MD, [], _MIDDLE) == []
    assert build_page_mapping_from_content_list(_MD, "不是json", _MIDDLE) == []


def test_auto_prefers_content_list():
    mapping = build_page_mapping_auto(_MD, _MIDDLE, _CL)
    assert len(mapping) == 5


def test_auto_falls_back_to_middle_json_when_no_content_list():
    md = "第一段内容用于定位测试的文本片段"
    middle = {"pdf_info": [{"page_idx": 0, "para_blocks": [
        {"lines": [{"spans": [{"content": "第一段内容用于定位测试的文本片段"}]}]},
    ]}]}
    for empty in ("", None, []):
        mapping = build_page_mapping_auto(md, middle, empty)
        assert len(mapping) == 1
        assert mapping[0]["page_num"] == 1


def test_auto_falls_back_when_replay_yields_nothing():
    # content_list 全是 page_number → 重放产出空 → 降级 middle_json 路径
    md = "第一段内容用于定位测试的文本片段"
    middle = {"pdf_info": [{"page_idx": 0, "para_blocks": [
        {"lines": [{"spans": [{"content": "第一段内容用于定位测试的文本片段"}]}]},
    ]}]}
    cl = [{"type": "page_number", "text": "1", "page_idx": 0}]
    mapping = build_page_mapping_auto(md, middle, cl)
    assert len(mapping) == 1


# ── 真实 MinerU 产物回归(应对气候变化规划.pdf, 14 页) ─────────────

import json as _json
from pathlib import Path

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "content_list_sample"


def _load_real_sample():
    md = (_FIXTURE_DIR / "1.md").read_text(encoding="utf-8")
    middle = _json.loads((_FIXTURE_DIR / "middle.json").read_text(encoding="utf-8"))
    cl = _json.loads((_FIXTURE_DIR / "content_list.json").read_text(encoding="utf-8"))
    return md, middle, cl


def test_real_sample_full_coverage():
    md, middle, cl = _load_real_sample()
    mapping = build_page_mapping_from_content_list(md, cl, middle)
    # 88 项中 74 项为实内容(其余为 page_number),顺序重放应全命中
    assert len(mapping) == 74
    # 覆盖 1-13 页；第 14 页(page_idx=13)content_list 仅一个 page_number 水印、
    # 无实内容,md 不渲染故无锚点——正确覆盖为 1..13
    assert {m["page_num"] for m in mapping} == set(range(1, 14))
    # 锚点单调
    positions = [m["start_pos"] for m in mapping]
    assert positions == sorted(positions)


def test_real_sample_bbox_within_page_size():
    md, middle, cl = _load_real_sample()
    mapping = build_page_mapping_from_content_list(md, cl, middle)
    with_bbox = [m for m in mapping if "bbox" in m]
    assert with_bbox, "真实产物应有 bbox"
    for m in with_bbox:
        w, h = m["page_size"]
        x0, y0, x1, y1 = m["bbox"]
        assert 0 <= x0 <= x1 <= w
        assert 0 <= y0 <= y1 <= h


def test_real_sample_known_page_lookup():
    from utils.page_mapping import lookup_page_num
    md, middle, cl = _load_real_sample()
    mapping = build_page_mapping_from_content_list(md, cl, middle)
    # 第 4 页(page_idx=3)的表格「专栏1 非二氧化碳温室气体管控工程」
    pos = md.find("专栏1 非二氧化碳温室气体管控工程")
    assert pos != -1
    assert lookup_page_num(mapping, pos, pos + 10) == "4"
