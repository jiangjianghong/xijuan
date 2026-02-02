"""Pydantic v2 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── 通用响应包装 ────────────────────────────────────────────

class ResponseWrapper(BaseModel):
    code: int = 200
    message: str = "success"
    data: Any = None


# ── 文件相关 ────────────────────────────────────────────────

class FileStatusResponse(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    progress: str
    error: Optional[str] = None
    create_time: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FileTableItem(BaseModel):
    file_id: str
    table_index: int
    total_table: int
    table_name: str
    table_content: str


class FileChunkItem(BaseModel):
    file_id: str
    chunk_id: str
    chunk_index: int
    total_chunks: int
    chunk_content: str


# ── 字段提取配置 ────────────────────────────────────────────

class SourceTypeEnum(str, Enum):
    table = "table"
    text = "text"


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


class ExtractionFieldCreate(BaseModel):
    field_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$", max_length=100)
    field_name: str = Field(..., max_length=200)
    source_type: SourceTypeEnum
    enabled: int = 1
    priority: int = 0
    # 表格类
    table_name_pattern: Optional[str] = None
    table_match_type: Optional[TableMatchTypeEnum] = None
    table_extract_prompt: Optional[str] = None
    # 文本类
    search_type: Optional[SearchTypeEnum] = None
    search_config: Optional[Dict[str, Any]] = None
    text_extract_prompt: Optional[str] = None


class ExtractionFieldResponse(ExtractionFieldCreate):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── 逻辑分析配置 ────────────────────────────────────────────

class RuleTypeEnum(str, Enum):
    judge = "judge"
    calc = "calc"


class AnalysisRuleCreate(BaseModel):
    rule_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$", max_length=100)
    rule_name: str = Field(..., max_length=200)
    rule_type: RuleTypeEnum
    expression: str
    depend_fields: Optional[List[str]] = None
    enabled: int = 1
    priority: int = 0


class AnalysisRuleResponse(AnalysisRuleCreate):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── 提取结果 ────────────────────────────────────────────────

class ExtractionResultItem(BaseModel):
    file_id: str
    field_id: str
    extracted_value: str


class AnalysisResultItem(BaseModel):
    file_id: str
    rule_id: str
    result_value: str
    input_values: Optional[Dict[str, str]] = None


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


class AnalysisTestRequest(BaseModel):
    """逻辑分析调试请求：rule_id + file_id 模式 或 完整 config 模式。"""
    file_id: str
    rule_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class AnalysisTestResponse(BaseModel):
    input_values: Dict[str, str] = {}
    expression_resolved: str = ""
    result_value: str = ""


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
