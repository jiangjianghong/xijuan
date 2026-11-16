"""基于分块快照的 BM25 文本检索。"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Sequence, Tuple


_TOKEN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[a-z0-9]+(?:[._/-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _tokenize(text: str) -> List[str]:
    """英文/数字按词切分，连续汉字按双字词切分。"""
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer((text or "").lower()):
        term = match.group(0)
        if _CJK_RE.fullmatch(term):
            if len(term) == 1:
                tokens.append(term)
            else:
                tokens.extend(term[i:i + 2] for i in range(len(term) - 1))
        else:
            tokens.append(term)
    return tokens


def _query_texts(config: Dict[str, Any]) -> List[str]:
    raw = config.get("query_text", "")
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    queries: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        query = value.strip()
        if query and query not in seen:
            seen.add(query)
            queries.append(query)
    return queries


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bm25_parameters(config: Dict[str, Any]) -> Tuple[float, float]:
    try:
        k1 = float(config.get("k1", 1.5))
        b = float(config.get("b", 0.75))
    except (TypeError, ValueError) as exc:
        raise ValueError("BM25 的 k1、b 必须是数字") from exc
    if not math.isfinite(k1) or k1 <= 0:
        raise ValueError("BM25 的 k1 必须大于 0")
    if not math.isfinite(b) or not 0 <= b <= 1:
        raise ValueError("BM25 的 b 必须在 0 到 1 之间")
    return k1, b


def search_bm25(
    file_id: str,
    config: Dict[str, Any],
    chunks: Sequence[Any],
    *,
    candidate_only: bool = False,
) -> List[Dict[str, Any]]:
    """按 BM25 对当前文件的文本分块排序，结果形态兼容 chunk_db。"""
    del file_id  # 保持与其他检索器一致的调用签名。
    queries = _query_texts(config)
    if not queries:
        return []

    k1, b = _bm25_parameters(config)
    top_k = _positive_int(config.get("top_k", config.get("max_results", 10)), 10)

    documents: List[Tuple[Any, List[str]]] = []
    postings: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for chunk in chunks:
        content = getattr(chunk, "chunk_content", "") or ""
        terms = _tokenize(content)
        if not terms:
            continue
        doc_index = len(documents)
        documents.append((chunk, terms))
        for term, frequency in Counter(terms).items():
            postings[term].append((doc_index, frequency))

    if not documents:
        return []

    doc_count = len(documents)
    average_length = sum(len(terms) for _, terms in documents) / doc_count
    if average_length <= 0:
        return []

    results: List[Dict[str, Any]] = []
    for query in queries:
        query_terms = dict.fromkeys(_tokenize(query))
        if not query_terms:
            continue

        scores = [0.0] * doc_count
        for term in query_terms:
            matching = postings.get(term)
            if not matching:
                continue
            document_frequency = len(matching)
            inverse_frequency = math.log(
                1 + (doc_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            for doc_index, frequency in matching:
                document_length = len(documents[doc_index][1])
                denominator = frequency + k1 * (
                    1 - b + b * document_length / average_length
                )
                scores[doc_index] += inverse_frequency * frequency * (k1 + 1) / denominator

        ranked = sorted(
            ((i, score) for i, score in enumerate(scores) if score > 0),
            key=lambda item: (-item[1], getattr(documents[item[0]][0], "chunk_index", 0)),
        )
        if not candidate_only:
            ranked = ranked[:top_k]
        for doc_index, score in ranked:
            chunk = documents[doc_index][0]
            results.append({
                "keyword": query,
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "chunk_content": chunk.chunk_content,
                "start_pos": chunk.start_pos,
                "end_pos": chunk.end_pos,
                "page_num": chunk.page_num,
                "score": score,
            })
    return results
