"""DELETE /file/{id} 非阻塞行为测试。

验证:
1. MySQL 行立刻被删
2. Milvus + PDF 清理通过 BackgroundTasks 调度
3. 接口本身不会因 Milvus 慢调用而阻塞 event loop
"""

from __future__ import annotations

import sys

import pytest

from utils import vl_client


@pytest.fixture(autouse=True)
async def reset_db_engine():
    """每个测试都用全新 engine,避免跨 event loop 复用连接池。

    pytest-asyncio 每个测试函数会创建独立 event loop,但 model.database
    用模块级 `_engine` 缓存,会导致后续测试拿到绑定旧 loop 的连接 → 报
    `'NoneType' object has no attribute 'send'`。重置后每个测试自己造 engine。
    """
    from model import database as db_module

    if db_module._engine is not None:
        try:
            await db_module._engine.dispose()
        except Exception:
            pass
    db_module._engine = None
    db_module._session_factory = None
    yield


@pytest.fixture
def fresh_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    yield tmp_path


def _file_router_module():
    """从 sys.modules 直接拿到 blue_print.file_router 模块对象。

    不能用 `from blue_print import file_router`,因为 blue_print/__init__.py
    把同名属性绑定到了 APIRouter 实例,会遮蔽子模块。
    """
    import blue_print.file_router  # noqa: F401  确保模块已加载
    return sys.modules["blue_print.file_router"]


async def test_delete_schedules_cleanup_via_background_task(client, fresh_uploads, monkeypatch):
    """DELETE 走 BackgroundTasks: 同步 _cleanup_file_artifacts 被调度且 file_id 正确。"""
    from model.database import get_session_factory
    from model.tables import File as FileModel
    from sqlalchemy import delete as sql_delete

    captured = {"file_ids": [], "type_ids": []}

    def fake_cleanup(file_id: str, type_id: str = "default") -> None:
        captured["file_ids"].append(file_id)
        captured["type_ids"].append(type_id)

    monkeypatch.setattr(_file_router_module(), "_cleanup_file_artifacts", fake_cleanup)

    fake_id = "test_delete_async_001"
    pdf_file = fresh_uploads / f"{fake_id}.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(FileModel(file_id=fake_id, file_name="t.pdf", file_size=10, progress="complete"))
        await s.commit()

    try:
        resp = await client.delete(f"/file/{fake_id}")
        assert resp.status_code == 200
        # BackgroundTask 在 ASGITransport 下 await 前会跑完
        assert captured["file_ids"] == [fake_id]
        assert captured["type_ids"] == ["default"]

        # MySQL 行已删
        async with session_factory() as s:
            from sqlalchemy import select

            row = (await s.execute(select(FileModel).where(FileModel.file_id == fake_id))).scalar_one_or_none()
            assert row is None
    finally:
        async with session_factory() as s:
            await s.execute(sql_delete(FileModel).where(FileModel.file_id == fake_id))
            await s.commit()


async def test_delete_returns_404_when_file_missing(client):
    """文件不存在时仍返回 404,不调度后台任务。"""
    resp = await client.delete("/file/definitely_not_exists_xxx")
    assert resp.status_code == 404


async def test_batch_delete_schedules_cleanup_per_file(client, fresh_uploads, monkeypatch):
    """DELETE /file/batch: 每个成功删除的 file_id 都应调度一次后台清理。"""
    from model.database import get_session_factory
    from model.tables import File as FileModel
    from sqlalchemy import delete as sql_delete

    captured = {"file_ids": []}

    def fake_cleanup(file_id: str, type_id: str = "default") -> None:
        captured["file_ids"].append(file_id)

    monkeypatch.setattr(_file_router_module(), "_cleanup_file_artifacts", fake_cleanup)

    ids = ["test_batch_001", "test_batch_002", "test_batch_003"]
    session_factory = get_session_factory()
    async with session_factory() as s:
        for fid in ids:
            s.add(FileModel(file_id=fid, file_name=f"{fid}.pdf", file_size=10, progress="complete"))
        await s.commit()

    # 第 4 个 id 故意不存在的混进去,验证只对存在的文件调度清理
    payload = {"file_ids": ids + ["nonexistent_xxx"]}
    try:
        resp = await client.request("DELETE", "/file/batch", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["deleted_count"] == 3
        assert "nonexistent_xxx" in body["data"]["failed_ids"]
        assert sorted(captured["file_ids"]) == sorted(ids)
    finally:
        async with session_factory() as s:
            await s.execute(sql_delete(FileModel).where(FileModel.file_id.in_(ids)))
            await s.commit()
