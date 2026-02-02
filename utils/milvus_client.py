"""Milvus 连接与操作封装。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from utils.config import MilvusConfig, get_config


class MilvusClient:
    """Milvus 向量数据库客户端封装。"""

    def __init__(self, config: Optional[MilvusConfig] = None) -> None:
        self.config = config or get_config().milvus
        self._collection: Optional[Collection] = None

    def connect(self) -> None:
        """建立 Milvus 连接。"""
        connections.connect(
            alias="default",
            host=self.config.host,
            port=self.config.port,
        )

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
            return self._collection

        fields = [
            FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="total_chunks", dtype=DataType.INT64),
            FieldSchema(name="chunk_content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
        ]
        schema = CollectionSchema(fields=fields, description="file_chunks")
        self._collection = Collection(name=name, schema=schema)
        # TODO: 创建索引
        return self._collection

    def insert(self, data: List[Dict[str, Any]]) -> None:
        """批量插入数据。

        Args:
            data: 待插入记录列表，每条记录包含 chunk_id, file_id, chunk_index,
                  total_chunks, chunk_content, embedding。
        """
        # TODO: 实现批量插入
        pass

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
        # TODO: 实现向量检索
        return []

    def delete_by_file_id(self, file_id: str) -> None:
        """删除指定 file_id 的所有记录。

        Args:
            file_id: 文件 ID。
        """
        # TODO: 实现按 file_id 删除
        pass
