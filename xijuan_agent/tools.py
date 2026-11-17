"""析卷智能体可调用的文档检索工具。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import fitz

from service.bm25_search import search_bm25
from service.extraction_service import (
    parse_sections,
    search_chunk_db,
    search_context,
    search_rule,
    search_vector_db,
)
from service.extraction_snapshot import FileExtractionSnapshot
from service.file_vector_index import FileVectorIndex, load_file_vector_index
from utils.config import get_config
from utils.vl_client import pdf_path, render_pages_to_b64
from utils.web_search import bocha_web_search


MAX_RESULT_CHARS = 30000
MAX_PDF_PAGES_PER_CALL = 8


@dataclass(frozen=True)
class ToolOutput:
    content: str
    images: tuple[dict[str, Any], ...] = ()


@dataclass
class AgentToolContext:
    file_id: str
    snapshot: FileExtractionSnapshot
    vector_index: FileVectorIndex | None = None
    vector_index_loaded: bool = False
    vector_index_error: str | None = None


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _object(properties, required),
        },
    }


TOOL_DEFINITIONS = [
    _tool(
        "search_context",
        "在当前文件的完整 Markdown 中查找一个或多个精确关键词，并返回每个命中位置前后指定字符数的原文窗口。适合已知术语、字段名、金额名称或短语，想查看其邻近上下文时使用。它会合并重叠窗口并按相关度排序；结果含页内原文位置，最多返回 20 条。关键词应拆成简短且有区分度的词，不要直接把整句问题当成一个关键词。",
        {"keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "description": "要在原文中查找的精确关键词列表。"}, "context_before": {"type": "integer", "minimum": 0, "maximum": 2000, "description": "每个命中向前扩展的字符数，默认 200。"}, "context_after": {"type": "integer", "minimum": 0, "maximum": 2000, "description": "每个命中向后扩展的字符数，默认 200。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回结果数，默认 8。"}},
        ["keywords"],
    ),
    _tool(
        "list_sections",
        "列出当前文件解析出的全部 Markdown 章节标题与层级，不读取章节正文。返回 section_index、章节编号、标题、层级和目录标记；Agent 后续应选择一个 section_index 调用 read_section 获取完整章节。适合先了解文档结构、定位合同条款、报告章节或目录层次。索引是当前文件解析结果中的稳定顺序号，不是页码。",
        {},
        [],
    ),
    _tool(
        "read_section",
        "按 list_sections 返回的 section_index 读取该章节的完整正文，并包含其下级小节；目录条目会尽量映射到对应正文。适合已经从章节列表中选定目标章节后读取完整条款。请使用章节索引，不要把标题文字当索引。章节过长时结果会在 30000 字符处截断，并明确标记。",
        {"section_index": {"type": "integer", "minimum": 0, "description": "由 list_sections 返回的章节索引。"}},
        ["section_index"],
    ),
    _tool(
        "search_rule",
        "按关键词定位规则文本，并沿 forward（命中后）、backward（命中前）或 both（前后）方向扩展到最近停用词边界，适合查找带条件、限制、例外或相邻说明的规则句段。与 search_context 的固定字符窗口不同，本工具使用停用词边界和 max_length 控制段落范围。可传多个关键词；结果含原文位置和命中词，最多 20 条。",
        {"keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "description": "用于定位规则段落的精确关键词。"}, "direction": {"type": "string", "enum": ["forward", "backward", "both"], "description": "扩展方向；默认 forward。"}, "max_length": {"type": "integer", "minimum": 1, "maximum": 4000, "description": "单个命中向前/向后的最大扩展字符数，默认 300。"}, "stop_words": {"type": "array", "items": {"type": "string"}, "description": "可选段落边界词；省略时使用系统默认标点与换行边界。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回结果数，默认 8。"}},
        ["keywords"],
    ),
    _tool(
        "search_chunk_db",
        "在当前文件已保存的 MySQL 文本分块中按关键词匹配，返回命中的完整分块、chunk_id、分块序号和页码。适合问题对应的关键词在原文中可能跨越较大范围，或希望以系统切分后的语义单元核对上下文时使用。这里是词项匹配，不是语义向量搜索；最多返回 20 个分块。",
        {"keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "description": "用于匹配分块的关键词。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回分块数，默认 8。"}},
        ["keywords"],
    ),
    _tool(
        "search_bm25",
        "使用 BM25 词项相关度对当前文件全部文本分块排序，返回分数最高的分块及其页码。适合查询词与文档原词大致一致、需要比简单关键词命中更好的词项排序时使用。BM25 不会调用 embedding，也不等同于语义向量检索；最多返回 20 个分块。",
        {"query_text": {"type": "string", "minLength": 1, "description": "用于 BM25 排序的短查询，可包含多个相关词。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回分块数，默认 8。"}},
        ["query_text"],
    ),
    _tool(
        "search_vector_db",
        "使用现有 embedding 配置在当前文件的 Milvus 子块向量索引中进行语义检索，并将命中映射回可读分块。适合用户用同义表达、概念描述或问题句提问，文档未必出现相同关键词的情况。该工具只查当前 file_id，不跨文件搜索；需要文件已完成向量化且对应索引可用。最多返回 20 个分块。",
        {"query_text": {"type": "string", "minLength": 1, "description": "自然语言语义查询，写清问题对象和关系。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回分块数，默认 8。"}},
        ["query_text"],
    ),
    _tool(
        "search_page",
        "按页码范围读取 MinerU 解析后的逐页 Markdown 文本，不调用视觉模型。适合快速核对指定页的可复制文本或比较相邻页内容；页码为从 1 开始的 PDF 页码，page_range 可写单页（例如 4）或连续范围（例如 4-6），也可写 all。结果按页分组，默认最多 12000 字符，适合大段原文请用章节、分块或分段检索工具缩小范围。扫描图片中未识别出的内容请改用 pdf_reader。",
        {"page_range": {"type": "string", "minLength": 1, "description": "单页、闭区间页码（如 4-6）或 all。"}, "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000, "description": "返回 Markdown 的总字符上限，默认 12000。"}},
        ["page_range"],
    ),
    _tool(
        "list_tables",
        "列出当前文件解析出的全部表格索引、表名和页码，不返回表格单元格内容。Agent 应先使用本工具确认候选表，再将返回的 table_index 传给 read_table。索引来自解析结果；不要把表格名称、表序号或页码混作索引。",
        {},
        [],
    ),
    _tool(
        "read_table",
        "按 list_tables 返回的 table_index 读取一个表格的完整解析内容，并附表名与页码。此工具按索引精确读取，不执行 exact/fuzzy/contains/LLM 表名匹配。适合先列出表名后选择目标表，避免把相似名称的表误当作同一张表。超长表格内容会在 30000 字符处截断并标记。",
        {"table_index": {"type": "integer", "minimum": 0, "description": "由 list_tables 返回的表格索引。"}},
        ["table_index"],
    ),
    _tool(
        "web_search",
        "调用系统配置的博查网络搜索服务查询当前文件之外的公开网页信息。仅在问题需要外部或最新信息、或文档缺少背景时使用；它不会检索当前 PDF。返回标题、来源、日期、摘要和 URL，引用时保留来源链接，并将网页内容视为待核实的外部材料。此工具依赖 web_search 配置中的 API Key。",
        {"query": {"type": "string", "minLength": 1, "description": "简洁明确的联网搜索词。"}, "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回网页数量，默认使用系统设置。"}, "freshness": {"type": "string", "enum": ["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"], "description": "网页时间范围；省略时使用系统设置。"}},
        ["query"],
    ),
    _tool(
        "pdf_reader",
        "将原始 PDF 的指定连续页渲染成图片，并把图片直接交给当前 Agent 模型视觉读取；不依赖 MinerU Markdown，也不调用另一套 VL 模型。页码从 1 开始且起止页均包含，每次最多 8 页；超出范围会报错，需要分多次读取。该工具适合核对扫描件、印章、签字、图表布局或解析文本缺失的页面。读取结果会在过程时间线展示页图。",
        {"start_page": {"type": "integer", "minimum": 1, "description": "起始页码，包含该页。"}, "end_page": {"type": "integer", "minimum": 1, "description": "结束页码，包含该页；须不小于 start_page，且与起始页合计不超过 8 页。"}},
        ["start_page", "end_page"],
    ),
]


def _bounded_int(value: Any, name: str, default: int, minimum: int = 1, maximum: int = 20) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数")
    return value


def _keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("keywords 必须是字符串数组")
    result = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if not result or len(result) > 8:
        raise ValueError("keywords 至少包含 1 个且最多包含 8 个非空关键词")
    return result


def _json_result(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= MAX_RESULT_CHARS:
        return serialized
    return json.dumps(
        {"truncated": True, "preview": serialized[:MAX_RESULT_CHARS], "message": "工具结果超过 30000 字符，已截断。"},
        ensure_ascii=False,
    )


def _parse_page_selection(raw: str, available: list[int]) -> list[int]:
    raw = raw.strip().lower()
    if raw == "all":
        return available
    match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", raw)
    if not match:
        raise ValueError("page_range 仅支持单页、连续范围（例如 2-4）或 all")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end < start:
        raise ValueError("page_range 页码无效")
    return [page for page in available if start <= page <= end]


def _read_pdf_page_count(data: bytes) -> int:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        return document.page_count
    finally:
        document.close()


async def _execute_pdf_reader(context: AgentToolContext, args: dict[str, Any]) -> ToolOutput:
    start = _bounded_int(args.get("start_page"), "start_page", 1, 1, 10_000_000)
    end = _bounded_int(args.get("end_page"), "end_page", start, 1, 10_000_000)
    if end < start:
        raise ValueError("end_page 不能小于 start_page")
    if end - start + 1 > MAX_PDF_PAGES_PER_CALL:
        raise ValueError(f"pdf_reader 每次最多读取 {MAX_PDF_PAGES_PER_CALL} 页")

    source = pdf_path(context.file_id)
    if not source.is_file():
        raise ValueError("该文件的原始 PDF 不存在，可能已按存储保留策略清理")
    pdf_bytes = await asyncio.to_thread(source.read_bytes)
    total_pages = await asyncio.to_thread(_read_pdf_page_count, pdf_bytes)
    if end > total_pages:
        raise ValueError(f"请求页码超出 PDF 总页数（共 {total_pages} 页）")

    page_indexes = list(range(start - 1, end))
    images_b64 = await asyncio.to_thread(
        render_pages_to_b64,
        pdf_bytes,
        page_indexes,
        scale=1.6,
        max_pixels=1_500_000,
        jpeg_quality=88,
    )
    images = tuple(
        {"page": page, "mime_type": "image/jpeg", "data_url": f"data:image/jpeg;base64,{image}"}
        for page, image in zip(range(start, end + 1), images_b64)
    )
    return ToolOutput(
        content=f"已读取 PDF 第 {start}-{end} 页（共 {total_pages} 页）。页图已附加给视觉模型分析。",
        images=images,
    )


async def execute_tool(name: str, args: dict[str, Any], context: AgentToolContext) -> ToolOutput:
    snapshot = context.snapshot

    if name == "search_context":
        keywords = _keywords(args.get("keywords"))
        before = _bounded_int(args.get("context_before"), "context_before", 200, 0, 2000)
        after = _bounded_int(args.get("context_after"), "context_after", 200, 0, 2000)
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        result = await search_context(
            snapshot.content,
            {"keywords": keywords, "context_before": before, "context_after": after, "max_results": top_k, "max_total_results": top_k},
            snapshot.chunks,
        )
        return ToolOutput(_json_result(result))

    if name == "list_sections":
        result = [
            {"section_index": section.index, "number": section.number, "title": section.title, "level": section.level, "numbered": section.numbered, "is_toc": section.is_toc}
            for section in parse_sections(snapshot.content)
        ]
        return ToolOutput(_json_result(result))

    if name == "read_section":
        index = _bounded_int(args.get("section_index"), "section_index", 0, 0, 10_000_000)
        sections = parse_sections(snapshot.content)
        section = next((item for item in sections if item.index == index), None)
        if section is None:
            raise ValueError("section_index 不存在；请先调用 list_sections")
        if section.is_toc:
            body_matches = [item for item in sections if not item.is_toc and item.number == section.number and item.title == section.title]
            if body_matches:
                section = min(body_matches, key=lambda item: (item.index < index, abs(item.index - index)))
        body = snapshot.content[section.start_pos:section.tree_end_pos]
        truncated = len(body) > MAX_RESULT_CHARS
        result = {"section_index": section.index, "number": section.number, "title": section.title, "level": section.level, "content": body[:MAX_RESULT_CHARS], "truncated": truncated}
        return ToolOutput(_json_result(result))

    if name == "search_rule":
        keywords = _keywords(args.get("keywords"))
        direction = args.get("direction", "forward")
        if direction not in {"forward", "backward", "both"}:
            raise ValueError("direction 必须是 forward、backward 或 both")
        max_length = _bounded_int(args.get("max_length"), "max_length", 300, 1, 4000)
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        stop_words = args.get("stop_words")
        if stop_words is not None and (not isinstance(stop_words, list) or any(not isinstance(word, str) for word in stop_words)):
            raise ValueError("stop_words 必须是字符串数组")
        search_config = {"keywords": keywords, "direction": direction, "max_length": max_length, "max_results": top_k, "max_total_results": top_k}
        if stop_words is not None:
            search_config["stop_words"] = stop_words
        result = await search_rule(snapshot.content, search_config, snapshot.chunks)
        return ToolOutput(_json_result(result))

    if name == "search_chunk_db":
        keywords = _keywords(args.get("keywords"))
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        result = await search_chunk_db(
            context.file_id,
            {"keywords": keywords, "max_results": top_k, "max_total_results": top_k},
            snapshot.chunks,
        )
        return ToolOutput(_json_result(result))

    if name == "search_bm25":
        query = args.get("query_text")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query_text 不能为空")
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        result = await asyncio.to_thread(search_bm25, context.file_id, {"query_text": query.strip(), "top_k": top_k}, snapshot.chunks)
        return ToolOutput(_json_result(result))

    if name == "search_vector_db":
        query = args.get("query_text")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query_text 不能为空")
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        if context.vector_index_error:
            raise ValueError(context.vector_index_error)
        if not context.vector_index_loaded:
            context.vector_index_loaded = True
            try:
                context.vector_index = await load_file_vector_index(context.file_id, snapshot.chunks)
            except Exception as exc:
                context.vector_index_error = f"向量索引加载失败: {exc}"
                raise ValueError(context.vector_index_error) from exc
        result = await search_vector_db(
            context.file_id,
            {"query_text": query.strip(), "top_k": top_k},
            context.vector_index,
        )
        return ToolOutput(_json_result(result))

    if name == "search_page":
        page_range = args.get("page_range")
        if not isinstance(page_range, str) or not page_range.strip():
            raise ValueError("page_range 不能为空")
        max_chars = _bounded_int(args.get("max_chars"), "max_chars", 12000, 500, 20000)
        rows = sorted(
            snapshot.page_contents.items(),
            key=lambda item: (0, int(item[0])) if str(item[0]).isdigit() else (1, str(item[0])),
        )
        available = [int(page) for page, _ in rows if str(page).isdigit()]
        selected = set(_parse_page_selection(page_range, available))
        result = []
        chars_left = max_chars
        for page, content in rows:
            if not str(page).isdigit() or int(page) not in selected:
                continue
            if chars_left <= 0:
                break
            fragment = (content or "")[:chars_left]
            result.append({"page_num": int(page), "content": fragment, "truncated": len(fragment) < len(content or "")})
            chars_left -= len(fragment)
        return ToolOutput(_json_result({"pages": result, "truncated": chars_left <= 0}))

    if name == "list_tables":
        result = [
            {"table_index": table.table_index, "table_name": table.table_name or f"表格{table.table_index}", "page_num": table.page_num or ""}
            for table in sorted(snapshot.tables, key=lambda item: item.table_index)
        ]
        return ToolOutput(_json_result(result))

    if name == "read_table":
        index = _bounded_int(args.get("table_index"), "table_index", 0, 0, 10_000_000)
        table = next((item for item in snapshot.tables if item.table_index == index), None)
        if table is None:
            raise ValueError("table_index 不存在；请先调用 list_tables")
        content = table.table_content or ""
        return ToolOutput(_json_result({"table_index": index, "table_name": table.table_name or f"表格{index}", "page_num": table.page_num or "", "table_content": content[:MAX_RESULT_CHARS], "truncated": len(content) > MAX_RESULT_CHARS}))

    if name == "web_search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 不能为空")
        count = _bounded_int(args.get("count"), "count", 5, 1, 10)
        freshness = args.get("freshness")
        allowed_freshness = {"noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"}
        if freshness is not None and freshness not in allowed_freshness:
            raise ValueError("freshness 不合法")
        formatted, results = await bocha_web_search(query.strip(), count=count, freshness=freshness)
        return ToolOutput(_json_result({"summary": formatted, "results": results}))

    if name == "pdf_reader":
        return await _execute_pdf_reader(context, args)

    raise ValueError(f"未知工具: {name}")

