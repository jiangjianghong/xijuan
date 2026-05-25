# 删除文件接口非阻塞化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `DELETE /file/{file_id}` 与 `DELETE /file/batch` 立刻返回响应,Milvus 删除 + PDF 清理走 FastAPI BackgroundTasks 后台执行,前端列表轮询不再被阻塞;顺手做点 Milvus 客户端复用 + 去掉无用 `flush()` 让单次耗时也降下来。

**Architecture:** Starlette 的 `BackgroundTasks` 在响应体发出之后才执行任务,且同步函数被 `run_in_threadpool` 包住——所以只要把 Milvus + PDF 清理写成同步函数 `_cleanup_file_artifacts(file_id)` 并通过 `background_tasks.add_task()` 调度即可:① HTTP 客户端立刻拿到 200;② 同步阻塞调用跑在线程池里,不堵 asyncio 事件循环,其他请求(列表刷新)可以照常处理。同时给 `MilvusClient` 加一个模块级单例 `get_milvus_client()`,避免每次删除都 `connections.connect()` + `collection.load()`。

**Tech Stack:** FastAPI `BackgroundTasks`(Starlette 自动用 anyio threadpool 跑同步函数)、pymilvus(同步 SDK)、pytest-asyncio + httpx ASGITransport(BackgroundTasks 在测试中会在 `await client.delete(...)` 返回前跑完,因此现有测试断言不需要"等"逻辑)。

---

## File Structure

- `utils/milvus_client.py` —— 增加模块级 `get_milvus_client()` 单例工厂;`delete_by_file_id` 去掉 `collection.flush()`。
- `blue_print/file_router.py` —— 增加 `_cleanup_file_artifacts(file_id)` 同步辅助函数;改造 `delete_file` / `batch_delete_files` 两个端点,把 Milvus + PDF 清理改成 `background_tasks.add_task()` 调度。
- `tests/test_file_router_vl_storage.py` —— 现有 `test_delete_removes_pdf` 继续可用(BackgroundTasks 在 ASGITransport 下 await 返回前已跑完),新增对单例 + 批量删除非阻塞行为的测试。

---

## Task 1: Milvus 客户端单例 + 删除时跳过 flush

**Files:**
- Modify: `utils/milvus_client.py:180-193`(`delete_by_file_id` 方法)
- Modify: `utils/milvus_client.py`(文件末尾追加单例工厂)
- Test: `tests/test_milvus_client_singleton.py`(新增)

- [ ] **Step 1: 写失败的单例测试**

新建 `tests/test_milvus_client_singleton.py`:

```python
"""Milvus 客户端单例测试。"""

from __future__ import annotations

import pytest

from utils import milvus_client as mc_module
from utils.milvus_client import MilvusClient, get_milvus_client


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前后都把单例复位,避免互相污染。"""
    mc_module._singleton = None
    yield
    mc_module._singleton = None


def test_get_milvus_client_returns_same_instance(monkeypatch):
    """连续调用返回同一个对象,connect/ensure_collection 只触发一次。"""
    call_counter = {"connect": 0, "ensure_collection": 0}

    def fake_connect(self):
        call_counter["connect"] += 1

    def fake_ensure_collection(self, embedding_dim=None):
        call_counter["ensure_collection"] += 1
        return object()

    monkeypatch.setattr(MilvusClient, "connect", fake_connect)
    monkeypatch.setattr(MilvusClient, "ensure_collection", fake_ensure_collection)

    c1 = get_milvus_client()
    c2 = get_milvus_client()
    c3 = get_milvus_client()

    assert c1 is c2 is c3
    assert call_counter["connect"] == 1
    assert call_counter["ensure_collection"] == 1


def test_get_milvus_client_first_call_failure_does_not_cache(monkeypatch):
    """首次创建失败时,单例不被缓存,下次调用可重试。"""
    call_counter = {"connect": 0}

    def fake_connect(self):
        call_counter["connect"] += 1
        if call_counter["connect"] == 1:
            raise RuntimeError("first attempt fails")

    monkeypatch.setattr(MilvusClient, "connect", fake_connect)
    monkeypatch.setattr(MilvusClient, "ensure_collection", lambda self, embedding_dim=None: object())

    with pytest.raises(RuntimeError):
        get_milvus_client()

    # 第二次:不再抛错,应该返回正常实例
    client = get_milvus_client()
    assert client is not None
    assert call_counter["connect"] == 2
```

- [ ] **Step 2: 跑测试确认 ImportError**

Run: `uv run pytest tests/test_milvus_client_singleton.py -v`

Expected: `ImportError: cannot import name 'get_milvus_client' from 'utils.milvus_client'`

- [ ] **Step 3: 实现单例 + 去掉 flush**

修改 `utils/milvus_client.py`:

① 修改 `delete_by_file_id` 方法(`utils/milvus_client.py:180-193`),把 `collection.flush()` 那一行删掉:

```python
    def delete_by_file_id(self, file_id: str) -> None:
        """删除指定 file_id 的所有记录。

        Args:
            file_id: 文件 ID。
        """
        collection = self._collection
        if collection is None:
            collection = self.ensure_collection()

        expr = f'file_id == "{file_id}"'
        collection.delete(expr)
        logger.info("Milvus 删除 file_id={} 的所有记录", file_id)
```

② 在文件末尾追加单例工厂:

```python


_singleton: Optional["MilvusClient"] = None


def get_milvus_client() -> "MilvusClient":
    """返回进程级 Milvus 客户端单例。

    首次调用会 connect + ensure_collection,后续调用直接返回缓存实例。
    创建过程中抛错则不缓存,下次调用会重试。
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    client = MilvusClient()
    client.connect()
    client.ensure_collection()
    _singleton = client
    return _singleton
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_milvus_client_singleton.py -v`

Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add utils/milvus_client.py tests/test_milvus_client_singleton.py
git commit -m "perf: Milvus 客户端单例化并跳过删除时的 flush"
```

---

## Task 2: 添加清理辅助函数 + 改造单文件删除接口

**Files:**
- Modify: `blue_print/file_router.py:287-322`(`delete_file` 端点)
- Modify: `blue_print/file_router.py`(在 `delete_file` 上方新增 `_cleanup_file_artifacts`)
- Test: `tests/test_file_router_delete_async.py`(新增)

- [ ] **Step 1: 写失败的非阻塞行为测试**

新建 `tests/test_file_router_delete_async.py`:

```python
"""DELETE /file/{id} 非阻塞行为测试。

验证:
1. MySQL 行立刻被删
2. Milvus + PDF 清理通过 BackgroundTasks 调度
3. 接口本身不会因 Milvus 慢调用而阻塞 event loop
"""

from __future__ import annotations

import pytest

from utils import vl_client


@pytest.fixture
def fresh_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    yield tmp_path


async def test_delete_schedules_cleanup_via_background_task(client, fresh_uploads, monkeypatch):
    """DELETE 走 BackgroundTasks: 同步 _cleanup_file_artifacts 被调度且 file_id 正确。"""
    from model.database import get_session_factory
    from model.tables import File as FileModel
    from sqlalchemy import delete as sql_delete

    captured = {"file_ids": []}

    def fake_cleanup(file_id: str) -> None:
        captured["file_ids"].append(file_id)

    monkeypatch.setattr("blue_print.file_router._cleanup_file_artifacts", fake_cleanup)

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_file_router_delete_async.py -v`

Expected: 第一个测试 FAIL —— `AttributeError: module 'blue_print.file_router' has no attribute '_cleanup_file_artifacts'`(因为目前还没拆出辅助函数);第二个测试可能 PASS。

- [ ] **Step 3: 实现 `_cleanup_file_artifacts` + 改造 `delete_file`**

修改 `blue_print/file_router.py`:

① 顶部 import 调整,把 `MilvusClient` 那行替换成单例工厂的 import:

```python
from utils.milvus_client import get_milvus_client
```

② 在 `delete_file` 函数定义**之前**新增辅助函数(同步函数,FastAPI 会自动用 threadpool 跑):

```python
def _cleanup_file_artifacts(file_id: str) -> None:
    """删除接口的后台清理:Milvus 向量 + 持久化 PDF。

    设计为同步函数,通过 BackgroundTasks 调度时 Starlette 会用
    anyio.run_in_threadpool 执行,所以不会阻塞 asyncio 事件循环。
    任何异常都吞掉只记日志——清理失败不影响用户已收到的删除成功响应。
    """
    try:
        get_milvus_client().delete_by_file_id(file_id)
    except Exception as e:
        logger.warning("Milvus 删除失败 file_id={}: {}", file_id, e)

    try:
        from utils import vl_client as _vl_client_for_storage
        _vl_client_for_storage.pdf_path(file_id).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("清理 PDF 失败 file_id={}: {}", file_id, e)
```

③ 把 `delete_file`(`blue_print/file_router.py:287-322`)整体替换为:

```python
@router.delete("/{file_id}", response_model=ResponseWrapper)
async def delete_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """删除文件及所有关联数据。

    MySQL 关联表立刻删完并提交;Milvus 向量与持久化 PDF 走后台
    清理,接口立即返回——前端列表轮询不会被 Milvus 调用阻塞。
    """
    stmt = select(FileModel).where(FileModel.file_id == file_id)
    result = await db.execute(stmt)
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    await db.execute(delete(FileContent).where(FileContent.file_id == file_id))
    await db.execute(delete(FileTable).where(FileTable.file_id == file_id))
    await db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
    await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
    await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
    await db.execute(delete(FileModel).where(FileModel.file_id == file_id))
    await db.commit()

    background_tasks.add_task(_cleanup_file_artifacts, file_id)

    return ResponseWrapper(message="文件已删除")
```

- [ ] **Step 4: 跑新增测试**

Run: `uv run pytest tests/test_file_router_delete_async.py -v`

Expected: 2 passed

- [ ] **Step 5: 跑既有相关测试,确保没回归**

Run: `uv run pytest tests/test_file_router_vl_storage.py::test_delete_removes_pdf -v`

Expected: PASS(BackgroundTasks 在 ASGITransport 下会在 `await client.delete(...)` 返回前跑完,所以 `assert not pdf_file.exists()` 仍然成立)

- [ ] **Step 6: 提交**

```bash
git add blue_print/file_router.py tests/test_file_router_delete_async.py
git commit -m "perf: DELETE /file/{id} 通过 BackgroundTasks 后台清理 Milvus 与 PDF"
```

---

## Task 3: 改造批量删除接口

**Files:**
- Modify: `blue_print/file_router.py:106-158`(`batch_delete_files` 端点)
- Test: `tests/test_file_router_delete_async.py`(追加用例)

- [ ] **Step 1: 追加批量删除的非阻塞测试**

在 `tests/test_file_router_delete_async.py` 末尾追加:

```python
async def test_batch_delete_schedules_cleanup_per_file(client, fresh_uploads, monkeypatch):
    """DELETE /file/batch: 每个成功删除的 file_id 都应调度一次后台清理。"""
    from model.database import get_session_factory
    from model.tables import File as FileModel
    from sqlalchemy import delete as sql_delete

    captured = {"file_ids": []}

    def fake_cleanup(file_id: str) -> None:
        captured["file_ids"].append(file_id)

    monkeypatch.setattr("blue_print.file_router._cleanup_file_artifacts", fake_cleanup)

    ids = ["test_batch_001", "test_batch_002", "test_batch_003"]
    session_factory = get_session_factory()
    async with session_factory() as s:
        for fid in ids:
            s.add(FileModel(file_id=fid, file_name=f"{fid}.pdf", file_size=10, progress="complete"))
        await s.commit()

    # 第 3 个 id 故意不存在的混进去,验证只对存在的文件调度清理
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_file_router_delete_async.py::test_batch_delete_schedules_cleanup_per_file -v`

Expected: FAIL —— `assert captured["file_ids"] == []`(因为当前批量端点还是直接同步调用 Milvus,没走 `_cleanup_file_artifacts`)

- [ ] **Step 3: 改造 `batch_delete_files`**

把 `blue_print/file_router.py:106-158` 整段替换为:

```python
@router.delete("/batch", response_model=ResponseWrapper)
async def batch_delete_files(
    request: BatchDeleteRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """批量删除文件及关联数据。

    MySQL 关联表同步删完并一次性提交;每个被成功删除的 file_id 都
    调度一次后台 `_cleanup_file_artifacts` 处理 Milvus + PDF。
    """
    deleted_count = 0
    failed_ids: list[str] = []
    cleanup_ids: list[str] = []

    for file_id in request.file_ids:
        try:
            stmt = select(FileModel).where(FileModel.file_id == file_id)
            result = await db.execute(stmt)
            file_record = result.scalar_one_or_none()

            if not file_record:
                failed_ids.append(file_id)
                continue

            await db.execute(delete(FileContent).where(FileContent.file_id == file_id))
            await db.execute(delete(FileTable).where(FileTable.file_id == file_id))
            await db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
            await db.execute(delete(ExtractionResult).where(ExtractionResult.file_id == file_id))
            await db.execute(delete(AnalysisResult).where(AnalysisResult.file_id == file_id))
            await db.execute(delete(FileModel).where(FileModel.file_id == file_id))

            cleanup_ids.append(file_id)
            deleted_count += 1
        except Exception as e:
            logger.error("删除文件失败 (file_id={}): {}", file_id, e)
            failed_ids.append(file_id)

    await db.commit()

    for fid in cleanup_ids:
        background_tasks.add_task(_cleanup_file_artifacts, fid)

    return ResponseWrapper(
        data=BatchDeleteResponse(
            deleted_count=deleted_count,
            failed_ids=failed_ids,
        ).model_dump()
    )
```

> 关键改动:① 增加 `background_tasks` 参数;② 把 Milvus 与 PDF 内联清理从循环里全部去掉;③ 用一个 `cleanup_ids` 列表收集成功的 id,**在 commit 之后**统一调度 BackgroundTasks——这样保证只有真正落库的删除才会触发后台清理(如果 commit 失败,后台任务不会跑)。

- [ ] **Step 4: 跑新增测试**

Run: `uv run pytest tests/test_file_router_delete_async.py::test_batch_delete_schedules_cleanup_per_file -v`

Expected: PASS

- [ ] **Step 5: 跑整组测试,确认没回归**

Run: `uv run pytest tests/test_file_router_delete_async.py tests/test_file_router_vl_storage.py tests/test_milvus_client_singleton.py -v`

Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add blue_print/file_router.py tests/test_file_router_delete_async.py
git commit -m "perf: DELETE /file/batch 通过 BackgroundTasks 后台清理"
```

---

## Task 4: 手动验证 + 收尾

代码改完后必须实地在浏览器跑一遍,确认前端体验真的改善——单元测试只能验证调度逻辑,验证不了"列表轮询不再被阻塞"这件事(因为 ASGITransport 下 BackgroundTasks 是 await 前跑完的)。

- [ ] **Step 1: 启动开发服务器**

Run: `uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload`

- [ ] **Step 2: 浏览器打开 `http://localhost:5019/ui`**

- [ ] **Step 3: 准备至少一个文件(能跑完完整管线,这样 Milvus 里有该 file_id 的向量)**

- [ ] **Step 4: 删除单文件验证**

打开 DevTools Network 面板,点删除按钮:
- `DELETE /file/{id}` 应在几十毫秒内返回 200(原先会卡几秒)
- 紧跟着的 `GET /file/list` 应该立刻返回新列表,不被卡住
- 后台日志能看到 `Milvus 删除 file_id=... 的所有记录`(可能在响应之后零点几秒)

- [ ] **Step 5: 批量删除验证**

选中多个文件批量删除:
- `DELETE /file/batch` 立刻返回
- 列表立刻刷新,反映新状态
- 日志能看到对应数量的 Milvus 删除记录

- [ ] **Step 6: 异常路径验证**

故意停掉 Milvus(或改坏配置),再删一个文件:
- 接口仍然返回 200,前端列表正常刷新
- 日志能看到 `Milvus 删除失败 file_id=...` warning,主流程不受影响

- [ ] **Step 7: 如果手动测试通过则无需额外提交**

如果手动测试发现问题,回到对应任务修复,新建提交。
