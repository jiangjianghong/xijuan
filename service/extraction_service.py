"""字段提取服务：对应 design.md 第 7 节。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import ExtractionField, ExtractionResult, FileChunk, FileContent, FileTable
from utils.config import get_config
from utils.llm_client import chat_completion, get_embeddings
from utils.milvus_client import MilvusClient


# ── JSON 解析辅助 ────────────────────────────────────────────

def parse_llm_json_response(response: str) -> Tuple[str, str]:
    """解析 LLM 返回的 JSON 响应，提取 value 和 reason。

    Args:
        response: LLM 返回的原始响应（可能是 JSON 或纯文本）。

    Returns:
        (value, reason) 元组。如果解析失败，reason 为空。
    """
    response = response.strip()

    # 尝试提取 JSON 块（支持 ```json ... ``` 格式）
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        response = json_match.group(1)

    # 尝试解析 JSON
    try:
        data = json.loads(response)
        if isinstance(data, dict):
            raw_value = data.get("value", "")
            # value 可能是 list/dict（用户自定义格式），转为 JSON 字符串存储
            if isinstance(raw_value, (list, dict)):
                value = json.dumps(raw_value, ensure_ascii=False)
            else:
                value = str(raw_value).strip()
            reason = str(data.get("reason", "")).strip()
            return value, reason
    except json.JSONDecodeError:
        pass

    # 尝试直接提取 JSON 对象
    json_obj_match = re.search(r"\{[^{}]*\"value\"[^{}]*\}", response, re.DOTALL)
    if json_obj_match:
        try:
            data = json.loads(json_obj_match.group())
            raw_value = data.get("value", "")
            if isinstance(raw_value, (list, dict)):
                value = json.dumps(raw_value, ensure_ascii=False)
            else:
                value = str(raw_value).strip()
            reason = str(data.get("reason", "")).strip()
            return value, reason
        except json.JSONDecodeError:
            pass

    # 解析失败，返回原始响应作为 value
    return response.strip(), ""


# ── 搜索结果占位符处理 ────────────────────────────────────────


def replace_search_result_placeholders(
    prompt_template: str,
    results_by_label: Dict[str, str],
    no_result_hint: str = "（未找到 '{}' 的相关内容）"
) -> str:
    """替换 prompt 中的 <search_result>标签</search_result> 占位符。

    Args:
        prompt_template: 包含占位符的提示词模板
        results_by_label: {标签: 搜索结果文本} 字典
        no_result_hint: 无结果时的提示模板，{} 会被替换为标签名

    Returns:
        替换后的提示词
    """
    pattern = r"<search_result>(.+?)</search_result>"

    def replacer(match):
        label = match.group(1).strip()
        if label in results_by_label and results_by_label[label]:
            return results_by_label[label]
        return no_result_hint.format(label)

    return re.sub(pattern, replacer, prompt_template)


def validate_prompt_has_placeholder(prompt: str) -> bool:
    """校验 prompt 中是否包含至少一个有效占位符。"""
    pattern = r"<search_result>.+?</search_result>"
    return bool(re.search(pattern, prompt))


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
            - max_results: 最大返回条数（默认 5）
            - sort_order: 排序方式 asc/desc（默认 asc，按出现位置）

    Returns:
        检索结果列表，每项包含 keyword, position, context。
    """
    keywords = config.get("keywords", [])
    context_before = config.get("context_before", 200)
    context_after = config.get("context_after", 200)
    max_results = config.get("max_results", 5)
    sort_order = config.get("sort_order", "asc")

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
                "start_pos": start,
                "end_pos": end,
            })

    # 按 position 排序
    results.sort(key=lambda x: x["position"], reverse=(sort_order == "desc"))

    # 限制返回条数
    return results[:max_results]


async def search_section(
    content: str, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """章节检索：匹配章节标题并返回章节内容。

    Args:
        content: 文档全文。
        config: search_config 配置，包含:
            - section_pattern: 章节标题模式
            - section_match_type 或 match_type: 匹配方式 (exact/fuzzy/contains/llm)
            - threshold: 模糊匹配阈值（默认 0.8）
            - max_results: 最大返回条数（默认 3）
            - sort_order: 排序方式 asc/desc（默认 asc，按章节顺序）

    Returns:
        检索结果列表，每项包含 section_number, section_title, content。
    """
    section_pattern = config.get("section_pattern", "")
    # 兼容两种字段名：section_match_type（设计文档）和 match_type
    match_type = config.get("section_match_type") or config.get("match_type", "contains")
    threshold = config.get("threshold", 0.8)
    max_results = config.get("max_results", 3)
    sort_order = config.get("sort_order", "asc")

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
                "section_index": section.index,
                "content": section_content,
                "start_pos": section.start_pos,
                "end_pos": section.end_pos,
            })

    # 按章节索引排序
    results.sort(key=lambda x: x["section_index"], reverse=(sort_order == "desc"))

    # 限制返回条数
    return results[:max_results]


async def search_rule(
    content: str, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """规则检索：关键词 + 停用词边界扩展。

    Args:
        content: 文档全文。
        config: search_config 配置，包含:
            - keywords: 关键词列表
            - stop_words: 停用词列表（默认 ["#", "##", "###", "\\n\\n", "\\n", "。", ".", "；", ";"]）
            - direction: 扩展方向 (forward/backward/both)
            - min_length: 最小提取长度（默认 2）
            - max_length: 最大提取长度（默认 200）
            - max_results: 最大返回条数（默认 5）
            - sort_order: 排序方式 asc/desc（默认 asc，按出现位置）

    Returns:
        检索结果列表，每项包含 keyword, position, extracted_text。
    """
    keywords = config.get("keywords", [])
    # 设计文档指定的默认停用词
    default_stop_words = ["#", "##", "###", "\n\n", "\n", "。", ".", "；", ";"]
    stop_words = config.get("stop_words", default_stop_words)
    direction = config.get("direction", "forward")
    min_length = config.get("min_length", 2)
    max_length = config.get("max_length", 200)
    max_results = config.get("max_results", 5)
    sort_order = config.get("sort_order", "asc")

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

            # 向前扩展（找关键词之后最近的停用词）
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

            # 检查最小长度
            if len(extracted_text) < min_length:
                continue

            results.append({
                "keyword": keyword,
                "position": pos,
                "extracted_text": extracted_text,
                "start_pos": start,
                "end_pos": end,
            })

    # 按 position 排序
    results.sort(key=lambda x: x["position"], reverse=(sort_order == "desc"))

    # 限制返回条数
    return results[:max_results]


async def search_chunk_db(
    file_id: str, config: Dict[str, Any], session: AsyncSession
) -> List[Dict[str, Any]]:
    """关系数据库检索：从 file_chunk 表按关键词过滤分块。

    Args:
        file_id: 文件 ID。
        config: search_config 配置，包含:
            - keyword_filter 或 keywords: 关键词（单个字符串或列表）
            - max_results 或 top_k: 返回条数（默认 10）
            - sort_order: 排序方式 asc/desc（默认 asc，按 chunk_index）

    Returns:
        检索结果列表，每项包含 chunk_id, chunk_index, chunk_content。
    """
    # 兼容两种字段名：keyword_filter（设计文档，单个字符串）和 keywords（列表）
    keyword_filter = config.get("keyword_filter")
    keywords = config.get("keywords", [])
    if keyword_filter:
        # 如果是单个字符串，转为列表
        keywords = [keyword_filter] if isinstance(keyword_filter, str) else keyword_filter
    # 兼容两种字段名：max_results（设计文档）和 top_k
    max_results = config.get("max_results") or config.get("top_k", 10)
    sort_order = config.get("sort_order", "asc")

    stmt = select(FileChunk).where(FileChunk.file_id == file_id)
    result = await session.execute(stmt)
    chunks = result.scalars().all()

    # 按关键词过滤
    if keywords:
        filtered_chunks = []
        for chunk in chunks:
            # 检查是否包含任一关键词
            if any(kw.lower() in chunk.chunk_content.lower() for kw in keywords):
                filtered_chunks.append(chunk)
        chunks = filtered_chunks

    # 按 chunk_index 排序（设计文档要求）
    chunks.sort(key=lambda x: x.chunk_index, reverse=(sort_order == "desc"))

    # 限制返回条数
    results = []
    for chunk in chunks[:max_results]:
        results.append({
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "chunk_content": chunk.chunk_content,
            "start_pos": chunk.start_pos,
            "end_pos": chunk.end_pos,
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
        检索结果列表，每项包含 chunk_id, chunk_index, chunk_content, start_pos, end_pos, score。
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

# JSON 输出格式说明（附加到 prompt 末尾）
JSON_OUTPUT_INSTRUCTION = """

请以 JSON 格式返回结果，包含 value（提取的值）和 reason（提取理由/依据）两个字段：
{"value": "提取的值", "reason": "说明从哪里提取、为什么这样提取"}
注意：value 的格式请严格遵循 system_prompt 中的要求（如有），可以是字符串、JSON数组或JSON对象。"""


async def extract_table_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> Tuple[str, str, Optional[Dict]]:
    """表格类字段提取。

    Args:
        file_id: 文件 ID。
        field: ExtractionField ORM 对象。
        session: 数据库会话。

    Returns:
        (extracted_value, reason, source_refs) 元组。
    """
    # 查询所有表格
    stmt = select(FileTable).where(FileTable.file_id == file_id)
    result = await session.execute(stmt)
    tables = result.scalars().all()

    if not tables:
        return "", "", None

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
        return "", "", None

    # 构建 source_refs（表格类使用 _tables 键）
    source_refs: Dict[str, List[Dict]] = {}
    table_refs = []
    for table in matched_tables:
        table_refs.append({
            "type": "table",
            "table_index": table.table_index,
            "table_name": table.table_name,
            "start_pos": table.start_pos,
            "end_pos": table.end_pos,
        })
    source_refs["_tables"] = table_refs

    # 构建按表名分组的结果
    results_text_by_label: Dict[str, str] = {}
    for table in matched_tables:
        table_name = table.table_name or f"表格{table.table_index}"
        content = f"表格名称: {table_name}\n{table.table_content}"
        if table_name in results_text_by_label:
            results_text_by_label[table_name] += "\n---\n" + content
        else:
            results_text_by_label[table_name] = content

    # 构建 LLM 输入
    prompt_template = field.table_extract_prompt or ""
    if not validate_prompt_has_placeholder(prompt_template):
        logger.warning("字段 {} 的 table_extract_prompt 缺少占位符", field.field_id)
        return "", "", None

    llm_input = replace_search_result_placeholders(prompt_template, results_text_by_label)
    llm_input += JSON_OUTPUT_INSTRUCTION

    # 调用 LLM 提取
    try:
        system_prompt = (field.table_system_prompt or "").strip()
        if system_prompt:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": llm_input},
            ]
            response = await chat_completion("", messages=messages)
        else:
            response = await chat_completion(llm_input)
        value, reason = parse_llm_json_response(response)
        return value, reason, source_refs
    except Exception as e:
        logger.error("LLM 表格提取失败: {}", e)
        return "", "", None


async def extract_text_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> Tuple[str, str, Optional[Dict]]:
    """文本类字段提取。

    Args:
        file_id: 文件 ID。
        field: ExtractionField ORM 对象。
        session: 数据库会话。

    Returns:
        (extracted_value, reason, source_refs) 元组。
    """
    # 获取文件内容
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content:
        return "", "", None

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
        return "", "", None

    # 按关键词分组收集 source_refs
    source_refs: Dict[str, List[Dict]] = {}
    for r in search_results:
        keyword = r.get("keyword", "")
        # section 类型没有 keyword，用 section_title 作为 key
        if not keyword and "section_title" in r:
            keyword = r.get("section_title", "")

        ref: Dict[str, Any] = {
            "type": search_type,
            "start_pos": r.get("start_pos"),
            "end_pos": r.get("end_pos"),
        }
        if "chunk_id" in r:
            ref["chunk_id"] = r["chunk_id"]
            ref["chunk_index"] = r["chunk_index"]

        if keyword not in source_refs:
            source_refs[keyword] = []
        source_refs[keyword].append(ref)

    # 按关键词分组搜索结果
    results_by_keyword: Dict[str, List[Dict]] = {}
    for r in search_results:
        kw = r.get("keyword", "")
        if kw:
            if kw not in results_by_keyword:
                results_by_keyword[kw] = []
            results_by_keyword[kw].append(r)

    # 将分组结果转换为文本
    results_text_by_label: Dict[str, str] = {}
    for keyword, items in results_by_keyword.items():
        if search_type == "context":
            results_text_by_label[keyword] = "\n---\n".join([r["context"] for r in items])
        elif search_type == "section":
            results_text_by_label[keyword] = "\n---\n".join([r["content"] for r in items])
        elif search_type == "rule":
            results_text_by_label[keyword] = "\n---\n".join([r["extracted_text"] for r in items])
        elif search_type in ("chunk_db", "vector_db"):
            results_text_by_label[keyword] = "\n---\n".join([r["chunk_content"] for r in items])

    # 构建 LLM 输入
    prompt_template = field.text_extract_prompt or ""
    if not validate_prompt_has_placeholder(prompt_template):
        logger.warning("字段 {} 的 text_extract_prompt 缺少占位符", field.field_id)
        return "", "", None

    llm_input = replace_search_result_placeholders(prompt_template, results_text_by_label)
    llm_input += JSON_OUTPUT_INSTRUCTION

    # 调用 LLM 提取
    try:
        system_prompt = (field.text_system_prompt or "").strip()
        if system_prompt:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": llm_input},
            ]
            response = await chat_completion("", messages=messages)
        else:
            response = await chat_completion(llm_input)
        value, reason = parse_llm_json_response(response)
        return value, reason, source_refs
    except Exception as e:
        logger.error("LLM 文本提取失败: {}", e)
        return "", "", None


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
                extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
            else:
                extracted_value, reason, source_refs = await extract_text_field(file_id, field, session)

            # 保存结果
            stmt = select(ExtractionResult).where(
                ExtractionResult.file_id == file_id,
                ExtractionResult.field_id == field.field_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.extracted_value = extracted_value
                existing.reason = reason
                existing.source_refs = source_refs
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value=extracted_value,
                    reason=reason,
                    source_refs=source_refs,
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
                existing.reason = ""
                existing.source_refs = None
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value="",
                    reason="",
                    source_refs=None,
                )
                session.add(extraction_result)

            await session.commit()

    logger.info("字段提取完成: {}", file_id)


async def run_extraction_stream(file_id: str, session: AsyncSession):
    """流式执行文件的完整字段提取流程，每提取完一个字段 yield 一次结果。

    1. 获取所有 enabled=1 的 extraction_field，按 priority 排序
    2. 对每个字段执行提取，提取完成后立即 yield 结果
    3. 结果写入 extraction_result 表
    4. 单字段失败跳过继续

    Args:
        file_id: 文件 ID。
        session: 数据库会话。

    Yields:
        Dict: 每个字段的提取结果，包含 field_id, field_name, extracted_value, reason, source_refs, success
    """
    logger.info("开始流式字段提取: {}", file_id)

    # 获取所有启用的字段配置，按 priority 排序
    stmt = (
        select(ExtractionField)
        .where(ExtractionField.enabled == 1)
        .order_by(ExtractionField.priority)
    )
    result = await session.execute(stmt)
    fields = result.scalars().all()

    total_fields = len(fields)

    for idx, field in enumerate(fields):
        try:
            if field.source_type == "table":
                extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
            else:
                extracted_value, reason, source_refs = await extract_text_field(file_id, field, session)

            # 保存结果
            stmt = select(ExtractionResult).where(
                ExtractionResult.file_id == file_id,
                ExtractionResult.field_id == field.field_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.extracted_value = extracted_value
                existing.reason = reason
                existing.source_refs = source_refs
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value=extracted_value,
                    reason=reason,
                    source_refs=source_refs,
                )
                session.add(extraction_result)

            await session.commit()
            logger.info("字段提取成功: field_id={}, value={}", field.field_id, extracted_value[:100] if extracted_value else "")

            # yield 提取结果
            yield {
                "field_id": field.field_id,
                "field_name": field.field_name,
                "extracted_value": extracted_value,
                "reason": reason,
                "source_refs": source_refs,
                "success": True,
                "current": idx + 1,
                "total": total_fields,
            }

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
                existing.reason = ""
                existing.source_refs = None
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value="",
                    reason="",
                    source_refs=None,
                )
                session.add(extraction_result)

            await session.commit()

            # yield 失败结果
            yield {
                "field_id": field.field_id,
                "field_name": field.field_name,
                "extracted_value": "",
                "reason": str(e),
                "source_refs": None,
                "success": False,
                "current": idx + 1,
                "total": total_fields,
            }

    logger.info("流式字段提取完成: {}", file_id)
