"""page_mapping 工具测试：bbox / page_size 携带与 lookup_bboxes 查找。"""

from utils.page_mapping import build_page_mapping, lookup_bboxes, lookup_page_num


def test_unique_anchor_survives_table_blind_find_poison():
    """旧算法 <table 盲锚会把游标冲过头、殃及后续文本;新唯一锚不再依赖盲锚。

    构造:p2 有一张表但 md 未转出 <table>(扫描件常见,渲染为图片),其后 p4 才有
    真正的 <table>。旧算法处理 p2 表块时 md.find("<table", cursor) 会跳到 p4 的表,
    游标越过 p3 正文 → p3 正文被 strand,页码塌到邻页。新算法用表格 html 全局唯一锚,
    p2 无 html 命中则跳过、不推进游标,p3 正文照常各自归位。
    """
    md = "\n\n".join([
        "第一页正文甲内容AAAA",                                 # p1
        "第二页正文乙内容BBBB",                                 # p2(表未转 HTML)
        "第三页正文丙内容CCCC",                                 # p3 <- 旧算法会 strand
        "<table><tr><td>真实表X</td></tr></table>",             # p4 的表
        "第五页正文戊内容EEEE",                                 # p5
    ])

    def txt(s):
        return {"lines": [{"spans": [{"content": s}]}]}

    middle = {"pdf_info": [
        {"page_idx": 0, "para_blocks": [txt("第一页正文甲内容AAAA")]},
        {"page_idx": 1, "para_blocks": [
            txt("第二页正文乙内容BBBB"),
            {"type": "table", "bbox": [10, 10, 500, 300]},  # 无 html/无 md 对应表
        ]},
        {"page_idx": 2, "para_blocks": [txt("第三页正文丙内容CCCC")]},
        {"page_idx": 3, "para_blocks": [
            txt("第四页正文丁内容DDDD"),
            {"type": "table", "bbox": [10, 10, 500, 300],
             "blocks": [{"type": "table_body", "lines": [
                 {"spans": [{"type": "table",
                             "html": "<table><tr><td>真实表X</td></tr></table>"}]}]}]},
        ]},
        {"page_idx": 4, "para_blocks": [txt("第五页正文戊内容EEEE")]},
    ]}

    mapping = build_page_mapping(md, middle)
    # 关键:p3 正文丙必须落在第 3 页(旧算法会塌到第 2 页)
    pos_c = md.find("第三页正文丙内容CCCC")
    assert lookup_page_num(mapping, pos_c, pos_c + 5) == "3"
    # p5 正文戊落第 5 页
    pos_e = md.find("第五页正文戊内容EEEE")
    assert lookup_page_num(mapping, pos_e, pos_e + 5) == "5"


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


def test_build_page_mapping_table_anchored_by_unique_html():
    """表格块用 table_body span 的 html 作全局唯一锚,携带整表 bbox。"""
    table_html = "<table><tr><td>甲乙丙</td><td>1234</td></tr></table>"
    md = (
        "前文段落内容用于定位测试的文本片段\n\n"
        f"{table_html}\n\n"
        "后文段落内容也用于定位测试的文本片段"
    )
    middle = {"pdf_info": [{
        "page_idx": 0,
        "page_size": [612, 792],
        "para_blocks": [
            {"bbox": [40, 60, 560, 100],
             "lines": [{"spans": [{"content": "前文段落内容用于定位测试的文本片段"}]}]},
            {"type": "table", "bbox": [40, 120, 560, 400],
             "blocks": [{"type": "table_body", "lines": [
                 {"spans": [{"type": "table", "html": table_html}]}]}]},
            {"bbox": [40, 420, 560, 460],
             "lines": [{"spans": [{"content": "后文段落内容也用于定位测试的文本片段"}]}]},
        ],
    }]}
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 3
    table_entry = mapping[1]
    assert table_entry["start_pos"] == md.find("<table")
    assert table_entry["bbox"] == [40, 120, 560, 400]
    assert table_entry["page_size"] == [612, 792]
    assert table_entry["page_num"] == 1


def test_build_page_mapping_table_without_html_no_anchor():
    """表格块无 html 探针(仅 type/bbox)→ 无锚点,不影响其他块。"""
    md = "只有文本段落内容用于定位测试的片段"
    middle = {"pdf_info": [{
        "page_idx": 0,
        "page_size": [612, 792],
        "para_blocks": [
            {"type": "table", "bbox": [1, 2, 3, 4]},
            {"bbox": [40, 60, 560, 100],
             "lines": [{"spans": [{"content": "只有文本段落内容用于定位测试的片段"}]}]},
        ],
    }]}
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 1
    assert mapping[0]["bbox"] == [40, 60, 560, 100]


def test_build_page_mapping_non_unique_text_skipped():
    """文本前缀在 md 中非全局唯一(出现多次)→ 该块不产锚(避免毒化)。"""
    repeated = "完全相同的重复段落内容用于测试唯一性判定逻辑"
    md = f"{repeated}\n\n中间独有段落甲编号777\n\n{repeated}"
    middle = {"pdf_info": [{
        "page_idx": 0,
        "para_blocks": [
            {"lines": [{"spans": [{"content": repeated}]}]},
            {"lines": [{"spans": [{"content": "中间独有段落甲编号777"}]}]},
        ],
    }]}
    mapping = build_page_mapping(md, middle)
    # 重复段落非唯一被跳过,只剩独有段落 1 个锚
    assert len(mapping) == 1
    assert mapping[0]["start_pos"] == md.find("中间独有段落甲编号777")
