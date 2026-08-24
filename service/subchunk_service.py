"""子块切分：向量检索的匹配单元。

父块（512 字，存 file_chunk 表）是喂给 LLM 的返回单元；子块（128 字，只进
Milvus）是向量匹配单元。两者解耦的原因：512 字块压成一个向量时，目标（如
「项目名称：XX污水处理厂」）只占十几个字，信号被另外 500 字稀释，余弦上不去；
但单纯把块切小又会丢上下文，LLM 分不清哪个「名称」才是要的。

子块不落 MySQL：它纯粹是「向量化实现细节」，只活在 embedding → Milvus 这一段，
命中后靠 parent_chunk_id 映射回父块取文本。
"""

from __future__ import annotations

from typing import Dict, List

from service.chunk_service import split_text_with_positions

# 子块目标长度。取父块 512 的 1/4：足够让单个语义单元独占一个向量，
# 又不至于短到只剩孤立词组。
SUBCHUNK_SIZE = 128

# 子块切分边界。父块已按 chunking.separators 粗切过一轮，这里只在句内再细分，
# 故用更细的句读边界。不加 overlap——子块只负责匹配，上下文由父块提供。
SUBCHUNK_SEPARATORS = ["\n", "。", "；", "！", "？", "，", " "]


def _is_table_chunk(chunk_content: str) -> bool:
    """表格块判定：含 <table> 标签。

    表格整体是一个语义单元，切碎后行与表头分离，匹配和阅读都会失真，
    故保持 chunk_service 里「整表作为独立块」的既有语义不变。
    """
    return "<table>" in chunk_content.lower()


def split_into_subchunks(parent_chunks: List[Dict]) -> List[Dict]:
    """把父块切成子块，供向量化使用。

    Args:
        parent_chunks: chunk_service.chunk_content() 产出的父块列表。

    Returns:
        子块列表。每项含 chunk_id（子块 id，形如 {父块id}_s{序号}）、
        parent_chunk_id、file_id、chunk_content（子块文本），以及从父块
        **继承**的 chunk_index / total_chunks / start_pos / end_pos / page_num
        —— 溯源与 bbox 一律按父块口径，下游 _build_text_source_refs 因此无需改动。
        空白父块不产出子块。
    """
    subs: List[Dict] = []

    for parent in parent_chunks:
        text = parent.get("chunk_content") or ""
        if not text.strip():
            continue

        parent_id = parent["chunk_id"]

        if _is_table_chunk(text) or len(text) <= SUBCHUNK_SIZE:
            pieces = [text]
        else:
            pieces = [
                piece
                for piece, _start, _end in split_text_with_positions(
                    text, SUBCHUNK_SIZE, 0, SUBCHUNK_SEPARATORS
                )
                if piece.strip()
            ]

        for index, piece in enumerate(pieces):
            subs.append({
                "chunk_id": f"{parent_id}_s{index}",
                "parent_chunk_id": parent_id,
                "file_id": parent["file_id"],
                "chunk_index": parent.get("chunk_index", 0),
                "total_chunks": parent.get("total_chunks", 0),
                "chunk_content": piece,
                "start_pos": parent.get("start_pos", 0),
                "end_pos": parent.get("end_pos", 0),
                "page_num": parent.get("page_num", ""),
            })

    return subs
