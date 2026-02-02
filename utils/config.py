"""配置加载模块：从 YAML 加载配置并通过 Pydantic Settings 管理。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
_DEFAULT_CONFIG_PATH = _CONFIG_DIR / "config.yaml"


# ── 子配置模型 ──────────────────────────────────────────────

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class MineruConfig(BaseModel):
    base_url: str = "http://localhost:8888"
    queue_width: int = 1
    parse_timeout: int = 300
    max_file_size: int = 104857600


class ChunkingConfig(BaseModel):
    chunk_size: int = 512
    chunk_overlap: int = 50
    max_chunk_size: int = 2048
    separators: List[str] = ["\n\n", "\n", "。", " "]


class EmbeddingConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    model_name: str = "bge-large-zh"
    api_key: str = ""
    embedding_dim: int = 1024
    batch_size: int = 32
    timeout: int = 60
    retry_count: int = 3


class MilvusConfig(BaseModel):
    host: str = "localhost"
    port: int = 19530
    collection_name: str = "file_chunks"
    index_type: str = "IVF_FLAT"
    metric_type: str = "L2"
    nlist: int = 1024
    search_topk: int = 10


class MySQLConfig(BaseModel):
    host: str = "localhost"
    port: int = 3306
    database: str = "file_parser"
    username: str = "root"
    password: str = ""
    pool_size: int = 10


class ExtractionConfig(BaseModel):
    llm_base_url: str = "http://localhost:8000/v1"
    llm_model: str = "qwen-7b"
    llm_timeout: int = 60
    llm_retry_count: int = 3
    max_context_length: int = 4096


class AnalysisConfig(BaseModel):
    calc_precision: int = 2
    judge_timeout: int = 30


# ── 顶层配置 ────────────────────────────────────────────────

class AppConfig(BaseSettings):
    """应用配置，可通过环境变量 APP_CONFIG_PATH 指定配置文件路径。"""

    server: ServerConfig = ServerConfig()
    mineru: MineruConfig = MineruConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    milvus: MilvusConfig = MilvusConfig()
    mysql: MySQLConfig = MySQLConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    analysis: AnalysisConfig = AnalysisConfig()


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def get_config() -> AppConfig:
    """获取全局配置单例。"""
    config_path = Path(os.getenv("APP_CONFIG_PATH", str(_DEFAULT_CONFIG_PATH)))
    if config_path.exists():
        data = _load_yaml(config_path)
        return AppConfig(**data)
    return AppConfig()
