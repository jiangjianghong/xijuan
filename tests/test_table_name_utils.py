"""表名规则纯函数的 case 表测试。

case 全部来自 30 个真实文档 / 1718 张表的抽样，不是构造出来的。
"""

from __future__ import annotations

import pytest

from service.table_name_utils import _looks_like_invalid_candidate


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
