"""把 context/rule/chunk_db 字段配置里的 sort_order="asc" 剥掉，回落到新默认 relevance。

前端历来把 sort_order 显式写进 search_config，所以改后端默认值影响不到存量
配置。本脚本是一次性的、可选的补齐动作。

**会覆盖用户此前的显式「正序」选择**，跑之前先用 --backup 存一份。

只处理 sort_order == "asc" 的字段：
- "desc" 是用户明确要倒序，不动
- 缺该键的字段本来就已经在吃新默认值，不用动

单关键词字段剥了也没有行为变化（relevance 在同分时退化成位置升序），真正
会改变排序的是多关键词字段。

用法：
    uv run python scripts/migrate_search_sort_order.py --backup out.json  # 先备份
    uv run python scripts/migrate_search_sort_order.py --dry-run          # 看会改哪些
    uv run python scripts/migrate_search_sort_order.py                    # 实际写库
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from model.database import get_session_factory  # noqa: E402
from model.tables import ExtractionField  # noqa: E402

TARGET_TYPES = {"context", "rule", "chunk_db"}


def _is_target(row: ExtractionField) -> bool:
    """该字段是否需要迁移：三种关键词检索之一，且 sort_order 显式为 asc。"""
    if row.search_type not in TARGET_TYPES:
        return False
    config = row.search_config
    return isinstance(config, dict) and config.get("sort_order") == "asc"


async def main(dry_run: bool, backup_path: str | None) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        rows = (await session.execute(select(ExtractionField))).scalars().all()

        targets = [r for r in rows if _is_target(r)]

        if backup_path:
            payload = [
                {
                    "type_id": r.type_id,
                    "field_id": r.field_id,
                    "field_name": r.field_name,
                    "search_type": r.search_type,
                    "search_config": r.search_config,
                }
                for r in targets
            ]
            Path(backup_path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已备份 {len(payload)} 个字段的原始配置到 {backup_path}")
            return

        multi = 0
        for row in targets:
            config = row.search_config
            kw_count = len(config.get("keywords") or [])
            if kw_count > 1:
                multi += 1
            if not dry_run:
                # JSON 列必须整体替换：原地改 dict 不会被 SQLAlchemy 侦测为脏
                new_config = dict(config)
                new_config.pop("sort_order", None)
                row.search_config = new_config

        print(f"命中 {len(targets)} 个字段（{multi} 个多关键词，排序会真的改变；"
              f"{len(targets) - multi} 个单关键词，退化成原行为无变化）")

        if dry_run:
            print("[dry-run] 未写库")
            return

        await session.commit()
        print(f"已修改 {len(targets)} 个字段")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--backup", metavar="PATH", help="把待迁移字段的原始配置导出到该文件后退出")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.backup))
