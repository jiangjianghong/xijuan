"""入参在抽取 / 分析链路上的接入。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from model.tables import ExtractionField
from service.extraction_service import _NON_REF_KEYS, _has_real_source_refs


def test_params_key_is_not_a_source_ref():
    """_params 是元数据，不能被当成一条命中。

    否则任何引用了参数的字段哪怕一条都没检索到，也会因为带着 _params 被
    _is_extraction_success 误判成功——_resolved_refs 当初就踩过这个坑。
    """
    assert "_params" in _NON_REF_KEYS
    assert _has_real_source_refs({"_params": {"d": "2026-08-31"}}) is False
    assert _has_real_source_refs({"_params": {"d": "x"}, "关键词": [{"text": "命中"}]}) is True


@pytest.mark.anyio
async def test_snapshot_carries_params():
    from service.extraction_snapshot import FileExtractionSnapshot

    snapshot = FileExtractionSnapshot(
        file_id="f", type_id="t", content="", page_mapping=[],
        page_contents={}, tables=(), chunks=(), params={"d": "2026-08-31"},
    )
    assert snapshot.params == {"d": "2026-08-31"}


@pytest.mark.anyio
async def test_extract_field_result_renders_params(monkeypatch):
    """_extract_field_result 渲染参数并把 _params 并进 source_refs。"""
    from service import extraction_service
    from service.extraction_snapshot import FileExtractionSnapshot

    seen: dict = {}

    async def fake_extract_text_field(file_id, field, snapshot):
        seen["prompt"] = field.text_extract_prompt
        return "值", "原因", {"关键词": [{"text": "命中"}]}, []

    monkeypatch.setattr(
        extraction_service, "extract_text_field", fake_extract_text_field
    )

    field = ExtractionField(
        field_id="f1", type_id="t1", field_name="有效期", source_type="text",
        search_type="context", enabled=1, priority=0,
        text_extract_prompt="今天是<param>d</param>",
    )
    snapshot = FileExtractionSnapshot(
        file_id="f", type_id="t1", content="", page_mapping=[],
        page_contents={}, tables=(), chunks=(), params={"d": "2026-08-31"},
    )

    value, reason, source_refs, _ = await extraction_service._extract_field_result(
        "f", field, snapshot, {}, {},
    )

    assert seen["prompt"] == "今天是2026-08-31"
    assert source_refs["_params"] == {"d": "2026-08-31"}
    # 原 ORM 对象未被就地改写
    assert field.text_extract_prompt == "今天是<param>d</param>"


# ── 分析链路 ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_compute_file_rule_renders_params(monkeypatch):
    """管线分析：expression 里的 <param> 被渲染，_params 进 source_refs。"""
    from service import analysis_service
    from service.analysis_service import FileRuleSnapshot, _compute_file_rule

    seen: dict = {}

    async def fake_judge(resolved_expression, *, system_prompt="", **kwargs):
        seen["expression"] = resolved_expression
        seen["system_prompt"] = system_prompt
        return "true", "符合"

    monkeypatch.setattr(analysis_service, "execute_judge", fake_judge)

    rule = FileRuleSnapshot(
        rule_id="r1", rule_name="是否过期", rule_type="judge",
        depend_fields=("f1",),
        expression="截至<param>d</param>，有效期<field_result>f1</field_result>是否有效",
        web_search=None, system_prompt="你是<param>role</param>",
        is_formatted=False, output_schema=None,
    )
    result = await _compute_file_rule(
        rule, {"f1": "2026-12-31"}, {}, 2,
        params={"d": "2026-08-31", "role": "审核员"},
    )

    assert seen["expression"] == "截至2026-08-31，有效期2026-12-31是否有效"
    assert seen["system_prompt"] == "你是审核员"
    assert result.success is True
    assert result.source_refs["_params"] == {"d": "2026-08-31", "role": "审核员"}


@pytest.mark.anyio
async def test_execute_rule_renders_params(monkeypatch):
    """独立分析：同一份渲染在 execute_rule 侧也生效。"""
    from service import analysis_run_service
    from service.analysis_run_service import AnalysisRuleSnapshot, execute_rule

    seen: dict = {}

    async def fake_judge(resolved_expression, *, system_prompt="", **kwargs):
        seen["expression"] = resolved_expression
        return "true", "符合"

    monkeypatch.setattr(analysis_run_service, "execute_judge", fake_judge)

    rule = AnalysisRuleSnapshot(
        rule_id="r1", type_id="t1", rule_name="是否过期", rule_type="judge",
        expression="截至<param>d</param>是否有效",
        system_prompt="", depend_fields=[], web_search=None, priority=0,
    )
    result = await execute_rule(rule, {}, params={"d": "2026-08-31"})

    assert seen["expression"] == "截至2026-08-31是否有效"
    assert result["source_refs"]["_params"] == {"d": "2026-08-31"}


@pytest.mark.anyio
async def test_execute_rule_without_params_has_no_params_key(monkeypatch):
    from service import analysis_run_service
    from service.analysis_run_service import AnalysisRuleSnapshot, execute_rule

    async def fake_judge(resolved_expression, *, system_prompt="", **kwargs):
        return "true", ""

    monkeypatch.setattr(analysis_run_service, "execute_judge", fake_judge)

    rule = AnalysisRuleSnapshot(
        rule_id="r1", type_id="t1", rule_name="规则", rule_type="judge",
        expression="恒真", system_prompt="", depend_fields=[],
        web_search=None, priority=0,
    )
    result = await execute_rule(rule, {})
    assert result["source_refs"] is None


# ── /file/parse 入参校验 ────────────────────────────────────

_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


@pytest.mark.anyio
async def test_parse_rejects_unknown_param(client: AsyncClient):
    tid = "tp_parse_unknown"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        await client.post(f"/doctype/{tid}/params", json={
            "param_key": "d", "param_name": "日期",
        })
        resp = await client.post(
            f"/file/parse?type_id={tid}&mode=async",
            files={"file": ("t.pdf", _PDF_BYTES, "application/pdf")},
            data={"params": '{"typo_key": "x"}'},
        )
        assert resp.status_code == 400, resp.text
        assert "未知入参" in resp.json()["detail"]
    finally:
        await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_parse_rejects_missing_required_param(client: AsyncClient):
    tid = "tp_parse_required"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        await client.post(f"/doctype/{tid}/params", json={
            "param_key": "d", "param_name": "日期", "required": 1,
        })
        resp = await client.post(
            f"/file/parse?type_id={tid}&mode=async",
            files={"file": ("t.pdf", _PDF_BYTES, "application/pdf")},
        )
        assert resp.status_code == 400, resp.text
        assert "缺少必填入参" in resp.json()["detail"]
    finally:
        await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_parse_rejects_malformed_json(client: AsyncClient):
    tid = "tp_parse_badjson"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        resp = await client.post(
            f"/file/parse?type_id={tid}&mode=async",
            files={"file": ("t.pdf", _PDF_BYTES, "application/pdf")},
            data={"params": "{not json"},
        )
        assert resp.status_code == 400, resp.text
        assert "不是合法 JSON" in resp.json()["detail"]
    finally:
        await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_parse_stores_merged_snapshot(client: AsyncClient, monkeypatch):
    """落库的是合并后的完整快照，不只是传入部分。

    这是本文件里唯一会走通建档的上传测试，故必须把后台管线换掉——否则会真的
    去调 MinerU，测试挂死在外部服务上。
    """
    import importlib

    from model.database import get_session_factory
    from model.tables import File
    from sqlalchemy import select

    # blue_print/__init__.py 把 file_router 这个名字绑给了 APIRouter 对象，
    # `from blue_print import file_router` 拿到的不是模块，故走 import_module
    file_router = importlib.import_module("blue_print.file_router")

    async def noop_pipeline(*args, **kwargs):
        return None

    monkeypatch.setattr(file_router, "_run_pipeline_background", noop_pipeline)

    tid = "tp_parse_merge"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        await client.post(f"/doctype/{tid}/params", json={
            "param_key": "d", "param_name": "日期",
        })
        await client.post(f"/doctype/{tid}/params", json={
            "param_key": "year", "param_name": "年度", "default_value": "2025",
        })
        resp = await client.post(
            f"/file/parse?type_id={tid}&mode=async",
            files={"file": ("t.pdf", _PDF_BYTES, "application/pdf")},
            data={"params": '{"d": "2026-08-31"}'},
        )
        assert resp.status_code == 200, resp.text
        file_id = resp.json()["data"]["file_id"]

        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                select(File).where(File.file_id == file_id)
            )).scalar_one()
            assert row.input_params == {"d": "2026-08-31", "year": "2025"}
    finally:
        await client.delete(f"/doctype/{tid}?force=true")
