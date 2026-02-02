"""MinerU 解析 + 存 md + 表格提取服务。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Dict

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import File, FileContent, FileTable
from service.mineru_client import parse_pdf
from utils.config import get_config


async def parse_file(file_path: str, file_content_bytes: bytes, file_id: str, session: AsyncSession) -> str:
    """调用 MinerU 解析文件，返回 Markdown 内容。

    Args:
        file_path: 文件路径/文件名。
        file_content_bytes: 文件二进制内容。
        file_id: 文件 ID。
        session: 数据库会话。

    Returns:
        解析后的 Markdown 文本。
    """
    logger.info("开始解析文件: {}", file_id)

    # 更新状态为 parsing，记录开始时间
    stmt = (
        update(File)
        .where(File.file_id == file_id)
        .values(progress="parsing", start_parsing_time=datetime.now())
    )
    await session.execute(stmt)
    await session.commit()

    try:
        # 调用 MinerU 解析
        cfg = get_config().mineru
        content = await parse_pdf(
            file_name=file_path,
            file_content=file_content_bytes,
            base_url=cfg.base_url,
            timeout=cfg.parse_timeout,
        )

        # 更新解析完成时间
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(end_parsing_time=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()

        logger.info("文件解析完成: {}, 内容长度: {}", file_id, len(content))
        return content

    except Exception as e:
        # 更新状态为 parsing_failed
        stmt = (
            update(File)
            .where(File.file_id == file_id)
            .values(progress="parsing_failed", error=str(e))
        )
        await session.execute(stmt)
        await session.commit()
        logger.error("文件解析失败: {}, 错误: {}", file_id, e)
        raise


async def save_file_content(file_id: str, content: str, session: AsyncSession) -> None:
    """将解析结果存入 file_content 表。

    Args:
        file_id: 文件 ID。
        content: Markdown 内容。
        session: 数据库会话。
    """
    # 检查是否已存在
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.file_content = content
    else:
        file_content = FileContent(file_id=file_id, file_content=content)
        session.add(file_content)

    await session.commit()
    logger.info("文件内容已保存: {}", file_id)


def parse_tables(content: str, file_id: str) -> List[Dict]:
    """解析 Markdown 中的 HTML 表格。

    Args:
        content: Markdown 全文。
        file_id: 文件 ID。

    Returns:
        表格信息列表，每项包含 file_id, table_index, total_table, table_name, table_content。
    """
    tables = []
    table_pattern = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
    matches = list(table_pattern.finditer(content))
    total_table = len(matches)

    for table_index, match in enumerate(matches, 1):
        table_content = match.group(0)
        start_pos = match.start()
        preceding_text = content[:start_pos].rstrip()

        last_double_newline = preceding_text.rfind("\n\n")
        if last_double_newline != -1:
            after_double_newline = preceding_text[last_double_newline:].strip()
            lines = after_double_newline.split("\n")
            table_name = lines[-1].strip() if lines else ""
        else:
            lines = preceding_text.strip().split("\n")
            table_name = lines[-1].strip() if lines else ""

        table_name = re.sub(r"^#+\s*", "", table_name)

        if "<table>" in table_name.lower() or "</table>" in table_name.lower():
            table_name = ""

        if not table_name or len(table_name) > 200:
            table_name = f"表{table_index}"

        tables.append(
            {
                "file_id": file_id,
                "table_index": table_index,
                "total_table": total_table,
                "table_name": table_name[:500],
                "table_content": table_content,
            }
        )

    return tables


async def save_tables(tables: List[Dict], session: AsyncSession) -> None:
    """将表格信息批量存入 file_table 表。

    Args:
        tables: parse_tables 返回的表格列表。
        session: 数据库会话。
    """
    if not tables:
        return

    for table_data in tables:
        file_table = FileTable(
            file_id=table_data["file_id"],
            table_index=table_data["table_index"],
            total_table=table_data["total_table"],
            table_name=table_data["table_name"],
            table_content=table_data["table_content"],
        )
        session.add(file_table)

    await session.commit()
    logger.info("表格已保存: {} 个", len(tables))
