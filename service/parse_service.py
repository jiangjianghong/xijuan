"""MinerU 解析 + 存 md 服务。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_session_factory
from model.tables import File, FileContent
from service.mineru_client import parse_pdf
from service.stage_status import mark_file_failed
from service.type_config_service import get_file_type_runtime_config
from utils.config import get_config
from utils.concurrency import get_limiter


# 连接在大字段写入期间断开时，客户端无法判断服务端是否已经提交。
# 重试使用幂等的「按 file_id 更新/插入」操作，允许覆盖这种不确定状态。
_FILE_CONTENT_RETRY_DELAYS = (1, 2, 4)
_MYSQL_CONNECTION_ERROR_CODES = {2006, 2013, 2055}


def _payload_bytes(value: object) -> int:
    """返回发送到 MySQL 前的 UTF-8 字节数，用于诊断大字段写入。"""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    try:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def _is_mysql_connection_loss(exc: BaseException) -> bool:
    """识别 SQLAlchemy/aiomysql 包装后的连接断开错误。

    2013 既可能直接出现在 pymysql 异常的 ``args``，也可能被
    ``sqlalchemy.exc.DBAPIError`` 包装；``connection_invalidated`` 是 SQLAlchemy
    方言已判定连接失效时更可靠的信号。
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "connection_invalidated", False):
            return True
        args = getattr(current, "args", ())
        if args:
            try:
                if int(args[0]) in _MYSQL_CONNECTION_ERROR_CODES:
                    return True
            except (TypeError, ValueError):
                pass
        pending.extend(
            candidate
            for candidate in (
                getattr(current, "orig", None),
                getattr(current, "__cause__", None),
                getattr(current, "__context__", None),
            )
            if isinstance(candidate, BaseException)
        )
    return False


async def _save_file_content_once(
    file_id: str,
    content: str,
    session: AsyncSession,
    middle_json: Optional[str],
    page_mapping: Optional[List],
) -> None:
    """在一个会话中完成 file_content 的幂等更新/插入。"""
    result = await session.execute(
        select(FileContent).where(FileContent.file_id == file_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.file_content = content
        existing.middle_json = middle_json
        existing.page_mapping = page_mapping
    else:
        session.add(
            FileContent(
                file_id=file_id,
                file_content=content,
                middle_json=middle_json,
                page_mapping=page_mapping,
            )
        )

    await session.commit()


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
        # 读完配置即释放连接:get_file_type_runtime_config 是纯 SELECT,留下的只读事务
        # 会占着连接熬过下面 MinerU 的分钟级轮询。提前 rollback(无数据可丢)把连接还回池,
        # 避免并发解析时连接池被空转事务占满(QueuePool timeout)。
        await session.rollback()
        if type_cfg.max_parse_pages:
            logger.info(
                "文件 {} 使用类型 {} 的最大解析页数: {}",
                file_id,
                type_cfg.type_id,
                type_cfg.max_parse_pages,
            )
        # 解析请求独立限流；排队与请求期间不占用数据库连接，响应后即释放令牌。
        limiter = get_limiter("mineru_parse", get_config().mineru.queue_width)
        async with limiter.context({"file_id": file_id, "stage": "parsing"}):
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

        logger.info(
            "文件解析完成（MinerU 已返回，尚未完成解析结果落库）: {}, 内容长度: {}",
            file_id,
            len(content),
        )
        return content, middle_json_str

    except Exception as e:
        await mark_file_failed(session, file_id, "parsing_failed", e)
        logger.error("文件解析失败: {}, 错误: {}", file_id, e)
        raise


async def save_file_content(
    file_id: str,
    content: str,
    session: AsyncSession,
    middle_json: Optional[str] = None,
    page_mapping: Optional[List] = None,
) -> None:
    """将解析结果存入 ``file_content`` 表。

    解析阶段通常在 MinerU 轮询后才开始落库。虽然会话启用了
    ``pool_pre_ping``，它只能发现「执行前」已失效的连接，无法防止大字段
    INSERT/UPDATE 或 COMMIT 期间连接被服务端/网络重置。对 MySQL 连接断开
    （2006/2013/2055）使用新会话重试，写入按主键查询后更新/插入，因此即使
    第一次 COMMIT 的结果不确定也不会重复产生行。其它数据错误不重试，避免
    把不可恢复的约束/字段错误误认为网络问题。
    """
    payload_sizes = {
        "content_bytes": _payload_bytes(content),
        "middle_json_bytes": _payload_bytes(middle_json),
        "page_mapping_bytes": _payload_bytes(page_mapping),
    }
    payload_sizes["total_bytes"] = sum(payload_sizes.values())
    started_at = time.perf_counter()
    logger.info(
        "开始保存文件解析结果: file_id={}, content_bytes={}, middle_json_bytes={}, "
        "page_mapping_bytes={}, total_bytes={}",
        file_id,
        payload_sizes["content_bytes"],
        payload_sizes["middle_json_bytes"],
        payload_sizes["page_mapping_bytes"],
        payload_sizes["total_bytes"],
    )

    try:
        await _save_file_content_once(
            file_id, content, session, middle_json, page_mapping
        )
    except Exception as first_exc:
        if not _is_mysql_connection_loss(first_exc):
            raise

        # SQLAlchemy 会把执行/提交失败的会话置为 DEACTIVE；回滚既清掉事务，
        # 也让连接池知道应丢弃已失效的底层连接。回滚失败不覆盖原始 2013。
        try:
            await session.rollback()
        except Exception as rollback_exc:
            logger.warning(
                "file_content 连接断开后原会话回滚失败，将改用独立会话重试: "
                "file_id={}, error={}",
                file_id,
                rollback_exc,
            )
            # rollback 本身可能也要向已断开的连接发送 ROLLBACK。若失败，
            # invalidate 令 SQLAlchemy 丢弃底层连接并清理事务状态，避免本次
            # file_content 重试成功后，管线下一条 UPDATE 又撞上 PendingRollbackError。
            invalidate = getattr(session, "invalidate", None)
            if invalidate is not None:
                try:
                    result = invalidate()
                    if hasattr(result, "__await__"):
                        await result
                except Exception as invalidate_exc:
                    logger.warning(
                        "file_content 连接断开后会话 invalidate 失败: file_id={}, "
                        "error={}",
                        file_id,
                        invalidate_exc,
                    )

        for retry_index, delay in enumerate(_FILE_CONTENT_RETRY_DELAYS, start=1):
            await asyncio.sleep(delay)
            try:
                async with get_session_factory()() as retry_session:
                    await _save_file_content_once(
                        file_id,
                        content,
                        retry_session,
                        middle_json,
                        page_mapping,
                    )
                logger.warning(
                    "file_content 写入因 MySQL 连接断开后重试成功: file_id={}, "
                    "attempt={}, delay={}s, payload_bytes={}, elapsed_ms={}",
                    file_id,
                    retry_index + 1,
                    delay,
                    payload_sizes["total_bytes"],
                    int((time.perf_counter() - started_at) * 1000),
                )
                return
            except Exception as retry_exc:
                if not _is_mysql_connection_loss(retry_exc):
                    logger.error(
                        "file_content 重试遇到不可重试错误: file_id={}, attempt={}, "
                        "error={}",
                        file_id,
                        retry_index + 1,
                        retry_exc,
                    )
                    raise retry_exc from first_exc
                logger.warning(
                    "file_content 写入连接断开，{} 秒后重试: file_id={}, "
                    "attempt={}/{}, payload_bytes={}, error={}",
                    delay,
                    file_id,
                    retry_index + 1,
                    len(_FILE_CONTENT_RETRY_DELAYS) + 1,
                    payload_sizes["total_bytes"],
                    retry_exc,
                )

        logger.error(
            "file_content 写入因 MySQL 连接断开重试耗尽: file_id={}, attempts={}, "
            "payload_bytes={}, elapsed_ms={}, original_error={}",
            file_id,
            len(_FILE_CONTENT_RETRY_DELAYS) + 1,
            payload_sizes["total_bytes"],
            int((time.perf_counter() - started_at) * 1000),
            first_exc,
        )
        raise

    logger.info(
        "文件内容已保存: file_id={}, payload_bytes={}, elapsed_ms={}",
        file_id,
        payload_sizes["total_bytes"],
        int((time.perf_counter() - started_at) * 1000),
    )
