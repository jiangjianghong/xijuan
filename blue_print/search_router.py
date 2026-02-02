"""向量检索路由：/search"""

from __future__ import annotations

from fastapi import APIRouter

from model.schemas import ResponseWrapper, SearchRequest
from service import search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=ResponseWrapper)
async def search(req: SearchRequest):
    """向量检索。"""
    results = await search_service.search(
        query=req.query,
        top_k=req.top_k,
        file_id=req.file_id,
        score_threshold=req.score_threshold,
    )
    return ResponseWrapper(data=results)
