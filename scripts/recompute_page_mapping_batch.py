"""存量 page_mapping 批量重算脚本。

用落库的 md + middle_json 重算 `file_content.page_mapping`，并把 `file_table` /
`file_chunk` 里已落库的 `page_num` 一并按新映射刷新（两张表都存了 start_pos /
end_pos，重查 `lookup_page_num` 即可，无需重跑 MinerU 解析或 LLM 表名识别）。

单文件版接口是 `POST /file/{id}/recompute_page_mapping`；本脚本是它的全库批量版，
额外负责下游 page_num 的回填。

用法：
    uv run python scripts/recompute_page_mapping_batch.py --dry-run   # 只看影响面
    uv run python scripts/recompute_page_mapping_batch.py             # 实际写库
    uv run python scripts/recompute_page_mapping_batch.py --file-id abc123  # 指定文件
    uv run python scripts/recompute_page_mapping_batch.py --limit 20         # 限量试跑

注意：抽取结果 `extraction_result.source_refs` 里的 bboxes / page_num 是抽取当时的
快照，本脚本**不**改写它们——那需要重跑抽取（`POST /file/{id}/retry/extracting`）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from model.database import get_session_factory  # noqa: E402
from model.tables import FileChunk, FileContent, FileTable  # noqa: E402
from utils.page_mapping import build_page_mapping, lookup_page_num  # noqa: E402


def _as_list(raw: Any) -> List[Dict[str, Any]]:
    """page_mapping 列可能是 JSON 字符串或已反序列化的 list。"""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []
    return raw or []


def _page_span(mapping: List[Dict[str, Any]]) -> str:
    pages = [m["page_num"] for m in mapping]
    return f"{min(pages)}-{max(pages)}" if pages else "—"


async def _recompute_one(
    session,
    row: FileContent,
    dry_run: bool,
) -> Optional[Dict[str, Any]]:
    """重算单个文件的 page_mapping 与下游 page_num，返回变更摘要（无变更返回 None）。"""
    old_mapping = _as_list(row.page_mapping)
    new_mapping = build_page_mapping(row.file_content, row.middle_json or "")

    stat: Dict[str, Any] = {
        "file_id": row.file_id,
        "anchors": f"{len(old_mapping)} -> {len(new_mapping)}",
        "pages": f"{_page_span(old_mapping)} -> {_page_span(new_mapping)}",
        "tables_repaged": 0,
        "chunks_repaged": 0,
    }

    tables = (await session.execute(
        select(FileTable).where(FileTable.file_id == row.file_id)
    )).scalars().all()
    chunks = (await session.execute(
        select(FileChunk).where(FileChunk.file_id == row.file_id)
    )).scalars().all()

    # 空映射不回填下游：算不出锚点时把已有 page_num 清成空串是净损失
    if new_mapping:
        for t in tables:
            fresh = lookup_page_num(new_mapping, t.start_pos, t.end_pos)
            if fresh != (t.page_num or ""):
                stat["tables_repaged"] += 1
                if not dry_run:
                    t.page_num = fresh
        for c in chunks:
            fresh = lookup_page_num(new_mapping, c.start_pos, c.end_pos)
            if fresh != (c.page_num or ""):
                stat["chunks_repaged"] += 1
                if not dry_run:
                    c.page_num = fresh

    changed = (
        new_mapping != old_mapping
        or stat["tables_repaged"]
        or stat["chunks_repaged"]
    )
    if not changed:
        return None
    if not dry_run:
        row.page_mapping = new_mapping
    return stat


async def main() -> int:
    parser = argparse.ArgumentParser(description="批量重算存量 page_mapping 及下游页码")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计影响面，不写库")
    parser.add_argument("--file-id", help="只处理指定 file_id")
    parser.add_argument("--limit", type=int, help="最多处理多少个文件（试跑用）")
    args = parser.parse_args()

    stmt = select(FileContent)
    if args.file_id:
        stmt = stmt.where(FileContent.file_id == args.file_id)
    if args.limit:
        stmt = stmt.limit(args.limit)

    mode = "DRY-RUN（不写库）" if args.dry_run else "写库"
    print(f"模式: {mode}")

    async with get_session_factory()() as session:
        rows = (await session.execute(stmt)).scalars().all()
        print(f"待处理文件数: {len(rows)}\n")

        changed: List[Dict[str, Any]] = []
        skipped = 0
        for i, row in enumerate(rows, 1):
            if not row.file_content:
                skipped += 1
                continue
            try:
                stat = await _recompute_one(session, row, args.dry_run)
            except Exception as exc:                       # noqa: BLE001
                # 单个文件的脏数据不该中断整批
                print(f"[{i}/{len(rows)}] {row.file_id} 失败: {exc!r}")
                await session.rollback()
                continue
            if stat is None:
                continue
            changed.append(stat)
            print(f"[{i}/{len(rows)}] {stat['file_id']}  锚点 {stat['anchors']}  "
                  f"页码范围 {stat['pages']}  "
                  f"表格改页 {stat['tables_repaged']}  分块改页 {stat['chunks_repaged']}")

        if not args.dry_run:
            await session.commit()

    print(f"\n{'=' * 60}")
    print(f"有变更的文件: {len(changed)} / {len(rows)}（内容为空跳过 {skipped} 个）")
    print(f"表格改页合计: {sum(c['tables_repaged'] for c in changed)}")
    print(f"分块改页合计: {sum(c['chunks_repaged'] for c in changed)}")
    if args.dry_run:
        print("\nDRY-RUN 未写库。去掉 --dry-run 实际执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
