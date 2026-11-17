"""析卷智能体可调用的文档检索工具。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field as dataclass_field
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


DEFAULT_RESULT_MAX_CHARS = 20000
MAX_PDF_PAGES_PER_CALL = 8
FILE_IDS_PROPERTY = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "maxItems": 20,
    "description": "要检索的来源文件 ID。必须从当前消息引用文件中选择；默认传入全部引用文件，聚焦单个文件时只传该文件 ID。",
}


@dataclass(frozen=True)
class ToolOutput:
    content: str
    images: tuple[dict[str, Any], ...] = ()


@dataclass
class AgentFileContext:
    file_id: str
    file_name: str
    snapshot: FileExtractionSnapshot
    include_source: bool = True
    vector_index: FileVectorIndex | None = None
    vector_index_loaded: bool = False
    vector_index_error: str | None = None


@dataclass
class AgentToolContext:
    """一次用户消息引用的一个或多个文件快照。"""

    file_id: str | None = None
    snapshot: FileExtractionSnapshot | None = None
    file_name: str = ""
    files: dict[str, AgentFileContext] = dataclass_field(default_factory=dict)
    tool_result_max_chars: int = DEFAULT_RESULT_MAX_CHARS
    vector_index: FileVectorIndex | None = None
    vector_index_loaded: bool = False
    vector_index_error: str | None = None

    def __post_init__(self) -> None:
        # 保留旧测试与外部调用的单文件构造方式。
        if not self.files and self.file_id and self.snapshot is not None:
            self.files[self.file_id] = AgentFileContext(
                file_id=self.file_id,
                file_name=self.file_name or self.file_id,
                snapshot=self.snapshot,
                include_source=False,
                vector_index=self.vector_index,
                vector_index_loaded=self.vector_index_loaded,
                vector_index_error=self.vector_index_error,
            )

    @property
    def default_file_id(self) -> str:
        if self.file_id and self.file_id in self.files:
            return self.file_id
        if self.files:
            return next(iter(self.files))
        raise ValueError("当前消息没有引用文件")

    def iter_files(self) -> list[AgentFileContext]:
        return list(self.files.values())

    def get_file(self, file_id: Any = None) -> AgentFileContext:
        selected = str(file_id).strip() if file_id not in (None, "") else self.default_file_id
        item = self.files.get(selected)
        if item is None:
            raise ValueError(f"file_id 不在当前消息引用范围内: {selected}")
        return item

    def sync_legacy_vector_state(self, item: AgentFileContext) -> None:
        self.vector_index = item.vector_index
        self.vector_index_loaded = item.vector_index_loaded
        self.vector_index_error = item.vector_index_error


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
        "在 file_ids 指定文件的完整 Markdown 中查找一个或多个精确关键词，并合并返回每个命中位置前后指定字符数的原文窗口。结果始终带 file_id 和 file_name，便于区分来源；适合已知术语、字段名、金额名称或短语，想查看其邻近上下文时使用。需要比较本条消息引用的全部文档时传入全部 file_ids；只查某个文档时传入单个 ID。关键词应拆成简短且有区分度的词，不要直接把整句问题当成一个关键词。",
        {"file_ids": FILE_IDS_PROPERTY, "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "description": "要在原文中查找的精确关键词列表。"}, "context_before": {"type": "integer", "minimum": 0, "maximum": 2000, "description": "每个命中向前扩展的字符数，默认 200。"}, "context_after": {"type": "integer", "minimum": 0, "maximum": 2000, "description": "每个命中向后扩展的字符数，默认 200。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回结果数，默认 8。"}},
        ["file_ids", "keywords"],
    ),
    _tool(
        "list_sections",
        "列出 file_ids 指定文件解析出的 Markdown 章节标题与层级，不读取章节正文。比较全部引用文档时传入全部 file_ids；只查看某个文档时传入单个 ID。每项返回 file_id、file_name、section_index、章节编号、标题、层级和目录标记；后续必须把选中的 file_id 与 section_index 一起传给 read_section。索引只在对应文件内有效，不是页码。",
        {"file_ids": FILE_IDS_PROPERTY},
        ["file_ids"],
    ),
    _tool(
        "read_section",
        "按 list_sections 返回的 file_id 和 section_index 读取对应文件的完整章节正文，并包含其下级小节；目录条目会尽量映射到对应正文。请先列章节再精确读取，不能把标题文字当索引。文本结果受 Agent 设置中的单次工具结果上限约束（默认 20000 字符），超长时保留来源并明确标记截断。",
        {"file_id": {"type": "string", "minLength": 1, "description": "由 list_sections 返回的来源文件 ID。"}, "section_index": {"type": "integer", "minimum": 0, "description": "由 list_sections 返回、且只在该 file_id 内有效的章节索引。"}},
        ["file_id", "section_index"],
    ),
    _tool(
        "search_rule",
        "在 file_ids 指定文件中按关键词定位规则文本，并沿 forward（命中后）、backward（命中前）或 both（前后）方向扩展到最近停用词边界。结果带 file_id/file_name，适合查找带条件、限制、例外或相邻说明的规则句段。比较全部引用文档时传入全部 file_ids；只查某个文档时传入单个 ID。",
        {"file_ids": FILE_IDS_PROPERTY, "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "description": "用于定位规则段落的精确关键词。"}, "direction": {"type": "string", "enum": ["forward", "backward", "both"], "description": "扩展方向；默认 forward。"}, "max_length": {"type": "integer", "minimum": 1, "maximum": 4000, "description": "单个命中向前/向后的最大扩展字符数，默认 300。"}, "stop_words": {"type": "array", "items": {"type": "string"}, "description": "可选段落边界词；省略时使用系统默认标点与换行边界。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回结果数，默认 8。"}},
        ["file_ids", "keywords"],
    ),
    _tool(
        "search_chunk_db",
        "在 file_ids 指定文件已保存的 MySQL 文本分块中按关键词匹配，合并返回命中的完整分块。每项带 file_id/file_name、chunk_id、分块序号和页码；这是词项匹配，不是语义向量搜索。比较全部引用文档时传入全部 file_ids；只查某个文档时传入单个 ID。",
        {"file_ids": FILE_IDS_PROPERTY, "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "description": "用于匹配分块的关键词。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回分块数，默认 8。"}},
        ["file_ids", "keywords"],
    ),
    _tool(
        "search_bm25",
        "使用 BM25 词项相关度对 file_ids 指定文件的文本分块合并排序，返回分数最高的分块及其页码。每项带 file_id/file_name；BM25 不会调用 embedding，也不等同于语义向量检索。比较全部引用文档时传入全部 file_ids；只查某个文档时传入单个 ID。",
        {"file_ids": FILE_IDS_PROPERTY, "query_text": {"type": "string", "minLength": 1, "description": "用于 BM25 排序的短查询，可包含多个相关词。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回分块数，默认 8。"}},
        ["file_ids", "query_text"],
    ),
    _tool(
        "search_vector_db",
        "使用现有 embedding 配置在 file_ids 指定文件的 Milvus 子块向量索引中进行语义检索，并将命中映射回可读分块。每项带 file_id/file_name；需要对应文件已完成向量化且索引可用。比较全部引用文档时传入全部 file_ids；只查某个文档时传入单个 ID。",
        {"file_ids": FILE_IDS_PROPERTY, "query_text": {"type": "string", "minLength": 1, "description": "自然语言语义查询，写清问题对象和关系。"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回分块数，默认 8。"}},
        ["file_ids", "query_text"],
    ),
    _tool(
        "search_page",
        "按页码范围读取 file_ids 指定文件 MinerU 逐页 Markdown 文本，不调用视觉模型。页码范围会在每个文件内分别解释，结果带 file_id/file_name/page_num；扫描图片中未识别出的内容请改用 pdf_reader。比较全部引用文档时传入全部 file_ids；只查某个文档时传入单个 ID。",
        {"file_ids": FILE_IDS_PROPERTY, "page_range": {"type": "string", "minLength": 1, "description": "单页、闭区间页码（如 4-6）或 all。"}, "max_chars": {"type": "integer", "minimum": 500, "maximum": 200000, "description": "可选较小的 Markdown 字符窗口；省略时使用 Agent 设置的单次工具结果上限（默认 20000 字符），不能突破该设置。"}},
        ["file_ids", "page_range"],
    ),
    _tool(
        "list_tables",
        "列出 file_ids 指定文件解析出的表格索引、表名和页码，不返回单元格内容。比较全部引用文档时传入全部 file_ids；只查看某个文档时传入单个 ID。每项带 file_id/file_name；先用本工具确认候选表，再将对应 file_id 与 table_index 传给 read_table。索引只在对应文件内有效。",
        {"file_ids": FILE_IDS_PROPERTY},
        ["file_ids"],
    ),
    _tool(
        "read_table",
        "按 list_tables 返回的 file_id 与 table_index 读取一个表格的完整解析内容，并附表名与页码。此工具按索引精确读取，不执行 exact/fuzzy/contains/LLM 表名匹配。文本结果受 Agent 设置中的单次工具结果上限约束（默认 20000 字符），超长时保留来源并明确标记截断。",
        {"file_id": {"type": "string", "minLength": 1, "description": "由 list_tables 返回的来源文件 ID。"}, "table_index": {"type": "integer", "minimum": 0, "description": "由 list_tables 返回、且只在该 file_id 内有效的表格索引。"}},
        ["file_id", "table_index"],
    ),
    _tool(
        "web_search",
        "调用系统配置的博查网络搜索服务查询当前文件之外的公开网页信息。仅在问题需要外部或最新信息、或文档缺少背景时使用；它不会检索当前 PDF。返回标题、来源、日期、摘要和 URL，引用时保留来源链接，并将网页内容视为待核实的外部材料。此工具依赖 web_search 配置中的 API Key。",
        {"query": {"type": "string", "minLength": 1, "description": "简洁明确的联网搜索词。"}, "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回网页数量，默认使用系统设置。"}, "freshness": {"type": "string", "enum": ["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"], "description": "网页时间范围；省略时使用系统设置。"}},
        ["query"],
    ),
    _tool(
        "pdf_reader",
        "将当前消息引用范围内明确指定 file_id 的原始 PDF 连续页渲染成图片，并把图片直接交给当前 Agent 模型视觉读取；页码从 1 开始，每次最多 8 页。适合核对扫描件、印章、签字、图表布局或解析文本缺失的页面，过程时间线会展示带来源的页图。",
        {"file_id": {"type": "string", "minLength": 1, "description": "当前消息引用范围内需要查看页图的文件 ID。"}, "start_page": {"type": "integer", "minimum": 1, "description": "起始页码，包含该页。"}, "end_page": {"type": "integer", "minimum": 1, "description": "结束页码，包含该页；须不小于 start_page，且与起始页合计不超过 8 页。"}},
        ["file_id", "start_page", "end_page"],
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


def _json_result(value: Any, max_chars: int = DEFAULT_RESULT_MAX_CHARS) -> str:
    """限制最终 JSON 字符数，保留来源与截断标记，避免重复包装长正文。"""
    def encode(item: Any) -> str:
        return json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))

    serialized = encode(value)
    if len(serialized) <= max_chars:
        return serialized
    message = f"工具结果超过 {max_chars} 字符，已截断；可缩小检索范围或调高 Agent 设置中的单次工具结果上限。"
    text_key = next((key for key in ("content", "table_content", "error") if isinstance(value, dict) and isinstance(value.get(key), str)), None)
    if text_key:
        result = {**value, "truncated": True, "message": message}
        text = value[text_key]
        key = text_key
    else:
        sources = []
        stack = [value]
        while stack and len(sources) < 20:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("file_id"):
                    source = {key: item[key] for key in ("file_id", "file_name", "page_num", "section_index", "table_index") if key in item}
                    if source not in sources:
                        sources.append(source)
                stack.extend(part for part in item.values() if isinstance(part, (dict, list)))
            elif isinstance(item, list):
                stack.extend(reversed(item))
        result = {"truncated": True, "sources": sources, "original_chars": len(serialized), "message": message}
        text, key = serialized, "preview"
    result[key] = ""
    # 极小预算或超长来源名称时退化为有明确标记的预览，保证 JSON 始终有效。
    if len(encode(result)) > max_chars:
        result = {"truncated": True, "message": message}
        text, key = serialized, "preview"
    low, high = 0, min(len(text), max_chars)
    while low < high:
        middle = (low + high + 1) // 2
        result[key] = text[:middle]
        if len(encode(result)) <= max_chars:
            low = middle
        else:
            high = middle - 1
    result[key] = text[:low]
    return encode(result)


def limit_tool_output(output: ToolOutput, max_chars: int) -> ToolOutput:
    """统一约束普通结果、异常结果及 PDF 文本说明；图片单独保留。"""
    if len(output.content) <= max_chars:
        return output
    try:
        value = json.loads(output.content)
    except (TypeError, ValueError):
        value = {"content": output.content}
    return ToolOutput(_json_result(value, max_chars), output.images)


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


def _selected_files(args: dict[str, Any], context: AgentToolContext) -> list[AgentFileContext]:
    raw_ids = args.get("file_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("文档检索工具必须在参数中提供非空 file_ids 数组")
    if len(raw_ids) > 20 or any(not isinstance(value, str) or not value.strip() for value in raw_ids):
        raise ValueError("file_ids 必须是最多包含 20 个非空文件 ID 的数组")
    ids = list(dict.fromkeys(value.strip() for value in raw_ids))
    return [context.get_file(file_id) for file_id in ids]


def _selected_file(args: dict[str, Any], context: AgentToolContext) -> AgentFileContext:
    file_id = args.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        raise ValueError("文档读取工具必须在参数中提供 file_id")
    return context.get_file(file_id)


def _read_pdf_page_count(data: bytes) -> int:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        return document.page_count
    finally:
        document.close()


async def _execute_pdf_reader(file_context: AgentFileContext, args: dict[str, Any]) -> ToolOutput:
    start = _bounded_int(args.get("start_page"), "start_page", 1, 1, 10_000_000)
    end = _bounded_int(args.get("end_page"), "end_page", start, 1, 10_000_000)
    if end < start:
        raise ValueError("end_page 不能小于 start_page")
    if end - start + 1 > MAX_PDF_PAGES_PER_CALL:
        raise ValueError(f"pdf_reader 每次最多读取 {MAX_PDF_PAGES_PER_CALL} 页")

    source = pdf_path(file_context.file_id)
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
        {
            "file_id": file_context.file_id,
            "file_name": file_context.file_name,
            "page": page,
            "mime_type": "image/jpeg",
            "data_url": f"data:image/jpeg;base64,{image}",
        }
        for page, image in zip(range(start, end + 1), images_b64)
    )
    return ToolOutput(
        content=f"已读取文件 {file_context.file_name}（file_id={file_context.file_id}）PDF 第 {start}-{end} 页（共 {total_pages} 页）。页图已附加给视觉模型分析。",
        images=images,
    )


def _tag_source(file_context: AgentFileContext, result: Any) -> dict[str, Any]:
    item = dict(result) if isinstance(result, dict) else {"content": result}
    if file_context.include_source:
        item["file_id"] = file_context.file_id
        item["file_name"] = file_context.file_name
    return item


def _merge_ranked(rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    if any(isinstance(row.get("score"), (int, float)) for row in rows):
        rows.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
        return rows[:top_k]
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        file_id = str(row.get("file_id") or "__single__")
        if file_id not in grouped:
            grouped[file_id] = []
            order.append(file_id)
        grouped[file_id].append(row)
    merged: list[dict[str, Any]] = []
    while len(merged) < top_k and any(grouped.values()):
        for file_id in order:
            if grouped[file_id] and len(merged) < top_k:
                merged.append(grouped[file_id].pop(0))
    return merged


async def _vector_index(file_context: AgentFileContext, context: AgentToolContext) -> FileVectorIndex | None:
    if file_context.vector_index_error:
        raise ValueError(file_context.vector_index_error)
    if not file_context.vector_index_loaded:
        file_context.vector_index_loaded = True
        try:
            file_context.vector_index = await load_file_vector_index(
                file_context.file_id, file_context.snapshot.chunks
            )
        except Exception as exc:
            file_context.vector_index_error = f"向量索引加载失败: {exc}"
            raise ValueError(file_context.vector_index_error) from exc
        context.sync_legacy_vector_state(file_context)
    return file_context.vector_index


async def execute_tool(name: str, args: dict[str, Any], context: AgentToolContext) -> ToolOutput:
    def result_json(value: Any) -> str:
        return _json_result(value, context.tool_result_max_chars)

    available_files = context.iter_files()
    if not available_files:
        raise ValueError("当前消息没有有效的文件上下文")

    scoped_tools = {
        "search_context", "list_sections", "search_rule", "search_chunk_db",
        "search_bm25", "search_vector_db", "search_page", "list_tables",
    }
    files = _selected_files(args, context) if name in scoped_tools else available_files

    if name == "search_context":
        keywords = _keywords(args.get("keywords"))
        before = _bounded_int(args.get("context_before"), "context_before", 200, 0, 2000)
        after = _bounded_int(args.get("context_after"), "context_after", 200, 0, 2000)
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        rows = []
        for item in files:
            found = await search_context(
                item.snapshot.content,
                {"keywords": keywords, "context_before": before, "context_after": after, "max_results": top_k, "max_total_results": top_k},
                item.snapshot.chunks,
            )
            rows.extend(_tag_source(item, row) for row in found)
        return ToolOutput(result_json(_merge_ranked(rows, top_k)))

    if name == "list_sections":
        result = []
        for item in files:
            result.extend(
                _tag_source(item, {"section_index": section.index, "number": section.number, "title": section.title, "level": section.level, "numbered": section.numbered, "is_toc": section.is_toc})
                for section in parse_sections(item.snapshot.content)
            )
        return ToolOutput(result_json(result))

    if name == "read_section":
        item = _selected_file(args, context)
        index = _bounded_int(args.get("section_index"), "section_index", 0, 0, 10_000_000)
        sections = parse_sections(item.snapshot.content)
        section = next((entry for entry in sections if entry.index == index), None)
        if section is None:
            raise ValueError("section_index 不存在；请先调用 list_sections，并确认 file_id")
        if section.is_toc:
            body_matches = [entry for entry in sections if not entry.is_toc and entry.number == section.number and entry.title == section.title]
            if body_matches:
                section = min(body_matches, key=lambda entry: (entry.index < index, abs(entry.index - index)))
        body = item.snapshot.content[section.start_pos:section.tree_end_pos]
        result = _tag_source(item, {"section_index": section.index, "number": section.number, "title": section.title, "level": section.level, "content": body, "truncated": False})
        return ToolOutput(result_json(result))

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
        rows = []
        for item in files:
            found = await search_rule(item.snapshot.content, search_config, item.snapshot.chunks)
            rows.extend(_tag_source(item, row) for row in found)
        return ToolOutput(result_json(_merge_ranked(rows, top_k)))

    if name == "search_chunk_db":
        keywords = _keywords(args.get("keywords"))
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        rows = []
        for item in files:
            found = await search_chunk_db(
                item.file_id,
                {"keywords": keywords, "max_results": top_k, "max_total_results": top_k},
                item.snapshot.chunks,
            )
            rows.extend(_tag_source(item, row) for row in found)
        return ToolOutput(result_json(_merge_ranked(rows, top_k)))

    if name == "search_bm25":
        query = args.get("query_text")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query_text 不能为空")
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        rows = []
        for item in files:
            found = await asyncio.to_thread(search_bm25, item.file_id, {"query_text": query.strip(), "top_k": top_k}, item.snapshot.chunks)
            rows.extend(_tag_source(item, row) for row in found)
        return ToolOutput(result_json(_merge_ranked(rows, top_k)))

    if name == "search_vector_db":
        query = args.get("query_text")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query_text 不能为空")
        top_k = _bounded_int(args.get("top_k"), "top_k", 8)
        rows = []
        errors = []
        for item in files:
            try:
                index = await _vector_index(item, context)
                found = await search_vector_db(item.file_id, {"query_text": query.strip(), "top_k": top_k}, index)
                rows.extend(_tag_source(item, row) for row in found)
            except ValueError as exc:
                errors.append({"file_id": item.file_id, "file_name": item.file_name, "error": str(exc)})
        return ToolOutput(result_json({"results": _merge_ranked(rows, top_k), "errors": errors}))

    if name == "search_page":
        page_range = args.get("page_range")
        if not isinstance(page_range, str) or not page_range.strip():
            raise ValueError("page_range 不能为空")
        max_chars = min(context.tool_result_max_chars, _bounded_int(args.get("max_chars"), "max_chars", context.tool_result_max_chars, 500, 200000))
        result = []
        chars_left = max_chars
        for item in files:
            rows = sorted(item.snapshot.page_contents.items(), key=lambda entry: (0, int(entry[0])) if str(entry[0]).isdigit() else (1, str(entry[0])))
            available = [int(page) for page, _ in rows if str(page).isdigit()]
            selected = set(_parse_page_selection(page_range, available))
            for page, content in rows:
                if not str(page).isdigit() or int(page) not in selected or chars_left <= 0:
                    continue
                fragment = (content or "")[:chars_left]
                result.append(_tag_source(item, {"page_num": int(page), "content": fragment, "truncated": len(fragment) < len(content or "")}))
                chars_left -= len(fragment)
        return ToolOutput(result_json({"pages": result, "truncated": chars_left <= 0}))

    if name == "list_tables":
        result = []
        for item in files:
            result.extend(
                _tag_source(item, {"table_index": table.table_index, "table_name": table.table_name or f"表格{table.table_index}", "page_num": table.page_num or ""})
                for table in sorted(item.snapshot.tables, key=lambda entry: entry.table_index)
            )
        return ToolOutput(result_json(result))

    if name == "read_table":
        item = _selected_file(args, context)
        index = _bounded_int(args.get("table_index"), "table_index", 0, 0, 10_000_000)
        table = next((entry for entry in item.snapshot.tables if entry.table_index == index), None)
        if table is None:
            raise ValueError("table_index 不存在；请先调用 list_tables，并确认 file_id")
        content = table.table_content or ""
        result = _tag_source(item, {"table_index": index, "table_name": table.table_name or f"表格{index}", "page_num": table.page_num or "", "table_content": content, "truncated": False})
        return ToolOutput(result_json(result))

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
        return ToolOutput(result_json({"summary": formatted, "results": results}))

    if name == "pdf_reader":
        item = _selected_file(args, context)
        return limit_tool_output(await _execute_pdf_reader(item, args), context.tool_result_max_chars)

    raise ValueError(f"未知工具: {name}")

