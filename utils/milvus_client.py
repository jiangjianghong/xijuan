"""Milvus 连接与操作封装。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from utils.config import MilvusConfig, get_config


# insert 的列顺序。schema 字段顺序、INSERT_COLUMNS、insert_data 三者必须一致，
# 错位不会报错、只会让向量与文本静默对不上。
INSERT_COLUMNS = [
    "chunk_id",
    "file_id",
    "parent_chunk_id",
    "chunk_index",
    "total_chunks",
    "chunk_content",
    "start_pos",
    "end_pos",
    "page_num",
    "embedding",
]

# 检索结果回填的标量字段（不含 embedding）。
OUTPUT_FIELDS = [c for c in INSERT_COLUMNS if c != "embedding"]


def build_collection_schema(dim: int) -> CollectionSchema:
    """构造子块 collection 的 schema。

    chunk_id 是**子块** id，parent_chunk_id 指回 file_chunk 表里的父块——
    检索单元（子块）与返回单元（父块）解耦，命中子块后取父块文本喂 LLM。
    """
    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="parent_chunk_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="total_chunks", dtype=DataType.INT64),
        FieldSchema(name="chunk_content", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="start_pos", dtype=DataType.INT64),
        FieldSchema(name="end_pos", dtype=DataType.INT64),
        FieldSchema(name="page_num", dtype=DataType.VARCHAR, max_length=20),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    return CollectionSchema(fields=fields, description="file_subchunks")


class MilvusClient:
    """Milvus 向量数据库客户端封装。"""

    def __init__(self, config: Optional[MilvusConfig] = None) -> None:
        self.config = config or get_config().milvus
        self._collection: Optional[Collection] = None

    def connect(self) -> None:
        """建立 Milvus 连接。"""
        connect_kwargs = {
            "alias": "default",
            "host": self.config.host,
            "port": self.config.port,
        }
        if self.config.user:
            connect_kwargs["user"] = self.config.user
        if self.config.password:
            connect_kwargs["password"] = self.config.password
        connections.connect(**connect_kwargs)

    def ensure_collection(self, embedding_dim: Optional[int] = None) -> Collection:
        """确保 Collection 存在，不存在则创建。

        Args:
            embedding_dim: 向量维度，默认从 embedding 配置读取。

        Returns:
            Milvus Collection 对象。
        """
        dim = embedding_dim or get_config().embedding.embedding_dim
        name = self.config.collection_name

        if utility.has_collection(name):
            self._collection = Collection(name)
            self._collection.load()
            return self._collection

        schema = build_collection_schema(dim)
        self._collection = Collection(name=name, schema=schema)

        index_params = {
            "index_type": self.config.index_type,
            "metric_type": self.config.metric_type,
            "params": {"nlist": self.config.nlist},
        }
        self._collection.create_index(field_name="embedding", index_params=index_params)
        logger.info("Milvus collection '{}' 创建完成，索引已建立", name)
        self._collection.load()
        return self._collection

    def insert(self, data: List[Dict[str, Any]]) -> None:
        """批量插入数据。

        Args:
            data: 待插入记录列表，每条记录包含 chunk_id, file_id, chunk_index,
                  total_chunks, chunk_content, embedding。
        """
        if not data:
            return

        collection = self._collection
        if collection is None:
            collection = self.ensure_collection()

        # 行转列，列顺序严格按 INSERT_COLUMNS
        columns: Dict[str, List[Any]] = {name: [] for name in INSERT_COLUMNS}
        for row in data:
            for key in INSERT_COLUMNS:
                columns[key].append(row[key])

        insert_data = [columns[name] for name in INSERT_COLUMNS]

        collection.insert(insert_data)
        collection.flush()
        logger.info("Milvus 插入 {} 条记录", len(data))

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        file_id: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """向量检索。

        Args:
            query_vector: 查询向量。
            top_k: 返回条数。
            file_id: 限定文件 ID。
            score_threshold: 分数阈值过滤。

        Returns:
            检索结果列表。
        """
        collection = self._collection
        if collection is None:
            collection = self.ensure_collection()

        search_params = {
            "metric_type": self.config.metric_type,
            "params": {"nprobe": 16},
        }

        expr = None
        if file_id:
            expr = f'file_id == "{file_id}"'

        results = collection.search(
            data=[query_vector],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=OUTPUT_FIELDS,
        )

        hits = []
        for result in results:
            for hit in result:
                score = hit.distance
                # COSINE 相似度：越大越相似，低于阈值则丢弃
                if score_threshold is not None and score < score_threshold:
                    continue
                hits.append({name: hit.entity.get(name) for name in OUTPUT_FIELDS} | {"score": score})

        return hits

    def delete_by_file_id(self, file_id: str) -> None:
        """删除指定 file_id 的所有记录。

        Args:
            file_id: 文件 ID。
        """
        collection = self._collection
        if collection is None:
            collection = self.ensure_collection()

        expr = f'file_id == "{file_id}"'
        collection.delete(expr)
        logger.info("Milvus 删除 file_id={} 的所有记录", file_id)


_singleton: Optional["MilvusClient"] = None


def get_milvus_client() -> "MilvusClient":
    """返回进程级 Milvus 客户端单例。

    首次调用会 connect + ensure_collection,后续调用直接返回缓存实例。
    创建过程中抛错则不缓存,下次调用会重试。
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    client = MilvusClient()
    client.connect()
    client.ensure_collection()
    _singleton = client
    return _singleton
