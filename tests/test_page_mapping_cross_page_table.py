"""跨页表格末页补锚测试。

MinerU 把跨页表格合并成一个 <table> 输出到 md，middle_json 里只有首页的 table
块携带完整 html，后续页是 {"lines": [], "lines_deleted": true} 空壳。空壳块提不出
探针文本 → 表格覆盖的第 2..N 页一个锚点都没有。而 lookup_page_num 的语义是「取
start_pos 之前最近的锚点页码」，于是表格之后、下一个真实锚点之前的正文继承了表格
**之前**的页码（生产实例：22-23 页的表，其后第 23 页正文被标成第 21 页）。
"""

from utils.page_mapping import build_page_mapping, lookup_page_num


def _text_block(content: str, bbox=None) -> dict:
    block = {"lines": [{"spans": [{"type": "text", "content": content}]}]}
    if bbox is not None:
        block["bbox"] = bbox
    return block


def _table_head(html: str, caption: str = "", bbox=None) -> dict:
    """跨页表格的首页块：caption + 携带完整合并 html 的 table_body。"""
    blocks = []
    if caption:
        blocks.append({
            "type": "table_caption",
            "lines": [{"spans": [{"type": "text", "content": caption}]}],
        })
    blocks.append({
        "type": "table_body",
        "lines": [{"spans": [{"type": "table", "html": html}]}],
    })
    return {"type": "table", "bbox": bbox or [50, 100, 550, 700], "blocks": blocks}


def _table_shell(bbox=None) -> dict:
    """跨页表格的后续页块：lines 被清空，提不出任何文本。"""
    return {
        "type": "table",
        "bbox": bbox or [50, 70, 550, 700],
        "blocks": [{
            "bbox": bbox or [50, 70, 550, 700],
            "lines": [],
            "type": "table_body",
            "lines_deleted": True,
        }],
    }


def _big_table_html(rows: int = 40) -> str:
    body = "".join(
        f"<tr><td>{i}</td><td>管廊建设项目第{i}标段</td><td>{i * 137}</td></tr>"
        for i in range(1, rows + 1)
    )
    return f"<table><tr><td>序号</td><td>项目名称</td><td>投资额</td></tr>{body}</table>"


def test_text_after_cross_page_table_gets_last_page():
    """跨页表格之后的正文必须归到表格末页，而不是继承表格之前的页码。

    构造：第 2 页起一张横跨 2~4 页的表，第 4 页表格结束后接正文。表格后紧邻的
    「备注：」太短不产锚，若无末页补锚，该位置会一路回落到第 1 页的锚点。
    """
    html = _big_table_html()
    md = "\n\n".join([
        "第一页正文甲内容用于定位测试的文本片段",   # p1
        "投资估算调整对照表",                         # p2 表格标题
        html,                                          # p2~p4 合并后的跨页表
        "备注：",                                      # p4 表后短文本(不产锚)
        "第四页正文乙内容用于定位测试的文本片段",   # p4 正文
    ])

    middle = {"pdf_info": [
        {"page_idx": 0, "page_size": [612, 792],
         "para_blocks": [_text_block("第一页正文甲内容用于定位测试的文本片段")]},
        {"page_idx": 1, "page_size": [612, 792], "para_blocks": [
            _text_block("投资估算调整对照表"),
            _table_head(html),
        ]},
        {"page_idx": 2, "page_size": [612, 792], "para_blocks": [_table_shell()]},
        {"page_idx": 3, "page_size": [612, 792], "para_blocks": [
            _table_shell(),
            _text_block("备注："),
            _text_block("第四页正文乙内容用于定位测试的文本片段"),
        ]},
    ]}

    mapping = build_page_mapping(md, middle)
    table_end = md.find("</table>") + len("</table>")

    # 紧贴 </table> 之后的位置属于表格末页(第 4 页)
    assert lookup_page_num(mapping, table_end, table_end + 1) == "4"
    # 表格后的正文同样归第 4 页
    pos = md.find("第四页正文乙内容")
    assert lookup_page_num(mapping, pos, pos + 5) == "4"


def test_single_page_table_gets_no_extra_anchor():
    """单页表格不跨页，不应补出多余锚点。"""
    html = "<table><tr><td>甲乙丙</td><td>1234</td></tr></table>"
    md = f"前文段落内容用于定位测试的文本片段\n\n{html}\n\n后文段落内容用于定位的片段"
    middle = {"pdf_info": [{
        "page_idx": 0, "page_size": [612, 792], "para_blocks": [
            _text_block("前文段落内容用于定位测试的文本片段", [40, 60, 560, 100]),
            _table_head(html, bbox=[40, 120, 560, 400]),
            _text_block("后文段落内容用于定位的片段", [40, 420, 560, 460]),
        ],
    }]}
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 3
    assert [m["page_num"] for m in mapping] == [1, 1, 1]


def test_backfilled_anchor_carries_no_bbox():
    """补的末页锚只是页码分界标记，位置在 </table> 之后，不得挂表格 bbox。

    否则前端会在「表格之后的正文」位置画出整表高亮框。
    """
    html = _big_table_html()
    md = f"第一页正文甲内容用于定位测试的片段\n\n{html}\n\n第三页正文乙内容用于定位的片段"
    middle = {"pdf_info": [
        {"page_idx": 0, "page_size": [612, 792], "para_blocks": [
            _text_block("第一页正文甲内容用于定位测试的片段"),
            _table_head(html),
        ]},
        {"page_idx": 1, "page_size": [612, 792], "para_blocks": [_table_shell()]},
        {"page_idx": 2, "page_size": [612, 792], "para_blocks": [
            _table_shell(),
            _text_block("第三页正文乙内容用于定位的片段"),
        ]},
    ]}
    mapping = build_page_mapping(md, middle)
    table_end = md.find("</table>") + len("</table>")
    backfilled = [m for m in mapping if m["start_pos"] == table_end]
    assert len(backfilled) == 1
    assert backfilled[0]["page_num"] == 3
    assert "bbox" not in backfilled[0]


def test_multiple_tables_pair_by_order():
    """多张表按顺序 1:1 配对：只有跨页的那张补锚，单页表不补。"""
    html1 = _big_table_html(30)
    html2 = "<table><tr><td>单页表</td><td>999</td></tr></table>"
    md = "\n\n".join([
        "第一页正文甲内容用于定位测试的片段",
        html1,                                  # p1~p2 跨页
        "第二页正文乙内容用于定位测试的片段",
        html2,                                  # p3 单页
        "第三页正文丙内容用于定位测试的片段",
    ])
    middle = {"pdf_info": [
        {"page_idx": 0, "para_blocks": [
            _text_block("第一页正文甲内容用于定位测试的片段"),
            _table_head(html1),
        ]},
        {"page_idx": 1, "para_blocks": [
            _table_shell(),
            _text_block("第二页正文乙内容用于定位测试的片段"),
        ]},
        {"page_idx": 2, "para_blocks": [
            _table_head(html2),
            _text_block("第三页正文丙内容用于定位测试的片段"),
        ]},
    ]}
    mapping = build_page_mapping(md, middle)
    end1 = md.find("</table>") + len("</table>")
    assert lookup_page_num(mapping, end1, end1 + 1) == "2"
    # 第二张表是单页表，其后不应出现补锚
    end2 = md.rfind("</table>") + len("</table>")
    assert not [m for m in mapping if m["start_pos"] == end2]


def test_table_count_mismatch_skips_backfill():
    """md 中 <table> 数与 middle 表格组数对不上时放弃补锚（避免错位污染）。

    构造：middle 有两组表格，但 md 只转出了一张（另一张被渲染成图片，扫描件常见）。
    """
    html = _big_table_html()
    md = f"第一页正文甲内容用于定位测试的片段\n\n{html}\n\n第三页正文乙内容用于定位的片段"
    middle = {"pdf_info": [
        {"page_idx": 0, "para_blocks": [
            _text_block("第一页正文甲内容用于定位测试的片段"),
            _table_head(html),
        ]},
        {"page_idx": 1, "para_blocks": [_table_shell()]},
        {"page_idx": 2, "para_blocks": [
            _table_shell(),
            # md 里没有对应 <table> 的第二组表格
            _table_head("<table><tr><td>未转出的表</td></tr></table>"),
            _text_block("第三页正文乙内容用于定位的片段"),
        ]},
    ]}
    mapping = build_page_mapping(md, middle)
    table_end = md.find("</table>") + len("</table>")
    assert not [m for m in mapping if m["start_pos"] == table_end]


def test_shell_only_table_group_is_ignored():
    """空壳块出现在任何有内容的表格块之前（异常数据）→ 忽略，不产生组。"""
    md = "只有文本段落内容用于定位测试的片段"
    middle = {"pdf_info": [
        {"page_idx": 0, "para_blocks": [_table_shell()]},
        {"page_idx": 1, "para_blocks": [
            _text_block("只有文本段落内容用于定位测试的片段"),
        ]},
    ]}
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 1
    assert mapping[0]["page_num"] == 2
