"""子块切分测试：全部为纯函数，不碰 Milvus / DB。"""

from service.subchunk_service import SUBCHUNK_SIZE, split_into_subchunks


def _parent(chunk_id="p1", content="正文", **kw):
    """构造一个父块，字段与 chunk_service.chunk_content() 产出一致。"""
    base = {
        "file_id": "f1",
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "total_chunks": 1,
        "chunk_content": content,
        "start_pos": 100,
        "end_pos": 200,
        "page_num": "3",
    }
    base.update(kw)
    return base


def test_short_parent_becomes_single_subchunk():
    """父块本身不超过子块长度时原样保留，不做无意义切分。"""
    subs = split_into_subchunks([_parent(content="本项目名称为XX污水处理厂")])

    assert len(subs) == 1
    assert subs[0]["chunk_content"] == "本项目名称为XX污水处理厂"
    assert subs[0]["chunk_id"] == "p1_s0"
    assert subs[0]["parent_chunk_id"] == "p1"


def test_long_parent_splits_into_multiple_subchunks():
    """超长父块按句读边界切成多个子块，每个都不超过 SUBCHUNK_SIZE。"""
    content = "。".join(f"第{i}句话内容填充到足够长度用于测试切分行为" for i in range(30))
    subs = split_into_subchunks([_parent(content=content)])

    assert len(subs) > 1
    assert all(len(s["chunk_content"]) <= SUBCHUNK_SIZE for s in subs)
    assert [s["chunk_id"] for s in subs] == [f"p1_s{i}" for i in range(len(subs))]
    assert all(s["parent_chunk_id"] == "p1" for s in subs)


def test_table_chunk_is_never_split():
    """表格块整体是一个语义单元，切碎后表头与行分离，故不切。"""
    table = "费用表\n<table>" + "<tr><td>行内容填充</td></tr>" * 60 + "</table>"
    assert len(table) > SUBCHUNK_SIZE

    subs = split_into_subchunks([_parent(content=table)])

    assert len(subs) == 1
    assert subs[0]["chunk_content"] == table


def test_subchunk_inherits_parent_position_and_page():
    """溯源与 bbox 一律按父块口径，子块继承父块的位置与页码。

    下游 _build_text_source_refs 因此无需改动。
    """
    content = "。".join(f"第{i}句话内容填充到足够长度用于测试" for i in range(30))
    subs = split_into_subchunks([
        _parent(content=content, start_pos=500, end_pos=1012, page_num="7", chunk_index=4)
    ])

    assert len(subs) > 1
    for s in subs:
        assert s["start_pos"] == 500
        assert s["end_pos"] == 1012
        assert s["page_num"] == "7"
        assert s["chunk_index"] == 4
        assert s["file_id"] == "f1"


def test_blank_parent_is_dropped():
    """空白父块不产生子块，避免空文本进 embedding 接口。"""
    assert split_into_subchunks([_parent(content="   \n  ")]) == []
    assert split_into_subchunks([_parent(content="")]) == []


def test_multiple_parents_keep_independent_id_sequences():
    """每个父块的子块序号从 0 重新开始，靠 parent_chunk_id 区分归属。"""
    subs = split_into_subchunks([
        _parent(chunk_id="pa", content="甲方内容"),
        _parent(chunk_id="pb", content="乙方内容"),
    ])

    assert [s["chunk_id"] for s in subs] == ["pa_s0", "pb_s0"]
    assert [s["parent_chunk_id"] for s in subs] == ["pa", "pb"]


def test_subchunk_id_fits_milvus_varchar_64():
    """chunk_id 列是 VARCHAR(64)；父块 id 为 32 位 sha256 前缀，加后缀不能超限。"""
    parent_id = "a" * 32
    content = "。".join(f"第{i}句话内容填充到足够长度用于测试" for i in range(40))

    subs = split_into_subchunks([_parent(chunk_id=parent_id, content=content)])

    assert len(subs) > 1
    assert all(len(s["chunk_id"]) <= 64 for s in subs)
