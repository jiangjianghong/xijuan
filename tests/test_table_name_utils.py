"""表名规则纯函数的 case 表测试。

case 全部来自 30 个真实文档 / 1718 张表的抽样，不是构造出来的。
"""

from __future__ import annotations

import re

import pytest

from service.table_name_utils import (
    _contains_table_type_word,
    _extract_last_line,
    _extract_table_name,
    _gap_has_title,
    _is_same_page,
    _is_unknown_table_name,
    _looks_like_invalid_candidate,
    _looks_like_non_caption,
    _resolve_continuation_names,
)

TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)


def _matches(content: str):
    return list(TABLE_RE.finditer(content))


@pytest.mark.parametrize(
    "name",
    [
        "![](images/3a7f.jpg)",                      # markdown 图片
        '<table><tr><td rowspan="2" col',            # 被截断的 HTML 残片
        "</td></tr></table>",                        # HTML 闭合标签
        r"\begin{array}{c}a\\b\end{array}",          # LaTeX 环境
        r"y = \frac{a}{b}",                          # LaTeX 命令
        "",                                          # 空串
    ],
)
def test_invalid_candidate_rejects_structural_garbage(name):
    assert _looks_like_invalid_candidate(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "表 3-6 现状供水管道统计表",
        "工程款支付表($)",        # 含 $ 但不是公式：不能因为一个美元符号就判废
        "成本对比表 A/B 方案",
        "单位：万元",
    ],
)
def test_invalid_candidate_keeps_real_names(name):
    assert _looks_like_invalid_candidate(name) is False


@pytest.mark.parametrize(
    "name",
    [
        "1、货币资金",                    # 行首阿拉伯数字序号
        "2、应收账款",
        "一、申报书正文",                  # 行首中文数字序号
        "（一）本企业的母公司",             # 行首括号序号
        "(2) 年初、年末余额",              # 半角括号序号；含顿号但不靠顿号判废
        "3.2 财务测算",                   # 行首章节号
        "2.居民点分类",                   # 行首「数字.」
        "2025年5月",                     # 纯日期
        "2025年2月12日",
        "1:50",                          # 比例尺
        "1：500",
        "编制单位：安徽全柴动力股份有限公司",   # 以「公司」结尾
        "注：请在会后将书面意见交我院。",       # 含真句读
        "（除特别注明外，金额单位均为人民币元）",
        "甲乙双方根据相关法律法规的规定在平等自愿互利互惠的基础上订立本合同",  # 超长且无类型词
        "",
    ],
)
def test_non_caption_rejects_semantic_garbage(name):
    assert _looks_like_non_caption(name) is True


@pytest.mark.parametrize(
    "name",
    [
        # 顿号在中间的标准表题——按顿号判废会误杀这一整类，这是本任务的核心回归点
        "表 3-4 一水厂现状构筑物、设备一览表",
        "表 3-5 二水厂现状构筑物、设备一览表",
        "表 3-1 2013-2015 年歙县城市供水、售水情况统计",
        "市（县、区）天然气终端用气价格表",
        "主要材料、设备价格表",
        "表 3-2 安徽省高速公路特大桥梁、隧道车辆加收通行费标准",
        # 普通表题
        "表 3-6 现状供水管道统计表",
        "单位：万元",
        "涉税基本情况",
        # 超长但含类型词：是表名不是正文句
        "表 3-4 本项目特大桥梁、隧道车辆加收通行费标准一览表 单位",
    ],
)
def test_non_caption_keeps_real_captions(name):
    assert _looks_like_non_caption(name) is False


def test_table_type_word_excludes_ambiguous_single_chars():
    """词表不含单字「式」「单」：否则「单位」「方式」会被当成表类型词。"""
    assert _contains_table_type_word("投资估算表") is True
    assert _contains_table_type_word("现状供水管道一览表") is True
    assert _contains_table_type_word("本项目采用如下方式") is False
    assert _contains_table_type_word("单位工程质量验收") is False


@pytest.mark.parametrize(
    "name",
    ["未知", "unknown", "无法提取", "未找到明确标题", "![](img/a.png)", "1、货币资金", "注：本表数据截止到年底。"],
)
def test_is_unknown_covers_placeholder_and_both_filters(name):
    assert _is_unknown_table_name(name) is True


@pytest.mark.parametrize(
    "name",
    ["表 3-4 一水厂现状构筑物、设备一览表", "投资估算表", "单位：万元"],
)
def test_is_unknown_keeps_real_names(name):
    assert _is_unknown_table_name(name) is False


def test_is_unknown_checks_image_syntax_before_bracket_stripping():
    """"![" 的括号会被 strip 掉，必须对未剥括号的原始候选做格式判断。"""
    assert _is_unknown_table_name("![](images/9c2.jpg)") is True


def test_extract_last_line_skips_garbage_and_walks_up():
    """最后一行是 HTML 残片时，继续向上找——旧实现死取最后一行会整条作废。"""
    preceding = "\n".join([
        "表 3-6 现状供水管道统计表",
        "![](images/4b1.jpg)",
        "</td></tr></table>",
        "",
    ])
    assert _extract_last_line(preceding) == "表 3-6 现状供水管道统计表"


def test_extract_last_line_skips_list_lead_lines():
    preceding = "涉税基本情况\n1、货币资金"
    assert _extract_last_line(preceding) == "涉税基本情况"


def test_extract_last_line_returns_empty_when_nothing_valid():
    assert _extract_last_line("![](a.png)\n</table>") == ""
    assert _extract_last_line("") == ""


def test_extract_table_name_falls_back_to_empty_not_unknown():
    """找不到时返回空串：下游 `table_name or f'表格{index}'` 会兜底成「表格3」，
    而「未知」会绕过这个兜底，还让多张表在 exact 模式下命中同一个名字。"""
    assert _extract_table_name("![](a.png)") == ""


def test_extract_table_name_truncates_to_30_chars():
    long_name = "现状供水管道统计表" * 5
    assert len(_extract_table_name(long_name)) == 30


def test_gap_has_title_ignores_page_numbers_and_garbage():
    assert _gap_has_title("\n51\n") is False
    assert _gap_has_title("\n第 51 页\n") is False
    assert _gap_has_title("\n![](img/a.png)\n") is False
    assert _gap_has_title("\n-----\n") is False
    assert _gap_has_title("\n表 3-7 管网水压统计表\n") is True


def test_same_page_only_when_both_are_plain_equal_numbers():
    assert _is_same_page("30", "30") is True
    assert _is_same_page("30", "31") is False
    assert _is_same_page("31-32", "32") is False   # 跨页表：无法确定，不否决
    assert _is_same_page("", "30") is False
    assert _is_same_page("30", "") is False


def test_continuation_inherits_parent_name_without_suffix():
    """续表存与父表完全相同的名字。

    加 (1)(2) 会让续表在 exact 模式下匹配不到父名，fuzzy 也只有 0.769、
    卡在 0.8 阈值下——而跨页续表恰恰是数据被拆开、最需要被全部命中的。
    """
    content = (
        "<table><tr><td>A</td></tr></table>\n"
        "<table><tr><td>B</td></tr></table>\n"
        "<table><tr><td>C</td></tr></table>"
    )
    names = ["表 3-6 现状供水管道统计表", "", ""]
    pages = ["12", "13", "14"]
    assert _resolve_continuation_names(names, content, _matches(content), pages) == [
        "表 3-6 现状供水管道统计表",
        "表 3-6 现状供水管道统计表",
        "表 3-6 现状供水管道统计表",
    ]


def test_same_page_neighbours_are_not_continuations():
    """同一页上紧邻的两张独立表不该继承前表名。"""
    content = "<table><tr><td>A</td></tr></table>\n<table><tr><td>B</td></tr></table>"
    names = ["投资估算表", "材料价格表"]
    pages = ["7", "7"]
    assert _resolve_continuation_names(names, content, _matches(content), pages) == [
        "投资估算表",
        "材料价格表",
    ]


def test_title_in_gap_breaks_continuation():
    content = (
        "<table><tr><td>A</td></tr></table>\n"
        "表 3-7 管网水压统计表\n"
        "<table><tr><td>B</td></tr></table>"
    )
    names = ["表 3-6 现状供水管道统计表", "表 3-7 管网水压统计表"]
    pages = ["12", "13"]
    assert _resolve_continuation_names(names, content, _matches(content), pages) == [
        "表 3-6 现状供水管道统计表",
        "表 3-7 管网水压统计表",
    ]


def test_empty_parent_name_is_not_inherited():
    """父表自己没名字时不继承，让续表保留自己抽到的名字。"""
    content = "<table><tr><td>A</td></tr></table>\n<table><tr><td>B</td></tr></table>"
    names = ["", "材料价格表"]
    pages = ["7", "8"]
    assert _resolve_continuation_names(names, content, _matches(content), pages) == [
        "",
        "材料价格表",
    ]


def test_missing_page_nums_falls_back_to_gap_rule():
    """page_nums 缺省时不报错，退回纯 gap 判据。"""
    content = "<table><tr><td>A</td></tr></table>\n<table><tr><td>B</td></tr></table>"
    names = ["投资估算表", ""]
    assert _resolve_continuation_names(names, content, _matches(content)) == [
        "投资估算表",
        "投资估算表",
    ]
