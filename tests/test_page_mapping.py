"""page_mapping 工具测试：bbox / page_size 携带与 lookup_bboxes 查找。"""

from utils.page_mapping import build_page_mapping, lookup_bboxes


def _make_middle(blocks_per_page):
    """构造最小 middle_json。blocks_per_page: [(page_size, [(bbox, text), ...]), ...]"""
    pdf_info = []
    for page_idx, (page_size, blocks) in enumerate(blocks_per_page):
        para_blocks = []
        for bbox, text in blocks:
            block = {"lines": [{"spans": [{"content": text}]}]}
            if bbox is not None:
                block["bbox"] = bbox
            para_blocks.append(block)
        page = {"page_idx": page_idx, "para_blocks": para_blocks}
        if page_size is not None:
            page["page_size"] = page_size
        pdf_info.append(page)
    return {"pdf_info": pdf_info}


def test_build_page_mapping_carries_bbox_and_page_size():
    md = "第一段内容用于定位测试的文本片段\n\n第二段内容也用于定位测试的文本"
    middle = _make_middle([
        ([612, 792], [
            ([50, 100, 500, 150], "第一段内容用于定位测试的文本片段"),
            ([50, 200, 500, 260], "第二段内容也用于定位测试的文本"),
        ]),
    ])
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 2
    assert mapping[0]["bbox"] == [50, 100, 500, 150]
    assert mapping[0]["page_size"] == [612, 792]
    assert mapping[1]["bbox"] == [50, 200, 500, 260]
    # 原有字段不受影响
    assert mapping[0]["page_num"] == 1
    assert mapping[0]["start_pos"] == 0


def test_build_page_mapping_block_without_bbox():
    """block 无 bbox / 页面无 page_size 时 entry 不带对应键（容错）。"""
    md = "第一段内容用于定位测试的文本片段"
    middle = _make_middle([(None, [(None, "第一段内容用于定位测试的文本片段")])])
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 1
    assert "bbox" not in mapping[0]
    assert "page_size" not in mapping[0]


def test_lookup_bboxes_range_spans_multiple_blocks():
    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 1,
         "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"start_pos": 100, "end_pos": 120, "page_num": 2,
         "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
        {"start_pos": 300, "end_pos": 320, "page_num": 3,
         "bbox": [10, 140, 300, 180], "page_size": [612, 792]},
    ]
    # 范围 [5, 110] 落在第 1、2 块（5 在块 1 锚点之后 → 包含块 1）
    result = lookup_bboxes(mapping, 5, 110)
    assert result == [
        {"page_num": 1, "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"page_num": 2, "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
    ]


def test_lookup_bboxes_legacy_mapping_without_bbox():
    """存量老数据 entry 无 bbox → 跳过，返回空列表。"""
    mapping = [{"start_pos": 0, "end_pos": 20, "page_num": 1}]
    assert lookup_bboxes(mapping, 0, 100) == []


def test_lookup_bboxes_empty_mapping():
    assert lookup_bboxes([], 0, 100) == []


def test_build_page_mapping_table_block_anchor():
    """表格块（type=table，无 lines 文本）应锚定 markdown 中 <table 位置并携带整表 bbox。"""
    md = (
        "前文段落内容用于定位测试的文本\n\n"
        "<table><tr><td>1</td></tr></table>\n\n"
        "后文段落内容也用于定位测试啊"
    )
    middle = {
        "pdf_info": [{
            "page_idx": 0,
            "page_size": [612, 792],
            "para_blocks": [
                {"bbox": [40, 60, 560, 100],
                 "lines": [{"spans": [{"content": "前文段落内容用于定位测试的文本"}]}]},
                {"type": "table", "bbox": [40, 120, 560, 400]},
                {"bbox": [40, 420, 560, 460],
                 "lines": [{"spans": [{"content": "后文段落内容也用于定位测试啊"}]}]},
            ],
        }],
    }
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 3
    table_entry = mapping[1]
    assert table_entry["start_pos"] == md.find("<table")
    assert table_entry["bbox"] == [40, 120, 560, 400]
    assert table_entry["page_size"] == [612, 792]
    assert table_entry["page_num"] == 1


def test_build_page_mapping_table_block_without_bbox_no_anchor():
    """表格块无 bbox 时不产生锚点（无文本前缀可退化，直接跳过）。"""
    md = "<table><tr><td>1</td></tr></table>"
    middle = {"pdf_info": [{"page_idx": 0, "para_blocks": [{"type": "table"}]}]}
    assert build_page_mapping(md, middle) == []


def test_build_page_mapping_table_block_no_table_in_md():
    """markdown 中找不到 <table（如表格未转出 HTML）时不产生锚点、不影响后续块。"""
    md = "只有文本段落内容用于定位测试"
    middle = {
        "pdf_info": [{
            "page_idx": 0,
            "page_size": [612, 792],
            "para_blocks": [
                {"type": "table", "bbox": [1, 2, 3, 4]},
                {"bbox": [40, 60, 560, 100],
                 "lines": [{"spans": [{"content": "只有文本段落内容用于定位测试"}]}]},
            ],
        }],
    }
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 1  # 仅文本块锚点
    assert "bbox" in mapping[0] and mapping[0]["bbox"] == [40, 60, 560, 100]
