"""Pydantic v2 请求/响应模型。"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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
    enabled: int = 1


class DocTypeResponse(BaseModel):
    type_id: str
    type_name: str
    description: Optional[str] = None
    is_default: int = 0
    enabled: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CopyConfigsRequest(BaseModel):
    """从源类型复制配置到目标类型。

    field_ids / rule_ids 留空表示全部复制；
    on_conflict 决定目标类型已有同名字段/规则时的行为。
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
    table_name_pattern: Optional[str] = None
    table_match_type: Optional[str] = None
    table_match_keywords: Optional[List[str]] = None
    table_match_max_results: Optional[int] = None
    table_system_prompt: Optional[str] = None
    table_extract_prompt: Optional[str] = None
    search_type: Optional[str] = None
    search_config: Optional[Dict[str, Any]] = None
    text_system_prompt: Optional[str] = None
    text_extract_prompt: Optional[str] = None


class ExportRuleItem(BaseModel):
    """导出格式的规则项。depend_fields 用 field_name 列表，便于跨环境恢复。"""
    rule_id: str
    rule_name: str
    rule_type: str
    expression: str
    system_prompt: Optional[str] = None
    depend_field_names: List[str] = []
    enabled: int = 1
    priority: int = 0


class ExportPayload(BaseModel):
    """导出/导入的整体载荷。"""
    type_id: str
    type_name: str
    description: Optional[str] = None
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
    # 表格类
    table_name_pattern: Optional[str] = None
    table_match_type: Optional[TableMatchTypeEnum] = None
    table_match_keywords: Optional[List[str]] = None
    table_match_max_results: Optional[int] = None
    table_system_prompt: Optional[str] = None
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

    @field_validator("text_extract_prompt")
    @classmethod
    def validate_text_prompt(cls, v, info):
        if info.data.get("source_type") == SourceTypeEnum.text and v:
            if not re.search(r"<search_result>.+?</search_result>", v):
                raise ValueError("text_extract_prompt 必须包含至少一个 <search_result>标签</search_result> 占位符")
        return v

    @field_validator("table_extract_prompt")
    @classmethod
    def validate_table_prompt(cls, v, info):
        if info.data.get("source_type") == SourceTypeEnum.table and v:
            if not re.search(r"<search_result>.+?</search_result>", v):
                raise ValueError("table_extract_prompt 必须包含至少一个 <search_result>标签</search_result> 占位符")
        return v

    @field_validator("vl_method")
    @classmethod
    def validate_vl_method_required(cls, v, info):
        if info.data.get("source_type") == SourceTypeEnum.vl and not v:
            raise ValueError("source_type='vl' 时 vl_method 必填")
        return v

    @field_validator("vl_extract_prompt")
    @classmethod
    def validate_vl_extract_prompt(cls, v, info):
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
    def validate_vl_required_when_source_is_vl(self):
        """source_type='vl' 时强校验 vl_method / vl_extract_prompt 存在。

        field_validator 默认不在字段未提供时触发，所以补一道 model 层校验。
        """
        if self.source_type == SourceTypeEnum.vl:
            if not self.vl_method:
                raise ValueError("source_type='vl' 时 vl_method 必填")
            if not self.vl_extract_prompt:
                raise ValueError("source_type='vl' 时 vl_extract_prompt 必填")
        return self


class ExtractionFieldResponse(ExtractionFieldCreate):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── 逻辑分析配置 ────────────────────────────────────────────

class RuleTypeEnum(str, Enum):
    judge = "judge"
    calc = "calc"


class AnalysisRuleCreate(BaseModel):
    rule_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$", max_length=100)
    type_id: str = Field("default", pattern=r"^[a-zA-Z0-9_-]+$", max_length=64)
    rule_name: str = Field(..., max_length=200)
    rule_type: RuleTypeEnum
    expression: str
    system_prompt: Optional[str] = None
    depend_fields: Optional[List[str]] = None
    enabled: int = 1
    priority: int = 0

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, v):
        if v and not re.search(r"<field_result>.+?</field_result>", v):
            raise ValueError("expression 必须包含至少一个 <field_result>字段标识</field_result> 占位符")
        return v


class AnalysisRuleResponse(AnalysisRuleCreate):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── 提取结果 ────────────────────────────────────────────────

class ExtractionResultItem(BaseModel):
    file_id: str
    field_id: str
    extracted_value: str
    reason: Optional[str] = None


class AnalysisResultItem(BaseModel):
    file_id: str
    rule_id: str
    result_value: str
    input_values: Optional[Dict[str, str]] = None
    reason: Optional[str] = None


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


class AnalysisTestRequest(BaseModel):
    """逻辑分析调试请求：rule_id + file_id 模式 或 完整 config 模式。"""
    file_id: str
    rule_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


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
    end_extracting_time: Optional[datetime] = None
    end_analyzing_time: Optional[datetime] = None


class BatchDeleteRequest(BaseModel):
    file_ids: List[str]


class BatchDeleteResponse(BaseModel):
    deleted_count: int
    failed_ids: List[str] = []
