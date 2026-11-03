"""模型不可用时，优先局部关键词候选，无关键词则使用最近文本行。"""
import json

import pytest
import service.table_service as service


@pytest.mark.parametrize("response", [None, '{"table_name":"未知"}'])
@pytest.mark.parametrize("external", [False, True])
async def test_local_text_precedes_internal_title_on_model_failure(monkeypatch, response, external):
    async def chat(*args, **kwargs):
        if response is None:
            raise RuntimeError("模型不可用")
        return response

    monkeypatch.setattr(service, "chat_completion", chat)
    internal_title = "边坝县都瓦乡瓦地行政村瓦自然村道路维修项目投资概算(预算)表"
    external_title = "7.1.1. 劳动力调查情况表（195人）"
    preceding = "第6章 项目投融资与财务方案\n\n详见总概算。"
    if external:
        preceding += "\n\n" + external_title
    result = await service._extract_table_name_with_llm(
        preceding, 19, "第6章 项目投融资与财务方案",
        f'<table><tr><td colspan="9">{internal_title}</td></tr></table>',
    )
    assert result == (external_title if external else "详见总概算。")


@pytest.mark.parametrize("response_kind", ["correct", "unknown", "invalid_json", "missing_name", "null_name", "timeout"])
async def test_traffic_caption_survives_model_output_and_failure(monkeypatch, response_kind):
    """59 库真实案例：单位括注内的中文逗号不能让表题退回前面的图名。"""
    title = "表 1.1.2-1 交通量预测最终结果（单位:辆/日，小客车）"

    async def chat(*args, **kwargs):
        if response_kind == "timeout":
            raise TimeoutError("模拟超时")
        return {
            "correct": json.dumps({"table_name": title}, ensure_ascii=False),
            "unknown": '{"table_name":"未知"}',
            "invalid_json": "非 JSON 响应",
            "missing_name": '{}',
            "null_name": '{"table_name":null}',
        }[response_kind]

    monkeypatch.setattr(service, "chat_completion", chat)
    content = (
        "图 1.1.1-1 项目地理位置图\n\n"
        "# 1.1.2 交通量预测及技术标准\n\n"
        "# 1、交通量预测\n\n" + title + "\n\n"
        "<table><tr><td>特征年</td><td>路段</td><td>客车</td><td>货车</td><td>汽车</td></tr></table>"
    )
    tables = await service.parse_tables(content, "traffic_caption_regression")
    assert tables[0]["table_name"] == title


async def test_internal_title_is_used_when_local_text_is_empty(monkeypatch):
    async def chat(*args, **kwargs):
        raise TimeoutError("模拟超时")

    monkeypatch.setattr(service, "chat_completion", chat)
    result = await service._extract_table_name_with_llm(
        "", 1, "", '<table><tr><td colspan="2">投资概算表</td></tr></table>',
    )
    assert result == "投资概算表"
