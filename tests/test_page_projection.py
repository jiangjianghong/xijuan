"""基于 MinerU middle_json 的页码取文投影测试。"""

from __future__ import annotations

from utils.page_mapping import build_page_projection, select_page_projection


def _line_block(block_type: str, text: str, bbox: list[int]) -> dict:
    return {
        "type": block_type,
        "bbox": bbox,
        "lines": [{"spans": [{"content": text}]}],
    }


def _table_block(html: str, bbox: list[int]) -> dict:
    return {
        "type": "table",
        "bbox": bbox,
        "blocks": [
            _line_block("table_caption", "汇总表", bbox),
            {
                "type": "table_body",
                "lines": [{"spans": [{"html": html}]}],
            },
        ],
    }


def _continuation_table_block(bbox: list[int]) -> dict:
    return {
        "type": "table",
        "bbox": bbox,
        "blocks": [{"type": "table_body", "lines": [], "lines_deleted": True}],
    }


def _middle_json() -> dict:
    return {
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [600, 800],
                "para_blocks": [
                    _line_block("title", "重复封面", [10, 10, 90, 30]),
                    _line_block("text", "建设单位", [10, 700, 90, 720]),
                ],
            },
            {
                "page_idx": 1,
                "page_size": [600, 800],
                "para_blocks": [
                    _line_block("title", "重复封面", [10, 10, 90, 30]),
                    _line_block("text", "建设单位", [10, 700, 90, 720]),
                ],
            },
            {
                "page_idx": 2,
                "page_size": [600, 800],
                "para_blocks": [_table_block("<table><tr><td>完整表格</td></tr></table>", [20, 20, 580, 700])],
            },
            {
                "page_idx": 3,
                "page_size": [600, 800],
                "para_blocks": [_continuation_table_block([20, 20, 580, 700])],
            },
            {
                "page_idx": 4,
                "page_size": [600, 800],
                "para_blocks": [_line_block("text", "表格后的正文", [10, 40, 590, 100])],
            },
        ]
    }


def test_projection_separates_repeated_cover_pages_by_page_index():
    projection = build_page_projection(_middle_json())

    selected = select_page_projection(projection, 1, 2)

    assert [item["page_num"] for item in selected] == [1, 2]
    assert [item["source_pages"] for item in selected] == [[1], [2]]
    assert all(item["content"] == "重复封面\n建设单位" for item in selected)
    assert all(item["mapping_quality"] == "middle_json" for item in selected)


def test_projection_includes_cross_page_table_once_for_intersecting_request():
    projection = build_page_projection(_middle_json())

    selected = select_page_projection(projection, 4, 4)

    assert len(selected) == 1
    table = selected[0]
    assert table["page_num"] == "3-4"
    assert table["source_pages"] == [3, 4]
    assert "完整表格" in table["content"]
    assert [bbox["page_num"] for bbox in table["bboxes"]] == [3, 4]


def test_projection_returns_empty_for_valid_image_only_page():
    projection = build_page_projection(
        {"pdf_info": [{"page_idx": 0, "page_size": [600, 800], "para_blocks": [{"type": "image"}]}]}
    )

    assert projection == []
    assert select_page_projection(projection, 1, 1) == []


def test_projection_returns_none_for_missing_or_invalid_middle_json():
    assert build_page_projection("") is None
    assert build_page_projection("{") is None


def test_projection_returns_none_for_malformed_page_structure():
    assert build_page_projection({"pdf_info": [{"page_idx": "0", "para_blocks": []}]}) is None
    assert build_page_projection({"pdf_info": [{"page_idx": 0, "para_blocks": None}]}) is None
