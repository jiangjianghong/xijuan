"""提取阶段只读快照测试。

快照存在的唯一理由：AsyncSession 非并发安全，字段并发前必须把
所有只读数据一次性取出，并发段不再碰 session。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from service.extraction_snapshot import (
    ChunkRow,
    FileExtractionSnapshot,
    TableRow,
    load_extraction_snapshot,
)


class _FakeResult:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """按 execute 调用顺序返回预置结果，并记录调用次数。"""

    def __init__(self, results):
        self._results = list(results)
        self.execute_count = 0

    async def execute(self, stmt):
        self.execute_count += 1
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_load_snapshot_maps_all_columns():
    file_content = SimpleNamespace(
        file_content="# 标题\n正文",
        page_mapping=[{"page_num": 1, "start_pos": 0, "end_pos": 8}],
    )
    table_row = SimpleNamespace(
        table_index=0, table_name="资产表", table_content="<table></table>",
        start_pos=1, end_pos=5, page_num=2,
    )
    chunk_row = SimpleNamespace(
        chunk_id="c1", chunk_index=0, chunk_content="分块内容",
        start_pos=0, end_pos=4, page_num=1,
    )
    session = _FakeSession([
        _FakeResult(value=file_content),
        _FakeResult(rows=[table_row]),
        _FakeResult(rows=[chunk_row]),
    ])

    snapshot = await load_extraction_snapshot("f1", session)

    assert snapshot.file_id == "f1"
    assert snapshot.content == "# 标题\n正文"
    assert snapshot.page_mapping == [{"page_num": 1, "start_pos": 0, "end_pos": 8}]
    assert snapshot.tables == (TableRow(0, "资产表", "<table></table>", 1, 5, 2),)
    assert snapshot.chunks == (ChunkRow("c1", 0, "分块内容", 0, 4, 1),)
    # 三次查询：file_content / file_table / file_chunk
    assert session.execute_count == 3


@pytest.mark.asyncio
async def test_load_snapshot_without_file_content():
    """未解析的文件也能拿到快照，content 为空串而非抛异常。"""
    session = _FakeSession([_FakeResult(value=None), _FakeResult(), _FakeResult()])

    snapshot = await load_extraction_snapshot("f2", session)

    assert snapshot.content == ""
    assert snapshot.page_mapping == []
    assert snapshot.tables == ()
    assert snapshot.chunks == ()
    assert snapshot.page_contents == {}


@pytest.mark.asyncio
async def test_snapshot_page_contents_prebuilt():
    """page_contents 在加载时预建，供 ref 包含度排序复用，不在并发段重算。"""
    file_content = SimpleNamespace(
        file_content="第一页内容\n\n第二页内容",
        page_mapping=[
            {"page_num": 1, "start_pos": 0, "end_pos": 5},
            {"page_num": 2, "start_pos": 7, "end_pos": 12},
        ],
    )
    session = _FakeSession([_FakeResult(value=file_content), _FakeResult(), _FakeResult()])

    snapshot = await load_extraction_snapshot("f3", session)

    assert set(snapshot.page_contents.keys()) == {1, 2}


def test_snapshot_is_immutable():
    """frozen dataclass：并发段拿到的快照不可能被某个字段改坏。"""
    snapshot = FileExtractionSnapshot(
        file_id="f", type_id="default", content="", page_mapping=[],
        page_contents={}, tables=(), chunks=(),
    )
    with pytest.raises(Exception):
        snapshot.content = "改写"
