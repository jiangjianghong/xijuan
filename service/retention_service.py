"""PDF 保留策略:按最久保存时间(TTL)与总容量上限清理 uploads 下的 PDF。

只删物理 PDF 文件,不动数据库;被清文件的解析/抽取结果仍可查,
仅 PDF 预览(GET /file/{id}/pdf)与 VL 抽取会 404。
孤儿 PDF(不在 files 表)不在本模块职责内,由 init_service.cleanup_orphan_pdfs 处理。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import get_session_factory
from model.tables import File
from utils import vl_client
from utils.config import get_config


def _try_unlink(path: Path) -> bool:
    """删除单个 PDF,失败只记日志不抛。返回是否删成功。"""
    try:
        path.unlink()
        return True
    except OSError as e:
        logger.warning("删除 PDF 失败 {}: {}", path, e)
        return False


async def enforce_pdf_retention(session: AsyncSession) -> None:
    """按 storage 配置清理 uploads 下的 PDF。两项配置 ≤0 时对应策略关闭。"""
    cfg = get_config().storage
    max_bytes = cfg.max_total_bytes
    max_minutes = cfg.max_retention_minutes
    if max_bytes <= 0 and max_minutes <= 0:
        return

    storage_dir = vl_client._get_pdf_storage_dir()
    if not storage_dir.exists():
        return

    rows = (await session.execute(select(File.file_id, File.create_time))).all()
    create_time_map = {fid: ct for fid, ct in rows}

    # 仅处理 files 表中登记的 PDF;孤儿交给 cleanup_orphan_pdfs
    pdfs: list[tuple[Path, datetime, int]] = []
    for p in storage_dir.glob("*.pdf"):
        ct = create_time_map.get(p.stem)
        if ct is None:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        pdfs.append((p, ct, size))

    removed = 0
    freed = 0

    # ① 最久保存时间(TTL)
    if max_minutes > 0:
        cutoff = datetime.now() - timedelta(minutes=max_minutes)
        survivors: list[tuple[Path, datetime, int]] = []
        for p, ct, size in pdfs:
            if ct < cutoff and _try_unlink(p):
                removed += 1
                freed += size
            else:
                survivors.append((p, ct, size))
        pdfs = survivors

    # ② 总容量上限(按 create_time 从最旧淘汰,直到回落到上限以下)
    if max_bytes > 0:
        total = sum(size for _, _, size in pdfs)
        if total > max_bytes:
            pdfs.sort(key=lambda t: t[1])  # create_time 升序,最旧在前
            for p, ct, size in pdfs:
                if total <= max_bytes:
                    break
                if _try_unlink(p):
                    removed += 1
                    freed += size
                    total -= size

    if removed:
        logger.info("PDF 保留清理: 删除 {} 个, 释放 {} 字节", removed, freed)


async def retention_loop() -> None:
    """后台周期任务:每 cleanup_interval_minutes 分钟执行一次 PDF 保留清理。

    启动时 run_init 已清理过一次,故循环先 sleep 再执行。整体 try/except
    包裹,单轮失败只记日志不杀循环;收到取消向上抛出以便优雅退出。
    """
    interval = max(1, get_config().storage.cleanup_interval_minutes) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await enforce_pdf_retention(session)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("PDF 保留策略后台清理失败: {}", e)
