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

# protobuf 每行还有字段标签、长度和列级元数据。这里按行额外预留空间，
# 再配合默认 32 MiB 预算，共同保证实际请求明显低于 64 MiB 服务端上限。
_INSERT_ROW_OVERHEAD_BYTES = 256


def estimate_row_bytes(row: Dict[str, Any]) -> int:
    """保守估算一条 Milvus insert 记录的序列化体积。"""
    size = _INSERT_ROW_OVERHEAD_BYTES
    for name in INSERT_COLUMNS:
        value = row.get(name)
        if name == "embedding":
            size += len(value or []) * 4
        elif isinstance(value, str):
            size += len(value.encode("utf-8"))
        elif value is not None:
            size += 8
    return size


def plan_insert_batches(
    data: List[Dict[str, Any]], max_bytes: int
) -> List[List[Dict[str, Any]]]:
    """按估算报文体积切分 insert 批次，保持原始行顺序。"""
    if not data:
        return []

    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_bytes = 0

    for row in data:
        row_bytes = estimate_row_bytes(row)
        if current and current_bytes + row_bytes > max_bytes:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(row)
        current_bytes += row_bytes

    if current:
        batches.append(current)
    return batches


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


class MilvusSchemaMismatchError(RuntimeError):
    """已存在的 collection 与当前代码的 schema 不一致。

    典型成因：`milvus.collection_name` 指向了父子分块改造前建的旧库（9 字段、
    无 parent_chunk_id）。不校验就复用，错误会一路潜伏到 embedding 末尾的
    insert 才爆成 `expect 9 list, got 10`——既看不出是配置指错了库，
    又白烧了一整轮 embedding 调用。
    """


def _vector_dim(field: Any) -> Optional[int]:
    """取向量字段声明的维度，非向量字段返回 None。"""
    return (getattr(field, "params", None) or {}).get("dim")


def describe_schema_mismatch(existing_fields: List[Any], expected_fields: List[Any]) -> Optional[str]:
    """比对已存在 collection 与期望 schema 的字段，一致返回 None。

    顺序也必须一致：insert 按 INSERT_COLUMNS 行转列，pymilvus 按 schema 字段
    顺序对位，顺序错了**不报错**、只会让向量与文本静默对不上。

    Args:
        existing_fields: 已存在 collection 的 schema.fields。
        expected_fields: build_collection_schema() 的 fields。

    Returns:
        人类可读的差异描述；完全一致时返回 None。
    """
    existing_names = [f.name for f in existing_fields]
    expected_names = [f.name for f in expected_fields]

    if existing_names != expected_names:
        missing = [n for n in expected_names if n not in existing_names]
        extra = [n for n in existing_names if n not in expected_names]
        parts = []
        if missing:
            parts.append(f"缺少字段 {missing}")
        if extra:
            parts.append(f"多出字段 {extra}")
        if not parts:
            parts.append(f"字段顺序不一致（现有 {existing_names}，期望 {expected_names}）")
        return "；".join(parts)

    existing_by_name = {f.name: f for f in existing_fields}
    for expected in expected_fields:
        expected_dim = _vector_dim(expected)
        if expected_dim is None:
            continue
        actual_dim = _vector_dim(existing_by_name[expected.name])
        # actual 读不到时不报警：pymilvus 从服务端还原 schema 的表示可能没带 dim，
        # 误判会让守卫自己变成故障源（向量化 / 检索全线不可用）。真实的维度不符
        # 一律带得出整数，读不到就交给 insert 自己报维度错。
        if actual_dim is None:
            logger.warning("无法读取已存在字段 {} 的向量维度，跳过维度校验", expected.name)
            continue
        if actual_dim != expected_dim:
            return f"字段 {expected.name} 的向量维度不一致（现有 {actual_dim}，期望 {expected_dim}）"

    return None


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
        """确保 Collection 存在，不存在则创建；已存在则校验 schema 后复用。

        Args:
            embedding_dim: 向量维度，默认从 embedding 配置读取。

        Returns:
            Milvus Collection 对象。

        Raises:
            MilvusSchemaMismatchError: 同名 collection 已存在但 schema 与当前
                代码不一致（如指向了改造前的旧库）。
        """
        dim = embedding_dim or get_config().embedding.embedding_dim
        name = self.config.collection_name
        schema = build_collection_schema(dim)

        if utility.has_collection(name):
            collection = Collection(name)
            # 已存在不等于可用：schema 不匹配必须当场炸，别留到 insert
            mismatch = describe_schema_mismatch(collection.schema.fields, schema.fields)
            if mismatch:
                raise MilvusSchemaMismatchError(
                    f"Milvus collection '{name}' 的 schema 与当前代码不一致：{mismatch}。"
                    f"这通常是 milvus.collection_name 指向了父子分块改造前建的旧库"
                    f"（9 字段、无 parent_chunk_id）。处理方式二选一："
                    f"①把 configs/config.yaml 的 milvus.collection_name 换成一个全新的名字，"
                    f"重启后会自动按当前 schema 建库；"
                    f"②先 uv run python scripts/drop_milvus_collection.py 删掉旧库再重启。"
                    f"两种方式下存量文件都需 POST /file/{{file_id}}/retry/embedding "
                    f"重新切分并灌入子块向量。"
                )
            collection.load()
            self._collection = collection
            return collection

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

        batches = plan_insert_batches(data, self.config.max_insert_bytes)
        logger.info(
            "Milvus 准备分 {} 批插入 {} 条记录，单批预算 {} MiB",
            len(batches),
            len(data),
            self.config.max_insert_bytes // (1024 * 1024),
        )
        for batch_index, batch in enumerate(batches, start=1):
            # 行转列，列顺序严格按 INSERT_COLUMNS
            columns: Dict[str, List[Any]] = {name: [] for name in INSERT_COLUMNS}
            for row in batch:
                for key in INSERT_COLUMNS:
                    columns[key].append(row[key])
            collection.insert([columns[name] for name in INSERT_COLUMNS])
            logger.info(
                "Milvus 插入批次 {}/{} 完成，本批 {} 条",
                batch_index,
                len(batches),
                len(batch),
            )

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
