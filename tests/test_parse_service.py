"""table_service 表格标题提取测试。"""

from __future__ import annotations

import pytest

import service.table_service as parse_service


@pytest.mark.anyio
async def test_parse_tables_llm_failure_fallback_last_line(monkeypatch):
    """LLM 失败时，严格回退到表格前最后一行。"""

    async def _mock_chat_completion(*args, **kwargs):
        raise TimeoutError("mock timeout")

    monkeypatch.setattr(parse_service, "chat_completion", _mock_chat_completion)

    content = """# （七）项目投资现金流量表
单位：万元
<table><tr><td>现金流入</td></tr></table>
"""

    tables = await parse_service.parse_tables(content, "file_x")

    assert len(tables) == 1
    assert tables[0]["table_name"] == "单位：万元"


@pytest.mark.anyio
async def test_parse_tables_llm_can_override_last_line(monkeypatch):
    """LLM 返回有效标题时，覆盖最后一行回退值。"""

    async def _mock_chat_completion(*args, **kwargs):
        return '{"table_name":"项目投资现金流量表", "reason":"根据上文判断"}'

    monkeypatch.setattr(parse_service, "chat_completion", _mock_chat_completion)

    content = """# 3.2 财务测算
本项目测算如下
单位：万元
<table><tr><td>现金流入</td></tr></table>
"""

    tables = await parse_service.parse_tables(content, "file_y")

    assert len(tables) == 1
    assert tables[0]["table_name"] == "项目投资现金流量表"


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_name", ["未知", "未找到明确标题", "无法提取"])
async def test_parse_tables_llm_unknown_like_output_fallback_last_line(monkeypatch, invalid_name):
    """LLM 返回未知/失败类文案时，应触发规则回退。"""

    async def _mock_chat_completion(*args, **kwargs):
        return f'{{"table_name":"{invalid_name}", "reason":"无法判断"}}'

    monkeypatch.setattr(parse_service, "chat_completion", _mock_chat_completion)

    content = """# （七）项目投资现金流量表
单位：万元
<table><tr><td>现金流入</td></tr></table>
"""

    tables = await parse_service.parse_tables(content, "file_unknown")

    assert len(tables) == 1
    assert tables[0]["table_name"] == "单位：万元"


@pytest.mark.anyio
async def test_parse_tables_llm_context_is_max_three_lines(monkeypatch):
    """传给 LLM 的上文最多 3 行。"""
    captured = {"prompt": ""}

    async def _mock_chat_completion(prompt: str, *args, **kwargs):
        captured["prompt"] = prompt
        return '{"table_name":"表名", "reason":""}'

    monkeypatch.setattr(parse_service, "chat_completion", _mock_chat_completion)

    content = """第1行
第2行
第3行
第4行
<table><tr><td>A</td></tr></table>
"""

    await parse_service.parse_tables(content, "file_z")

    context = captured["prompt"].split("上文片段:\n", 1)[1]
    assert context == "第2行\n第3行\n第4行"


@pytest.mark.anyio
async def test_parse_tables_llm_context_uses_previous_table_end_segment(monkeypatch):
    """若最近 3 行内含 </table>，上下文应取 </table> 到当前表之间的文本。"""
    prompts = []

    async def _mock_chat_completion(prompt: str, *args, **kwargs):
        prompts.append(prompt)
        return '{"table_name":"模型表名", "reason":""}'

    monkeypatch.setattr(parse_service, "chat_completion", _mock_chat_completion)

    content = """第一段
<table><tr><td>A</td></tr></table>
单位：万元
项目投资现金流量表
<table><tr><td>B</td></tr></table>
"""

    await parse_service.parse_tables(content, "file_w")

    assert len(prompts) == 2  # 全量调用：每个表都调用一次 LLM
    second_context = prompts[1].split("上文片段:\n", 1)[1]
    assert second_context == "单位：万元\n项目投资现金流量表"
