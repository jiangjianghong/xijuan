"""doc_type 新列与 project 表迁移验证。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from model.database import get_engine
from service.init_service import init_database


@pytest.mark.anyio
async def test_doctype_new_columns_and_project_table_exist():
    await init_database()  # 幂等：建表 + 补列
    engine = get_engine()
    async with engine.connect() as conn:
        for col in ("is_template", "parent_type_id", "project_id"):
            r = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                    "AND COLUMN_NAME = :c"
                ),
                {"c": col},
            )
            assert r.scalar() == 1, f"doc_type 缺少列 {col}"

        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            )
        )
        assert r.scalar() == 1, "缺少 project 表"
