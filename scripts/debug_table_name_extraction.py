"""调试脚本：复跑表格命名并导出模型输入输出到 JSON。

功能：
- 指定 file_id（数据已在数据库）
- 按当前 parse_service 逻辑复跑表名提取
- 强制指定模型（默认 Qwen3-32B）
- 控制 LLM 最大并发（默认 20）
- 导出每张表的模型输入输出、最终表名、页码、表格内容等

用法：
  c:/.../.venv/Scripts/python.exe scripts/debug_table_name_extraction.py \
      --file-id 9170beeb49e2a32f5185395de1a468d3 \
      --model Qwen3-32B \
      --max-concurrency 20 \
      --output data/test_data/table_name_debug_9170beeb49e2a32f5185395de1a468d3.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import service.table_service as parse_service
from model.database import get_session_factory
from model.tables import FileContent, FileTable
from utils.page_mapping import lookup_page_num


def _extract_table_index_from_prompt(prompt: str) -> Optional[int]:
    match = re.search(r"表格序号:\s*(\d+)", prompt)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


async def run_debug(
    file_id: str,
    output_path: Path,
    model_name: str,
    max_concurrency: int,
    sequential: bool,
    verbose: bool,
) -> None:
    # 非 verbose 模式下关闭 parse_service 警告，避免刷屏
    if not verbose:
        logger.disable("service.parse_service")

    session_factory = get_session_factory()

    async with session_factory() as session:
        file_content = (
            await session.execute(
                select(FileContent).where(FileContent.file_id == file_id)
            )
        ).scalar_one_or_none()

        if not file_content:
            raise ValueError(f"file_content 不存在: {file_id}")

        existing_tables = (
            await session.execute(
                select(FileTable)
                .where(FileTable.file_id == file_id)
                .order_by(FileTable.table_index)
            )
        ).scalars().all()
        existing_by_index = {t.table_index: t for t in existing_tables}

    content = file_content.file_content or ""
    page_mapping = file_content.page_mapping or []

    table_pattern = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
    matches = list(table_pattern.finditer(content))

    llm_calls: List[Dict[str, Any]] = []
    llm_by_index: Dict[int, Dict[str, Any]] = {}

    original_chat_completion = parse_service.chat_completion
    semaphore = asyncio.Semaphore(max_concurrency)

    async def wrapped_chat_completion(*args: Any, **kwargs: Any) -> str:
        prompt = kwargs.get("prompt")
        if prompt is None and args:
            prompt = args[0]
        prompt = prompt or ""

        table_index = _extract_table_index_from_prompt(prompt)
        queued_at = time.perf_counter()

        call_record: Dict[str, Any] = {
            "table_index": table_index,
            "prompt": prompt,
            "model": model_name,
            "started_at": datetime.now().isoformat(),
        }

        async with semaphore:
            call_record["queue_wait_ms"] = round((time.perf_counter() - queued_at) * 1000, 2)
            started = time.perf_counter()

            try:
                # 强制覆盖模型
                kwargs["model"] = model_name
                response = await original_chat_completion(*args, **kwargs)
                parsed = parse_service._extract_json_obj(response)
                candidate_name = ""
                if parsed:
                    candidate_name = parse_service._clean_text_line(
                        str(parsed.get("table_name", ""))
                    )

                call_record.update(
                    {
                        "ok": True,
                        "response": response,
                        "response_json": parsed,
                        "response_table_name": candidate_name,
                    }
                )
                if verbose:
                    print(
                        f"[LLM][table={table_index}] OK model={model_name} "
                        f"wait={call_record['queue_wait_ms']}ms cost={round((time.perf_counter() - started) * 1000, 2)}ms "
                        f"name={candidate_name!r}"
                    )
                return response
            except Exception as e:
                call_record.update(
                    {
                        "ok": False,
                        "error_type": type(e).__name__,
                        "error_repr": repr(e),
                    }
                )
                if verbose:
                    print(
                        f"[LLM][table={table_index}] FAIL model={model_name} "
                        f"wait={call_record['queue_wait_ms']}ms cost={round((time.perf_counter() - started) * 1000, 2)}ms "
                        f"{type(e).__name__}: {e}"
                    )
                raise
            finally:
                call_record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
                llm_calls.append(call_record)
                if isinstance(table_index, int):
                    llm_by_index[table_index] = call_record

    parse_service.chat_completion = wrapped_chat_completion

    async def process_one(match_obj: re.Match[str], table_index: int) -> Dict[str, Any]:
        table_content = match_obj.group(0)
        start_pos = match_obj.start()
        end_pos = match_obj.end()

        if verbose:
            print(f"[TABLE][{table_index}] START pos=({start_pos},{end_pos}) len={len(table_content)}")

        preceding_text = content[:start_pos].rstrip()
        fallback_name = parse_service._extract_table_name(preceding_text)
        generated_name = await parse_service._extract_table_name_with_llm(
            preceding_text=preceding_text,
            table_index=table_index,
            fallback_name=fallback_name,
        )

        if verbose:
            print(
                f"[TABLE][{table_index}] DONE fallback={fallback_name!r} generated={generated_name!r}"
            )

        existing = existing_by_index.get(table_index)
        llm_info = llm_by_index.get(table_index, {})

        return {
            "table_index": table_index,
            "generated_table_name": generated_name,
            "fallback_table_name": fallback_name,
            "generated_page_num": lookup_page_num(page_mapping, start_pos, end_pos),
            "start_pos": start_pos,
            "end_pos": end_pos,
            "table_content": table_content,
            "table_content_preview": (table_content[:300] + "...")
            if len(table_content) > 300
            else table_content,
            "existing_db_table_name": existing.table_name if existing else None,
            "existing_db_page_num": existing.page_num if existing else None,
            "llm_input_prompt": llm_info.get("prompt"),
            "llm_output_raw": llm_info.get("response"),
            "llm_output_json": llm_info.get("response_json"),
            "llm_output_table_name": llm_info.get("response_table_name"),
            "llm_ok": llm_info.get("ok"),
            "llm_error_type": llm_info.get("error_type"),
            "llm_error_repr": llm_info.get("error_repr"),
            "llm_queue_wait_ms": llm_info.get("queue_wait_ms"),
            "llm_duration_ms": llm_info.get("duration_ms"),
        }

    try:
        if sequential:
            table_items = []
            for i, m in enumerate(matches, 1):
                table_items.append(await process_one(m, i))
        else:
            tasks = [
                asyncio.create_task(process_one(m, i))
                for i, m in enumerate(matches, 1)
            ]
            table_items = await asyncio.gather(*tasks)
    finally:
        parse_service.chat_completion = original_chat_completion
        logger.enable("service.parse_service")

    # 确保按 table_index 排序
    table_items.sort(key=lambda x: x["table_index"])

    result = {
        "file_id": file_id,
        "generated_at": datetime.now().isoformat(),
        "model": model_name,
        "max_concurrency": max_concurrency,
        "content_length": len(content),
        "page_mapping_count": len(page_mapping),
        "generated_table_count": len(table_items),
        "existing_db_table_count": len(existing_tables),
        "llm_call_count": len(llm_calls),
        "llm_calls": sorted(llm_calls, key=lambda x: (x.get("table_index") or 0)),
        "tables": table_items,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"完成: file_id={file_id}")
    print(f"模型: {model_name}")
    print(f"最大并发: {max_concurrency}")
    print(f"表格数: {len(table_items)}")
    print(f"LLM 调用次数: {len(llm_calls)}")
    print(f"输出文件: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="调试表格命名并导出模型输入输出")
    parser.add_argument("--file-id", required=True, help="文件 ID")
    parser.add_argument(
        "--model",
        default="Qwen3-32B",
        help="LLM 模型名（默认: Qwen3-32B）",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=20,
        help="LLM 最大并发（默认: 20）",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="逐条串行处理表格（忽略并发优势，便于观察日志）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印逐条日志",
    )
    parser.add_argument(
        "--output",
        default="data/test_data/table_name_debug_result.json",
        help="输出 JSON 路径（默认: data/test_data/table_name_debug_result.json）",
    )
    args = parser.parse_args()

    if args.max_concurrency <= 0:
        raise ValueError("--max-concurrency 必须大于 0")

    asyncio.run(
        run_debug(
            file_id=args.file_id,
            output_path=Path(args.output),
            model_name=args.model,
            max_concurrency=args.max_concurrency,
            sequential=args.sequential,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
