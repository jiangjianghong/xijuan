"""MinerU 解析 + 存 md 服务。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import File, FileContent
from service.mineru_client import parse_pdf
from service.type_config_service import get_file_type_runtime_config
from utils.config import get_config


async def parse_file(
    file_path: str, file_content_bytes: bytes, file_id: str, session: AsyncSession
) -> Tuple[str, str]:
    """调用 MinerU 解析文件，返回 Markdown 内容和 middle_json。"""
    logger.info("开始解析文件: {}", file_id)

    stmt = (
        update(File)
        .where(File.file_id == file_id)
        .values(progress="parsing", start_parsing_time=datetime.now())
    )
    await session.execute(stmt)
    await session.commit()

    try:
        cfg = get_config().mineru
        type_cfg = await get_file_type_runtime_config(file_id, session)
        if type_cfg.max_parse_pages:
            logger.info(
                "文件 {} 使用类型 {} 的最大解析页数: {}",
                file_id,
                type_cfg.type_id,
                type_cfg.max_parse_pages,
            )
        result = await parse_pdf(
            file_name=file_path,
            file_content=file_content_bytes,
            file_id=file_id,
            base_url=cfg.base_url,
            timeout=cfg.parse_timeout,
            max_parse_pages=type_cfg.max_parse_pages,
        )
        content = result["md_content"]
        middle_json_str = result["middle_json"]

        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(end_parsing_time=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()

        logger.info("文件解析完成: {}, 内容长度: {}", file_id, len(content))
        return content, middle_json_str

    except Exception as e:
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="parsing_failed", error=str(e))
        )
        await session.execute(stmt)
        await session.commit()
        logger.error("文件解析失败: {}, 错误: {}", file_id, e)
        raise


async def save_file_content(
    file_id: str,
    content: str,
    session: AsyncSession,
    middle_json: Optional[str] = None,
    page_mapping: Optional[List] = None,
) -> None:
    """将解析结果存入 file_content 表。"""
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.file_content = content
        existing.middle_json = middle_json
        existing.page_mapping = page_mapping
    else:
        file_content = FileContent(
            file_id=file_id,
            file_content=content,
            middle_json=middle_json,
            page_mapping=page_mapping,
        )
        session.add(file_content)

    await session.commit()
    logger.info("文件内容已保存: {}", file_id)
