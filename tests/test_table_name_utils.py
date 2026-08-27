"""表名规则纯函数的 case 表测试。

case 全部来自 30 个真实文档 / 1718 张表的抽样，不是构造出来的。
"""

from __future__ import annotations

import pytest

from service.table_name_utils import (
    _contains_table_type_word,
    _looks_like_invalid_candidate,
    _looks_like_non_caption,
)


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
