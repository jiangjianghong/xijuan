"""审计表名质量：对库内已落库文档，统计新规则下的表名指标。

用法:
  uv run python scripts/audit_table_names.py --limit 30

指标:
  原文可命中率 —— 表名是否为 markdown 子串。默认 contains 匹配靠它命中表格，
                  这是表名唯一的硬性可用性指标。
  标准表题数   —— 形如「表 3-6 ...」的表名数量，越多说明标准表题保得越好。

做法: 把库内已有的表名当作 LLM 候选喂进当前规则链复算，因此只反映后处理侧的
      变化，不涉及 LLM 调用（也就不受模型端点可用性影响）。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from model.database import get_engine, get_session_factory
from service.table_name_utils import (
    _clean_text_line,
    _extract_table_name,
    _is_unknown_table_name,
    _resolve_continuation_names,
)

TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
STD_CAP_RE = re.compile(r"^(表|附表|图|附图)\s*[\d一二三四五六七八九十]")


def _recompute(cands, precedings, content, matches, page_nums):
    """用当前规则复算表名（把库内已有名字当作 LLM 候选）。"""
    out = []
    for cand, preceding in zip(cands, precedings):
        cleaned = _clean_text_line(cand or "")
        if not cleaned or _is_unknown_table_name(cleaned):
            out.append(_extract_table_name(preceding))
        else:
            out.append(cleaned[:30])
    return _resolve_continuation_names(out, content, matches, page_nums)


async def main(limit: int) -> None:
    try:
        await _audit(limit)
    finally:
        # 显式关连接池：否则解释器退出时 aiomysql 在已关闭的 event loop 上清理，
        # 会往 stderr 刷一堆 "RuntimeError: Event loop is closed" 淹没审计结果
        await get_engine().dispose()


async def _audit(limit: int) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT file_id, COUNT(*) n FROM file_table "
                "GROUP BY file_id HAVING n BETWEEN 15 AND 90 "
                "ORDER BY RAND(42) LIMIT :lim"
            ),
            {"lim": limit},
        )
        file_ids = [row["file_id"] for row in result.mappings()]

    stats = {"before": [0, 0], "after": [0, 0]}
    total = 0
    for file_id in file_ids:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT file_content FROM file_content WHERE file_id=:f"),
                {"f": file_id},
            )
            content = result.scalar_one_or_none()
            if not content:
                continue
            result = await session.execute(
                text(
                    "SELECT table_name, page_num FROM file_table "
                    "WHERE file_id=:f ORDER BY table_index"
                ),
                {"f": file_id},
            )
            rows = list(result.mappings())

        matches = list(TABLE_RE.finditer(content))
        if len(matches) != len(rows):
            continue

        cands = [r["table_name"] for r in rows]
        pages = [r["page_num"] or "" for r in rows]
        precedings = [content[: m.start()].rstrip() for m in matches]
        after = _recompute(cands, precedings, content, matches, pages)

        lowered = content.lower()
        total += len(rows)
        for key, names in (("before", cands), ("after", after)):
            stats[key][0] += sum(1 for n in names if n and n.lower() in lowered)
            stats[key][1] += sum(1 for n in names if STD_CAP_RE.match(n or ""))

    print(f"文档 {len(file_ids)} 个 / 表格 {total} 张")
    for key, label in (("before", "现状"), ("after", "新规则")):
        hit, cap = stats[key]
        rate = hit / total * 100 if total else 0.0
        print(f"  {label:<6} 原文可命中 {hit:>5} ({rate:5.1f}%)   标准表题 {cap:>4}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30, help="抽样文档数")
    asyncio.run(main(parser.parse_args().limit))
