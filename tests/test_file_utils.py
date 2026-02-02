"""工具函数测试。"""

from __future__ import annotations

from utils.file_utils import generate_file_id, generate_chunk_id


def test_generate_file_id():
    """测试文件 ID 生成。"""
    file_id = generate_file_id("test.pdf")
    assert len(file_id) == 32
    # 同名文件应生成相同 ID
    assert generate_file_id("test.pdf") == file_id


def test_generate_chunk_id():
    """测试分块 ID 生成。"""
    chunk_id = generate_chunk_id("file123", 0)
    assert len(chunk_id) == 32
    # 不同 index 应生成不同 ID
    assert generate_chunk_id("file123", 1) != chunk_id
