"""字段提取服务：对应 design.md 第 7 节。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import ExtractionField, ExtractionResult, FileChunk, FileContent, FileTable
from utils.config import get_config
from utils.llm_client import chat_completion, get_embeddings
from utils.milvus_client import MilvusClient


# ── 章节解析 ────────────────────────────────────────────────

@dataclass
class SectionInfo:
    """章节信息。"""
    index: int
    number: str
    title: str
    start_pos: int
    end_pos: int


def parse_sections(content: str) -> List[SectionInfo]:
    """解析 Markdown 文档中所有章节。

    Args:
        content: Markdown 文档内容。

    Returns:
        章节信息列表。
    """
    pattern = re.compile(r"^#\s+([\d.]+)\s+(.+?)(?:\s+\d+)?\s*$", re.MULTILINE)
    matches = list(pattern.finditer(content))
    sections = []

    for i, match in enumerate(matches):
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections.append(
            SectionInfo(
                index=i,
                number=match.group(1),
                title=match.group(2).strip(),
                start_pos=match.start(),
                end_pos=end_pos,
            )
        )

    return sections


# ── 检索方法 ────────────────────────────────────────────────

async def search_context(
    content: str, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """上下文检索：根据关键词在全文中定位并提取上下文。

    Args:
        content: 文档全文。
        config: search_config 配置，包含:
            - keywords: 关键词列表
            - context_before: 关键词前取的字节数（默认 200）
            - context_after: 关键词后取的字节数（默认 200）

    Returns:
        检索结果列表，每项包含 keyword, position, context。
    """
    keywords = config.get("keywords", [])
    context_before = config.get("context_before", 200)
    context_after = config.get("context_after", 200)

    results = []
    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), content):
            pos = match.start()
            start = max(0, pos - context_before)
            end = min(len(content), pos + len(keyword) + context_after)
            context_text = content[start:end]
            results.append({
                "keyword": keyword,
                "position": pos,
                "context": context_text,
            })

    return results


async def search_section(
    content: str, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """章节检索：匹配章节标题并返回章节内容。

    Args:
        content: 文档全文。
        config: search_config 配置，包含:
            - section_pattern: 章节标题模式
            - match_type: 匹配方式 (exact/fuzzy/contains/llm)
            - threshold: 模糊匹配阈值（默认 0.8）

    Returns:
        检索结果列表，每项包含 section_number, section_title, content。
    """
    section_pattern = config.get("section_pattern", "")
    match_type = config.get("match_type", "contains")
    threshold = config.get("threshold", 0.8)

    sections = parse_sections(content)
    results = []

    for section in sections:
        matched = False

        if match_type == "exact":
            matched = section.title == section_pattern
        elif match_type == "fuzzy":
            ratio = SequenceMatcher(None, section.title, section_pattern).ratio()
            matched = ratio >= threshold
        elif match_type == "contains":
            matched = section_pattern.lower() in section.title.lower()
        elif match_type == "llm":
            prompt = f"判断以下章节标题是否与查询匹配。\n\n查询: {section_pattern}\n章节标题: {section.title}\n\n只回答'是'或'否'。"
            try:
                response = await chat_completion(prompt)
                matched = "是" in response
            except Exception as e:
                logger.warning("LLM 章节匹配失败: {}", e)
                matched = False

        if matched:
            section_content = content[section.start_pos:section.end_pos]
            results.append({
                "section_number": section.number,
                "section_title": section.title,
                "content": section_content,
            })

    return results


async def search_rule(
    content: str, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """规则检索：关键词 + 停用词边界扩展。

    Args:
        content: 文档全文。
        config: search_config 配置，包含:
            - keywords: 关键词列表
            - stop_words: 停用词列表
            - direction: 扩展方向 (forward/backward/both)
            - max_length: 最大提取长度（默认 500）

    Returns:
        检索结果列表，每项包含 keyword, extracted_text。
    """
    keywords = config.get("keywords", [])
    stop_words = config.get("stop_words", ["\n\n", "。\n", "\n#"])
    direction = config.get("direction", "both")
    max_length = config.get("max_length", 500)

    results = []

    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), content):
            pos = match.start()
            end_pos = match.end()

            # 向后扩展（找关键词之前最近的停用词）
            search_start = max(0, pos - max_length)
            start = search_start  # 默认扩展到最大范围
            if direction in ("backward", "both"):
                for stop_word in stop_words:
                    idx = content.rfind(stop_word, search_start, pos)
                    if idx != -1:
                        # 取停用词之后的位置，保留最近的（最大的 idx）
                        start = max(start, idx + len(stop_word))

            # 向后扩展
            end = end_pos
            if direction in ("forward", "both"):
                search_end = min(len(content), end_pos + max_length)
                for stop_word in stop_words:
                    idx = content.find(stop_word, end_pos, search_end)
                    if idx != -1:
                        end = min(end, idx) if end != end_pos else idx
                if end == end_pos:
                    end = search_end

            extracted_text = content[start:end].strip()
            results.append({
                "keyword": keyword,
                "extracted_text": extracted_text,
            })

    return results


async def search_chunk_db(
    file_id: str, config: Dict[str, Any], session: AsyncSession
) -> List[Dict[str, Any]]:
    """关系数据库检索：从 file_chunk 表按关键词过滤分块。

    Args:
        file_id: 文件 ID。
        config: search_config 配置，包含:
            - keywords: 关键词列表
            - top_k: 返回条数（默认 5）

    Returns:
        检索结果列表，每项包含 chunk_id, chunk_index, chunk_content。
    """
    keywords = config.get("keywords", [])
    top_k = config.get("top_k", 5)

    stmt = select(FileChunk).where(FileChunk.file_id == file_id)
    result = await session.execute(stmt)
    chunks = result.scalars().all()

    # 按关键词匹配数量排序
    scored_chunks = []
    for chunk in chunks:
        score = sum(1 for kw in keywords if kw.lower() in chunk.chunk_content.lower())
        if score > 0:
            scored_chunks.append((score, chunk))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _, chunk in scored_chunks[:top_k]:
        results.append({
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "chunk_content": chunk.chunk_content,
        })

    return results


async def search_vector_db(
    file_id: str, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """向量数据库检索：将 query_text 向量化后检索 Milvus。

    Args:
        file_id: 文件 ID。
        config: search_config 配置，包含:
            - query_text: 查询文本
            - top_k: 返回条数（默认 5）
            - score_threshold: 分数阈值（可选）

    Returns:
        检索结果列表，每项包含 chunk_id, chunk_index, chunk_content, score。
    """
    query_text = config.get("query_text", "")
    top_k = config.get("top_k", 5)
    score_threshold = config.get("score_threshold")

    if not query_text:
        return []

    # 向量化查询文本
    embeddings = await get_embeddings([query_text])
    if not embeddings:
        return []

    query_vector = embeddings[0]

    # Milvus 检索
    milvus_client = MilvusClient()
    milvus_client.connect()
    milvus_client.ensure_collection()

    results = milvus_client.search(
        query_vector=query_vector,
        top_k=top_k,
        file_id=file_id,
        score_threshold=score_threshold,
    )

    return results


# ── 提取主流程 ──────────────────────────────────────────────

async def extract_table_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> str:
    """表格类字段提取。

    Args:
        file_id: 文件 ID。
        field: ExtractionField ORM 对象。
        session: 数据库会话。

    Returns:
        提取的值。
    """
    # 查询所有表格
    stmt = select(FileTable).where(FileTable.file_id == file_id)
    result = await session.execute(stmt)
    tables = result.scalars().all()

    if not tables:
        return ""

    # 4 种表格匹配方式
    match_type = field.table_match_type or "contains"
    pattern = field.table_name_pattern or ""

    matched_tables = []
    for table in tables:
        matched = False

        if match_type == "exact":
            matched = table.table_name == pattern
        elif match_type == "fuzzy":
            ratio = SequenceMatcher(None, table.table_name, pattern).ratio()
            matched = ratio >= 0.8
        elif match_type == "contains":
            matched = pattern.lower() in table.table_name.lower()
        elif match_type == "llm":
            prompt = f"判断以下表格名称是否与查询匹配。\n\n查询: {pattern}\n表格名称: {table.table_name}\n\n只回答'是'或'否'。"
            try:
                response = await chat_completion(prompt)
                matched = "是" in response
            except Exception as e:
                logger.warning("LLM 表格匹配失败: {}", e)
                matched = False

        if matched:
            matched_tables.append(table)

    if not matched_tables:
        return ""

    # 构建 LLM 输入，用 <search_result> 占位符
    search_results_text = "\n---\n".join([
        f"表格名称: {t.table_name}\n{t.table_content}"
        for t in matched_tables
    ])

    prompt_template = field.table_extract_prompt or "从以下表格中提取信息：\n<search_result>\n请提取相关字段值。"
    llm_input = prompt_template.replace("<search_result>", search_results_text)

    # 调用 LLM 提取
    try:
        extracted_value = await chat_completion(llm_input)
        return extracted_value.strip()
    except Exception as e:
        logger.error("LLM 表格提取失败: {}", e)
        return ""


async def extract_text_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> str:
    """文本类字段提取。

    Args:
        file_id: 文件 ID。
        field: ExtractionField ORM 对象。
        session: 数据库会话。

    Returns:
        提取的值。
    """
    # 获取文件内容
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content:
        return ""

    content = file_content.file_content
    search_type = field.search_type or "context"
    search_config = field.search_config or {}

    # 调用对应的检索方法
    search_results = []
    if search_type == "context":
        search_results = await search_context(content, search_config)
    elif search_type == "section":
        search_results = await search_section(content, search_config)
    elif search_type == "rule":
        search_results = await search_rule(content, search_config)
    elif search_type == "chunk_db":
        search_results = await search_chunk_db(file_id, search_config, session)
    elif search_type == "vector_db":
        search_results = await search_vector_db(file_id, search_config)

    if not search_results:
        return ""

    # 拼接检索结果
    if search_type == "context":
        results_text = "\n---\n".join([r["context"] for r in search_results])
    elif search_type == "section":
        results_text = "\n---\n".join([r["content"] for r in search_results])
    elif search_type == "rule":
        results_text = "\n---\n".join([r["extracted_text"] for r in search_results])
    elif search_type in ("chunk_db", "vector_db"):
        results_text = "\n---\n".join([r["chunk_content"] for r in search_results])
    else:
        results_text = str(search_results)

    # 构建 LLM 输入
    prompt_template = field.text_extract_prompt or "从以下内容中提取信息：\n<search_result>\n请提取相关字段值。"
    llm_input = prompt_template.replace("<search_result>", results_text)

    # 调用 LLM 提取
    try:
        extracted_value = await chat_completion(llm_input)
        return extracted_value.strip()
    except Exception as e:
        logger.error("LLM 文本提取失败: {}", e)
        return ""


async def run_extraction(file_id: str, session: AsyncSession) -> None:
    """执行文件的完整字段提取流程。

    1. 获取所有 enabled=1 的 extraction_field，按 priority 排序
    2. 对每个字段执行提取
    3. 结果写入 extraction_result 表
    4. 单字段失败跳过继续

    Args:
        file_id: 文件 ID。
        session: 数据库会话。
    """
    logger.info("开始字段提取: {}", file_id)

    # 获取所有启用的字段配置，按 priority 排序
    stmt = (
        select(ExtractionField)
        .where(ExtractionField.enabled == 1)
        .order_by(ExtractionField.priority)
    )
    result = await session.execute(stmt)
    fields = result.scalars().all()

    for field in fields:
        try:
            if field.source_type == "table":
                extracted_value = await extract_table_field(file_id, field, session)
            else:
                extracted_value = await extract_text_field(file_id, field, session)

            # 保存结果
            stmt = select(ExtractionResult).where(
                ExtractionResult.file_id == file_id,
                ExtractionResult.field_id == field.field_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.extracted_value = extracted_value
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value=extracted_value,
                )
                session.add(extraction_result)

            await session.commit()
            logger.info("字段提取成功: field_id={}, value={}", field.field_id, extracted_value[:100] if extracted_value else "")

        except Exception as e:
            logger.error("字段提取失败: field_id={}, error={}", field.field_id, e)
            # 保存空值
            stmt = select(ExtractionResult).where(
                ExtractionResult.file_id == file_id,
                ExtractionResult.field_id == field.field_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.extracted_value = ""
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value="",
                )
                session.add(extraction_result)

            await session.commit()

    logger.info("字段提取完成: {}", file_id)
