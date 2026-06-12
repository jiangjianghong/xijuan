"""字段提取服务：对应 design.md 第 7 节。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import ExtractionField, ExtractionResult, File, FileChunk, FileContent, FileTable
from service import vl_service
from utils import vl_client
from utils.callback import notify_callback
from utils.config import get_config
from utils.llm_client import chat_completion, get_embeddings
from utils.milvus_client import MilvusClient
from utils.page_mapping import lookup_bboxes, lookup_page_num


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


# ── 页码区间解析（page 检索方式） ─────────────────────────────


def _parse_page_range(raw: Any) -> Optional[Tuple[int, int]]:
    """解析 '5' 或 '5-7' 形式的页码区间。

    Returns:
        (start_page, end_page)；不合法返回 None。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if "-" in s:
        parts = s.split("-")
        if len(parts) != 2:
            return None
        a_str, b_str = parts[0].strip(), parts[1].strip()
        if not a_str or not b_str:
            return None
        try:
            a = int(a_str)
            b = int(b_str)
        except ValueError:
            return None
    else:
        try:
            a = int(s)
        except ValueError:
            return None
        b = a
    if a < 1 or b < a:
        return None
    return a, b


def slice_by_page_range(
    md: str,
    page_mapping: List[Dict[str, Any]],
    start_page: int,
    end_page: int,
    max_length: int,
) -> Dict[str, Any]:
    """按页码区间切 markdown。

    Returns:
        {"ok": True, "text": str, "start_pos": int, "end_pos": int,
         "length": int, "truncated": bool}
        失败时返回 {"ok": False, "reason": str}。
    """
    if not md:
        return {"ok": False, "reason": "文档内容为空"}
    if not page_mapping:
        return {
            "ok": False,
            "reason": "该文件无 page_mapping，无法按页码取文本",
        }

    slice_start: Optional[int] = None
    for entry in page_mapping:
        if entry["page_num"] >= start_page:
            slice_start = entry["start_pos"]
            break
    if slice_start is None:
        return {
            "ok": False,
            "reason": f"页码区间 {start_page}-{end_page} 不在文档范围内",
        }

    slice_end = len(md)
    for entry in page_mapping:
        if entry["page_num"] > end_page:
            slice_end = entry["start_pos"]
            break

    if slice_end <= slice_start:
        return {
            "ok": False,
            "reason": f"页码区间 {start_page}-{end_page} 切片为空",
        }

    text = md[slice_start:slice_end]
    truncated = False
    if len(text) > max_length:
        text = text[:max_length]
        truncated = True

    return {
        "ok": True,
        "text": text,
        "start_pos": slice_start,
        "end_pos": slice_start + len(text),
        "length": len(text),
        "truncated": truncated,
    }


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
        检索结果列表，每项包含 keyword(=section_pattern), section_number, section_title, content。
    """
    section_pattern = config.get("section_pattern", "")
    # 兼容两种字段名：section_match_type（设计文档）和 match_type
    match_type = config.get("section_match_type") or config.get("match_type", "contains")
    threshold = config.get("threshold", 0.8)
    max_results = config.get("max_results", 3)
    sort_order = config.get("sort_order", "asc")

    sections = parse_sections(content)
    results = []

    # LLM 匹配：一次性给出所有章节标题列表，让模型返回匹配的序号
    if match_type == "llm" and section_pattern:
        section_list = "\n".join(
            f"{i + 1}. {section.number} {section.title}"
            for i, section in enumerate(sections)
        )
        prompt = (
            f"以下是文档中所有章节的序号和标题列表：\n\n"
            f"{section_list}\n\n"
            f"请找出与查询「{section_pattern}」最相关的章节，"
            f"返回其序号（多个用逗号分隔）。\n\n"
            f"只返回序号，不要输出其他内容。例如：2 或 1,3"
        )
        try:
            response = await chat_completion(prompt)
            indices = [int(x) for x in re.findall(r"\d+", response)]
            for idx in indices:
                if 1 <= idx <= len(sections):
                    section = sections[idx - 1]
                    section_content = content[section.start_pos:section.end_pos]
                    results.append({
                        "section_number": section.number,
                        "section_title": section.title,
                        "section_index": section.index,
                        "content": section_content,
                        "start_pos": section.start_pos,
                        "end_pos": section.end_pos,
                    })
        except Exception as e:
            logger.warning("LLM 章节匹配失败: {}", e)
    elif match_type != "llm":
        # 非 LLM 匹配：逐章节匹配
        for section in sections:
            matched = False

            if match_type == "exact":
                matched = section.title == section_pattern
            elif match_type == "fuzzy":
                ratio = SequenceMatcher(None, section.title, section_pattern).ratio()
                matched = ratio >= threshold
            elif match_type == "contains":
                matched = section_pattern.lower() in section.title.lower()

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

    # section_pattern 作为占位符标签（与前端下拉插入的标签一致，contains/fuzzy/llm
    # 命中标题 ≠ pattern 时也能对上）；空 pattern 时下游回退 section_title
    for r in results:
        r["keyword"] = section_pattern

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
    # 统一优先读 keywords（数组），兼容旧 keyword_filter（逗号分隔字符串）
    keywords = config.get("keywords", [])
    if not keywords:
        keyword_filter = config.get("keyword_filter")
        if keyword_filter and isinstance(keyword_filter, str):
            keywords = [k.strip() for k in re.split(r"[,，]", keyword_filter) if k.strip()]
    # 兼容两种字段名：max_results（设计文档）和 top_k
    max_results = config.get("max_results") or config.get("top_k", 10)
    sort_order = config.get("sort_order", "asc")

    stmt = select(FileChunk).where(FileChunk.file_id == file_id)
    result = await session.execute(stmt)
    chunks = result.scalars().all()

    # 按 chunk_index 排序（设计文档要求）
    chunks.sort(key=lambda x: x.chunk_index, reverse=(sort_order == "desc"))

    # 按关键词分别过滤并标记，每个关键词独立限制条数
    results = []
    if keywords:
        for kw in keywords:
            count = 0
            for chunk in chunks:
                if kw.lower() in chunk.chunk_content.lower():
                    results.append({
                        "keyword": kw,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                        "chunk_content": chunk.chunk_content,
                        "start_pos": chunk.start_pos,
                        "end_pos": chunk.end_pos,
                        "page_num": chunk.page_num or "",
                    })
                    count += 1
                    if count >= max_results:
                        break
    else:
        # 无关键词时返回所有分块
        for chunk in chunks[:max_results]:
            results.append({
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "chunk_content": chunk.chunk_content,
                "start_pos": chunk.start_pos,
                "end_pos": chunk.end_pos,
                "page_num": chunk.page_num or "",
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
        检索结果列表，每项包含 keyword(=query_text), chunk_id, chunk_index, chunk_content, start_pos, end_pos, score。
    """
    # 去首尾空白：占位符 label 匹配时会 strip，keyword 带空格会永远对不上
    query_text = (config.get("query_text", "") or "").strip()
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

    # query_text 作为占位符标签：下游按 keyword 分组注入 <search_result>query_text</search_result>
    for r in results:
        r["keyword"] = query_text

    return results


# ── 提取主流程 ──────────────────────────────────────────────

# JSON 输出格式说明（附加到 prompt 末尾）
JSON_OUTPUT_INSTRUCTION = """

请以 JSON 格式返回结果，包含 value（提取的值）和 reason（提取理由/依据）两个字段：
{"value": "提取的值", "reason": "说明从哪里提取、为什么这样提取"}
注意：value 的格式请严格遵循 system_prompt 中的要求（如有），可以是字符串、JSON数组或JSON对象。"""


async def _extract_page_field(
    content: str,
    page_mapping: List[Dict[str, Any]],
    search_config: Dict[str, Any],
    field: ExtractionField,
) -> Tuple[str, str, Optional[Dict]]:
    """page 检索方式：直接按页码切 markdown 喂 LLM。

    与其他 5 种 text 方法不同，page 不做关键词过滤，只用一个固定 label
    `page_content` 作为 prompt 占位符。
    """
    page_range_raw = (search_config or {}).get("page_range", "")
    parsed = _parse_page_range(page_range_raw)
    if not parsed:
        return "", f"page_range 配置非法：{page_range_raw!r}", None
    start_page, end_page = parsed

    max_length = (search_config or {}).get("max_length", 30000)
    if not isinstance(max_length, int) or max_length <= 0:
        return "", f"max_length 配置非法：{max_length!r}", None

    sliced = slice_by_page_range(content, page_mapping, start_page, end_page, max_length)
    if not sliced["ok"]:
        return "", sliced["reason"], None

    results_text_by_label = {"page_content": sliced["text"]}
    source_refs: Dict[str, List[Dict[str, Any]]] = {
        "page_content": [
            {
                "type": "page",
                "page_range": page_range_raw,
                "start_pos": sliced["start_pos"],
                "end_pos": sliced["end_pos"],
                "length": sliced["length"],
                "truncated": sliced["truncated"],
                "page_num": page_range_raw,
                "text": sliced["text"],
            }
        ],
        "_texts": {"page_content": sliced["text"]},
    }

    prompt_template = field.text_extract_prompt or ""
    if not validate_prompt_has_placeholder(prompt_template):
        logger.warning("字段 {} 的 text_extract_prompt 缺少占位符", field.field_id)
        return "", "", None

    llm_input = replace_search_result_placeholders(prompt_template, results_text_by_label)
    llm_input += JSON_OUTPUT_INSTRUCTION

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
        logger.error("LLM 文本提取失败 (page): {}", e)
        return "", "", None


def _build_table_source_refs(
    matched_tables: List[FileTable],
    label: str,
    page_mapping: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """构建带表格原文的 source_refs 与拼接后的检索文本。

    每条 ref 的 text 含模型实际看到的 "表格名称: xxx\\n" 前缀；
    带全文坐标的 ref 另携带 bboxes（块级 PDF 框，老数据无 bbox 时不带该键）；
    source_refs["_texts"] = {label: 拼接后实际注入占位符的完整文本}。

    Returns:
        (source_refs, results_text_by_label) 元组。
    """
    table_refs = []
    parts = []
    for table in matched_tables:
        table_name = table.table_name or f"表格{table.table_index}"
        text = f"表格名称: {table_name}\n{table.table_content}"
        ref: Dict[str, Any] = {
            "type": "table",
            "table_index": table.table_index,
            "table_name": table.table_name,
            "start_pos": table.start_pos,
            "end_pos": table.end_pos,
            "page_num": table.page_num or "",
            "text": text,
        }
        if table.start_pos is not None and table.end_pos is not None:
            bboxes = lookup_bboxes(page_mapping, table.start_pos, table.end_pos)
            if bboxes:
                ref["bboxes"] = bboxes
        table_refs.append(ref)
        parts.append(text)

    results_text_by_label: Dict[str, str] = {label: "\n---\n".join(parts)} if parts else {}
    source_refs: Dict[str, Any] = {
        "_tables": table_refs,
        "_texts": results_text_by_label,
    }
    return source_refs, results_text_by_label


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

    # 4 种表格匹配方式，使用 table_match_keywords 做匹配
    match_type = field.table_match_type or "contains"
    keywords = field.table_match_keywords or []
    # 兼容：如果没有 keywords 则回退到 table_name_pattern
    if not keywords and field.table_name_pattern:
        keywords = [field.table_name_pattern]

    matched_tables = []

    # LLM 匹配：一次性给出所有表格名列表，让模型返回匹配的序号
    if match_type == "llm" and keywords:
        query_desc = "、".join(keywords)
        table_list = "\n".join(
            f"{i + 1}. {table.table_name or f'表格{table.table_index}'}"
            for i, table in enumerate(tables)
        )
        max_results = field.table_match_max_results or 0
        if max_results > 0:
            quantity_hint = f"最多返回 {max_results} 个表格的序号，按相关性从高到低排序。"
        else:
            quantity_hint = "返回所有匹配表格的序号。"
        prompt = (
            f"以下是文档中所有表格的名称和序号列表：\n\n"
            f"{table_list}\n\n"
            f"请找出与查询「{query_desc}」最相关的表格，{quantity_hint}\n\n"
            f"只返回序号，不要输出其他内容。例如：2 或 1,3"
        )
        try:
            response = await chat_completion(prompt)
            # 解析序号：提取所有整数
            indices = [int(x) for x in re.findall(r"\d+", response)]
            # 序号从1开始，转为列表索引
            matched_tables = [
                tables[idx - 1] for idx in indices
                if 1 <= idx <= len(tables)
            ]
        except Exception as e:
            logger.warning("LLM 表格匹配失败: {}", e)
            matched_tables = []
    elif match_type != "llm":
        # 非 LLM 匹配：逐关键词逐表格匹配
        for table in tables:
            matched = False
            for kw in keywords:
                if match_type == "exact":
                    matched = table.table_name == kw
                elif match_type == "fuzzy":
                    ratio = SequenceMatcher(None, table.table_name, kw).ratio()
                    matched = ratio >= 0.8
                elif match_type == "contains":
                    matched = kw.lower() in table.table_name.lower()
                if matched:
                    break
            if matched:
                matched_tables.append(table)

    # 限制返回数量
    max_results = field.table_match_max_results or 0
    if max_results > 0 and len(matched_tables) > max_results:
        matched_tables = matched_tables[:max_results]

    if not matched_tables:
        return "", "", None

    # 使用用户指定的表名作为统一 label
    label = field.table_name_pattern or "表格"

    # 构建 LLM 输入
    prompt_template = field.table_extract_prompt or ""
    if not validate_prompt_has_placeholder(prompt_template):
        logger.warning("字段 {} 的 table_extract_prompt 缺少占位符", field.field_id)
        return "", "", None

    # 查 page_mapping 用于 bbox 定位（无 file_content 时为空列表，ref 不挂 bboxes）
    page_mapping = (
        await session.execute(
            select(FileContent.page_mapping).where(FileContent.file_id == file_id)
        )
    ).scalar_one_or_none() or []

    source_refs, results_text_by_label = _build_table_source_refs(
        matched_tables, label, page_mapping
    )

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


# 各检索类型结果中"原文片段"的字段名
_SEGMENT_TEXT_KEY = {
    "context": "context",
    "section": "content",
    "rule": "extracted_text",
    "chunk_db": "chunk_content",
    "vector_db": "chunk_content",
}


def _build_text_source_refs(
    search_type: str,
    search_results: List[Dict[str, Any]],
    page_mapping: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """构建带检索原文的 source_refs 与按 label 拼接的检索文本。

    每条 ref 携带 text（该条命中注入 prompt 的原始片段）；
    带全文坐标的 ref 另携带 bboxes（块级 PDF 框，老数据无 bbox 时不带该键）；
    source_refs["_texts"] = {label: 拼接后实际注入占位符的完整文本}。

    Returns:
        (source_refs, results_text_by_label) 元组。
    """
    text_key = _SEGMENT_TEXT_KEY.get(search_type, "context")

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
            "text": r.get(text_key, ""),
        }
        if "chunk_id" in r:
            ref["chunk_id"] = r["chunk_id"]
            ref["chunk_index"] = r["chunk_index"]

        # 页码：chunk_db/vector_db 结果自带 page_num，其他类型通过 page_mapping 查找
        if "page_num" in r:
            ref["page_num"] = r["page_num"]
        elif r.get("start_pos") is not None and r.get("end_pos") is not None:
            ref["page_num"] = lookup_page_num(page_mapping, r["start_pos"], r["end_pos"])

        # 块级 bbox：所有带全文坐标的结果（含 chunk_db/vector_db）统一查 page_mapping；
        # 存量老 mapping 无 bbox 时返回空，不挂键（消费方容错）
        if r.get("start_pos") is not None and r.get("end_pos") is not None:
            bboxes = lookup_bboxes(page_mapping, r["start_pos"], r["end_pos"])
            if bboxes:
                ref["bboxes"] = bboxes

        source_refs.setdefault(keyword, []).append(ref)

    # 按关键词分组拼接检索文本（与注入 prompt 的内容完全一致；
    # section 无 keyword 用 section_title 兜底，vector_db 的 keyword 为 query_text）
    results_by_keyword: Dict[str, List[Dict]] = {}
    for r in search_results:
        kw = r.get("keyword", "") or r.get("section_title", "")
        if kw:
            results_by_keyword.setdefault(kw, []).append(r)

    results_text_by_label: Dict[str, str] = {
        kw: "\n---\n".join(r.get(text_key, "") for r in items)
        for kw, items in results_by_keyword.items()
    }
    source_refs["_texts"] = results_text_by_label
    return source_refs, results_text_by_label


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
    page_mapping = file_content.page_mapping or []
    search_type = field.search_type or "context"
    search_config = field.search_config or {}

    # page 方法走独立路径（不经按 keyword 分组的通用流程）
    if search_type == "page":
        return await _extract_page_field(content, page_mapping, search_config, field)

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

    source_refs, results_text_by_label = _build_text_source_refs(
        search_type, search_results, page_mapping
    )

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


async def extract_vl_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> Tuple[str, str, Optional[Dict]]:
    """VL 类字段提取：基于 PDF 视觉模型直接产出 {value, reason}。

    Returns:
        (extracted_value, reason, source_refs) 元组。source_refs 形如 {"_vl": {...}}。
    """
    pdf_file = vl_client.pdf_path(file_id)
    if not pdf_file.exists():
        return "", "PDF 原始文件不存在，无法 VL 抽取", None

    try:
        file_bytes = pdf_file.read_bytes()
    except OSError as e:
        return "", f"PDF 文件读取失败: {e}", None

    cfg = field.vl_config or {}
    method = field.vl_method
    default_max_pixels = get_config().vl_model.default_max_pixels

    try:
        if method == "vl_model":
            value, reason, refs = await vl_service.vl_model_extract(
                file_bytes,
                field.vl_extract_prompt or "",
                field.vl_system_prompt,
                page_range=cfg.get("page_range", "all"),
                max_pixels=cfg.get("max_pixels", default_max_pixels),
            )
        elif method == "vl_progressive":
            value, reason, refs = await vl_service.vl_progressive_extract(
                file_bytes,
                field.vl_extract_prompt or "",
                field.vl_system_prompt,
                field_hints=cfg.get("field_hints", ""),
                batch_size=cfg.get("batch_size", 2),
                max_pixels=cfg.get("max_pixels", default_max_pixels),
                batch_prompt_template=cfg.get("batch_prompt_template"),
            )
        elif method == "vl_locate":
            value, reason, refs = await vl_service.vl_locate_extract(
                file_bytes,
                field.vl_extract_prompt or "",
                field.vl_system_prompt,
                field_hints=cfg.get("field_hints", ""),
                grid_pages=cfg.get("grid_pages", 6),
                grid_cols=cfg.get("grid_cols", 3),
                max_concurrent=cfg.get("max_concurrent", 20),
                thumb_scale=cfg.get("thumb_scale", 0.75),
                key_pages_limit=cfg.get("key_pages_limit", 6),
                fallback_pages=cfg.get("fallback_pages", 3),
                max_pixels=cfg.get("max_pixels", default_max_pixels),
                locate_prompt_template=cfg.get("locate_prompt_template"),
            )
        else:
            return "", f"未知 vl_method={method}", None
    except Exception as e:
        logger.error("VL 抽取失败 file_id={} method={} error={}", file_id, method, e)
        return "", f"VL 抽取失败: {e}", None

    return value, reason, {"_vl": refs}


async def _vl_field_extraction_stream(
    file_id: str, field: ExtractionField
) -> AsyncIterator[Dict[str, Any]]:
    """VL 字段调试流式生成器：暴露 vl_service 内部进度事件 + 4 步骤事件。

    事件序列：
      pdf_loaded → progressive_batch×N | locate_locate×M + locate_extract
      → prompt → result → done
    或失败时 → error → return
    """
    import asyncio as _asyncio

    import fitz as _fitz

    pdf_file = vl_client.pdf_path(file_id)
    if not pdf_file.exists():
        yield {"event": "error", "data": {"step": "pdf_load", "message": "PDF 原始文件不存在"}}
        return

    try:
        file_bytes = pdf_file.read_bytes()
    except OSError as e:
        yield {"event": "error", "data": {"step": "pdf_load", "message": f"PDF 读取失败: {e}"}}
        return

    doc = _fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    yield {
        "event": "pdf_loaded",
        "data": {"total_pages": total_pages, "vl_method": field.vl_method},
    }

    cfg = field.vl_config or {}
    default_max_pixels = get_config().vl_model.default_max_pixels
    method = field.vl_method

    progress_queue: _asyncio.Queue = _asyncio.Queue()
    SENTINEL = object()

    async def progress_cb(evt: dict):
        if "phase" in evt:
            await progress_queue.put({"event": f"locate_{evt['phase']}", "data": evt})
        else:
            await progress_queue.put({"event": "progressive_batch", "data": evt})

    async def run_vl():
        try:
            if method == "vl_model":
                return await vl_service.vl_model_extract(
                    file_bytes,
                    field.vl_extract_prompt or "",
                    field.vl_system_prompt,
                    page_range=cfg.get("page_range", "all"),
                    max_pixels=cfg.get("max_pixels", default_max_pixels),
                )
            elif method == "vl_progressive":
                return await vl_service.vl_progressive_extract(
                    file_bytes,
                    field.vl_extract_prompt or "",
                    field.vl_system_prompt,
                    field_hints=cfg.get("field_hints", ""),
                    batch_size=cfg.get("batch_size", 2),
                    max_pixels=cfg.get("max_pixels", default_max_pixels),
                    batch_prompt_template=cfg.get("batch_prompt_template"),
                    progress_cb=progress_cb,
                )
            elif method == "vl_locate":
                return await vl_service.vl_locate_extract(
                    file_bytes,
                    field.vl_extract_prompt or "",
                    field.vl_system_prompt,
                    field_hints=cfg.get("field_hints", ""),
                    grid_pages=cfg.get("grid_pages", 6),
                    grid_cols=cfg.get("grid_cols", 3),
                    max_concurrent=cfg.get("max_concurrent", 20),
                    thumb_scale=cfg.get("thumb_scale", 0.75),
                    key_pages_limit=cfg.get("key_pages_limit", 6),
                    fallback_pages=cfg.get("fallback_pages", 3),
                    max_pixels=cfg.get("max_pixels", default_max_pixels),
                    locate_prompt_template=cfg.get("locate_prompt_template"),
                    progress_cb=progress_cb,
                )
            else:
                raise ValueError(f"未知 vl_method={method}")
        finally:
            await progress_queue.put(SENTINEL)

    vl_task = _asyncio.create_task(run_vl())

    while True:
        evt = await progress_queue.get()
        if evt is SENTINEL:
            break
        yield evt

    try:
        value, reason, refs = await vl_task
    except Exception as e:
        yield {"event": "error", "data": {"step": "vl_extract", "message": str(e)}}
        return

    yield {
        "event": "prompt",
        "data": {
            "system_prompt": field.vl_system_prompt or "",
            "user_prompt": field.vl_extract_prompt or "",
        },
    }
    yield {
        "event": "result",
        "data": {
            "extracted_value": value,
            "reason": reason,
            "source_refs": {"_vl": refs},
        },
    }
    yield {"event": "done", "data": {}}


async def run_extraction(
    file_id: str,
    session: AsyncSession,
    callback_url: Optional[str] = None,
) -> None:
    """执行文件的完整字段提取流程。

    1. 获取所有 enabled=1 的 extraction_field，按 priority 排序
    2. 对每个字段执行提取
    3. 结果写入 extraction_result 表
    4. 单字段失败跳过继续

    Args:
        file_id: 文件 ID。
        session: 数据库会话。
        callback_url: 可选回调地址。每完成一个字段会推 field_done；阶段终态推 stage_done。
    """
    logger.info("开始字段提取: {}", file_id)

    # 读取文件归属类型
    file_row = (await session.execute(select(File).where(File.file_id == file_id))).scalar_one_or_none()
    type_id = (file_row.type_id if file_row else None) or "default"

    # 获取所有启用的字段配置，按 priority 排序
    stmt = (
        select(ExtractionField)
        .where(ExtractionField.enabled == 1, ExtractionField.type_id == type_id)
        .order_by(ExtractionField.priority)
    )
    result = await session.execute(stmt)
    fields = result.scalars().all()

    total = len(fields)
    succeeded = 0
    failed = 0
    aggregated: List[Dict[str, Any]] = []

    for idx, field in enumerate(fields):
        try:
            if field.source_type == "table":
                extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
            elif field.source_type == "vl":
                extracted_value, reason, source_refs = await extract_vl_field(file_id, field, session)
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

            succeeded += 1
            item = {
                "field_id": field.field_id,
                "field_name": field.field_name,
                "value": extracted_value,
                "reason": reason,
                "source_refs": source_refs,
                "success": True,
                "index": idx + 1,
                "total": total,
            }
            aggregated.append(item)
            await notify_callback(callback_url, file_id, "extracting", event="field_done", data=item)

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

            failed += 1
            item = {
                "field_id": field.field_id,
                "field_name": field.field_name,
                "value": "",
                "reason": str(e),
                "source_refs": None,
                "success": False,
                "index": idx + 1,
                "total": total,
            }
            aggregated.append(item)
            await notify_callback(callback_url, file_id, "extracting", event="field_done", data=item)

    await notify_callback(
        callback_url,
        file_id,
        "extracting",
        event="stage_done",
        data={
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "results": aggregated,
        },
    )

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

    # 读取文件归属类型
    file_row = (await session.execute(select(File).where(File.file_id == file_id))).scalar_one_or_none()
    type_id = (file_row.type_id if file_row else None) or "default"

    # 获取所有启用的字段配置，按 priority 排序
    stmt = (
        select(ExtractionField)
        .where(ExtractionField.enabled == 1, ExtractionField.type_id == type_id)
        .order_by(ExtractionField.priority)
    )
    result = await session.execute(stmt)
    fields = result.scalars().all()

    total_fields = len(fields)

    for idx, field in enumerate(fields):
        try:
            if field.source_type == "table":
                extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
            elif field.source_type == "vl":
                extracted_value, reason, source_refs = await extract_vl_field(file_id, field, session)
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


async def test_field_extraction_stream(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> AsyncIterator[Dict[str, Any]]:
    """单字段提取调试流式生成器，分 4 步 yield 中间结果。

    Args:
        file_id: 文件 ID。
        field: ExtractionField ORM 对象（可以是临时构建的）。
        session: 数据库会话。

    Yields:
        Dict: 每步包含 event 和 data 两个键。
    """
    try:
        source_type = field.source_type or "text"

        # ── VL 模式：单独走通 4 步骤，结束后直接 return ───────
        if source_type == "vl":
            async for evt in _vl_field_extraction_stream(file_id, field):
                yield evt
            return

        # ── Step 1: 检索 ──────────────────────────────────────
        search_results: List[Dict[str, Any]] = []
        matched_tables: List[Dict[str, Any]] = []
        results_by_label: Dict[str, str] = {}

        try:
            if source_type == "table":
                # 表格检索
                stmt = select(FileTable).where(FileTable.file_id == file_id)
                result = await session.execute(stmt)
                tables = result.scalars().all()

                match_type = field.table_match_type or "contains"
                keywords = field.table_match_keywords or []
                if not keywords and field.table_name_pattern:
                    keywords = [field.table_name_pattern]

                # LLM 匹配：一次性给出所有表格名列表，让模型返回匹配的序号
                if match_type == "llm" and keywords:
                    query_desc = "、".join(keywords)
                    table_list = "\n".join(
                        f"{i + 1}. {table.table_name or f'表格{table.table_index}'}"
                        for i, table in enumerate(tables)
                    )
                    max_results = field.table_match_max_results or 0
                    if max_results > 0:
                        quantity_hint = f"最多返回 {max_results} 个表格的序号，按相关性从高到低排序。"
                    else:
                        quantity_hint = "返回所有匹配表格的序号。"
                    llm_match_prompt = (
                        f"以下是文档中所有表格的名称和序号列表：\n\n"
                        f"{table_list}\n\n"
                        f"请找出与查询「{query_desc}」最相关的表格，{quantity_hint}\n\n"
                        f"只返回序号，不要输出其他内容。例如：2 或 1,3"
                    )
                    yield {
                        "event": "match_llm",
                        "data": {
                            "step": "prompt",
                            "prompt": llm_match_prompt,
                        },
                    }
                    try:
                        resp = await chat_completion(llm_match_prompt)
                        indices = [int(x) for x in re.findall(r"\d+", resp)]
                        yield {
                            "event": "match_llm",
                            "data": {
                                "step": "response",
                                "llm_response": resp,
                                "matched_indices": indices,
                            },
                        }
                        matched_tables = [
                            {
                                "table_index": tables[idx - 1].table_index,
                                "table_name": tables[idx - 1].table_name,
                                "table_content": tables[idx - 1].table_content[:500] + "..." if len(tables[idx - 1].table_content) > 500 else tables[idx - 1].table_content,
                                "page_num": tables[idx - 1].page_num or "",
                            }
                            for idx in indices
                            if 1 <= idx <= len(tables)
                        ]
                    except Exception as e:
                        logger.warning("LLM 表格匹配失败: {}", e)
                        yield {
                            "event": "match_llm",
                            "data": {
                                "step": "error",
                                "error": str(e),
                            },
                        }
                        matched_tables = []
                elif match_type != "llm":
                    # 非 LLM 匹配：逐关键词逐表格匹配
                    for table in tables:
                        matched = False
                        for kw in keywords:
                            if match_type == "exact":
                                matched = table.table_name == kw
                            elif match_type == "fuzzy":
                                ratio = SequenceMatcher(None, table.table_name, kw).ratio()
                                matched = ratio >= 0.8
                            elif match_type == "contains":
                                matched = kw.lower() in table.table_name.lower()
                            if matched:
                                break

                        if matched:
                            matched_tables.append({
                                "table_index": table.table_index,
                                "table_name": table.table_name,
                                "table_content": table.table_content[:500] + "..." if len(table.table_content) > 500 else table.table_content,
                                "page_num": table.page_num or "",
                            })

                # 限制返回数量
                max_results = field.table_match_max_results or 0
                if max_results > 0 and len(matched_tables) > max_results:
                    matched_tables = matched_tables[:max_results]

                # 使用用户指定的表名作为统一 label
                label = field.table_name_pattern or "表格"
                parts = []
                for t in matched_tables:
                    table_name = t["table_name"] or f"表格{t['table_index']}"
                    # 调试用截断内容
                    full_table = next((tb for tb in tables if tb.table_index == t["table_index"]), None)
                    content_text = full_table.table_content if full_table else t["table_content"]
                    content = f"表格名称: {table_name}\n{content_text}"
                    parts.append(content)
                if parts:
                    results_by_label[label] = "\n---\n".join(parts)

                yield {
                    "event": "search_results",
                    "data": {
                        "source_type": "table",
                        "search_type": match_type,
                        "results": [],
                        "matched_tables": matched_tables,
                        "results_by_label": {k: v[:500] for k, v in results_by_label.items()},
                    },
                }

            else:
                # 文本检索
                search_type = field.search_type or "context"
                search_config = field.search_config or {}

                # 获取文件内容
                stmt = select(FileContent).where(FileContent.file_id == file_id)
                result = await session.execute(stmt)
                file_content = result.scalar_one_or_none()

                if not file_content:
                    yield {"event": "error", "data": {"message": "文件内容不存在"}}
                    return

                content = file_content.file_content
                page_mapping = file_content.page_mapping or []

                search_results = []
                if search_type == "page":
                    # page 方法不走通用按 keyword 分组流程，stream 预览也直接展示切片文本
                    page_range_raw = (search_config or {}).get("page_range", "")
                    parsed = _parse_page_range(page_range_raw)
                    if parsed:
                        start_p, end_p = parsed
                        max_len = (search_config or {}).get("max_length", 30000)
                        if not isinstance(max_len, int) or max_len <= 0:
                            max_len = 30000
                        sliced = slice_by_page_range(content, page_mapping, start_p, end_p, max_len)
                        if sliced["ok"]:
                            results_by_label["page_content"] = sliced["text"]
                elif search_type == "context":
                    search_results = await search_context(content, search_config)
                elif search_type == "section":
                    search_results = await search_section(content, search_config)
                elif search_type == "rule":
                    search_results = await search_rule(content, search_config)
                elif search_type == "chunk_db":
                    search_results = await search_chunk_db(file_id, search_config, session)
                elif search_type == "vector_db":
                    search_results = await search_vector_db(file_id, search_config)

                # 按关键词分组构建 results_by_label
                results_by_keyword: Dict[str, List[Dict]] = {}
                for r in search_results:
                    kw = r.get("keyword", "") or r.get("section_title", "")
                    if kw:
                        if kw not in results_by_keyword:
                            results_by_keyword[kw] = []
                        results_by_keyword[kw].append(r)

                for keyword, items in results_by_keyword.items():
                    if search_type == "context":
                        results_by_label[keyword] = "\n---\n".join([r["context"] for r in items])
                    elif search_type == "section":
                        results_by_label[keyword] = "\n---\n".join([r["content"] for r in items])
                    elif search_type == "rule":
                        results_by_label[keyword] = "\n---\n".join([r["extracted_text"] for r in items])
                    elif search_type in ("chunk_db", "vector_db"):
                        results_by_label[keyword] = "\n---\n".join([r["chunk_content"] for r in items])

                yield {
                    "event": "search_results",
                    "data": {
                        "source_type": "text",
                        "search_type": search_type,
                        "results": search_results,
                        "matched_tables": [],
                        "results_by_label": {k: v[:1000] for k, v in results_by_label.items()},
                    },
                }

        except Exception as e:
            logger.error("调试检索步骤失败: {}", e)
            yield {"event": "error", "data": {"message": f"检索失败: {str(e)}"}}
            return

        # ── Step 2: 构建提示词 ──────────────────────────────────
        try:
            if source_type == "table":
                prompt_template = field.table_extract_prompt or ""
                system_prompt = (field.table_system_prompt or "").strip()
            else:
                prompt_template = field.text_extract_prompt or ""
                system_prompt = (field.text_system_prompt or "").strip()

            if not validate_prompt_has_placeholder(prompt_template):
                yield {"event": "error", "data": {"message": "提示词模板缺少 <search_result>...</search_result> 占位符"}}
                return

            user_prompt = replace_search_result_placeholders(prompt_template, results_by_label)
            user_prompt += JSON_OUTPUT_INSTRUCTION

            yield {
                "event": "prompt",
                "data": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
            }

        except Exception as e:
            logger.error("调试提示词构建失败: {}", e)
            yield {"event": "error", "data": {"message": f"提示词构建失败: {str(e)}"}}
            return

        # ── Step 3: 调用 LLM ──────────────────────────────────
        try:
            if system_prompt:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                raw_response = await chat_completion("", messages=messages)
            else:
                raw_response = await chat_completion(user_prompt)

            yield {
                "event": "llm_response",
                "data": {"raw_response": raw_response},
            }

        except Exception as e:
            logger.error("调试 LLM 调用失败: {}", e)
            yield {"event": "error", "data": {"message": f"LLM 调用失败: {str(e)}"}}
            return

        # ── Step 4: 解析结果 ──────────────────────────────────
        try:
            value, reason = parse_llm_json_response(raw_response)

            yield {
                "event": "result",
                "data": {
                    "extracted_value": value,
                    "reason": reason,
                },
            }

        except Exception as e:
            logger.error("调试结果解析失败: {}", e)
            yield {"event": "error", "data": {"message": f"结果解析失败: {str(e)}"}}
            return

        # ── 完成 ──────────────────────────────────────────────
        yield {"event": "done", "data": {}}

    except Exception as e:
        logger.error("调试流式提取意外错误: {}", e)
        yield {"event": "error", "data": {"message": f"意外错误: {str(e)}"}}
