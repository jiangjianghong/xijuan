"""上传写盘 + 删除联动测试。

需要可用的 MySQL 数据库（参见 configs/config.yaml）。在无 DB 环境下
这些测试会被 conftest 间接的 DB 连接失败 fail；在用户的实际开发环境跑即可。
"""

from __future__ import annotations

import io

import fitz
import pytest

from utils import vl_client


@pytest.fixture(autouse=True)
async def reset_db_engine():
    """每个测试都用全新 engine,避免跨 event loop 复用连接池。"""
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


def _make_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((20, 20), "test", fontsize=12)
    return doc.tobytes()


async def test_upload_persists_pdf(client, fresh_uploads, monkeypatch):
    """上传 PDF 应在 uploads/ 写一份字节文件。"""
    # mock pipeline 跑空，让上传流程能完成而不真的解析
    async def fake_run_pipeline(*a, **kw):
        return None

    monkeypatch.setattr("blue_print.file_router.run_pipeline", fake_run_pipeline)

    pdf_bytes = _make_pdf_bytes()
    files = {"file": ("test.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    resp = await client.post("/file/upload", files=files, params={"sync": "true"})
    assert resp.status_code in (200, 201)

    # uploads/ 下应有 1 个 .pdf 文件
    pdfs = list(fresh_uploads.glob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes() == pdf_bytes


async def test_delete_removes_pdf(client, fresh_uploads):
    """DELETE /file/{id} 应该删除 uploads/{id}.pdf。"""
    from model.database import get_session_factory
    from model.tables import File as FileModel

    fake_id = "test_delete_pdf_001"
    pdf_file = fresh_uploads / f"{fake_id}.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(
            FileModel(
                file_id=fake_id, file_name="t.pdf", file_size=10, progress="complete"
            )
        )
        await s.commit()

    try:
        resp = await client.delete(f"/file/{fake_id}")
        assert resp.status_code == 200
        assert not pdf_file.exists()
    finally:
        # 清理：如果 DELETE 没成功也确保 DB 干净
        async with session_factory() as s:
            from sqlalchemy import delete

            await s.execute(delete(FileModel).where(FileModel.file_id == fake_id))
            await s.commit()
