"""extraction_router VL 分支测试。"""

from __future__ import annotations

import fitz
import pytest

from utils import vl_client


@pytest.fixture
def fake_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    yield tmp_path


async def test_extraction_test_vl_mode_with_temp_config(client, fake_uploads, monkeypatch):
    """/extraction/test 接受 source_type=vl 的临时 config。"""
    file_id = "vl_test_extract_001"

    doc = fitz.open()
    doc.new_page().insert_text((20, 20), "amount: 5000", fontsize=12)
    pdf_bytes = doc.tobytes()
    (fake_uploads / f"{file_id}.pdf").write_bytes(pdf_bytes)

    async def fake_vl_chat(messages, **kw):
        return {
            "choices": [
                {"message": {"content": '{"value": "5000", "reason": "见首页"}'}}
            ],
            "usage": {"total_tokens": 30},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    from model.database import get_session_factory
    from model.tables import File as FileModel

    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(
            FileModel(
                file_id=file_id, file_name="x.pdf", file_size=100, progress="complete"
            )
        )
        await s.commit()

    try:
        payload = {
            "file_id": file_id,
            "config": {
                "field_name": "金额",
                "source_type": "vl",
                "vl_method": "vl_model",
                "vl_config": {"page_range": "all", "max_pixels": 200000},
                "vl_extract_prompt": "提取金额，输出 JSON {value, reason}",
            },
        }
        resp = await client.post("/extraction/test", json=payload)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["extracted_value"] == "5000"
        assert body["reason"] == "见首页"
        assert body["search_results"][0]["type"] == "vl_meta"
        assert body["search_results"][0]["method"] == "vl_model"
    finally:
        async with session_factory() as s:
            from sqlalchemy import delete

            await s.execute(delete(FileModel).where(FileModel.file_id == file_id))
            await s.commit()
