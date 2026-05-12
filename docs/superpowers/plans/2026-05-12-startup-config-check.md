# 启动配置自检 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在服务启动 lifespan 末尾增加一组只读探活，对 MySQL / Milvus / MinerU / 向量化模型 / 2 个 LLM 配置 / VL 视觉模型共 7 项依赖做 PASS/FAIL 检查，并以对齐表格输出到启动日志。失败不阻塞启动。

**Architecture:** 新增 `service/startup_check.py` 集中 7 个 check 函数 + 公共 `_run_one`（10s 超时 + 异常吞掉）+ `_format_table` 渲染器；`service/init_service.run_init()` 在尾部追加一行 `await run_startup_checks()`。所有 check 通过 `asyncio.gather` 并发执行，单项失败不影响其他项。

**Tech Stack:** Python 3.x · asyncio · httpx · pytest-asyncio · loguru · 现有 `utils.llm_client` / `utils.milvus_client` / `utils.vl_client`

**Spec:** `docs/superpowers/specs/2026-05-12-startup-config-check-design.md`

---

## File Structure

| 路径 | 动作 | 职责 |
|---|---|---|
| `service/startup_check.py` | 新建 | `CheckResult` 数据类 / 7 个 check 函数 / `_run_one` / `_format_table` / `run_startup_checks` |
| `service/init_service.py` | 修改 | `run_init()` 末尾追加 `await run_startup_checks()` |
| `tests/test_startup_check.py` | 新建 | 对 `CheckResult` / `_run_one` / `_format_table` / `run_startup_checks` 做单元测试（7 个真实 check 不写 IO 测试） |

---

## Task 1: 建模 `CheckResult` + 模块骨架

**Files:**
- Create: `service/startup_check.py`
- Create: `tests/test_startup_check.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_startup_check.py
"""启动配置自检单测。"""
from __future__ import annotations

from service.startup_check import CheckResult


def test_check_result_defaults():
    r = CheckResult(name="x", ok=True, elapsed_ms=12, detail="d")
    assert r.name == "x"
    assert r.ok is True
    assert r.elapsed_ms == 12
    assert r.detail == "d"
    assert r.error is None


def test_check_result_with_error():
    r = CheckResult(name="x", ok=False, elapsed_ms=0, detail="", error="boom")
    assert r.ok is False
    assert r.error == "boom"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.startup_check'`

- [ ] **Step 3: 创建模块骨架**

```python
# service/startup_check.py
"""启动配置自检：lifespan 末尾对所有外部依赖做只读探活，
输出对齐表格；任何失败仅记录警告，绝不阻塞启动。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CheckResult:
    name: str
    ok: bool
    elapsed_ms: int
    detail: str
    error: Optional[str] = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add service/startup_check.py tests/test_startup_check.py
git commit -m "feat(startup-check): scaffold module + CheckResult dataclass"
```

---

## Task 2: `_run_one` —— 异常 / 超时边界

**Files:**
- Modify: `service/startup_check.py`
- Modify: `tests/test_startup_check.py`

- [ ] **Step 1: 在 test 文件追加 3 个失败测试**

```python
# 追加到 tests/test_startup_check.py
import asyncio
import pytest

from service.startup_check import _run_one


@pytest.mark.asyncio
async def test_run_one_returns_ok_result():
    async def good():
        return CheckResult(name="good", ok=True, elapsed_ms=5, detail="ok")

    r = await _run_one("good", good)
    assert r.ok is True
    assert r.name == "good"


@pytest.mark.asyncio
async def test_run_one_swallows_exception():
    async def boom():
        raise RuntimeError("nope")

    r = await _run_one("boom", boom)
    assert r.ok is False
    assert r.name == "boom"
    assert "RuntimeError" in r.error
    assert "nope" in r.error


@pytest.mark.asyncio
async def test_run_one_respects_timeout(monkeypatch):
    # 把超时压到 0.1s 加快测试
    monkeypatch.setattr("service.startup_check._CHECK_TIMEOUT", 0.1)

    async def slow():
        await asyncio.sleep(5)
        return CheckResult(name="slow", ok=True, elapsed_ms=0, detail="")

    r = await _run_one("slow", slow)
    assert r.ok is False
    assert "Timeout" in r.error
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: 3 FAIL — `_run_one` not defined

- [ ] **Step 3: 实现 `_run_one` 与超时常量**

在 `service/startup_check.py` 顶部 import 区追加：

```python
import asyncio
import time
from typing import Awaitable, Callable

from loguru import logger
```

在 `CheckResult` 类下方追加：

```python
_CHECK_TIMEOUT: float = 10.0  # 单项硬超时（秒）


async def _run_one(
    name: str,
    fn: Callable[[], Awaitable[CheckResult]],
) -> CheckResult:
    """运行单个 check 的边界包装：超时 + 异常 → 落到 CheckResult，绝不向上抛。"""
    start = time.monotonic()
    try:
        return await asyncio.wait_for(fn(), timeout=_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("启动检查 {} 超时（>{}s）", name, _CHECK_TIMEOUT)
        return CheckResult(
            name=name,
            ok=False,
            elapsed_ms=elapsed,
            detail="",
            error="TimeoutError",
        )
    except Exception as e:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        logger.exception("启动检查 {} 异常", name)
        return CheckResult(
            name=name,
            ok=False,
            elapsed_ms=elapsed,
            detail="",
            error=f"{type(e).__name__}: {e}",
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 提交**

```bash
git add service/startup_check.py tests/test_startup_check.py
git commit -m "feat(startup-check): _run_one with timeout + exception isolation"
```

---

## Task 3: `_format_table` —— ASCII 表格渲染

**Files:**
- Modify: `service/startup_check.py`
- Modify: `tests/test_startup_check.py`

- [ ] **Step 1: 追加测试**

```python
# 追加到 tests/test_startup_check.py
from service.startup_check import _format_table


def test_format_table_renders_all_rows():
    results = [
        CheckResult(name="mysql", ok=True, elapsed_ms=12, detail="db=foo, tables=3"),
        CheckResult(name="mineru", ok=False, elapsed_ms=10000, detail="", error="TimeoutError"),
    ]
    out = _format_table(results)
    assert "mysql" in out
    assert "mineru" in out
    assert "✓" in out
    assert "✗" in out
    # 失败行包含 error 字段
    assert "TimeoutError" in out


def test_format_table_handles_empty_list():
    out = _format_table([])
    # 不抛即可
    assert isinstance(out, str)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: 2 FAIL — `_format_table` not defined

- [ ] **Step 3: 实现渲染函数**

在 `service/startup_check.py` 追加：

```python
def _format_table(results: list[CheckResult]) -> str:
    """渲染 CheckResult 列表为对齐 ASCII 表格 + 汇总行。"""
    headers = ("Check", "OK", "ms", "Detail")
    if not results:
        return "(no checks ran)"

    rows = []
    for r in results:
        symbol = "✓" if r.ok else "✗"
        # 失败时把 error 拼到 detail 显示，方便一眼看到原因
        detail = r.detail if r.ok else (r.error or r.detail or "")
        rows.append((r.name, symbol, str(r.elapsed_ms), detail))

    col_widths = [
        max(len(headers[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(4)
    ]

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "│ " + " │ ".join(
            cells[i].ljust(col_widths[i]) if i != 1 else cells[i].center(col_widths[i])
            for i in range(4)
        ) + " │"

    sep_top = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    sep_mid = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    sep_bot = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    lines = [sep_top, fmt_row(headers), sep_mid]
    lines.extend(fmt_row(row) for row in rows)
    lines.append(sep_bot)

    ok_count = sum(1 for r in results if r.ok)
    total_ms = sum(r.elapsed_ms for r in results)
    lines.append(f"启动检查完成: {ok_count}/{len(results)} 通过, 总耗时 ~{total_ms / 1000:.1f}s")
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 提交**

```bash
git add service/startup_check.py tests/test_startup_check.py
git commit -m "feat(startup-check): _format_table ASCII renderer"
```

---

## Task 4: 7 个真实 check 函数

按 spec 章节 2/5，这一批是与外部服务交互的实跑代码，**不写 IO 单测**——它们的事实测试就是每次启动服务时的实跑。同一个 commit 里把 7 个 check + check 注册表一起加进去。

**Files:**
- Modify: `service/startup_check.py`

- [ ] **Step 1: 追加 import**

在 `service/startup_check.py` 顶部 import 区追加：

```python
from pathlib import Path

import httpx
from sqlalchemy import text

from model.database import get_engine
from utils.config import get_config
from utils.llm_client import chat_completion, get_embeddings
from utils.milvus_client import MilvusClient
from utils.vl_client import vl_chat
```

并在文件顶部加入项目根路径锚（用于定位 `tests/base64_pic.md`）：

```python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VL_TEST_IMAGE = _PROJECT_ROOT / "tests" / "base64_pic.md"
```

- [ ] **Step 2: 实现 `check_mysql`**

```python
async def check_mysql() -> CheckResult:
    start = time.monotonic()
    cfg = get_config().mysql
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text("SHOW TABLES"))
        rows = result.fetchall()
    elapsed = int((time.monotonic() - start) * 1000)
    return CheckResult(
        name="mysql",
        ok=True,
        elapsed_ms=elapsed,
        detail=f"db={cfg.database}, tables={len(rows)}",
    )
```

- [ ] **Step 3: 实现 `check_milvus`**

```python
async def check_milvus() -> CheckResult:
    from pymilvus import utility

    start = time.monotonic()
    cfg = get_config().milvus
    client = MilvusClient()
    # connect 是同步的；在 default loop 里直接调用也安全
    client.connect()
    collections = utility.list_collections()
    elapsed = int((time.monotonic() - start) * 1000)
    has_target = cfg.collection_name in collections
    return CheckResult(
        name="milvus",
        ok=has_target,
        elapsed_ms=elapsed,
        detail=f"collections={len(collections)}, target={cfg.collection_name} "
               f"{'✓' if has_target else '✗ MISSING'}",
        error=None if has_target else f"target collection {cfg.collection_name} not found",
    )
```

- [ ] **Step 4: 实现 `check_mineru`**

```python
async def check_mineru() -> CheckResult:
    start = time.monotonic()
    cfg = get_config().mineru
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(cfg.base_url)
    elapsed = int((time.monotonic() - start) * 1000)
    # 任何 HTTP 响应（含 4xx/5xx）都表示 TCP/HTTP 贯通
    return CheckResult(
        name="mineru",
        ok=True,
        elapsed_ms=elapsed,
        detail=f"GET {cfg.base_url} -> {resp.status_code} (TCP/HTTP ok)",
    )
```

- [ ] **Step 5: 实现 `check_embedding`**

```python
async def check_embedding() -> CheckResult:
    start = time.monotonic()
    cfg = get_config().embedding
    vectors = await get_embeddings(["ping"])
    elapsed = int((time.monotonic() - start) * 1000)
    if not vectors or len(vectors[0]) != cfg.embedding_dim:
        return CheckResult(
            name="embedding",
            ok=False,
            elapsed_ms=elapsed,
            detail=f"got dim={len(vectors[0]) if vectors else 0}, expected {cfg.embedding_dim}",
            error="embedding dim mismatch",
        )
    return CheckResult(
        name="embedding",
        ok=True,
        elapsed_ms=elapsed,
        detail=f"dim={cfg.embedding_dim} ✓",
    )
```

- [ ] **Step 6: 实现 `check_llm`（通用，供 extraction / table_name_validation 各调一次）**

```python
async def check_llm(
    name: str,
    base_url: str,
    model: str,
    api_key: str,
    timeout: int,
) -> CheckResult:
    start = time.monotonic()
    reply = await chat_completion(
        "ping",
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        max_retries=1,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    reply = reply or ""
    return CheckResult(
        name=name,
        ok=len(reply.strip()) > 0,
        elapsed_ms=elapsed,
        detail=f"model={model}, reply={len(reply)} chars",
        error=None if reply.strip() else "empty reply",
    )
```

- [ ] **Step 7: 实现 `check_vl`**

```python
async def check_vl() -> CheckResult:
    start = time.monotonic()
    if not _VL_TEST_IMAGE.exists():
        return CheckResult(
            name="vl_model",
            ok=False,
            elapsed_ms=0,
            detail="",
            error=f"test image not found: {_VL_TEST_IMAGE}",
        )
    data_url = _VL_TEST_IMAGE.read_text(encoding="utf-8").strip()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "请用一句话描述这张图片"},
            ],
        }
    ]
    resp = await vl_chat(messages, max_retries=1)
    reply = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    elapsed = int((time.monotonic() - start) * 1000)
    cfg = get_config().vl_model
    return CheckResult(
        name="vl_model",
        ok=len(reply.strip()) > 0,
        elapsed_ms=elapsed,
        detail=f"model={cfg.model}, reply={len(reply)} chars",
        error=None if reply.strip() else "empty reply",
    )
```

- [ ] **Step 8: 跑现有测试确认没回归**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: PASS (7 passed) — 新加的 check 函数不影响已有测试

- [ ] **Step 9: 提交**

```bash
git add service/startup_check.py
git commit -m "feat(startup-check): 7 dependency checks (mysql/milvus/mineru/embedding/2 LLM/VL)"
```

---

## Task 5: `run_startup_checks` 编排器

**Files:**
- Modify: `service/startup_check.py`
- Modify: `tests/test_startup_check.py`

- [ ] **Step 1: 追加测试 —— gather 中混入异常项，最外层不抛**

```python
# 追加到 tests/test_startup_check.py
from service.startup_check import run_startup_checks


@pytest.mark.asyncio
async def test_run_startup_checks_does_not_raise(monkeypatch):
    """即使每个 check 函数都抛异常，run_startup_checks 必须不抛、返回 7 条 fail 结果。"""
    import service.startup_check as mod

    async def boom():
        raise RuntimeError("simulated")

    monkeypatch.setattr(mod, "check_mysql", boom)
    monkeypatch.setattr(mod, "check_milvus", boom)
    monkeypatch.setattr(mod, "check_mineru", boom)
    monkeypatch.setattr(mod, "check_embedding", boom)
    monkeypatch.setattr(mod, "check_vl", boom)
    # check_llm 是 lambda 包装的，直接替换 chat_completion 让它抛
    async def boom_chat(*args, **kwargs):
        raise RuntimeError("simulated")
    monkeypatch.setattr(mod, "chat_completion", boom_chat)

    results = await run_startup_checks()
    assert len(results) == 7
    assert all(r.ok is False for r in results)
    # 名字齐全
    names = {r.name for r in results}
    assert names == {"mysql", "milvus", "mineru", "embedding",
                     "extraction_llm", "table_validation_llm", "vl_model"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_startup_check.py::test_run_startup_checks_does_not_raise -v`
Expected: FAIL — `run_startup_checks` not defined

- [ ] **Step 3: 实现编排器**

在 `service/startup_check.py` 文件末尾追加：

```python
async def run_startup_checks() -> list[CheckResult]:
    """并发执行 7 个 check，输出对齐表格，永不抛异常。"""
    cfg = get_config()

    async def _extraction_llm() -> CheckResult:
        c = cfg.extraction
        return await check_llm(
            "extraction_llm", c.llm_base_url, c.llm_model, c.llm_api_key, c.llm_timeout
        )

    async def _table_validation_llm() -> CheckResult:
        c = cfg.table_name_validation
        # table_name_validation 字段允许 None，None 时回退到 extraction
        ext = cfg.extraction
        return await check_llm(
            "table_validation_llm",
            c.llm_base_url or ext.llm_base_url,
            c.llm_model or ext.llm_model,
            c.llm_api_key or ext.llm_api_key,
            c.llm_timeout or ext.llm_timeout,
        )

    spec: list[tuple[str, Callable[[], Awaitable[CheckResult]]]] = [
        ("mysql", check_mysql),
        ("milvus", check_milvus),
        ("mineru", check_mineru),
        ("embedding", check_embedding),
        ("extraction_llm", _extraction_llm),
        ("table_validation_llm", _table_validation_llm),
        ("vl_model", check_vl),
    ]

    try:
        results = await asyncio.gather(
            *(_run_one(name, fn) for name, fn in spec),
            return_exceptions=False,  # _run_one 已经吞了所有异常
        )
        logger.info("启动配置自检结果：\n{}", _format_table(list(results)))
        return list(results)
    except Exception:  # noqa: BLE001
        logger.exception("run_startup_checks 自身异常（不应发生）")
        return []
```

- [ ] **Step 4: 跑全套测试确认通过**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 提交**

```bash
git add service/startup_check.py tests/test_startup_check.py
git commit -m "feat(startup-check): run_startup_checks orchestrator with table output"
```

---

## Task 6: 接入 `run_init()` + 启动冒烟

**Files:**
- Modify: `service/init_service.py`

- [ ] **Step 1: 在 `run_init` 末尾追加调用**

打开 `service/init_service.py`，定位到 `run_init` 函数（约第 300 行）。

在文件顶部 import 区追加（与已有 import 并列）：

```python
from service.startup_check import run_startup_checks
```

在 `run_init` 函数的 `logger.info("服务初始化完成")` **之前**追加：

```python
    # 配置自检：探活所有外部依赖，输出对齐表格；失败不阻塞
    await run_startup_checks()
```

修改后 `run_init` 末尾应该长这样：

```python
    # 执行状态恢复和垃圾清理
    session_factory = get_session_factory()
    async with session_factory() as session:
        await recover_abnormal_status(session)
        await cleanup_garbage_data(session)
        await cleanup_orphan_pdfs(session)

    # 配置自检：探活所有外部依赖，输出对齐表格；失败不阻塞
    await run_startup_checks()

    logger.info("服务初始化完成")
```

- [ ] **Step 2: 跑所有测试确认没回归**

Run: `uv run pytest -x`
Expected: 全部 PASS（含 test_startup_check.py 8 项 + 其它现有用例）

- [ ] **Step 3: 启动服务，肉眼验证表格**

Run: `uv run uvicorn app:app --host 0.0.0.0 --port 5019` （不开 --reload 避免重复执行）

Expected: 启动日志能看到类似：

```
启动配置自检结果：
┌──────────────────────┬────┬───────┬─────────────────────────────────────┐
│ Check                │ OK │  ms   │ Detail                              │
├──────────────────────┼────┼───────┼─────────────────────────────────────┤
│ mysql                │ ✓  │   ..  │ db=wanzi_prase2_001, tables=..      │
│ milvus               │ ✓  │   ..  │ collections=.., target=...base ✓    │
│ mineru               │ ✓  │   ..  │ GET http://...:7078 -> .. (ok)      │
│ embedding            │ ✓  │   ..  │ dim=1024 ✓                          │
│ extraction_llm       │ ✓  │   ..  │ model=qwen3.5-122b, reply=.. chars  │
│ table_validation_llm │ ✓  │   ..  │ model=qwen3.5-122b, reply=.. chars  │
│ vl_model             │ ✓  │   ..  │ model=qwen3.5-122b, reply=.. chars  │
└──────────────────────┴────┴───────┴─────────────────────────────────────┘
启动检查完成: 7/7 通过, 总耗时 ~..s
```

如果某项 ✗，先看日志里的 `WARNING` / `ERROR` 行确认根因（通常是网络或密钥），不必改代码。

按 Ctrl+C 退出。

- [ ] **Step 4: 提交**

```bash
git add service/init_service.py
git commit -m "feat(startup-check): wire run_startup_checks into lifespan init"
```

---

## Self-Review Notes

**Spec coverage**：
- §2 范围表 7 项 → Task 4 各对应一个 check 函数 ✓
- §3.1 模块结构 → Task 1-5 完整覆盖 ✓
- §3.2 接入点（run_init 末尾） → Task 6 ✓
- §3.3 并发 + 10s 超时 → Task 2 (`_CHECK_TIMEOUT`) + Task 5 (`asyncio.gather`) ✓
- §3.4 错误处理 → Task 2 `_run_one` + Task 5 外层 try/except ✓
- §4 输出格式 → Task 3 `_format_table` ✓
- §5 关键细节（`_VL_TEST_IMAGE` 路径锚 / LLM 显式传参 / Milvus 用 `list_collections` 而非 `ensure_collection` / MinerU 任何响应即 ok） → Task 4 各 Step 已落实 ✓
- §6 测试 5 用例 → Task 1 (×2 dataclass) / Task 2 (×3 `_run_one`) / Task 3 (×2 `_format_table`) / Task 5 (×1 编排不抛) = 8 项（spec 列了 5 类，本计划拆得更细）✓
- §7 无配置改动 ✓

**Placeholder scan**：无 TBD/TODO/"添加合适的错误处理"等占位；每步都有完整代码。

**Type consistency**：`CheckResult` 字段在 Task 1 定义后，所有 check 函数在 Task 4 一致使用 `name/ok/elapsed_ms/detail/error`；`_run_one` / `run_startup_checks` 签名贯穿一致；`_CHECK_TIMEOUT` 在 Task 2 引入后 Task 5 用 monkeypatch 覆盖路径正确。
