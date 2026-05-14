"""文件操作工具函数。"""

import hashlib
import secrets
import time


def generate_file_id(type_id: str, file_name: str) -> str:
    """根据 type_id + 文件名 + 当前纳秒时间戳 + 随机盐生成 file_id。

    每次调用都会得到不同的 id —— 同名文件重传也会生成新记录，
    强制重新走完整管线，不再做去重 / 重传重试。
    Windows 上 time.time_ns() 分辨率有限，需额外随机盐避免同毫秒冲突。
    """
    raw = f"{type_id}|{file_name}|{time.time_ns()}|{secrets.token_hex(8)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def generate_chunk_id(file_id: str, chunk_index: int) -> str:
    """根据 file_id 和 chunk_index 生成 chunk_id。"""
    return hashlib.sha256((file_id + str(chunk_index)).encode("utf-8")).hexdigest()[:32]
