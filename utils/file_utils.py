"""文件操作工具函数。"""

import hashlib


def generate_file_id(type_id: str, file_name: str) -> str:
    """根据 type_id + 文件名生成 file_id。

    同一类型下的同名文件视为同一记录，可触发"重传即重试"语义；
    不同类型下的同名文件互不影响。
    """
    raw = f"{type_id}|{file_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def generate_chunk_id(file_id: str, chunk_index: int) -> str:
    """根据 file_id 和 chunk_index 生成 chunk_id。"""
    return hashlib.sha256((file_id + str(chunk_index)).encode("utf-8")).hexdigest()[:32]
