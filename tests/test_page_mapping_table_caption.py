"""table 块含 caption 子块时的锚点回归。

MinerU 把表格上方的标题行识别成若干 `table_caption` 子块，markdown 里它们之间
是硬换行 `  \\n`，而块探针把子块文本用单空格拼接——前 25/40 字前缀一旦跨过第一个
caption 边界就与 md 逐字不符，`count=0`，整块产不出锚点。单页表格 PDF 整页只有
这一个块，page_mapping 因而全空，`search_type=page` 抽取直接报「该文件无
page_mapping」。
"""

from utils.page_mapping import build_page_mapping, lookup_page_num

_CAPTION1 = "产业化阶段设备清单"
_CAPTION2 = "项目名称：应用于新能源汽车多环境减震降噪高聚物复合材料研发及产业化项目"
_CAPTION3 = "期间：2024年1月1日-2025年12月25日"
_TABLE_HTML = "<table><tr><td>序号</td><td>设备名称</td><td>发票不含税金额</td></tr></table>"


def _caption_block(text):
    return {"type": "table_caption", "lines": [{"spans": [{"content": text}]}]}


def _table_block_with_captions(captions, html, bbox=None):
    blocks = [_caption_block(c) for c in captions]
    blocks.append({
        "type": "table_body",
        "lines": [{"spans": [{"type": "table", "html": html}]}],
    })
    block = {"type": "table", "blocks": blocks}
    if bbox is not None:
        block["bbox"] = bbox
    return block


def test_table_block_with_captions_produces_anchor():
    """caption 与表体之间隔着 markdown 硬换行时，该块仍须产出锚点。"""
    md = f"{_CAPTION1}  \n{_CAPTION2}  \n{_CAPTION3}  \n\n{_TABLE_HTML}"
    middle = {"pdf_info": [{
        "page_idx": 0,
        "page_size": [612, 792],
        "para_blocks": [
            _table_block_with_captions(
                [_CAPTION1, _CAPTION2, _CAPTION3], _TABLE_HTML, bbox=[40, 60, 560, 400]
            )
        ],
    }]}

    mapping = build_page_mapping(md, middle)

    assert mapping, "含 caption 的表格块必须产出锚点，否则单页表格 PDF 的 page_mapping 全空"
    assert mapping[0]["page_num"] == 1
    # 锚点应落在块的真实起始（首个 caption 在 md 开头）
    assert mapping[0]["start_pos"] == 0
    assert mapping[0]["bbox"] == [40, 60, 560, 400]
    assert mapping[0]["page_size"] == [612, 792]


def test_captioned_table_page_lookup_across_pages():
    """跨页场景：第 2 页带 caption 的表格不得把页码塌回第 1 页。"""
    page1_text = "第一页正文段落内容用于定位测试甲"
    md = f"{page1_text}\n\n{_CAPTION1}  \n{_CAPTION2}  \n\n{_TABLE_HTML}"
    middle = {"pdf_info": [
        {"page_idx": 0, "para_blocks": [
            {"lines": [{"spans": [{"content": page1_text}]}]}
        ]},
        {"page_idx": 1, "para_blocks": [
            _table_block_with_captions([_CAPTION1, _CAPTION2], _TABLE_HTML)
        ]},
    ]}

    mapping = build_page_mapping(md, middle)

    pos = md.find(_CAPTION1)
    assert lookup_page_num(mapping, pos, pos + 5) == "2"


def test_caption_only_block_without_table_body():
    """对照组：首个 caption 长于 25 字时前缀不跨界，修复前后都应正常定位。

    锁住「首片段够长」这条本来就能走通的路径，防止逐片段兜底反而改坏它。
    """
    md = f"前置正文段落内容用于定位测试乙\n\n{_CAPTION2}  \n{_CAPTION3}"
    middle = {"pdf_info": [{
        "page_idx": 0,
        "para_blocks": [
            {"lines": [{"spans": [{"content": "前置正文段落内容用于定位测试乙"}]}]},
            {"type": "table", "blocks": [
                _caption_block(_CAPTION2), _caption_block(_CAPTION3),
            ]},
        ],
    }]}

    mapping = build_page_mapping(md, middle)

    assert len(mapping) == 2
    assert mapping[1]["start_pos"] == md.find(_CAPTION2)
