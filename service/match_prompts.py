"""LLM 匹配阶段的提示词模板。

匹配提示词分两段：
  - 用户可编辑段：说明「要找什么、怎么找」，存字段配置（章节在
    search_config.section_match_prompt，表格在 extraction_field.table_match_prompt）
  - 系统固定段：输出格式指令，恒由 render_match_prompt 追加，用户改不到

之所以把输出格式攥在系统手里：匹配结果靠 re.findall(r"\\d+", response) 抓
整数序号解析，用户若在模板里改写成「返回 JSON」，解析器会把 JSON 里的其它
数字一并当成序号。与抽取阶段的 JSON_OUTPUT_INSTRUCTION 是同一套思路。
"""

from __future__ import annotations

from typing import Optional

# 系统固定段：拼在任何匹配模板末尾，用户不可编辑
MATCH_INDEX_OUTPUT_INSTRUCTION = "\n\n只返回序号，不要输出其他内容。例如：2 或 1,3"

# 章节匹配默认模板。占位符：{section_list} {query} {quantity_hint}
# 注意：默认不含 {quantity_hint}，以保持改造前的行为逐字不变；需要约束数量的
# 用户可自行把该占位符加进模板。
DEFAULT_SECTION_MATCH_PROMPT = (
    "以下是文档中所有章节的序号和标题列表：\n\n"
    "{section_list}\n\n"
    "请找出与查询「{query}」最相关的章节，"
    "返回其序号（多个用逗号分隔）。"
)

# 表格匹配默认模板。占位符：{table_list} {query} {quantity_hint}
DEFAULT_TABLE_MATCH_PROMPT = (
    "以下是文档中所有表格的名称和序号列表：\n\n"
    "{table_list}\n\n"
    "请找出与查询「{query}」最相关的表格，{quantity_hint}"
)


def build_quantity_hint(max_results: Optional[int], unit: str) -> str:
    """按最大返回数量生成数量约束句。unit 为「表格」或「章节」。"""
    n = max_results or 0
    if n > 0:
        return f"最多返回 {n} 个{unit}的序号，按相关性从高到低排序。"
    return f"返回所有匹配{unit}的序号。"


def render_match_prompt(template: str, **variables: str) -> str:
    """渲染匹配模板并追加系统固定输出段。

    用 str.replace 而非 str.format：用户模板里出现未知占位符或裸 `{` 时
    format 会抛 KeyError / ValueError，replace 原样保留不炸。
    """
    out = template or ""
    for key, val in variables.items():
        out = out.replace("{" + key + "}", "" if val is None else str(val))
    return out + MATCH_INDEX_OUTPUT_INSTRUCTION


def build_section_match_prompt(
    section_list: str,
    query: str,
    max_results: Optional[int],
    template: Optional[str] = None,
) -> str:
    """构建章节 LLM 匹配 prompt。template 为空时用系统默认模板。"""
    return render_match_prompt(
        (template or "").strip() or DEFAULT_SECTION_MATCH_PROMPT,
        section_list=section_list,
        query=query,
        quantity_hint=build_quantity_hint(max_results, "章节"),
    )


def build_table_match_prompt(
    table_list: str,
    query: str,
    max_results: Optional[int],
    template: Optional[str] = None,
) -> str:
    """构建表格 LLM 匹配 prompt。template 为空时用系统默认模板。"""
    return render_match_prompt(
        (template or "").strip() or DEFAULT_TABLE_MATCH_PROMPT,
        table_list=table_list,
        query=query,
        quantity_hint=build_quantity_hint(max_results, "表格"),
    )
