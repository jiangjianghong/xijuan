"""模型不可用时，使用局部表题，避免回退到远处章节名。"""
import pytest
import service.table_service as service


@pytest.mark.parametrize("response", [None, '{"table_name":"未知"}'])
@pytest.mark.parametrize("external", [False, True])
async def test_local_title_survives_model_failure(monkeypatch, response, external):
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
    assert result == (external_title if external else internal_title)
