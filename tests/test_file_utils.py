"""工具函数测试。"""

from __future__ import annotations

from utils.file_utils import generate_file_id, generate_chunk_id


def test_generate_file_id():
    """测试文件 ID 生成。

    每次调用都带纳秒时间戳，同名同类型也会得到不同 id，
    用于强制每次重传走全量解析、避免命中已解析的旧记录。
    """
    file_id = generate_file_id("default", "test.pdf")
    assert len(file_id) == 32
    # 同名文件每次生成应得到不同 ID
    assert generate_file_id("default", "test.pdf") != file_id


def test_generate_chunk_id():
    """测试分块 ID 生成。"""
    chunk_id = generate_chunk_id("file123", 0)
    assert len(chunk_id) == 32
    # 不同 index 应生成不同 ID
    assert generate_chunk_id("file123", 1) != chunk_id
