"""配置加载模块：从 YAML 加载配置并通过 Pydantic Settings 管理。"""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
_DEFAULT_CONFIG_PATH = _CONFIG_DIR / "config.yaml"


# ── 子配置模型 ──────────────────────────────────────────────

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class MineruConfig(BaseModel):
    base_url: str = "http://localhost:8888"
    backend: str = "vllm-async-engine"
    queue_width: int = Field(1, ge=1)
    parse_timeout: int = Field(300, ge=1)
    max_file_size: int = Field(104857600, ge=1)


class ChunkingConfig(BaseModel):
    chunk_size: int = Field(512, ge=1)
    chunk_overlap: int = Field(50, ge=0)
    max_chunk_size: int = Field(2048, ge=1)
    separators: List[str] = ["\n\n", "\n", "。", " "]

    @model_validator(mode="after")
    def validate_sizes(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if self.chunk_size > self.max_chunk_size:
            raise ValueError("chunk_size 不能大于 max_chunk_size")
        return self


class EmbeddingConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    model_name: str = "bge-large-zh"
    api_key: str = ""
    embedding_dim: int = Field(1024, ge=1)
    batch_size: int = Field(32, ge=1)
    timeout: int = Field(60, ge=1)
    retry_count: int = Field(3, ge=1)


class MilvusConfig(BaseModel):
    host: str = "localhost"
    port: int = 19530
    user: str = ""
    password: str = ""
    collection_name: str = "file_chunks"
    index_type: str = "IVF_FLAT"
    metric_type: str = "COSINE"
    nlist: int = 1024
    search_topk: int = 10


class MySQLConfig(BaseModel):
    host: str = "localhost"
    port: int = 3306
    database: str = "file_parser"
    username: str = "root"
    password: str = ""
    pool_size: int = 50
    max_overflow: int = 10
    pool_timeout: int = 10


class ExtractionConfig(BaseModel):
    llm_base_url: str = "http://localhost:8000/v1"
    llm_model: str = "qwen-7b"
    llm_api_key: str = ""
    llm_timeout: int = Field(60, ge=1)
    llm_retry_count: int = Field(3, ge=1)
    max_context_length: int = Field(4096, ge=1)
    llm_extra_body: Dict[str, Any] = {}


class TableNameValidationConfig(BaseModel):
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout: int | None = Field(None, ge=1)
    llm_retry_count: int | None = Field(None, ge=1)
    max_context_length: int | None = Field(None, ge=1)
    max_context_lines: int | None = Field(None, ge=1)
    max_concurrency: int | None = Field(None, ge=1)
    llm_extra_body: Dict[str, Any] | None = None


class AnalysisConfig(BaseModel):
    calc_precision: int = Field(2, ge=0)
    judge_timeout: int = Field(30, ge=1)
    max_concurrency: int = Field(10, ge=1)  # 独立分析接口 item 级最大并发数


class VLModelConfig(BaseModel):
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen-vl-max"
    temperature: float = Field(0.1, ge=0)
    max_tokens: int = Field(4096, ge=1)
    timeout: int = Field(180, ge=1)
    extra_body: Dict[str, Any] = {}
    global_max_concurrency: int = Field(8, ge=1)
    default_max_pixels: int = Field(4_000_000, ge=1)
    pdf_storage_dir: str = "uploads"


class WebSearchConfig(BaseModel):
    base_url: str = "https://api.bochaai.com/v1/web-search"
    api_key: str = ""
    count: int = Field(5, ge=1)
    summary: bool = True
    freshness: str = "noLimit"
    timeout: int = Field(10, ge=1)
    retry_count: int = Field(2, ge=1)
    max_result_length: int = Field(4000, ge=1)


class StorageConfig(BaseModel):
    max_total_bytes: int = Field(0, ge=0)            # uploads 下 PDF 总大小上限(字节)，0=不限
    max_retention_minutes: int = Field(0, ge=0)      # PDF 最久保存时间(分钟)，0=不限
    cleanup_interval_minutes: int = Field(10, ge=1)  # 后台清理扫描周期(分钟)


class SettingsSecurityConfig(BaseModel):
    password: str = ""
    session_minutes: int = Field(30, ge=1)
    secure_cookie: bool = False


# ── 顶层配置 ────────────────────────────────────────────────

class AppConfig(BaseSettings):
    """应用配置，可通过环境变量 APP_CONFIG_PATH 指定配置文件路径。"""

    model_config = SettingsConfigDict(extra="ignore")

    server: ServerConfig = ServerConfig()
    mineru: MineruConfig = MineruConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    milvus: MilvusConfig = MilvusConfig()
    mysql: MySQLConfig = MySQLConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    table_name_validation: TableNameValidationConfig = TableNameValidationConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    vl_model: VLModelConfig = VLModelConfig()
    web_search: WebSearchConfig = WebSearchConfig()
    storage: StorageConfig = StorageConfig()
    settings: SettingsSecurityConfig = SettingsSecurityConfig()


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_config_path() -> Path:
    """返回当前配置文件路径。"""
    return Path(os.getenv("APP_CONFIG_PATH", str(_DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> AppConfig:
    """从磁盘加载并校验完整配置。"""
    config_path = path or get_config_path()
    if config_path.exists():
        data = _load_yaml(config_path)
        return AppConfig(**data)
    return AppConfig()


_config_lock = RLock()
_current_config: AppConfig | None = None


def get_config() -> AppConfig:
    """获取当前进程的不可变配置快照。"""
    global _current_config
    with _config_lock:
        if _current_config is None:
            _current_config = load_config()
        return _current_config


def replace_config(config: AppConfig) -> None:
    """原子替换进程内配置；调用方必须先完成完整校验。"""
    global _current_config
    with _config_lock:
        _current_config = config


def reset_config() -> None:
    """清空进程内配置，下次读取时重新从磁盘加载。"""
    global _current_config
    with _config_lock:
        _current_config = None
