"""文档类型运行配置读取。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import DocType, File


@dataclass(frozen=True)
class FileTypeRuntimeConfig:
    """文件所属类型的运行时配置；缺失类型按默认值兜底。"""

    type_id: str = "default"
    max_parse_pages: int | None = None
    enable_embedding: bool = True


async def get_file_type_runtime_config(
    file_id: str, session: AsyncSession
) -> FileTypeRuntimeConfig:
    """根据 file_id 读取文档类型运行配置。"""
    stmt = (
        select(File.type_id, DocType.max_parse_pages, DocType.enable_embedding)
        .outerjoin(DocType, File.type_id == DocType.type_id)
        .where(File.file_id == file_id)
    )
    row = (await session.execute(stmt)).first()
    if not row:
        return FileTypeRuntimeConfig()

    type_id = row[0] or "default"
    max_parse_pages = row[1]
    enable_embedding_raw = row[2]
    enable_embedding = True if enable_embedding_raw is None else bool(enable_embedding_raw)
    return FileTypeRuntimeConfig(
        type_id=type_id,
        max_parse_pages=max_parse_pages,
        enable_embedding=enable_embedding,
    )
