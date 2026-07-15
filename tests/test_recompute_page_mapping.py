"""page_mapping 重算端点测试。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app import app
from model.database import get_db
from model.tables import FileContent


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    """按需返回预置 FileContent;commit 记录一次。"""

    def __init__(self, fc):
        self._fc = fc
        self.committed = False

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._fc)

    async def commit(self):
        self.committed = True


def _override(fc):
    async def _dep():
        yield _FakeSession(fc)
    return _dep


@pytest.mark.anyio
async def test_recompute_rebuilds_page_mapping_from_middle_json():
    md = "唯一标题甲编号111用于定位\n\n唯一标题乙编号222用于定位"
    middle = (
        '{"pdf_info": ['
        '{"page_idx": 0, "page_size": [600, 800], "para_blocks": ['
        '{"lines": [{"spans": [{"content": "唯一标题甲编号111用于定位"}]}]}]},'
        '{"page_idx": 1, "page_size": [600, 800], "para_blocks": ['
        '{"lines": [{"spans": [{"content": "唯一标题乙编号222用于定位"}]}]}]}]}'
    )
    fc = FileContent(file_id="f1", file_content=md, middle_json=middle, page_mapping=None)
    app.dependency_overrides[get_db] = _override(fc)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post("/file/f1/recompute_page_mapping")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["anchor_count"] == 2
        assert body["data"]["page_min"] == 1
        assert body["data"]["page_max"] == 2
        # 已写回对象
        assert fc.page_mapping is not None
        assert len(fc.page_mapping) == 2
        assert fc.page_mapping[1]["page_num"] == 2
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_recompute_missing_content_returns_404():
    app.dependency_overrides[get_db] = _override(None)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post("/file/nope/recompute_page_mapping")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
