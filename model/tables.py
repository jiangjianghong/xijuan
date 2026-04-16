"""ORM 模型定义：对应 design.md 中的所有关系数据库表。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import JSON, LONGTEXT, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── 1. files 表 ─────────────────────────────────────────────

class File(Base):
    __tablename__ = "files"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    start_parsing_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_parsing_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_tableing_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_tableing_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_chunking_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_chunking_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_embedding_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_embedding_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_extracting_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_analyzing_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress: Mapped[str] = mapped_column(String(32), default="parsing")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ── 2. file_content 表 ──────────────────────────────────────

class FileContent(Base):
    __tablename__ = "file_content"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_content: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    middle_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    page_mapping: Mapped[list | None] = mapped_column(JSON, nullable=True)


# ── 3. file_table 表 ────────────────────────────────────────

class FileTable(Base):
    __tablename__ = "file_table"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    table_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    total_table: Mapped[int] = mapped_column(Integer, default=0)
    table_name: Mapped[str] = mapped_column(String(500), default="")
    table_content: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    start_pos: Mapped[int] = mapped_column(Integer, default=0)  # 原文起始位置
    end_pos: Mapped[int] = mapped_column(Integer, default=0)    # 原文结束位置
    page_num: Mapped[str | None] = mapped_column(String(20), nullable=True, default="")

    __table_args__ = (
        Index("ix_file_table_file_id", "file_id"),
    )


# ── 4. file_chunk 表 ────────────────────────────────────────

class FileChunk(Base):
    __tablename__ = "file_chunk"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    chunk_content: Mapped[str] = mapped_column(Text, nullable=False)
    start_pos: Mapped[int] = mapped_column(Integer, default=0)  # 原文起始位置
    end_pos: Mapped[int] = mapped_column(Integer, default=0)    # 原文结束位置
    page_num: Mapped[str | None] = mapped_column(String(20), nullable=True, default="")

    __table_args__ = (
        Index("ix_file_chunk_file_id", "file_id"),
    )


# ── 5. extraction_field 表 ──────────────────────────────────

class ExtractionField(Base):
    __tablename__ = "extraction_field"

    field_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    field_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(
        Enum("table", "text", name="source_type_enum"), nullable=False
    )
    enabled: Mapped[int] = mapped_column(TINYINT, default=1)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    # 表格类专用
    table_name_pattern: Mapped[str | None] = mapped_column(String(500), nullable=True)
    table_match_type: Mapped[str | None] = mapped_column(
        Enum("exact", "fuzzy", "contains", "llm", name="table_match_type_enum"),
        nullable=True,
    )
    table_match_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    table_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    table_extract_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 文本类专用
    search_type: Mapped[str | None] = mapped_column(
        Enum("context", "section", "rule", "chunk_db", "vector_db", name="search_type_enum"),
        nullable=True,
    )
    search_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    text_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_extract_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── 6. analysis_rule 表 ─────────────────────────────────────

class AnalysisRule(Base):
    __tablename__ = "analysis_rule"

    rule_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(
        Enum("judge", "calc", name="rule_type_enum"), nullable=False
    )
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    depend_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[int] = mapped_column(TINYINT, default=1)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ── 8. extraction_result 表 ─────────────────────────────────

class ExtractionResult(Base):
    __tablename__ = "extraction_result"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    field_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    extracted_value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 参考块列表

    __table_args__ = (
        Index("ix_extraction_result_file_id", "file_id"),
    )


# ── 9. analysis_result 表 ───────────────────────────────────

class AnalysisResult(Base):
    __tablename__ = "analysis_result"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    result_value: Mapped[str] = mapped_column(String(500), default="")
    input_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 依赖字段的参考块

    __table_args__ = (
        Index("ix_analysis_result_file_id", "file_id"),
    )
