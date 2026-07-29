"""结果表大文本列扩容（TEXT → LONGTEXT）的迁移验证。

线上事故（2026-07-28）：extracted_value 为 MySQL TEXT（65535 字节 ≈ 21845 个中文字），
而 page 检索 + use_llm=0 会把整章原文当字段值落库，触发 DataError 1406。
max_length 默认 30000 字符 = 90000 字节，即默认配置就已超限。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from model.database import get_engine
from service.init_service import init_database

# (表名, 列名) —— 均需为 longtext
LONGTEXT_COLUMNS = [
    ("extraction_result", "extracted_value"),
    ("analysis_result", "result_value"),
]


async def _data_type(conn, table_name: str, column_name: str) -> str:
    result = await conn.execute(
        text(
            "SELECT DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table_name, "c": column_name},
    )
    return (result.scalar() or "").lower()


@pytest.mark.anyio
@pytest.mark.parametrize("table_name,column_name", LONGTEXT_COLUMNS)
async def test_result_columns_are_longtext(table_name, column_name):
    """结果表的大文本列必须是 longtext，否则超长抽取值落库即报 1406。"""
    await init_database()  # 幂等：建表 + 补列 + 扩容
    engine = get_engine()
    async with engine.connect() as conn:
        assert await _data_type(conn, table_name, column_name) == "longtext", (
            f"{table_name}.{column_name} 应为 longtext"
        )


@pytest.mark.anyio
async def test_longtext_migration_is_idempotent():
    """连跑两次不报错且类型稳定。

    这条专防「来回改」：若扩容逻辑与其他迁移块的目标类型不一致，
    每次启动都会 ALTER 一遍大表，线上表现为启动缓慢且类型反复横跳。
    """
    await init_database()
    engine = get_engine()
    async with engine.connect() as conn:
        first = {(t, c): await _data_type(conn, t, c) for t, c in LONGTEXT_COLUMNS}

    await init_database()
    engine = get_engine()
    async with engine.connect() as conn:
        second = {(t, c): await _data_type(conn, t, c) for t, c in LONGTEXT_COLUMNS}

    assert first == second
    assert all(v == "longtext" for v in second.values())


@pytest.mark.anyio
async def test_longtext_capacity_exceeds_default_page_max_length():
    """LONGTEXT 容量必须覆盖 page 检索默认 max_length 的最坏字节数。

    max_length 按字符算、MySQL 按字节算，这个单位错配正是事故根因；
    这里把「默认配置的最坏情况」钉成断言，防止将来又缩回 TEXT。
    """
    from service.extraction_service import _DEFAULT_PAGE_MAX_LENGTH

    worst_case_bytes = _DEFAULT_PAGE_MAX_LENGTH * 4  # utf8mb4 最坏 4 字节/字符
    text_limit = 65535
    longtext_limit = 4294967295

    assert worst_case_bytes > text_limit, "默认配置本就超 TEXT 上限——这正是事故根因"
    assert worst_case_bytes < longtext_limit


@pytest.mark.anyio
async def test_overlong_chinese_value_persists():
    """端到端复现线上场景：5 万中文字（约 153KB）必须能落库。

    这是本次修复的直接验证 —— 修复前此处必抛
    DataError 1406 "Data too long for column 'extracted_value'"。
    """
    from sqlalchemy import delete, select

    from model.database import get_session_factory
    from model.tables import ExtractionResult

    await init_database()
    file_id = "t_longtext_probe"
    field_id = "t_longtext_field"
    value = "汉" * 51000  # 153KB，超 TEXT 上限 2.3 倍，与线上量级一致

    factory = get_session_factory()
    async with factory() as session:
        try:
            session.add(
                ExtractionResult(
                    file_id=file_id,
                    field_id=field_id,
                    extracted_value=value,
                    reason="未启用 LLM，直接返回检索原文",
                    source_refs=None,
                )
            )
            await session.commit()

            stored = (
                await session.execute(
                    select(ExtractionResult).where(
                        ExtractionResult.file_id == file_id,
                        ExtractionResult.field_id == field_id,
                    )
                )
            ).scalar_one()
            assert len(stored.extracted_value) == 51000, "超长值必须完整存回，不能被静默截断"
        finally:
            await session.execute(
                delete(ExtractionResult).where(ExtractionResult.file_id == file_id)
            )
            await session.commit()
