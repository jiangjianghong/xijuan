"""API 手册只读接口。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from model.schemas import ResponseWrapper


router = APIRouter(prefix="/api-reference", tags=["api-reference"], include_in_schema=False)

_DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "API_FULL_REFERENCE.md"


@router.get("", response_model=ResponseWrapper)
async def get_api_reference() -> ResponseWrapper:
    """返回本仓库维护的全量 API Markdown 手册。"""
    if not _DOC_PATH.exists():
        raise HTTPException(status_code=404, detail="API 手册不存在")

    stat = _DOC_PATH.stat()
    content = _DOC_PATH.read_text(encoding="utf-8")
    return ResponseWrapper(
        data={
            "file_name": _DOC_PATH.name,
            "content": content,
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    )
