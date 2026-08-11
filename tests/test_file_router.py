"""file 路由测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_file_status_not_found(client: AsyncClient):
    """不存在的文件应返回 404。"""
    resp = await client.get("/file/nonexistent/status")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_file_tables(client: AsyncClient):
    """测试获取文件表格列表。"""
    resp = await client.get("/file/testfile/tables")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_file_outline_route(client: AsyncClient):
    """测试获取文件大纲(路由可达 + 空集回退)。"""
    resp = await client.get("/file/nonexistent/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


@pytest.mark.anyio
async def test_get_extraction_results_with_source_refs(client: AsyncClient):
    """提取结果应透出 source_refs（含检索原文）。"""
    from model.database import get_session_factory
    from model.tables import ExtractionResult

    file_id = "test_src_refs_file"
    refs = {
        "_texts": {"金额": "合同金额为100万元"},
        "金额": [{"type": "context", "start_pos": 1, "end_pos": 9,
                  "page_num": "1", "text": "合同金额为100万元"}],
    }
    factory = get_session_factory()
    async with factory() as session:
        session.add(ExtractionResult(
            file_id=file_id, field_id="f_amount",
            extracted_value="100万元", reason="r", source_refs=refs,
        ))
        await session.commit()

    try:
        resp = await client.get(f"/file/{file_id}/extraction")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["source_refs"] == refs
    finally:
        async with factory() as session:
            obj = await session.get(ExtractionResult, (file_id, "f_amount"))
            if obj:
                await session.delete(obj)
                await session.commit()


@pytest.mark.anyio
async def test_get_file_pdf_200(client: AsyncClient):
    """uploads 下存在 PDF 时应 200 并返回 application/pdf 原始字节。"""
    from utils import vl_client

    file_id = "test_pdf_endpoint_file"
    pdf = vl_client.pdf_path(file_id)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 minimal")
    try:
        resp = await client.get(f"/file/{file_id}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert resp.content == b"%PDF-1.4 minimal"
    finally:
        pdf.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_get_file_pdf_404(client: AsyncClient):
    """PDF 不存在时应 404。"""
    resp = await client.get("/file/nonexistent_pdf_file/pdf")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_file_pdf_path_traversal_blocked(client: AsyncClient):
    """file_id 含路径穿越字符时应 404（Windows 反斜杠穿越防护）。"""
    resp = await client.get("/file/..%5C..%5Csecret/pdf")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_list_processing_filters_and_joins_type_name(client: AsyncClient):
    """/file/processing 只返回处理中的文件，并带上 type_name；type_id 可过滤。"""
    from model.database import get_session_factory
    from model.tables import DocType, File as FileModel

    factory = get_session_factory()
    async with factory() as session:
        session.add(DocType(type_id="proc_t", type_name="处理中测试类型", project_id=None))
        session.add(FileModel(file_id="proc_f1", type_id="proc_t",
                              file_name="a.pdf", file_size=1, progress="parsing"))
        session.add(FileModel(file_id="proc_f2", type_id="proc_t",
                              file_name="b.pdf", file_size=1, progress="complete"))
        await session.commit()

    try:
        resp = await client.get("/file/processing?type_id=proc_t")
        assert resp.status_code == 200
        items = resp.json()["data"]
        ids = {it["file_id"] for it in items}
        assert "proc_f1" in ids          # 处理中 → 返回
        assert "proc_f2" not in ids      # complete → 不返回
        f1 = next(it for it in items if it["file_id"] == "proc_f1")
        assert f1["type_name"] == "处理中测试类型"
        assert f1["type_id"] == "proc_t"
    finally:
        async with factory() as session:
            for cls, key in [(FileModel, "proc_f1"), (FileModel, "proc_f2"), (DocType, "proc_t")]:
                obj = await session.get(cls, key)
                if obj:
                    await session.delete(obj)
            await session.commit()




def test_resolve_stats_range_windows():
    """时间窗口解析：1h/24h 走滚动时钟，today/yesterday/Nd 走自然日，all 无边界。"""
    from datetime import datetime, timedelta

    from blue_print.file_router import resolve_stats_range

    now = datetime(2026, 8, 11, 9, 44, 30)
    midnight = datetime(2026, 8, 11, 0, 0, 0)

    assert resolve_stats_range("all", now) == (None, None, "day")
    assert resolve_stats_range("1h", now) == (now - timedelta(hours=1), None, "hour")
    assert resolve_stats_range("24h", now) == (now - timedelta(hours=24), None, "hour")
    assert resolve_stats_range("today", now) == (midnight, None, "hour")
    # 昨天是唯一的闭区间窗口
    assert resolve_stats_range("yesterday", now) == (
        midnight - timedelta(days=1), midnight, "hour")
    # 自然日窗口含当天，故 3d 从前天 00:00 起；≤3 天用小时桶
    assert resolve_stats_range("3d", now) == (midnight - timedelta(days=2), None, "hour")
    assert resolve_stats_range("30d", now) == (midnight - timedelta(days=29), None, "day")


@pytest.mark.anyio
async def test_file_stats_aggregation(client: AsyncClient):
    """/file/stats 聚合口径正确。

    测试库是共享 dev 库、无隔离，全局统计必然混入既有数据，
    因此对 overview 一律比「插入前后的差值」，只有按 type/project 分组的
    条目才能直接断言绝对值（分组 key 是本用例独占的）。
    """
    from datetime import datetime, timedelta

    from model.database import get_session_factory
    from model.tables import DocType, File as FileModel, Project

    project_id, type_id = "pytest_stats_pj", "pytest_stats_ty"
    file_ids = ["pytest_stats_f0", "pytest_stats_f1", "pytest_stats_f2"]

    base = (await client.get("/file/stats?range=all")).json()["data"]["overview"]

    now = datetime.now().replace(microsecond=0)
    factory = get_session_factory()
    async with factory() as session:
        session.add(Project(project_id=project_id, project_name="统计测试项目"))
        session.add(DocType(type_id=type_id, type_name="统计测试类型", project_id=project_id))
        # 2 个 complete（parsing 耗时 10s / 20s）+ 1 个 parsing_failed（无耗时）
        session.add(FileModel(
            file_id=file_ids[0], type_id=type_id, file_name="a.pdf", file_size=1000,
            progress="complete", create_time=now,
            start_parsing_time=now, end_parsing_time=now + timedelta(seconds=10),
            start_analyzing_time=now, end_analyzing_time=now + timedelta(seconds=40)))
        session.add(FileModel(
            file_id=file_ids[1], type_id=type_id, file_name="b.pdf", file_size=2000,
            progress="complete", create_time=now,
            start_parsing_time=now, end_parsing_time=now + timedelta(seconds=20)))
        session.add(FileModel(
            file_id=file_ids[2], type_id=type_id, file_name="c.pdf", file_size=3000,
            progress="parsing_failed", create_time=now))
        await session.commit()

    try:
        resp = await client.get("/file/stats?range=all")
        assert resp.status_code == 200
        data = resp.json()["data"]

        ov = data["overview"]
        assert ov["total_files"] == base["total_files"] + 3
        assert ov["completed"] == base["completed"] + 2
        assert ov["failed"] == base["failed"] + 1
        assert ov["total_size"] == base["total_size"] + 6000

        by_type = {i["key"]: i for i in data["by_type"]}
        assert by_type[type_id]["count"] == 3
        assert by_type[type_id]["size"] == 6000
        assert by_type[type_id]["label"] == "统计测试类型"

        by_project = {i["key"]: i for i in data["by_project"]}
        assert by_project[project_id]["count"] == 3
        assert by_project[project_id]["label"] == "统计测试项目"

        # 状态分布的 label 保持 key 原值（中文由前端 Utils.getStatusText 映射）
        statuses = {i["key"]: i for i in data["status_distribution"]}
        assert statuses["parsing_failed"]["label"] == "parsing_failed"

        stages = {i["stage"]: i for i in data["stage_durations"]}
        assert list(stages) == ["parsing", "tableing", "chunking",
                                "embedding", "extracting", "analyzing"]
        # 只有 start/end 双端非空才计样本：本用例贡献 2 个 parsing 样本
        assert stages["parsing"]["samples"] >= 2
        assert stages["parsing"]["max_seconds"] >= 20

        assert data["range"] == "all"
        assert data["granularity"] == "day"
        assert data["start_time"] is None
    finally:
        async with factory() as session:
            for fid in file_ids:
                obj = await session.get(FileModel, fid)
                if obj:
                    await session.delete(obj)
            for cls, key in [(DocType, type_id), (Project, project_id)]:
                obj = await session.get(cls, key)
                if obj:
                    await session.delete(obj)
            await session.commit()


@pytest.mark.anyio
async def test_file_stats_range_scopes_every_metric(client: AsyncClient):
    """时间窗口必须作用于整页指标，而不只是趋势图。

    造一条 400 天前的旧文件：`range=all` 能看到它，`range=30d` 必须把它从
    overview / by_type / stage_durations 里一并排除。
    """
    from datetime import datetime, timedelta

    from model.database import get_session_factory
    from model.tables import DocType, File as FileModel

    type_id, file_id = "pytest_scope_ty", "pytest_scope_old"
    old = datetime.now().replace(microsecond=0) - timedelta(days=400)

    factory = get_session_factory()
    async with factory() as session:
        session.add(DocType(type_id=type_id, type_name="窗口测试类型", project_id=None))
        session.add(FileModel(
            file_id=file_id, type_id=type_id, file_name="old.pdf", file_size=4096,
            progress="complete", create_time=old,
            start_parsing_time=old, end_parsing_time=old + timedelta(seconds=7)))
        await session.commit()

    try:
        all_data = (await client.get("/file/stats?range=all")).json()["data"]
        win_data = (await client.get("/file/stats?range=30d")).json()["data"]

        all_types = {i["key"]: i for i in all_data["by_type"]}
        win_types = {i["key"]: i for i in win_data["by_type"]}
        assert all_types[type_id]["count"] == 1     # 全时间看得到
        assert type_id not in win_types             # 近 30 天被排除

        assert all_data["overview"]["total_files"] > win_data["overview"]["total_files"]
        assert all_data["overview"]["total_size"] - win_data["overview"]["total_size"] >= 4096

        # 阶段耗时同样受窗口约束
        all_parsing = next(i for i in all_data["stage_durations"] if i["stage"] == "parsing")
        win_parsing = next(i for i in win_data["stage_durations"] if i["stage"] == "parsing")
        assert all_parsing["samples"] == win_parsing["samples"] + 1
    finally:
        async with factory() as session:
            for cls, key in [(FileModel, file_id), (DocType, type_id)]:
                obj = await session.get(cls, key)
                if obj:
                    await session.delete(obj)
            await session.commit()


@pytest.mark.anyio
async def test_file_stats_hour_granularity_and_bad_range(client: AsyncClient):
    """短窗口用小时桶（date 带 HH:00），非法 range 走 422。"""
    data = (await client.get("/file/stats?range=24h")).json()["data"]
    assert data["granularity"] == "hour"
    assert data["start_time"] is not None
    for item in data["trend"]:
        assert item["date"].endswith(":00") and len(item["date"]) == 16

    assert (await client.get("/file/stats?range=all")).json()["data"]["granularity"] == "day"
    assert (await client.get("/file/stats?range=7d")).json()["data"]["granularity"] == "day"
    assert (await client.get("/file/stats?range=nonsense")).status_code == 422
