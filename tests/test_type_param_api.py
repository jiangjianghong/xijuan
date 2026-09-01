"""type_param 表与 CRUD 接口。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_type_param_table_roundtrip():
    """复合主键 (type_id, param_key)：同 key 不同 type 互不冲突。"""
    from model.database import get_session_factory
    from model.tables import TypeParam
    from sqlalchemy import delete, select

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            delete(TypeParam).where(TypeParam.type_id.in_(["tp_a", "tp_b"]))
        )
        session.add(TypeParam(
            type_id="tp_a", param_key="current_date",
            param_name="当前日期", default_value="", required=1, priority=0,
        ))
        session.add(TypeParam(
            type_id="tp_b", param_key="current_date",
            param_name="当前日期", default_value="2026-01-01", required=0, priority=0,
        ))
        await session.commit()

        rows = (await session.execute(
            select(TypeParam).where(TypeParam.type_id.in_(["tp_a", "tp_b"]))
        )).scalars().all()
        assert len(rows) == 2
        assert {r.type_id: r.default_value for r in rows} == {
            "tp_a": "", "tp_b": "2026-01-01",
        }

        await session.execute(
            delete(TypeParam).where(TypeParam.type_id.in_(["tp_a", "tp_b"]))
        )
        await session.commit()


@pytest.mark.anyio
async def test_files_has_input_params_column():
    from model.tables import File
    assert "input_params" in File.__table__.columns


@pytest.mark.anyio
async def test_param_crud(client: AsyncClient):
    tid = "tp_crud"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        resp = await client.post(f"/doctype/{tid}/params", json={
            "param_key": "current_date", "param_name": "当前日期",
            "description": "提交时的系统日期", "default_value": "", "required": 1,
        })
        assert resp.status_code == 200, resp.text

        resp = await client.get(f"/doctype/{tid}/params")
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["param_key"] == "current_date"
        assert items[0]["required"] == 1

        # 同 key 再 POST = 更新
        resp = await client.post(f"/doctype/{tid}/params", json={
            "param_key": "current_date", "param_name": "当前日期",
            "default_value": "2026-01-01", "required": 0,
        })
        assert resp.status_code == 200
        items = (await client.get(f"/doctype/{tid}/params")).json()["data"]
        assert len(items) == 1
        assert items[0]["default_value"] == "2026-01-01"

        resp = await client.delete(f"/doctype/{tid}/params/current_date")
        assert resp.status_code == 200
        assert (await client.get(f"/doctype/{tid}/params")).json()["data"] == []
    finally:
        await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_delete_param_blocked_when_referenced(client: AsyncClient):
    tid = "tp_ref"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        await client.post(f"/doctype/{tid}/params", json={
            "param_key": "d", "param_name": "日期",
        })
        await client.post("/extraction/fields", json={
            "field_id": "tp_ref_f", "type_id": tid, "field_name": "有效期",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["有效期"]},
            "text_extract_prompt": "今天是<param>d</param>，从<search_result>有效期</search_result>提取",
        })

        resp = await client.delete(f"/doctype/{tid}/params/d")
        assert resp.status_code == 409, resp.text
        assert "有效期" in resp.json()["detail"]

        resp = await client.delete(f"/doctype/{tid}/params/d?force=true")
        assert resp.status_code == 200
    finally:
        await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_delete_param_blocked_by_rule_reference(client: AsyncClient):
    tid = "tp_ref_rule"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        await client.post(f"/doctype/{tid}/params", json={
            "param_key": "d", "param_name": "日期",
        })
        # 规则的 expression 必须含至少一个 <field_result>（schema 层既有约束：
        # 不引用任何字段的规则不读文档），故这里用「参数 + 字段」的真实形态
        await client.post("/extraction/fields", json={
            "field_id": "tp_ref_rf", "type_id": tid, "field_name": "有效期",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["有效期"]},
            "text_extract_prompt": "从<search_result>有效期</search_result>提取",
        })
        resp = await client.post("/analysis/rules", json={
            "rule_id": "tp_ref_r", "type_id": tid, "rule_name": "是否过期",
            "rule_type": "judge",
            "expression": "截至<param>d</param>，有效期<field_result>tp_ref_rf</field_result>是否已过期",
            "depend_fields": ["tp_ref_rf"],
        })
        assert resp.status_code == 200, resp.text

        resp = await client.delete(f"/doctype/{tid}/params/d")
        assert resp.status_code == 409, resp.text
        assert "是否过期" in resp.json()["detail"]
    finally:
        await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_delete_type_cascades_params(client: AsyncClient):
    from model.database import get_session_factory
    from model.tables import TypeParam
    from sqlalchemy import select

    tid = "tp_cascade"
    await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    await client.post(f"/doctype/{tid}/params", json={
        "param_key": "d", "param_name": "日期",
    })
    resp = await client.delete(f"/doctype/{tid}?force=true")
    assert resp.status_code == 200, resp.text

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(TypeParam).where(TypeParam.type_id == tid)
        )).scalars().all()
        assert rows == []


# ── 复制 / 导出 / 导入 ──────────────────────────────────────


@pytest.mark.anyio
async def test_copy_from_carries_params_without_remap(client: AsyncClient):
    """param_key 不变，故被复制的字段里的 <param> 占位符天然继续有效。"""
    src, dst = "tp_cp_src", "tp_cp_dst"
    for tid in (src, dst):
        await client.post("/doctype", json={"type_id": tid, "type_name": tid})
    try:
        await client.post(f"/doctype/{src}/params", json={
            "param_key": "d", "param_name": "日期", "default_value": "2026-01-01",
            "required": 1,
        })
        await client.post("/extraction/fields", json={
            "field_id": "tp_cp_f", "type_id": src, "field_name": "有效期",
            "source_type": "text", "search_type": "context",
            "search_config": {"keywords": ["有效期"]},
            "text_extract_prompt": "今天是<param>d</param>，从<search_result>有效期</search_result>提取",
        })

        resp = await client.post(f"/doctype/{dst}/copy_from", json={
            "source_type_id": src,
        })
        assert resp.status_code == 200, resp.text

        params = (await client.get(f"/doctype/{dst}/params")).json()["data"]
        assert len(params) == 1
        assert params[0]["param_key"] == "d"
        assert params[0]["default_value"] == "2026-01-01"
        assert params[0]["required"] == 1

        fields = (await client.get(f"/extraction/fields?type_id={dst}")).json()["data"]
        assert "<param>d</param>" in fields[0]["text_extract_prompt"]
    finally:
        for tid in (src, dst):
            await client.delete(f"/doctype/{tid}?force=true")


@pytest.mark.anyio
async def test_export_import_roundtrip_params(client: AsyncClient):
    src, dst = "tp_ei_src", "tp_ei_dst"
    await client.post("/doctype", json={"type_id": src, "type_name": src})
    try:
        await client.post(f"/doctype/{src}/params", json={
            "param_key": "year", "param_name": "申报年度",
            "description": "四位数字", "default_value": "2025", "required": 0,
        })

        payload = (await client.get(f"/doctype/{src}/export")).json()["data"]
        assert payload["params"][0]["param_key"] == "year"
        assert payload["params"][0]["description"] == "四位数字"

        resp = await client.post("/doctype/import", json={
            "target_type_id": dst, "payload": payload,
        })
        assert resp.status_code == 200, resp.text

        params = (await client.get(f"/doctype/{dst}/params")).json()["data"]
        assert params[0]["param_key"] == "year"
        assert params[0]["default_value"] == "2025"
    finally:
        for tid in (src, dst):
            await client.delete(f"/doctype/{tid}?force=true")
