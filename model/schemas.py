"""Pydantic v2 请求/响应模型。"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator


# ── 通用响应包装 ────────────────────────────────────────────

class ResponseWrapper(BaseModel):
    code: int = 200
    message: str = "success"
    data: Any = None


# ── 文档类型 ────────────────────────────────────────────────

class DocTypeCreate(BaseModel):
    type_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    type_name: str = Field(..., max_length=200)
    description: Optional[str] = None
    max_parse_pages: Optional[int] = Field(None, ge=1)
    enable_embedding: int = Field(1, ge=0, le=1)
    enabled: int = 1
    # 归属项目；仅新建（upsert 建档分支）生效，更新已存在类型时忽略。None = 未分组
    project_id: Optional[str] = None


class DocTypeResponse(BaseModel):
    type_id: str
    type_name: str
    description: Optional[str] = None
    max_parse_pages: Optional[int] = None
    enable_embedding: int = 1
    is_default: int = 0
    enabled: int = 1
    is_template: int = 0
    parent_type_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CopyConfigsRequest(BaseModel):
    """从源类型复制配置到目标类型。

    field_ids / rule_ids 不传或为 null 表示全部复制；空数组表示不复制；
    on_conflict 决定目标类型已有同源 field_id/rule_id 副本时的行为。
    """
    source_type_id: str
    field_ids: Optional[List[str]] = None
    rule_ids: Optional[List[str]] = None
    on_conflict: str = Field("rename", pattern=r"^(skip|rename)$")


class CopyConfigsResponse(BaseModel):
    copied_fields: int = 0
    skipped_fields: int = 0
    copied_rules: int = 0
    skipped_rules: int = 0
    missing_dependencies: List[str] = []


class ExportFieldItem(BaseModel):
    """导出格式的字段项（不含 type_id / field_id / 时间戳）。"""
    field_id: str
    field_name: str
    source_type: str
    enabled: int = 1
    priority: int = 0
    use_llm: int = 1
    is_advanced: int = 0
    depend_fields: Optional[List[str]] = None
    table_name_pattern: Optional[str] = None
    table_match_type: Optional[str] = None
    table_match_keywords: Optional[List[str]] = None
    table_match_max_results: Optional[int] = None
    table_system_prompt: Optional[str] = None
    table_match_prompt: Optional[str] = None
    table_extract_prompt: Optional[str] = None
    search_type: Optional[str] = None
    search_config: Optional[Dict[str, Any]] = None
    text_system_prompt: Optional[str] = None
    text_extract_prompt: Optional[str] = None
    vl_method: Optional[str] = None
    vl_config: Optional[Dict[str, Any]] = None
    vl_system_prompt: Optional[str] = None
    vl_extract_prompt: Optional[str] = None


class ExportRuleItem(BaseModel):
    """导出格式的规则项。depend_fields 用 field_name 列表，便于跨环境恢复。"""
    rule_id: str
    rule_name: str
    rule_type: str
    expression: str
    system_prompt: Optional[str] = None
    web_search: Optional[Dict[str, Any]] = None
    is_formatted: int = 0
    output_schema: Optional[List[Dict[str, Any]]] = None
    depend_field_names: List[str] = []
    enabled: int = 1
    priority: int = 0


class ExportPayload(BaseModel):
    """导出/导入的整体载荷。"""
    type_id: str
    type_name: str
    description: Optional[str] = None
    max_parse_pages: Optional[int] = Field(None, ge=1)
    enable_embedding: int = Field(1, ge=0, le=1)
    version: int = 1
    fields: List[ExportFieldItem] = []
    rules: List[ExportRuleItem] = []


class ImportConfigsRequest(BaseModel):
    """从 JSON 载荷导入到目标类型。

    target_type_id 为空则使用 payload.type_id；
    create_type_if_missing=true 时若目标类型不存在则自动创建。
    """
    payload: ExportPayload
    target_type_id: Optional[str] = None
    create_type_if_missing: bool = True
    on_conflict: str = Field("rename", pattern=r"^(skip|rename)$")


class ImportConfigsResponse(BaseModel):
    target_type_id: str
    created_type: bool = False
    copied_fields: int = 0
    skipped_fields: int = 0
    copied_rules: int = 0
    skipped_rules: int = 0
    missing_dependencies: List[str] = []


class DocTypeBatchDeleteRequest(BaseModel):
    type_ids: List[str]
    force: bool = False


class ProjectCreate(BaseModel):
    """创建/改名项目（按 project_id upsert）。"""
    project_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    project_name: str = Field(..., max_length=200)
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    project_id: str
    project_name: str
    description: Optional[str] = None
    type_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BatchAssignProjectRequest(BaseModel):
    """批量把类型归入项目；project_id 为 None 表示移出（未分组）。

    归类会级联到每个 type_id 的血缘下游（服务端计算），default 类型不受影响。
    """
    type_ids: List[str]
    project_id: Optional[str] = None


# ── 文件相关 ────────────────────────────────────────────────

class FileStatusResponse(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    progress: str
    type_id: str = "default"
    error: Optional[str] = None
    create_time: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FileTableItem(BaseModel):
    file_id: str
    table_index: int
    total_table: int
    table_name: str
    table_content: str
    page_num: Optional[str] = None


class FileChunkItem(BaseModel):
    file_id: str
    chunk_id: str
    chunk_index: int
    total_chunks: int
    chunk_content: str
    page_num: Optional[str] = None


class FileContextQueryRequest(BaseModel):
    """文件片段上下文查询请求。"""
    file_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    query_type: str = Field("keyword", pattern=r"^(keyword|text_fragment)$")
    context_before: int = Field(200, ge=0)
    context_after: int = Field(200, ge=0)
    case_sensitive: bool = False
    include_all_chunks: bool = True

    @field_validator("file_id", "query")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


class FileContextMatchItem(BaseModel):
    match_index: int
    keyword: str
    position: int
    match_start_pos: int
    match_end_pos: int
    context_start_pos: int
    context_end_pos: int
    context: str
    page_num: str = ""
    bboxes: List[Dict[str, Any]] = Field(default_factory=list)


class FileContextChunkItem(BaseModel):
    file_id: str
    chunk_id: str
    chunk_index: int
    total_chunks: int
    chunk_content: str
    start_pos: int
    end_pos: int
    page_num: str = ""
    hit: bool = False
    hit_count: int = 0


class FileContextQueryResponse(BaseModel):
    file_id: str
    query: str
    query_type: str
    matched: bool
    match_count: int
    matches: List[FileContextMatchItem] = Field(default_factory=list)
    chunks: List[FileContextChunkItem] = Field(default_factory=list)


# ── 字段提取配置 ────────────────────────────────────────────

class SourceTypeEnum(str, Enum):
    table = "table"
    text = "text"
    vl = "vl"


class TableMatchTypeEnum(str, Enum):
    exact = "exact"
    fuzzy = "fuzzy"
    contains = "contains"
    llm = "llm"


class SearchTypeEnum(str, Enum):
    context = "context"
    section = "section"
    rule = "rule"
    chunk_db = "chunk_db"
    vector_db = "vector_db"
    page = "page"


class VLMethodEnum(str, Enum):
    vl_model = "vl_model"
    vl_progressive = "vl_progressive"
    vl_locate = "vl_locate"


class ExtractionFieldCreate(BaseModel):
    field_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$", max_length=100)
    type_id: str = Field("default", pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    field_name: str = Field(..., max_length=200)
    source_type: SourceTypeEnum
    enabled: int = 1
    priority: int = 0
    # 0=跳过 LLM 直接返回检索原文（仅 text/table 生效）；须早于 *_extract_prompt 声明，供其校验器读取
    use_llm: int = 1
    # 表格类
    table_name_pattern: Optional[str] = None
    table_match_type: Optional[TableMatchTypeEnum] = None
    table_match_keywords: Optional[List[str]] = None
    table_match_max_results: Optional[int] = None
    table_system_prompt: Optional[str] = None
    # LLM 匹配方式下的自定义匹配提示词；须晚于 table_match_type 声明，供其校验器读取
    table_match_prompt: Optional[str] = None
    table_extract_prompt: Optional[str] = None
    # 文本类
    search_type: Optional[SearchTypeEnum] = None
    search_config: Optional[Dict[str, Any]] = None
    text_system_prompt: Optional[str] = None
    text_extract_prompt: Optional[str] = None
    # VL 类
    vl_method: Optional[VLMethodEnum] = None
    vl_config: Optional[Dict[str, Any]] = None
    vl_system_prompt: Optional[str] = None
    vl_extract_prompt: Optional[str] = None
    # 进阶字段标志与依赖（depend_fields 服务端会按实际配置重算覆盖）
    is_advanced: int = 0
    depend_fields: Optional[List[str]] = None

    @field_validator("text_extract_prompt")
    @classmethod
    def validate_text_prompt(cls, v, info):
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if info.data.get("use_llm") == 0:
            return v
        if info.data.get("source_type") == SourceTypeEnum.text and v:
            if not re.search(r"<search_result>.+?</search_result>", v):
                raise ValueError("text_extract_prompt 必须包含至少一个 <search_result>标签</search_result> 占位符")
        return v

    @field_validator("table_extract_prompt")
    @classmethod
    def validate_table_prompt(cls, v, info):
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if info.data.get("use_llm") == 0:
            return v
        if info.data.get("source_type") == SourceTypeEnum.table and v:
            if not re.search(r"<search_result>.+?</search_result>", v):
                raise ValueError("table_extract_prompt 必须包含至少一个 <search_result>标签</search_result> 占位符")
        return v

    @field_validator("table_match_prompt")
    @classmethod
    def validate_table_match_prompt(cls, v, info):
        """LLM 匹配的自定义模板必须含 {table_list}，否则模型看不到候选表格。"""
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if not v or not v.strip():
            return v
        if info.data.get("table_match_type") != TableMatchTypeEnum.llm:
            return v
        if "{table_list}" not in v:
            raise ValueError("table_match_prompt 必须包含 {table_list} 占位符")
        return v

    @field_validator("search_config")
    @classmethod
    def validate_section_match_prompt(cls, v, info):
        """章节 LLM 匹配的自定义模板必须含 {section_list}。"""
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if not isinstance(v, dict):
            return v
        tpl = v.get("section_match_prompt")
        if not isinstance(tpl, str) or not tpl.strip():
            return v
        match_type = v.get("section_match_type") or v.get("match_type")
        if match_type != "llm":
            return v
        if "{section_list}" not in tpl:
            raise ValueError("section_match_prompt 必须包含 {section_list} 占位符")
        return v

    @field_validator("vl_method")
    @classmethod
    def validate_vl_method_required(cls, v, info):
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if info.data.get("source_type") == SourceTypeEnum.vl and not v:
            raise ValueError("source_type='vl' 时 vl_method 必填")
        return v

    @field_validator("vl_extract_prompt")
    @classmethod
    def validate_vl_extract_prompt(cls, v, info):
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if info.data.get("source_type") == SourceTypeEnum.vl:
            if not v:
                raise ValueError("source_type='vl' 时 vl_extract_prompt 必填")
            lower = v.lower()
            if "value" not in lower or "reason" not in lower:
                raise ValueError(
                    "vl_extract_prompt 必须包含 'value' 与 'reason' 关键字（大小写不敏感），"
                    "因为最终要求 VL 输出 {value, reason} JSON"
                )
        return v

    @field_validator("vl_config")
    @classmethod
    def validate_vl_config_templates(cls, v, info):
        if cls.__name__ != "ExtractionFieldCreate":
            return v
        if v is None:
            return v
        method = info.data.get("vl_method")
        if method == VLMethodEnum.vl_progressive:
            tpl = v.get("batch_prompt_template")
            if tpl:
                required = ["{field_hints}", "{page_label}", "{total_pages}", "{history}"]
                missing = [r for r in required if r not in tpl]
                if missing:
                    raise ValueError(f"batch_prompt_template 缺少占位符 {missing}")
        elif method == VLMethodEnum.vl_locate:
            tpl = v.get("locate_prompt_template")
            if tpl:
                required = [
                    "{field_hints}",
                    "{page_labels}",
                    "{position_map}",
                    "{grid_rows}",
                    "{grid_cols}",
                ]
                missing = [r for r in required if r not in tpl]
                if missing:
                    raise ValueError(f"locate_prompt_template 缺少占位符 {missing}")
        return v

    @model_validator(mode="after")
    def validate_required_prompt_by_source_type(self):
        """按 source_type 补齐字段级校验。

        field_validator 默认不在字段未提供时触发，所以补一道 model 层校验。
        """
        if self.__class__.__name__ != "ExtractionFieldCreate":
            return self
        # use_llm=0：text/table 直接返回检索原文，不需要提取提示词（vl 不受影响，恒需 LLM）
        skip_prompt = self.use_llm == 0
        if self.source_type == SourceTypeEnum.table:
            if skip_prompt:
                return self
            prompt = (self.table_extract_prompt or "").strip()
            if not prompt:
                raise ValueError("source_type='table' 时 table_extract_prompt 必填")
            if not re.search(r"<search_result>.+?</search_result>", prompt):
                raise ValueError("table_extract_prompt 必须包含至少一个 <search_result>标签</search_result> 占位符")
        elif self.source_type == SourceTypeEnum.text:
            if skip_prompt:
                return self
            prompt = (self.text_extract_prompt or "").strip()
            if not prompt:
                raise ValueError("source_type='text' 时 text_extract_prompt 必填")
            if not re.search(r"<search_result>.+?</search_result>", prompt):
                raise ValueError("text_extract_prompt 必须包含至少一个 <search_result>标签</search_result> 占位符")
        elif self.source_type == SourceTypeEnum.vl:
            if not self.vl_method:
                raise ValueError("source_type='vl' 时 vl_method 必填")
            if not self.vl_extract_prompt:
                raise ValueError("source_type='vl' 时 vl_extract_prompt 必填")
        return self

    @model_validator(mode="after")
    def validate_query_text_placeholders(self):
        """vector_db 多路检索的每个 query 都必须在提取提示词里有对应占位符。

        replace_search_result_placeholders 按 label 替换，prompt 里没有的
        label 结果会被**静默丢弃**——用户加了同义词却没改 prompt，那一路
        检索白跑且完全不可察，故在此 fail-fast。
        """
        if self.__class__.__name__ != "ExtractionFieldCreate":
            return self
        if self.search_type != SearchTypeEnum.vector_db:
            return self
        # use_llm=0 直接返回检索原文，不经占位符替换
        if self.use_llm == 0:
            return self

        config = self.search_config or {}
        raw = config.get("query_text")
        items = raw if isinstance(raw, list) else [raw]
        labels = [i.strip() for i in items if isinstance(i, str) and i.strip()]
        if not labels:
            return self

        prompt = self.text_extract_prompt or ""
        missing = [
            label
            for label in labels
            if f"<search_result>{label}</search_result>" not in prompt
        ]
        if missing:
            raise ValueError(
                f"text_extract_prompt 缺少这些 query_text 的占位符: {missing}。"
                f"每路 query 各需一个 <search_result>查询词</search_result>，"
                f"否则该路检索结果会被静默丢弃"
            )
        return self


class ExtractionFieldResponse(ExtractionFieldCreate):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── 逻辑分析配置 ────────────────────────────────────────────

class RuleTypeEnum(str, Enum):
    judge = "judge"
    calc = "calc"
    custom = "custom"


class AnalysisRuleCreate(BaseModel):
    rule_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$", max_length=100)
    type_id: str = Field("default", pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    rule_name: str = Field(..., max_length=200)
    rule_type: RuleTypeEnum
    expression: str
    system_prompt: Optional[str] = None
    depend_fields: Optional[List[str]] = None
    web_search: Optional[Dict[str, Any]] = None
    # 自定义规则：格式化输出开关 + 字段树
    is_formatted: int = Field(0, ge=0, le=1)
    output_schema: Optional[List[Dict[str, Any]]] = None
    enabled: int = 1
    priority: int = 0

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, v):
        if v and not re.search(r"<field_result>.+?</field_result>", v):
            raise ValueError("expression 必须包含至少一个 <field_result>字段标识</field_result> 占位符")
        return v

    @model_validator(mode="after")
    def validate_web_search(self):
        """启用网络搜索时校验：judge 或 custom、query 非空、expression 含占位符。"""
        ws = self.web_search
        if ws and ws.get("enabled"):
            if self.rule_type not in (RuleTypeEnum.judge, RuleTypeEnum.custom):
                raise ValueError("仅 judge / custom 类型规则支持网络搜索")
            if not (ws.get("query") or "").strip():
                raise ValueError("启用网络搜索时 query 不能为空")
            if "<web_search_result/>" not in self.expression:
                raise ValueError("启用网络搜索时 expression 必须包含 <web_search_result/> 占位符")
        return self

    @model_validator(mode="after")
    def validate_output_schema_when_formatted(self):
        """custom 且开启格式化时，output_schema 必须存在且结构合法。"""
        if self.rule_type == RuleTypeEnum.custom and self.is_formatted == 1:
            if not self.output_schema:
                raise ValueError("custom 规则开启格式化输出时 output_schema 不能为空")
            from utils.output_schema import OutputSchemaError, validate_output_schema
            try:
                validate_output_schema(self.output_schema)
            except OutputSchemaError as e:
                raise ValueError(f"output_schema 结构非法：{e}")
        return self


class AnalysisRuleResponse(AnalysisRuleCreate):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── 独立逻辑分析 ────────────────────────────────────────────

class AnalysisRunModeEnum(str, Enum):
    sync = "sync"
    async_ = "async"
    stream = "stream"


class AnalysisRunSourceEnum(str, Enum):
    values = "values"   # 字段值由请求 field_values 提供
    file = "file"       # 字段值取自该 file_id 已落库的 extraction_result


class AnalysisRunItem(BaseModel):
    """独立逻辑分析的单组输入。

    `source=values`（默认）时 type_id + field_values 必填、不得传 file_id；
    `source=file` 时 file_id 必填、不得传 field_values，type_id 省略则取
    files.type_id（传了且不一致会在该 item 结果里报错）。
    rule_ids 语义两种模式一致：不传/null=全部启用规则（仅跑依赖被覆盖的）、
    []=不跑、显式点名=只跑指定的且缺依赖字段产出失败结果。
    """

    type_id: Optional[str] = Field(
        None, pattern=r"^[a-zA-Z0-9_-]+$", max_length=64,
    )
    biz_id: str = Field(..., min_length=1, max_length=200)
    field_values: Dict[str, str] = Field(default_factory=dict)
    rule_ids: Optional[List[str]] = None
    file_id: Optional[str] = Field(None, min_length=1, max_length=64)

    @field_validator("biz_id")
    @classmethod
    def strip_biz_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("biz_id 不能为空")
        return value


class AnalysisRunRuleResult(BaseModel):
    rule_id: str
    rule_name: str
    rule_type: str
    result: str = ""
    reason: str = ""
    input_values: Dict[str, str] = Field(default_factory=dict)
    source_refs: Optional[Dict[str, Any]] = None
    success: bool
    index: int
    total: int


class AnalysisRunItemResult(BaseModel):
    item_index: int
    biz_id: str
    type_id: str
    total: int
    succeeded: int
    failed: int
    results: List[AnalysisRunRuleResult] = Field(default_factory=list)
    # 请求点名了但该类型下不存在的 rule_id；显式点名的禁用规则仍会执行
    unknown_rule_ids: List[str] = Field(default_factory=list)
    # file 模式的 item 级错误（文件不存在 / type_id 不一致 / 无提取结果）；正常为 None
    error: Optional[str] = None


class AnalysisRunResponse(BaseModel):
    total_items: int
    items: List[AnalysisRunItemResult] = Field(default_factory=list)


class AnalysisRunRequest(BaseModel):
    """独立逻辑分析请求；async 模式必须通过 callback_url 接收结果。"""

    mode: AnalysisRunModeEnum
    source: AnalysisRunSourceEnum = AnalysisRunSourceEnum.values
    persist: bool = False
    callback_url: Optional[AnyHttpUrl] = None
    items: List[AnalysisRunItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_async_callback(self):
        if self.mode == AnalysisRunModeEnum.async_ and self.callback_url is None:
            raise ValueError("async 模式必须提供 callback_url")

        is_file = self.source == AnalysisRunSourceEnum.file
        if self.persist and not is_file:
            raise ValueError("persist=true 仅在 source=file 时可用")

        for item in self.items:
            if is_file:
                if not item.file_id:
                    raise ValueError("source=file 时每个 item 必须提供 file_id")
                if item.field_values:
                    raise ValueError(
                        "source=file 时不能传 field_values（字段值取自库内提取结果）"
                    )
            else:
                if item.file_id:
                    raise ValueError("source=values 时不能传 file_id")
                if not item.type_id:
                    raise ValueError("source=values 时每个 item 必须提供 type_id")
        return self


# ── 提取结果 ────────────────────────────────────────────────

class ExtractionResultItem(BaseModel):
    file_id: str
    field_id: str
    field_name: Optional[str] = None
    extracted_value: str
    reason: Optional[str] = None
    # 模型自报参考页（text/table 的 LLM 输出 pages）；VL / use_llm=0 / 模型未返回时为 []
    pages: List[int] = Field(default_factory=list)
    # 可用页码：pages 优先，无则由 source_refs 现算的程序命中页兜底。
    # 键恒存在，值可能为空数组（失败字段 / 无命中 / vl_progressive）
    source_pages: List[int] = Field(default_factory=list)
    source_refs: Optional[Dict[str, Any]] = None


class AnalysisResultItem(BaseModel):
    file_id: str
    rule_id: str
    rule_name: Optional[str] = None
    result_value: str
    input_values: Optional[Dict[str, str]] = None
    reason: Optional[str] = None
    source_refs: Optional[Dict[str, Any]] = None


# ── 调试接口 ────────────────────────────────────────────────

class ExtractionTestRequest(BaseModel):
    """字段提取调试请求：field_id + file_id 模式 或 完整 config 模式。"""
    file_id: str
    field_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class ExtractionTestResponse(BaseModel):
    search_results: List[Dict[str, Any]] = []
    llm_input: str = ""
    llm_output: str = ""
    extracted_value: str = ""
    reason: str = ""
    # 模型自报参考页；VL / use_llm=0 / 模型未返回时为 []
    pages: List[int] = Field(default_factory=list)
    # 可用页码：pages 优先，程序命中页兜底，键恒存在
    source_pages: List[int] = Field(default_factory=list)
    # 进阶字段（is_advanced=1）专属：引用解析溯源 {_resolved_refs?, _page_link?}；普通字段为 None
    resolved_refs: Optional[Dict[str, Any]] = None


class AnalysisTestRequest(BaseModel):
    """逻辑分析调试请求：rule_id + file_id 模式 或 完整 config 模式。"""
    file_id: str
    rule_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    re_extract: bool = False


class AnalysisTestResponse(BaseModel):
    input_values: Dict[str, str] = {}
    expression_resolved: str = ""
    result_value: str = ""
    reason: str = ""


# ── 向量检索 ────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    file_id: Optional[str] = None
    top_k: int = 10
    score_threshold: Optional[float] = None


class SearchResultItem(BaseModel):
    chunk_id: str
    file_id: str
    chunk_index: int
    chunk_content: str
    score: float
    page_num: Optional[str] = None


# ── 文件列表与详情 ────────────────────────────────────────────

class FileListItem(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    progress: str
    type_id: str = "default"
    error: Optional[str] = None
    create_time: Optional[datetime] = None


class FileListResponse(BaseModel):
    items: List[FileListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProcessingItem(BaseModel):
    """处理中队列项：/file/processing 返回。带 doc_type JOIN 出来的 type_name/project_id。"""
    file_id: str
    file_name: str
    progress: str
    type_id: str = "default"
    type_name: Optional[str] = None
    project_id: Optional[str] = None
    create_time: Optional[datetime] = None


class FileDetailResponse(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    progress: str
    type_id: str = "default"
    error: Optional[str] = None
    create_time: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    start_parsing_time: Optional[datetime] = None
    end_parsing_time: Optional[datetime] = None
    start_tableing_time: Optional[datetime] = None
    end_tableing_time: Optional[datetime] = None
    start_chunking_time: Optional[datetime] = None
    end_chunking_time: Optional[datetime] = None
    start_embedding_time: Optional[datetime] = None
    end_embedding_time: Optional[datetime] = None
    start_extracting_time: Optional[datetime] = None
    end_extracting_time: Optional[datetime] = None
    start_analyzing_time: Optional[datetime] = None
    end_analyzing_time: Optional[datetime] = None


class BatchDeleteRequest(BaseModel):
    file_ids: List[str]


class BatchDeleteResponse(BaseModel):
    deleted_count: int
    failed_ids: List[str] = []


# ── 文件统计 ────────────────────────────────────────────────

class StatsRangeEnum(str, Enum):
    """统计时间窗口。窗口对 `files.create_time` 生效，**统一作用于整页所有指标**。

    `1h` / `24h` 是滚动时钟窗口（从此刻往前推）；`today` / `yesterday` / `Nd`
    是自然日窗口（从某天 00:00 起算），因为按天分桶的图表用自然日更符合直觉。
    """
    ALL = "all"
    H1 = "1h"
    H24 = "24h"
    TODAY = "today"
    YESTERDAY = "yesterday"
    D3 = "3d"
    D7 = "7d"
    D30 = "30d"
    D90 = "90d"
    D365 = "365d"


class StatsOverview(BaseModel):
    """统计概览 KPI（全部文档类型 / 项目，但受时间窗口约束）。"""
    total_files: int = 0
    completed: int = 0
    failed: int = 0
    processing: int = 0
    total_size: int = 0
    success_rate: float = 0.0
    # 全流程平均耗时（秒）：每个文件六阶段 end-start 之和；缺失阶段按 0，全部文件计入
    avg_total_seconds: Optional[float] = None
    type_count: int = 0
    project_count: int = 0


class StatsCountItem(BaseModel):
    """分组计数项：状态分布 / 项目占比 / 类型排行共用。

    `label` 取数据库里的可读名（type_name / project_name）；状态分布没有可读名，
    此处回落为 `key` 原值，中文文案由前端 `Utils.getStatusText` 统一映射，
    避免后端再维护一份会漂移的状态中文表。
    """
    key: str
    label: str
    count: int = 0
    size: int = 0


class StatsTrendItem(BaseModel):
    """按桶聚合的处理趋势（按 files.create_time 落桶）。

    `date` 的格式随 `granularity` 变化：`day` 为 `YYYY-MM-DD`，`hour` 为 `YYYY-MM-DD HH:00`。
    只返回**有数据的桶**，空桶需消费方自行补零。
    """
    date: str
    count: int = 0
    completed: int = 0
    failed: int = 0


class StatsStageItem(BaseModel):
    """单阶段耗时统计（秒）。

    仅统计 `start_*_time` / `end_*_time` 双端非空的文件，因此 `samples` 通常小于
    文件总数（未跑到该阶段、或只有 end 没有 start 的历史遗留行都不计入）。
    阶段中文名由前端 `Utils.getStageText` 映射。
    """
    stage: str
    samples: int = 0
    avg_seconds: float = 0.0
    min_seconds: float = 0.0
    max_seconds: float = 0.0
    total_seconds: float = 0.0


class FileStatsResponse(BaseModel):
    overview: StatsOverview
    status_distribution: List[StatsCountItem] = []
    by_project: List[StatsCountItem] = []
    by_type: List[StatsCountItem] = []
    trend: List[StatsTrendItem] = []
    stage_durations: List[StatsStageItem] = []
    # 生效的时间窗口：range 为入参回显，start/end 为解析后的实际边界
    range: StatsRangeEnum = StatsRangeEnum.D30
    granularity: str = "day"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
