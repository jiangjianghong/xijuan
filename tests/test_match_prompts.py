"""LLM 匹配提示词模板纯函数单测（不需要数据库）。"""
from __future__ import annotations

from service.match_prompts import (
    DEFAULT_SECTION_MATCH_PROMPT,
    DEFAULT_TABLE_MATCH_PROMPT,
    MATCH_INDEX_OUTPUT_INSTRUCTION,
    build_quantity_hint,
    build_section_match_prompt,
    build_table_match_prompt,
    render_match_prompt,
)


def test_section_default_prompt_matches_legacy_text():
    """默认模板 + 固定段必须逐字等于改造前发出的 prompt（防行为回归）。"""
    section_list = "1. 第一章 总则\n2. 7.1 评标办法"
    got = build_section_match_prompt(section_list, "评标办法", max_results=0)
    legacy = (
        f"以下是文档中所有章节的序号和标题列表：\n\n"
        f"{section_list}\n\n"
        f"请找出与查询「评标办法」最相关的章节，"
        f"返回其序号（多个用逗号分隔）。\n\n"
        f"只返回序号，不要输出其他内容。例如：2 或 1,3"
    )
    assert got == legacy


def test_table_default_prompt_matches_legacy_text_unlimited():
    table_list = "1. 投标报价表\n2. 主要设备表"
    got = build_table_match_prompt(table_list, "报价", max_results=0)
    legacy = (
        f"以下是文档中所有表格的名称和序号列表：\n\n"
        f"{table_list}\n\n"
        f"请找出与查询「报价」最相关的表格，返回所有匹配表格的序号。\n\n"
        f"只返回序号，不要输出其他内容。例如：2 或 1,3"
    )
    assert got == legacy


def test_table_default_prompt_matches_legacy_text_limited():
    table_list = "1. 投标报价表"
    got = build_table_match_prompt(table_list, "报价", max_results=2)
    legacy = (
        f"以下是文档中所有表格的名称和序号列表：\n\n"
        f"{table_list}\n\n"
        f"请找出与查询「报价」最相关的表格，"
        f"最多返回 2 个表格的序号，按相关性从高到低排序。\n\n"
        f"只返回序号，不要输出其他内容。例如：2 或 1,3"
    )
    assert got == legacy


def test_custom_template_is_used():
    got = build_section_match_prompt(
        "1. 甲章", "甲", max_results=0,
        template="候选：{section_list}\n找：{query}",
    )
    assert got.startswith("候选：1. 甲章\n找：甲")


def test_output_instruction_always_appended():
    """用户模板即便写了别的输出要求，系统固定段仍在末尾。"""
    got = build_table_match_prompt(
        "1. 表", "x", max_results=0, template="{table_list} 请返回 JSON",
    )
    assert got.endswith(MATCH_INDEX_OUTPUT_INSTRUCTION)


def test_unknown_placeholder_does_not_raise():
    """未知占位符与裸花括号原样保留，不抛异常（str.format 会炸，replace 不会）。"""
    got = render_match_prompt("{table_list} {不存在} {", table_list="T")
    assert "{不存在}" in got
    assert "T" in got


def test_quantity_hint_wording():
    assert build_quantity_hint(0, "表格") == "返回所有匹配表格的序号。"
    assert build_quantity_hint(None, "章节") == "返回所有匹配章节的序号。"
    assert build_quantity_hint(3, "表格") == "最多返回 3 个表格的序号，按相关性从高到低排序。"


def test_section_template_supports_quantity_hint_placeholder():
    """默认章节模板不含 quantity_hint（保持现状），但用户可自行启用。"""
    assert "{quantity_hint}" not in DEFAULT_SECTION_MATCH_PROMPT
    assert "{quantity_hint}" in DEFAULT_TABLE_MATCH_PROMPT
    got = build_section_match_prompt(
        "1. 甲章", "甲", max_results=2,
        template="{section_list}\n{quantity_hint}",
    )
    assert "最多返回 2 个章节的序号，按相关性从高到低排序。" in got


def test_search_section_uses_custom_template(monkeypatch):
    """search_section 的 LLM 分支须使用 search_config.section_match_prompt。"""
    import asyncio

    from service import extraction_service

    captured = {}

    async def fake_chat(prompt, **kwargs):
        captured["prompt"] = prompt
        return "1"

    monkeypatch.setattr(extraction_service, "chat_completion", fake_chat)

    content = "# 第一章 总则\n正文甲\n\n# 第二章 评标\n正文乙\n"
    results = asyncio.run(
        extraction_service.search_section(
            content,
            {
                "section_pattern": "评标",
                "section_match_type": "llm",
                "section_match_prompt": "自定义候选：{section_list}／目标：{query}",
            },
        )
    )
    assert captured["prompt"].startswith("自定义候选：")
    assert "／目标：评标" in captured["prompt"]
    assert captured["prompt"].endswith(MATCH_INDEX_OUTPUT_INSTRUCTION)
    assert len(results) == 1


def test_search_section_default_template_unchanged(monkeypatch):
    """不配模板时 prompt 必须与改造前逐字一致。"""
    import asyncio

    from service import extraction_service

    captured = {}

    async def fake_chat(prompt, **kwargs):
        captured["prompt"] = prompt
        return ""

    monkeypatch.setattr(extraction_service, "chat_completion", fake_chat)

    content = "# 第一章 总则\n正文甲\n"
    asyncio.run(
        extraction_service.search_section(
            content, {"section_pattern": "总则", "section_match_type": "llm"}
        )
    )
    assert captured["prompt"] == (
        "以下是文档中所有章节的序号和标题列表：\n\n"
        "1. 第一章 总则\n\n"
        "请找出与查询「总则」最相关的章节，返回其序号（多个用逗号分隔）。\n\n"
        "只返回序号，不要输出其他内容。例如：2 或 1,3"
    )


def test_table_match_prompt_reads_field_column():
    """表格匹配模板取自 field.table_match_prompt，正式路径与调试流须一致。"""
    from types import SimpleNamespace

    field = SimpleNamespace(
        table_match_type="llm",
        table_match_keywords=["报价"],
        table_match_max_results=2,
        table_match_prompt="表清单：{table_list}／要找：{query}／{quantity_hint}",
        table_name_pattern=None,
    )
    got = build_table_match_prompt(
        "1. 投标报价表",
        "、".join(field.table_match_keywords),
        field.table_match_max_results,
        field.table_match_prompt,
    )
    assert got == (
        "表清单：1. 投标报价表／要找：报价／"
        "最多返回 2 个表格的序号，按相关性从高到低排序。"
        "\n\n只返回序号，不要输出其他内容。例如：2 或 1,3"
    )


def test_no_hardcoded_output_instruction_left_in_service():
    """输出格式段只允许存在于 match_prompts.py。

    改造前 extraction_service.py 里有三份各自拼装的匹配 prompt（章节、表格正式
    路径、表格调试流），末句输出格式指令重复三遍且已各自漂移过。这条测试防止
    今后有人再就地写死一份 —— 那样 match_prompts 的模板就管不到它了。
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "service" / "extraction_service.py"
    assert "只返回序号" not in src.read_text(encoding="utf-8")


def test_extraction_field_has_table_match_prompt_column():
    from model.tables import ExtractionField

    assert "table_match_prompt" in set(ExtractionField.__table__.columns.keys())


def test_field_create_accepts_table_match_prompt():
    from model.schemas import ExtractionFieldCreate

    m = ExtractionFieldCreate(
        field_id="t1", field_name="表字段", source_type="table",
        table_match_type="llm", table_match_keywords=["报价"],
        table_match_prompt="清单：{table_list}",
        table_extract_prompt="从 <search_result>报价</search_result> 提取",
    )
    assert m.table_match_prompt == "清单：{table_list}"


def test_field_create_rejects_table_prompt_without_list_placeholder():
    import pytest as _pytest
    from model.schemas import ExtractionFieldCreate

    with _pytest.raises(ValueError, match="table_list"):
        ExtractionFieldCreate(
            field_id="t2", field_name="表字段", source_type="table",
            table_match_type="llm", table_match_keywords=["报价"],
            table_match_prompt="没有候选列表占位符",
            table_extract_prompt="从 <search_result>报价</search_result> 提取",
        )


def test_field_create_ignores_table_prompt_when_match_type_not_llm():
    """非 LLM 匹配时该模板不生效，不做占位符强校验。"""
    from model.schemas import ExtractionFieldCreate

    m = ExtractionFieldCreate(
        field_id="t3", field_name="表字段", source_type="table",
        table_match_type="contains", table_match_keywords=["报价"],
        table_match_prompt="残留的旧模板",
        table_extract_prompt="从 <search_result>报价</search_result> 提取",
    )
    assert m.table_match_prompt == "残留的旧模板"


def test_field_create_rejects_section_prompt_without_list_placeholder():
    import pytest as _pytest
    from model.schemas import ExtractionFieldCreate

    with _pytest.raises(ValueError, match="section_list"):
        ExtractionFieldCreate(
            field_id="s1", field_name="章节字段", source_type="text",
            search_type="section",
            search_config={
                "section_pattern": "评标",
                "section_match_type": "llm",
                "section_match_prompt": "没有候选列表占位符",
            },
            text_extract_prompt="从 <search_result>评标</search_result> 提取",
        )


def test_field_create_accepts_valid_section_prompt():
    from model.schemas import ExtractionFieldCreate

    m = ExtractionFieldCreate(
        field_id="s2", field_name="章节字段", source_type="text",
        search_type="section",
        search_config={
            "section_pattern": "评标",
            "section_match_type": "llm",
            "section_match_prompt": "候选：{section_list}",
        },
        text_extract_prompt="从 <search_result>评标</search_result> 提取",
    )
    assert m.search_config["section_match_prompt"] == "候选：{section_list}"


def test_table_match_prompt_counts_as_dependency():
    """匹配提示词里的 <field_result> 必须被记为依赖，否则进阶字段依赖漏登记。"""
    from types import SimpleNamespace

    from service.extraction_service import collect_depend_fields

    field = SimpleNamespace(
        table_name_pattern=None, table_extract_prompt=None, table_system_prompt=None,
        text_extract_prompt=None, text_system_prompt=None,
        vl_extract_prompt=None, vl_system_prompt=None,
        table_match_prompt="找与 <field_result>up1</field_result> 有关的表：{table_list}",
        table_match_keywords=None, search_config=None, vl_config=None,
        search_type=None, source_type="table",
    )
    assert collect_depend_fields(field) == ["up1"]


def test_section_match_prompt_counts_as_dependency():
    """search_config 里的模板由既有遍历自动覆盖，此处固化该保证。"""
    from types import SimpleNamespace

    from service.extraction_service import collect_depend_fields

    field = SimpleNamespace(
        table_name_pattern=None, table_extract_prompt=None, table_system_prompt=None,
        text_extract_prompt=None, text_system_prompt=None,
        vl_extract_prompt=None, vl_system_prompt=None, table_match_prompt=None,
        table_match_keywords=None, vl_config=None,
        search_config={
            "section_match_prompt": "找 <field_result>up2</field_result>：{section_list}"
        },
        search_type="section", source_type="text",
    )
    assert collect_depend_fields(field) == ["up2"]


def test_resolve_advanced_field_resolves_table_match_prompt():
    from model.tables import ExtractionField
    from service.extraction_service import resolve_advanced_field

    field = ExtractionField(
        field_id="adv", type_id="default", field_name="进阶表字段",
        source_type="table", is_advanced=1,
        table_match_type="llm", table_match_keywords=["报价"],
        table_match_prompt="找与 <field_result>up1</field_result> 有关：{table_list}",
        table_extract_prompt="从 <search_result>报价</search_result> 提取",
    )
    resolved, prov = resolve_advanced_field(field, {"up1": "甲公司"}, {})
    assert resolved.table_match_prompt == "找与 甲公司 有关：{table_list}"
    assert prov["_resolved_refs"]["up1"] == "甲公司"


async def test_match_prompt_defaults_endpoint(client):
    """前端靠该接口取默认模板，故后端是唯一真相来源。"""
    resp = await client.get("/extraction/match-prompt-defaults")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["section"] == DEFAULT_SECTION_MATCH_PROMPT
    assert data["table"] == DEFAULT_TABLE_MATCH_PROMPT
    assert data["output_instruction"] == MATCH_INDEX_OUTPUT_INSTRUCTION
    # VL 两个模板一并下发，前端据此删除本地副本
    assert "{field_hints}" in data["vl_batch"]
    assert "{scan_scope}" in data["vl_batch"]
    assert "{page_labels}" in data["vl_locate"]
