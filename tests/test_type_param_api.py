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
