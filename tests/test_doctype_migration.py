"""doc_type 血缘 + 项目列保留、project 表存在的迁移验证（项目维度已恢复）。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from model.database import get_engine
from service.init_service import init_database


@pytest.mark.anyio
async def test_lineage_and_project_columns_present():
    await init_database()  # 幂等：建表 + 补列 + 建 project 表
    engine = get_engine()
    async with engine.connect() as conn:
        # 血缘 + 项目 + 运行配置列均存在
        for col in (
            "is_template",
            "parent_type_id",
            "project_id",
            "max_parse_pages",
            "enable_embedding",
        ):
            r = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                    "AND COLUMN_NAME = :c"
                ),
                {"c": col},
            )
            assert r.scalar() == 1, f"doc_type 应有列 {col}"

        # project_id 索引存在
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                "AND INDEX_NAME = 'ix_doc_type_project_id'"
            )
        )
        assert r.scalar() >= 1, "ix_doc_type_project_id 索引应存在"

        # project 表存在
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            )
        )
        assert r.scalar() == 1, "project 表应存在"
