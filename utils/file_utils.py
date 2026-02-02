"""文件操作工具函数。"""

import hashlib


def generate_file_id(file_name: str) -> str:
    """仅根据文件名生成 file_id，同名文件视为重复。"""
    return hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:32]


def generate_chunk_id(file_id: str, chunk_index: int) -> str:
    """根据 file_id 和 chunk_index 生成 chunk_id。"""
    return hashlib.sha256((file_id + str(chunk_index)).encode("utf-8")).hexdigest()[:32]
