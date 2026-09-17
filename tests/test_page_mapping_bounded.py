"""重复正文与表头的完整探针、固定窗口补锚回归。"""

from utils.page_mapping import build_page_mapping, lookup_page_num


def _text(value):
    return {"type": "text", "lines": [{"spans": [{"content": value}]}]}


def _table(html):
    return {"type": "table", "blocks": [
        {"type": "table_body", "lines": [{"spans": [{"html": html}]}]},
    ]}


def _middle(*pages):
    return {"pdf_info": [
        {"page_idx": i, "para_blocks": blocks} for i, blocks in enumerate(pages)
    ]}


def test_unique_full_text_recovers_repeated_prefix():
    """重复投资说明直到末尾才不同，不能因前 40 字重复丢掉第二页。"""
    common = "工程费用和资金安排重复说明用于核实原始页码。" * 12
    first, second = common + "第一期工程", common + "第二期工程"
    intro = "第一页用于建立基准的独有段落"
    md = "\n\n".join([intro, first, second])
    mapping = build_page_mapping(md, _middle([_text(intro), _text(first)], [_text(second)]))
    pos = md.index(second)
    assert lookup_page_num(mapping, pos, pos + len(second)) == "2"


def test_unique_full_html_recovers_repeated_table_header():
    """整表唯一但前缀重复时，第二张表应有自己的首页锚。"""
    header = "<table><tr><td>序号</td><td>项目名称</td><td>投资金额</td></tr>"
    first = header + "<tr><td>1</td><td>项目甲</td><td>100</td></tr></table>"
    second = header + "<tr><td>2</td><td>项目乙</td><td>200</td></tr></table>"
    intro = "第一页建立定位基准的唯一正文"
    md = "\n\n".join([intro, first, second])
    mapping = build_page_mapping(md, _middle([_text(intro), _table(first)], [_table(second)]))
    pos = md.index(second)
    assert lookup_page_num(mapping, pos, pos + len(second)) == "2"


def test_duplicate_text_unique_between_fixed_anchors():
    """全篇重复两次，但已知前后块之间只有一次，可以补回页首。"""
    repeated = "项目建设资金全部用于农村基础设施完善。"
    left, right = "第一页末尾独有审批说明甲", "第二页结尾独有审核说明乙"
    md = "\n\n".join([repeated, left, repeated, right])
    mapping = build_page_mapping(md, _middle(
        [_text(repeated), _text(left)], [_text(repeated), _text(right)],
    ))
    pos = md.rindex(repeated)
    assert lookup_page_num(mapping, pos, pos + len(repeated)) == "2"


def test_full_probe_precedes_later_unique_fragment():
    """前半段重复，后半段唯一，完整段落应锚在起点。"""
    prefix = "反复引用的项目建设规模和资金来源说明。" * 4
    tail = "仅存在于第二页的附加说明文字"
    left, right = "第一页独有开始内容用于定位", "第二页独有末尾内容用于定位"
    md = "\n\n".join([left, prefix + " 其他内容", prefix + " " + tail, right])
    block = {"type": "text", "lines": [{"spans": [{"content": prefix}, {"content": tail}]}]}
    mapping = build_page_mapping(md, _middle([_text(left)], [block, _text(right)]))
    pos = md.index(prefix + " " + tail)
    assert lookup_page_num(mapping, pos, pos + 5) == "2"


def test_ambiguous_window_does_not_guess_occurrence():
    """原文多了同样文本时，不能按第一次出现或简单按顺序强配。"""
    repeated = "重复模板条款不能仅凭出现次序判定归属"
    left, right = "第一页可靠起始边界用于定位", "第四页可靠结束边界用于定位"
    md = "\n\n".join([left, repeated, repeated, repeated, right])
    mapping = build_page_mapping(md, _middle(
        [_text(left)], [_text(repeated)], [_text(repeated)], [_text(right)],
    ))
    assert not any(m["page_num"] in (2, 3) for m in mapping)


def test_two_source_blocks_cannot_claim_one_local_occurrence():
    """窗口内唯一不代表源块唯一；同位置被不同页认领时均不可补锚。"""
    repeated = "同样的条款在解析结果的不同页重复出现"
    left, right = "第一页末尾可靠的独有边界", "第四页开头可靠的独有边界"
    md = "\n\n".join([repeated, left, repeated, right])
    mapping = build_page_mapping(md, _middle(
        [_text(repeated), _text(left)], [_text(repeated)], [_text(repeated)], [_text(right)],
    ))
    assert not any(m["page_num"] in (2, 3) for m in mapping)


def test_window_does_not_use_short_prefix_of_missing_block():
    """缺失的源块不能因为窗口内另一段有相同短前缀就被认领。"""
    prefix = "这是一段在多个位置出现但后文不同的模板正文。" * 10
    missing = prefix + "原文没有渲染的块结尾"
    left, right = "第一页末尾的独有可信内容", "第三页开头的独有可信内容"
    md = "\n\n".join([prefix + "前文内容", left, prefix + "实际另一块", right])
    mapping = build_page_mapping(md, _middle([_text(left)], [_text(missing)], [_text(right)]))
    assert not any(m["page_num"] == 2 for m in mapping)


def test_local_anchors_do_not_shrink_other_search_windows():
    """新补锚不能递推缩小窗口，使原本有歧义的重复块被冒险接纳。"""
    repeated = "窗口中的重复说明没有足够证据确定归属"
    local = "全篇重复但窗口中只出现一次的中间说明"
    left, right = "第一页可靠的左侧固定边界", "第四页可靠的右侧固定边界"
    md = "\n\n".join([local, left, repeated, local, repeated, right])
    mapping = build_page_mapping(md, _middle(
        [_text(local), _text(left)], [_text(local)], [_text(repeated)], [_text(right)],
    ))
    assert any(m["page_num"] == 2 for m in mapping)
    assert not any(m["page_num"] == 3 for m in mapping)


def test_reversed_local_claims_are_both_rejected():
    """各自局部唯一，但原始块顺序与正文顺序相反，不能任择一个补锚。"""
    first, second = "第一段重复正文需要核实实际位置", "第二段重复正文需要核实实际位置"
    left, right = "第一页独有的固定左侧边界", "第四页独有的固定右侧边界"
    md = "\n\n".join([first, second, left, second, first, right])
    mapping = build_page_mapping(md, _middle(
        [_text(first), _text(second), _text(left)], [_text(first)], [_text(second)], [_text(right)],
    ))
    assert not any(m["page_num"] in (2, 3) for m in mapping)


def test_reordered_fixed_blocks_do_not_open_overlapping_windows():
    """同页全局锚的原始块顺序逆序时，窗口不能跨过其他可靠锚。"""
    first, second = "第一段重复模板内容实际仍在第一页", "第二段重复模板内容实际仍在第一页"
    start = "第一页正文开头的独有定位边界"
    earlier, later = "第一页物理位置更靠前的独有内容", "第一页物理位置更靠后的独有内容"
    end = "第二页正文结尾的独有定位边界"
    md = "\n\n".join([first, second, start, earlier, first, second, later, end])
    # 原始块的阅读顺序与 md 在同一页发生交换；第二页同名块未出现在 md。
    middle = _middle(
        [_text(start), _text(later), _text(earlier)],
        [_text(first), _text(second), _text(end)],
    )
    mapping = build_page_mapping(md, middle)
    pos = md.index(later)
    assert lookup_page_num(mapping, pos, pos + len(later)) == "1"
    assert [m['start_pos'] for m in mapping if m['page_num'] == 2] == [md.index(end)]


def test_window_cannot_claim_text_inside_left_anchor_block():
    """左锚只因渲染换行命中短片段时，不能把块后半段认作下一页。"""
    first = "左侧可靠锚点文本"  # >= 8 字，单独片段可命中
    suffix = "后半段内容不应被当作缺失块"
    right = "第二页右侧可靠锚点文本"
    # 窗口之外也有该片段，确保它不会直接成为全局唯一锚。
    md = f"{suffix}\n\n{first}\n{suffix}\n\n{right}"
    left_block = {"type": "text", "lines": [{"spans": [
        {"content": first}, {"content": suffix},
    ]}]}
    missing_block = _text(suffix)
    mapping = build_page_mapping(md, _middle([left_block], [missing_block, _text(right)]))
    assert not any(m["page_num"] == 2 and m["start_pos"] == md.rindex(suffix) for m in mapping)


def test_window_cannot_claim_text_before_right_anchor_fragment():
    """右锚只命中块后半段时，窗口不能吞入该块开头的重复正文。"""
    repeated = "右侧块开头重复正文不属于上一页"
    left, tail = "第一页末尾可靠的唯一定位内容", "第三页末尾可靠的唯一定位内容"
    md = f"{repeated}\n\n{left}\n\n{repeated}\n{tail}"
    right_block = {"type": "text", "lines": [{"spans": [
        {"content": repeated}, {"content": tail},
    ]}]}
    mapping = build_page_mapping(md, _middle([_text(left)], [_text(repeated)], [right_block]))
    assert not any(m["page_num"] == 2 for m in mapping)


def test_cross_page_end_anchor_participates_in_initial_cleaning():
    """跨页末页锚须参与初次清洗，避免先删掉它支持的正确正文锚。"""
    html = "<table><tr><td>跨越三页的完整合并表格内容</td></tr></table>"
    intro, correct = "第一页开头独有的正文段落", "第三页表格后正确的正文段落"
    misplaced, finish = "第二页页脚在正文中异常后置", "第四页结尾独有的正文段落"
    shell = {"type": "table", "blocks": [{"type": "table_body", "lines": []}]}
    md = "\n\n".join([intro, html, correct, misplaced, finish])
    mapping = build_page_mapping(md, _middle(
        [_text(intro), _table(html)], [shell, _text(misplaced)],
        [shell, _text(correct)], [_text(finish)],
    ))
    pos = md.index(correct)
    assert lookup_page_num(mapping, pos, pos + len(correct)) == "3"
