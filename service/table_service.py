"""AI 表格名称校验服务：从 Markdown 中提取表格并通过 LLM 识别表名。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import FileTable
from utils.config import get_config
from utils.concurrency import (
    get_limiter,
    register_task_limiter,
    unregister_task_limiter,
    work_item,
)
from utils.llm_client import chat_completion
from utils.page_mapping import lookup_page_num

# 纯文本/规则函数抽到 service.table_name_utils；此处 import 同时充当 re-export，
# 保证 scripts/debug_table_name_extraction.py 的 parse_service._clean_text_line /
# _extract_table_name 等旧引用继续可用。
from service.table_name_utils import (
    _build_llm_context_text,
    _clean_text_line,
    _extract_last_line,
    _extract_table_name,
    _is_unknown_table_name,
)


def _extract_json_obj(text: str) -> Dict:
    """从 LLM 文本中提取 JSON 对象。"""
    text = (text or "").strip()
    if not text:
        return {}

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    inline = re.search(r"\{.*\}", text, re.DOTALL)
    if inline:
        try:
            data = json.loads(inline.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    return {}


async def _extract_table_name_with_llm(
    preceding_text: str,
    table_index: int,
    fallback_name: str,
) -> str:
    """调用 LLM 提取表名，失败时回退到最后一行。"""
    app_cfg = get_config()
    extraction_cfg = app_cfg.extraction
    table_name_cfg = app_cfg.table_name_validation

    max_lines = max(1, table_name_cfg.max_context_lines or 3)
    max_context_length = table_name_cfg.max_context_length or extraction_cfg.max_context_length
    context_text = _build_llm_context_text(preceding_text, max_lines=max_lines) or "(无)"
    if max_context_length and len(context_text) > max_context_length:
        context_text = context_text[-max_context_length:]

    base_url = table_name_cfg.base_url or extraction_cfg.base_url
    model = table_name_cfg.model or extraction_cfg.model
    api_key = table_name_cfg.api_key or extraction_cfg.api_key
    timeout = table_name_cfg.timeout or extraction_cfg.timeout
    retry_count = table_name_cfg.retry_count or extraction_cfg.retry_count
    extra_body = table_name_cfg.extra_body or None

    prompt = (
        "你是文档表格标题抽取助手。\n"
        "请根据给定上文，找出当前表格最贴近的标题。\n"
        "核心要求：table_name 必须【原样复制】上文中出现的标题文字，"
        "逐字照抄，不得改写、概括、缩写、翻译、补充或删减任何字词"
        "（包括年份、单位、编号、括号注释等修饰成分都要完整保留）。\n"
        "若上文有多个候选，选与当前表格最贴近且最完整的那一条。\n"
        "若无法确定标题，table_name 必须输出固定值\"未知\"。\n"
        "不要输出\"无法提取\"、\"未找到明确标题\"等其他失败文案。\n"
        "仅输出 JSON：{\"table_name\":\"...\", \"reason\":\"...\"}\n\n"
        f"表格序号: {table_index}\n"
        f"上文片段:\n{context_text}"
    )

    try:
        response = await chat_completion(
            prompt,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            max_retries=retry_count,
            extra_body=extra_body,
        )
        data = _extract_json_obj(response)
        llm_name = _clean_text_line(str(data.get("table_name", "")))
        if not llm_name or _is_unknown_table_name(llm_name):
            return fallback_name
        return llm_name[:30]
    except Exception as e:
        logger.warning(
            "LLM 表名抽取失败，回退最后一行: table_index={}, type={}, repr={}",
            table_index,
            type(e).__name__,
            repr(e),
        )
        return fallback_name


async def parse_tables(content: str, file_id: str, page_mapping: Optional[List] = None) -> List[Dict]:
    """解析 Markdown 中的 HTML 表格。"""
    table_pattern = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
    matches = list(table_pattern.finditer(content))
    total_table = len(matches)
    logger.info("开始 AI 校验表格名称: file_id={}, 共 {} 个表格", file_id, total_table)

    if total_table == 0:
        return []

    app_cfg = get_config()
    task_semaphore = register_task_limiter(
        "task_table_validation",
        file_id,
        app_cfg.concurrency.task_table_validation,
        {"file_id": file_id, "stage": "tableing"},
    )
    global_semaphore = get_limiter(
        "global_table_validation",
        app_cfg.concurrency.global_table_validation,
    )

    async def _process_single_table(table_index: int, match: re.Match[str]) -> Dict:
        table_content = match.group(0)
        start_pos = match.start()
        preceding_text = content[:start_pos].rstrip()

        fallback_name = _extract_table_name(preceding_text)
        context = {"file_id": file_id, "stage": "tableing", "index": table_index}
        # 标记本张表的等待起点：单文件闸的排队不在运行台展示，
        # 只有端到端指标能把它暴露出来
        with work_item():
            async with task_semaphore.context(context):
                async with global_semaphore.context(context):
                    table_name = await _extract_table_name_with_llm(
                        preceding_text=preceding_text,
                        table_index=table_index,
                        fallback_name=fallback_name,
                    )

        logger.info("表格名称校验完成: file_id={}, {}/{}, name={}", file_id, table_index, total_table, table_name)

        return {
            "file_id": file_id,
            "table_index": table_index,
            "total_table": total_table,
            "table_name": table_name,
            "table_content": table_content,
            "start_pos": match.start(),
            "end_pos": match.end(),
            "page_num": lookup_page_num(page_mapping or [], match.start(), match.end()),
        }

    try:
        tasks = [
            asyncio.create_task(_process_single_table(table_index, match))
            for table_index, match in enumerate(matches, 1)
        ]
        tables: List[Dict] = await asyncio.gather(*tasks)
        tables.sort(key=lambda x: x["table_index"])
    finally:
        unregister_task_limiter("task_table_validation", file_id)

    logger.info("AI 校验表格名称完成: file_id={}, 共 {} 个表格", file_id, total_table)
    return tables


async def save_tables(tables: List[Dict], session: AsyncSession) -> None:
    """将表格信息批量存入 file_table 表。"""
    if not tables:
        return

    for table_data in tables:
        file_table = FileTable(
            file_id=table_data["file_id"],
            table_index=table_data["table_index"],
            total_table=table_data["total_table"],
            table_name=table_data["table_name"],
            table_content=table_data["table_content"],
            start_pos=table_data.get("start_pos", 0),
            end_pos=table_data.get("end_pos", 0),
            page_num=table_data.get("page_num", ""),
        )
        session.add(file_table)

    await session.commit()
    logger.info("表格已保存: {} 个", len(tables))
