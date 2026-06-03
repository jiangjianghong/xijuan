"""doc_type 血缘列保留、项目维度（project_id 列 + project 表）已彻底移除的迁移验证。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from model.database import get_engine
from service.init_service import init_database


@pytest.mark.anyio
async def test_lineage_columns_kept_and_project_dropped():
    await init_database()  # 幂等：建表 + 补列 + 回收项目维度
    engine = get_engine()
    async with engine.connect() as conn:
        # 血缘维度保留
        for col in ("is_template", "parent_type_id"):
            r = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                    "AND COLUMN_NAME = :c"
                ),
                {"c": col},
            )
            assert r.scalar() == 1, f"doc_type 应保留列 {col}"

        # 项目维度移除：列
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_type' "
                "AND COLUMN_NAME = 'project_id'"
            )
        )
        assert r.scalar() == 0, "doc_type.project_id 应已删除"

        # 项目维度移除：表
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            )
        )
        assert r.scalar() == 0, "project 表应已删除"
