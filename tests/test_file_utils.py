"""工具函数测试。"""

from __future__ import annotations

from utils.errors import MAX_MESSAGE_LENGTH, format_exception
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


def test_format_exception_keeps_short_message():
    """短文案原样返回，只加异常类型前缀。"""
    msg = format_exception(ValueError("配置非法"))
    assert msg == "ValueError: 配置非法"


def test_format_exception_falls_back_to_repr_when_empty():
    """str(exc) 为空时用 repr 兜底，避免丢失关键信息。"""
    msg = format_exception(ValueError())
    assert msg.startswith("ValueError: ")
    assert msg != "ValueError: "


def test_format_exception_truncates_overlong_message():
    """超长文案截断到上限内，保证能安全写进 TEXT 列。

    DataError 的文案会带完整 SQL 与参数预览，不截断会撑爆 files.error
    与 extraction_result.reason（均为 MySQL TEXT）。
    """
    msg = format_exception(ValueError("х" * 50000))
    assert len(msg) <= MAX_MESSAGE_LENGTH
    assert msg.endswith("...(已截断)")
    # 类型前缀必须保留，否则截断后无法判断异常种类
    assert msg.startswith("ValueError: ")


def test_format_exception_boundary_not_truncated():
    """恰好等于上限时不截断（边界不多切一刀）。"""
    body = "a" * (MAX_MESSAGE_LENGTH - len("ValueError: "))
    msg = format_exception(ValueError(body))
    assert len(msg) == MAX_MESSAGE_LENGTH
    assert not msg.endswith("...(已截断)")
