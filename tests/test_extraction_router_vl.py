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


async def test_advanced_vl_field_page_link_from_model_pages(
    client, fake_uploads, monkeypatch
):
    """进阶 VL 字段按上游字段的 _model_pages 只渲染那几页。"""
    from sqlalchemy import delete

    from model.database import get_session_factory
    from model.tables import (
        ExtractionField as FieldModel,
        ExtractionResult as ResultModel,
        File as FileModel,
    )

    file_id = "vl_page_link_001"
    upstream_id = "vl_page_link_upstream"

    doc = fitz.open()
    for i in range(5):
        doc.new_page().insert_text((20, 20), f"page {i + 1}", fontsize=12)
    (fake_uploads / f"{file_id}.pdf").write_bytes(doc.tobytes())

    captured = {}

    async def fake_vl_chat(messages, **kw):
        captured["images"] = [
            c for c in messages[0]["content"] if c.get("type") == "image_url"
        ]
        return {
            "choices": [{"message": {"content": '{"value": "命中", "reason": "见指定页"}'}}],
            "usage": {"total_tokens": 30},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(FileModel(
            file_id=file_id, file_name="x.pdf", file_size=100,
            progress="complete", type_id="default",
        ))
        s.add(FieldModel(
            field_id=upstream_id, type_id="default", field_name="上游字段",
            source_type="text", search_type="context",
            search_config={"keywords": ["x"]},
            text_extract_prompt="<search_result>x</search_result>",
            is_advanced=0, enabled=1, priority=0,
        ))
        s.add(ResultModel(
            file_id=file_id, field_id=upstream_id, extracted_value="某值",
            reason="r", source_refs={"_model_pages": [2, 4]},
        ))
        await s.commit()

    try:
        payload = {
            "file_id": file_id,
            "config": {
                "field_name": "进阶VL",
                "source_type": "vl",
                "is_advanced": 1,
                "vl_method": "vl_model",
                "vl_config": {
                    "page_source_field": upstream_id,
                    "page_range": "1-5",       # 应被联动覆盖
                    "max_pixels": 200000,
                },
                "vl_extract_prompt": "提取，输出 JSON {value, reason}",
            },
        }
        resp = await client.post("/extraction/test", json=payload)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["extracted_value"] == "命中"
        # 只渲染第 2、4 页，手填的 1-5 被联动覆盖
        assert len(captured["images"]) == 2
        link = body["resolved_refs"]["_page_link"]
        assert link["mode"] == "discrete"
        assert link["derived_pages"] == [2, 4]
    finally:
        async with session_factory() as s:
            await s.execute(delete(ResultModel).where(ResultModel.file_id == file_id))
            await s.execute(delete(FieldModel).where(FieldModel.field_id == upstream_id))
            await s.execute(delete(FileModel).where(FileModel.file_id == file_id))
            await s.commit()
