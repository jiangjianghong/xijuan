"""配置加载模块：从 YAML 加载配置并通过 Pydantic Settings 管理。"""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping

import yaml
from loguru import logger
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
    model: str = "bge-large-zh"
    api_key: str = ""
    embedding_dim: int = Field(1024, ge=1)
    batch_size: int = Field(32, ge=1)
    timeout: int = Field(60, ge=1)
    retry_count: int = Field(3, ge=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "model" not in data and "model_name" in data:
            data["model"] = data["model_name"]
        return data

    @property
    def model_name(self) -> str:
        """兼容旧业务代码和旧配置消费者。"""
        return self.model


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
    base_url: str = "http://localhost:8000/v1"
    model: str = "qwen-7b"
    api_key: str = ""
    timeout: int = Field(60, ge=1)
    retry_count: int = Field(3, ge=1)
    max_context_length: int = Field(4096, ge=1)
    extra_body: Dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        aliases = {
            "llm_base_url": "base_url",
            "llm_model": "model",
            "llm_api_key": "api_key",
            "llm_timeout": "timeout",
            "llm_retry_count": "retry_count",
            "llm_extra_body": "extra_body",
        }
        for old, new in aliases.items():
            if new not in data and old in data:
                data[new] = data[old]
        return data

    @property
    def llm_base_url(self) -> str:
        return self.base_url

    @property
    def llm_model(self) -> str:
        return self.model

    @property
    def llm_api_key(self) -> str:
        return self.api_key

    @property
    def llm_timeout(self) -> int:
        return self.timeout

    @property
    def llm_retry_count(self) -> int:
        return self.retry_count

    @property
    def llm_extra_body(self) -> Dict[str, Any]:
        return self.extra_body


class TableNameValidationConfig(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout: int | None = Field(None, ge=1)
    retry_count: int | None = Field(None, ge=1)
    max_context_length: int | None = Field(None, ge=1)
    max_context_lines: int | None = Field(None, ge=1)
    max_concurrency: int | None = Field(None, ge=1)
    extra_body: Dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        aliases = {
            "llm_base_url": "base_url",
            "llm_model": "model",
            "llm_api_key": "api_key",
            "llm_timeout": "timeout",
            "llm_retry_count": "retry_count",
            "llm_extra_body": "extra_body",
        }
        for old, new in aliases.items():
            if new not in data and old in data:
                data[new] = data[old]
        return data

    @property
    def llm_base_url(self) -> str | None:
        return self.base_url

    @property
    def llm_model(self) -> str | None:
        return self.model

    @property
    def llm_api_key(self) -> str | None:
        return self.api_key

    @property
    def llm_timeout(self) -> int | None:
        return self.timeout

    @property
    def llm_retry_count(self) -> int | None:
        return self.retry_count

    @property
    def llm_extra_body(self) -> Dict[str, Any] | None:
        return self.extra_body


class AnalysisConfig(BaseModel):
    calc_precision: int = Field(2, ge=0)
    judge_timeout: int = Field(30, ge=1)


class ConcurrencyConfig(BaseModel):
    global_llm: int = Field(16, ge=1)
    global_embedding: int = Field(8, ge=1)
    global_vl: int = Field(8, ge=1)
    global_table_validation: int = Field(10, ge=1)
    global_extraction: int = Field(8, ge=1)
    global_analysis: int = Field(8, ge=1)
    task_table_validation: int = Field(4, ge=1)
    task_extraction: int = Field(4, ge=1)
    task_file_analysis: int = Field(4, ge=1)
    task_embedding: int = Field(4, ge=1)
    independent_analysis: int = Field(4, ge=1)
    global_pipeline: int = Field(4, ge=1)

    @model_validator(mode="after")
    def _warn_ineffective_task_limits(self) -> "ConcurrencyConfig":
        """单文件上限 >= 对应全局上限时这层限流不生效，给出告警。

        两者相等时单个文件就能占满全局池，文件间公平性消失——一个几百张表
        的文件会把后到的小文件连续堵在队尾。这三个池不在运行台展示，光看
        界面发现不了，故在配置层点出来。仅告警，不阻断启动。
        """
        pairs = (
            ("task_table_validation", "global_table_validation"),
            ("task_extraction", "global_extraction"),
            ("task_file_analysis", "global_analysis"),
            ("task_embedding", "global_embedding"),
        )
        for task_key, global_key in pairs:
            task_limit = getattr(self, task_key)
            global_limit = getattr(self, global_key)
            if task_limit >= global_limit:
                logger.warning(
                    "并发配置 {}={} >= {}={}，该单文件限流不会生效："
                    "单个文件即可占满全局池，多文件并行时后到的文件会被堵在队尾。"
                    "建议将 {} 调小到全局值以下。",
                    task_key,
                    task_limit,
                    global_key,
                    global_limit,
                    task_key,
                )
        return self


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
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    vl_model: VLModelConfig = VLModelConfig()
    web_search: WebSearchConfig = WebSearchConfig()
    storage: StorageConfig = StorageConfig()
    settings: SettingsSecurityConfig = SettingsSecurityConfig()

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_concurrency(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        limits = dict(data.get("concurrency") or {})
        table = data.get("table_name_validation") or {}
        vl = data.get("vl_model") or {}
        legacy_map = {
            "task_table_validation": table.get("max_concurrency"),
            "global_vl": vl.get("global_max_concurrency"),
        }
        for key, old_value in legacy_map.items():
            if key not in limits and old_value is not None:
                limits[key] = old_value
        if limits:
            data["concurrency"] = limits
        return data


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
