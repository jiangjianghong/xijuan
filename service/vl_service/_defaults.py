"""VL 抽取方法的内置 prompt 模板默认值。

来源：docs/VL端到端抽取方法.md §5（vl_progressive 每批 prompt） 与 §6（vl_locate 定位 prompt）。

用户可在 vl_config 里通过 batch_prompt_template / locate_prompt_template 覆盖。
"""

from __future__ import annotations


# vl_progressive 每批 prompt 模板。占位符：
#   {history}, {field_hints}, {page_label}, {total_pages}
DEFAULT_BATCH_PROMPT = (
    "{history}"
    "你正在逐页阅读一份文档，需要关注以下信息：{field_hints}\n\n"
    "当前是{page_label}（共{total_pages}页）。\n"
    "如果当前页包含上述相关信息，请输出精简摘要（保留关键数字、名称、金额等）。\n"
    "如果当前页无相关信息（如封面、目录、说明性文字），请仅输出\"无相关信息\"。"
)


# vl_locate 第一轮定位 prompt 模板。占位符：
#   {field_hints}, {page_labels}, {position_map}, {grid_rows}, {grid_cols}
DEFAULT_LOCATE_PROMPT = (
    "这张图片是一份文档的缩略图网格（{grid_rows}行×{grid_cols}列），"
    "包含第 {page_labels} 页。\n"
    "位置对应关系：{position_map}\n\n"
    "请判断哪些页面包含以下信息：{field_hints}\n\n"
    "选择标准——选择以下类型的页面：\n"
    "1. 封面/首页（包含企业名称的标题页）\n"
    "2. 正式报表页（资产负债表、利润表、现金流量表等，以完整表格形式呈现）\n"
    "3. 协议/合同的关键条款页（金额、签署方等核心条款）\n"
    "4. 包含汇总数据的表格页（如有明显的数字表格且与所需信息直接相关）\n\n"
    "不要选择：纯文字附注段落、审计意见页、目录页、空白页。\n\n"
    "注意：只能从 [{page_labels}] 中选择，不要返回其他页码。\n"
    "请只返回JSON格式：{{\"found_pages\": [页码数字列表], \"reason\": \"简要说明\"}}\n"
    "如果这几页都不包含相关信息，返回：{{\"found_pages\": [], \"reason\": \"无相关内容\"}}"
)


DEFAULT_MAX_PIXELS = 4_000_000
