"""上传接口的 type_id 存在性校验测试。

需要可用的 MySQL 数据库（参见 configs/config.yaml）。

背景：`POST /file/parse` 原先不校验 type_id，传一个不存在的类型会静默建档并
跑完整条管线，最终 progress=complete 但提取/分析结果全空——调用方看到 200 +
「处理完成」，无从发现类型传错了。
"""

from __future__ import annotations

import importlib
import io

import fitz
import pytest
from sqlalchemy import delete, select

from utils import vl_client


@pytest.fixture(autouse=True)
async def reset_db_engine():
    """每个测试都用全新 engine，避免跨 event loop 复用连接池。"""
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
    """把 vl_client 的 storage dir 重定向到临时目录。"""
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    yield tmp_path


@pytest.fixture
def stub_pipeline(monkeypatch):
    """把 pipeline 打桩成空跑，让上传流程能完成而不真的解析。

    必须用 importlib 拿真实模块对象：`blue_print.file_router` 字符串与
    `import blue_print.file_router as m` 都会被 blue_print/__init__.py 的
    router 属性遮蔽（详见 test_file_router_vl_storage.py 的注释）。
    """
    file_router_module = importlib.import_module("blue_print.file_router")

    async def fake_run_pipeline(*a, **kw):
        return None

    monkeypatch.setattr(file_router_module, "run_pipeline", fake_run_pipeline)
    yield


def _make_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((20, 20), "test", fontsize=12)
    return doc.tobytes()


def _upload_files():
    return {"file": ("test.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")}


async def _cleanup_file(file_id: str) -> None:
    from model.database import get_session_factory
    from model.tables import File as FileModel

    session_factory = get_session_factory()
    async with session_factory() as s:
        await s.execute(delete(FileModel).where(FileModel.file_id == file_id))
        await s.commit()


async def test_upload_unknown_type_id_returns_400(client, fresh_uploads, stub_pipeline):
    """传不存在的 type_id 应返回 HTTP 400 且不建档、不写盘。

    用 HTTP 状态码而非 body code：前端三条上传路径都只认 response.ok / xhr.status。
    """
    resp = await client.post(
        "/file/parse",
        files=_upload_files(),
        params={"mode": "sync", "type_id": "no_such_type_zzz"},
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "不存在" in detail
    assert "no_such_type_zzz" in detail

    # 校验发生在建档与写盘之前：不留半条记录、不留孤儿 PDF
    assert list(fresh_uploads.glob("*.pdf")) == []

    from model.database import get_session_factory
    from model.tables import File as FileModel

    session_factory = get_session_factory()
    async with session_factory() as s:
        rows = (
            await s.execute(
                select(FileModel.file_id).where(FileModel.type_id == "no_such_type_zzz")
            )
        ).all()
    assert rows == []


async def test_upload_default_type_still_works(client, fresh_uploads, stub_pipeline):
    """不传 type_id 时回落 default；default 由 init_service 保证存在，必须放行。"""
    resp = await client.post("/file/parse", files=_upload_files(), params={"mode": "sync"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200, body
    file_id = body["data"]["file_id"]
    try:
        assert len(list(fresh_uploads.glob("*.pdf"))) == 1
    finally:
        await _cleanup_file(file_id)


async def test_upload_existing_type_id_passes(client, fresh_uploads, stub_pipeline):
    """显式传一个真实存在的类型应正常放行。"""
    from model.database import get_session_factory
    from model.tables import DocType

    type_id = "tmp_upload_validation_type"
    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(DocType(type_id=type_id, type_name="临时校验类型"))
        await s.commit()

    file_id = None
    try:
        resp = await client.post(
            "/file/parse",
            files=_upload_files(),
            params={"mode": "sync", "type_id": type_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 200, body
        file_id = body["data"]["file_id"]
    finally:
        if file_id:
            await _cleanup_file(file_id)
        async with session_factory() as s:
            await s.execute(delete(DocType).where(DocType.type_id == type_id))
            await s.commit()
