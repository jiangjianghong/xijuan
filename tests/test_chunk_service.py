"""chunk_service 纯函数测试。"""

from __future__ import annotations

from service.chunk_service import split_text, split_text_with_positions


def test_split_text_force_split_tail_progress():
    """强制字符分割在尾段应正确结束，不应死循环。"""
    text = "a" * 520

    chunks = split_text(
        text=text,
        chunk_size=512,
        chunk_overlap=50,
        separators=["\n\n"],  # 不命中分隔符，走强制字符分割分支
    )

    assert len(chunks) == 2
    assert len(chunks[0]) == 512
    assert len(chunks[1]) == 58


def test_split_text_with_positions_overlap_ge_chunk_size_is_safe():
    """当 overlap >= chunk_size 时应自动收敛，且位置单调前进。"""
    text = "abcdefghij"

    chunks = split_text_with_positions(
        text=text,
        chunk_size=4,
        chunk_overlap=4,
        separators=["\n\n"],  # 不命中分隔符，走强制字符分割分支
        base_offset=0,
    )

    assert chunks
    assert chunks[-1][2] == len(text)
    assert len(chunks) <= len(text)

    starts = [start for _, start, _ in chunks]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
