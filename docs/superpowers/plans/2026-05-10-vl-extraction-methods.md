# VL 端到端抽取方法 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `extraction_field` 体系上新增 `source_type='vl'`，支持 `vl_model` / `vl_progressive` / `vl_locate` 三种基于 PDF 视觉模型的端到端抽取方法。

**Architecture:** PDF 字节在上传时持久化到 `uploads/{file_id}.pdf`；新增 `utils/vl_client.py` 封装 OpenAI 兼容 VL API 调用 + 全局并发信号量 + PDF 渲染工具；新增 `service/vl_service/` 包提供三种方法的抽取实现；`extraction_service.run_extraction` 新增 vl 分支调用 `extract_vl_field`；前端 `ui/js/ruleConfig.js` 加 vl 表单与调试事件渲染。

**Tech Stack:** FastAPI、SQLAlchemy 2.0 async、Pydantic v2、httpx（沿用项目既有风格，不引入 OpenAI SDK）、PyMuPDF（fitz）、Pillow、loguru、pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-05-10-vl-extraction-methods-design.md`

---

## 文件结构总览

### 新建

| 文件 | 职责 |
|---|---|
| `utils/vl_client.py` | VL API HTTP 客户端 + 全局信号量 + PDF 渲染工具（fitz/PIL） + `pdf_path()` |
| `service/vl_service/__init__.py` | re-export 三个公开函数 |
| `service/vl_service/_common.py` | `parse_vl_json_response` / `strip_think_tags` / `build_image_messages` |
| `service/vl_service/_defaults.py` | `DEFAULT_BATCH_PROMPT` / `DEFAULT_LOCATE_PROMPT` / `DEFAULT_MAX_PIXELS` |
| `service/vl_service/model.py` | `vl_model_extract` |
| `service/vl_service/progressive.py` | `vl_progressive_extract` |
| `service/vl_service/locate.py` | `vl_locate_extract` |
| `tests/test_vl_client.py` | 渲染工具与 page range 单测；`vl_chat` mock httpx 单测 |
| `tests/test_vl_service.py` | 三种方法的 mock vl_chat 行为测试 |
| `tests/test_extraction_router_vl.py` | `/extraction/test` 与 `/extraction/test/stream` 的 vl 分支 |
| `tests/test_file_router_vl_storage.py` | 上传写盘、删除联动、批量删除联动、孤儿清理 |

### 修改

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | 加 `pymupdf`、`pillow` 依赖 |
| `configs/config.yaml` | 新增 `vl_model:` 节 |
| `utils/config.py` | 新增 `VLModelConfig` + 挂到 `AppConfig` |
| `model/tables.py` | `source_type_enum` 加 `vl`；`ExtractionField` 加 4 列；新建 `vl_method_enum` |
| `model/schemas.py` | `SourceTypeEnum` 加 `vl`；新增 `VLMethodEnum`；`ExtractionFieldCreate` 加 4 字段 + 校验 |
| `service/extraction_service.py` | 新增 `extract_vl_field`；`run_extraction` 与 `run_extraction_stream` 加 vl 分支；`test_field_extraction_stream` 加 vl 分支 |
| `blue_print/extraction_router.py` | `/test` 加 vl 分支；临时 config 模式补 vl_* 字段透传 |
| `blue_print/file_router.py` | 上传 await 写盘；DELETE / batch_delete 路径联动清理 PDF |
| `service/init_service.py` | `migrations` 列表加 4 个 vl_* 列；新增 `cleanup_orphan_pdfs()` 并在 `run_init` 末尾调用 |
| `ui/js/ruleConfig.js` | source_type 下拉加 vl；新增 VL section 表单 + 收集；调试面板 vl 事件渲染；列表渲染；前端校验 |
| `.gitignore` | 加 `uploads/` |
| `CLAUDE.md` | "Extraction System" 节补 vl 部分 |

---

## Task 1：依赖与 .gitignore

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1：检查 .gitignore 现状**

Run: `cat .gitignore`
确认是否已有 `uploads/` 行；若无，下一步追加。

- [ ] **Step 2：追加 `uploads/` 到 `.gitignore`**

在 `.gitignore` 末尾追加一行：
```
uploads/
```

- [ ] **Step 3：在 `pyproject.toml` 加 PyMuPDF 与 Pillow 依赖**

打开 `pyproject.toml`，定位到 `[project]` → `dependencies` 列表（或 `[tool.poetry.dependencies]`，按你项目实际节为准），追加：
```toml
"pymupdf>=1.24.0",
"pillow>=10.0.0",
```
若用 `requirements.txt` 风格，则在对应文件加：
```
pymupdf>=1.24.0
pillow>=10.0.0
```

- [ ] **Step 4：运行依赖安装**

Run: `uv sync`
Expected: 输出无报错，新依赖被安装。

- [ ] **Step 5：smoke 检查 import 可用**

Run: `python -c "import fitz; from PIL import Image; print('ok')"`
Expected: 输出 `ok`，无异常。

- [ ] **Step 6：commit**

```bash
git add .gitignore pyproject.toml uv.lock
git commit -m "deps: add pymupdf and pillow for VL extraction"
```

---

## Task 2：VL 配置节

**Files:**
- Modify: `utils/config.py:91-110`
- Modify: `configs/config.yaml`

- [ ] **Step 1：在 `utils/config.py` 加 `VLModelConfig`**

在 `AnalysisConfig` 之后、`AppConfig` 之前，加：
```python
class VLModelConfig(BaseModel):
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen-vl-max"
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 180
    enable_thinking: bool = False
    global_max_concurrency: int = 8
    default_max_pixels: int = 4_000_000
    pdf_storage_dir: str = "uploads"
```

- [ ] **Step 2：把 `VLModelConfig` 挂到 `AppConfig`**

修改 `AppConfig`：
```python
class AppConfig(BaseSettings):
    server: ServerConfig = ServerConfig()
    mineru: MineruConfig = MineruConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    milvus: MilvusConfig = MilvusConfig()
    mysql: MySQLConfig = MySQLConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    table_name_validation: TableNameValidationConfig = TableNameValidationConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    vl_model: VLModelConfig = VLModelConfig()
```

- [ ] **Step 3：在 `configs/config.yaml` 末尾新增 `vl_model:` 节**

```yaml
# VL（视觉模型）抽取配置
vl_model:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key: "sk-c515937fecce4719823fcbfa364e09b8"
  model: "qwen-vl-max"
  temperature: 0.1
  max_tokens: 4096
  timeout: 180
  enable_thinking: false
  global_max_concurrency: 8
  default_max_pixels: 4000000
  pdf_storage_dir: "uploads"
```

- [ ] **Step 4：smoke 验证配置加载**

Run: `python -c "from utils.config import get_config; c = get_config(); print(c.vl_model.model, c.vl_model.global_max_concurrency)"`
Expected: 输出 `qwen-vl-max 8`。

- [ ] **Step 5：commit**

```bash
git add utils/config.py configs/config.yaml
git commit -m "config: add vl_model section"
```

---

## Task 3：vl_client — pdf_path 与解析工具（TDD）

**Files:**
- Create: `utils/vl_client.py`
- Test: `tests/test_vl_client.py`

- [ ] **Step 1：写第一个失败测试 — `pdf_path` 返回正确路径**

创建 `tests/test_vl_client.py`：
```python
"""utils/vl_client.py 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import vl_client


def test_pdf_path_relative_dir(tmp_path, monkeypatch):
    """pdf_path() 应该按配置的 pdf_storage_dir 拼接绝对路径。"""
    fake_dir = tmp_path / "uploads"
    monkeypatch.setattr(
        vl_client,
        "_get_pdf_storage_dir",
        lambda: fake_dir,
    )
    p = vl_client.pdf_path("abc123")
    assert p == fake_dir / "abc123.pdf"
```

- [ ] **Step 2：运行测试看到 ImportError**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'utils.vl_client'`。

- [ ] **Step 3：创建 `utils/vl_client.py` 最小实现**

```python
"""VL 视觉模型客户端：HTTP 调用、全局并发治理、PDF 渲染工具。"""

from __future__ import annotations

from pathlib import Path

from utils.config import get_config


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_pdf_storage_dir() -> Path:
    """按 vl_model.pdf_storage_dir 配置返回绝对路径目录。"""
    cfg = get_config().vl_model
    p = Path(cfg.pdf_storage_dir)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def pdf_path(file_id: str) -> Path:
    """返回 {pdf_storage_dir}/{file_id}.pdf 的绝对路径（不保证文件存在）。"""
    return _get_pdf_storage_dir() / f"{file_id}.pdf"
```

- [ ] **Step 4：运行测试确认通过**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: PASS。

- [ ] **Step 5：写 `parse_page_range` 测试**

在 `tests/test_vl_client.py` 末尾追加：
```python
def test_parse_page_range_all():
    assert vl_client.parse_page_range("all", 5) == [0, 1, 2, 3, 4]


def test_parse_page_range_single_and_range():
    assert vl_client.parse_page_range("1,3-4", 10) == [0, 2, 3]


def test_parse_page_range_clamp_out_of_bounds():
    """超界页直接丢弃。"""
    assert vl_client.parse_page_range("1-100", 3) == [0, 1, 2]


def test_parse_page_range_empty_string():
    assert vl_client.parse_page_range("", 5) == []
```

- [ ] **Step 6：运行测试确认 4 条都失败**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: 4 条新测试 FAIL（AttributeError）。

- [ ] **Step 7：实现 `parse_page_range`**

在 `utils/vl_client.py` 追加：
```python
def parse_page_range(page_range: str, total_pages: int) -> list[int]:
    """解析 \"1-2,5\" / \"all\" 这类字符串为 0-indexed 页码列表。"""
    if not page_range:
        return []
    if page_range == "all":
        return list(range(total_pages))
    pages: list[int] = []
    for part in page_range.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            pages.extend(range(int(start_s) - 1, int(end_s)))
        else:
            pages.append(int(part) - 1)
    return [p for p in pages if 0 <= p < total_pages]
```

- [ ] **Step 8：运行测试确认全过**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: 5 条全 PASS。

- [ ] **Step 9：commit**

```bash
git add utils/vl_client.py tests/test_vl_client.py
git commit -m "feat(vl_client): add pdf_path and parse_page_range with tests"
```

---

## Task 4：vl_client — PDF 渲染工具（TDD）

**Files:**
- Modify: `utils/vl_client.py`
- Modify: `tests/test_vl_client.py`

为后续测试准备一份单页 PDF fixture（用 fitz 现场创建）。

- [ ] **Step 1：写 `render_pages_to_b64` 与 `render_hires` 测试**

在 `tests/test_vl_client.py` 末尾追加：
```python
import base64

import fitz


def _make_test_pdf_bytes(num_pages: int = 2) -> bytes:
    """生成一份多页 PDF（每页写"Page N"），返回字节。"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), f"Page {i + 1}", fontsize=24)
    return doc.tobytes()


def test_render_pages_to_b64_returns_png_b64():
    pdf_bytes = _make_test_pdf_bytes(2)
    images = vl_client.render_pages_to_b64(pdf_bytes, [0, 1], scale=1.0)
    assert len(images) == 2
    for b64 in images:
        raw = base64.b64decode(b64)
        # PNG signature
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_pages_to_b64_max_pixels_limits_size():
    """max_pixels 上限会触发缩小渲染。"""
    pdf_bytes = _make_test_pdf_bytes(1)
    # 不限：scale=4 → 800×1200 = 96 万像素
    big = vl_client.render_pages_to_b64(pdf_bytes, [0], scale=4.0)
    big_raw = base64.b64decode(big[0])
    # 限：max_pixels=10000 → 强制缩小
    small = vl_client.render_pages_to_b64(
        pdf_bytes, [0], scale=4.0, max_pixels=10_000
    )
    small_raw = base64.b64decode(small[0])
    assert len(small_raw) < len(big_raw)


def test_render_hires_returns_b64():
    pdf_bytes = _make_test_pdf_bytes(1)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        b64 = vl_client.render_hires(doc, 0, scale=1.0)
        raw = base64.b64decode(b64)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        doc.close()
```

- [ ] **Step 2：运行测试确认 3 条都失败**

Run: `uv run pytest tests/test_vl_client.py::test_render_pages_to_b64_returns_png_b64 tests/test_vl_client.py::test_render_pages_to_b64_max_pixels_limits_size tests/test_vl_client.py::test_render_hires_returns_b64 -v`
Expected: 3 条 FAIL（AttributeError）。

- [ ] **Step 3：在 `utils/vl_client.py` 加渲染工具**

在文件顶端 `from utils.config import get_config` 之后加 import：
```python
import base64
import io

import fitz
from PIL import Image
```

在文件末尾追加：
```python
def _compute_safe_scale(
    base_w: float, base_h: float, target_scale: float, max_pixels: int | None
) -> float:
    """如果 target_scale 渲染会超 max_pixels，按比例降到刚好不超。"""
    if max_pixels is None:
        return target_scale
    target_pixels = (base_w * target_scale) * (base_h * target_scale)
    if target_pixels <= max_pixels:
        return target_scale
    return (max_pixels / (base_w * base_h)) ** 0.5


def render_pages_to_b64(
    file_bytes: bytes,
    pages: list[int],
    *,
    scale: float = 2.0,
    max_pixels: int | None = None,
    jpeg_quality: int | None = None,
) -> list[str]:
    """渲染指定页为 base64 字符串列表。

    - jpeg_quality=None：输出 PNG（推荐用于含文字 PDF）
    - jpeg_quality 给数值：输出 JPEG（仅彩色扫描件用，省体积）
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        results: list[str] = []
        for page_idx in pages:
            page = doc.load_page(page_idx)
            rect = page.rect
            actual_scale = _compute_safe_scale(rect.width, rect.height, scale, max_pixels)
            pix = page.get_pixmap(matrix=fitz.Matrix(actual_scale, actual_scale))

            if jpeg_quality is None:
                img_bytes = pix.tobytes("png")
            else:
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                img_bytes = buf.getvalue()

            results.append(base64.b64encode(img_bytes).decode("ascii"))
        return results
    finally:
        doc.close()


def render_thumbnail(doc: fitz.Document, page_idx: int, *, scale: float = 0.75) -> Image.Image:
    """渲染单页为低清 PIL Image（vl_locate 第一轮用）。"""
    page = doc.load_page(page_idx)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_hires(
    doc: fitz.Document,
    page_idx: int,
    *,
    scale: float = 2.0,
    max_pixels: int | None = None,
) -> str:
    """渲染单页为高清 base64 PNG（vl_locate 第二轮 / 通用高清场景）。"""
    page = doc.load_page(page_idx)
    rect = page.rect
    actual_scale = _compute_safe_scale(rect.width, rect.height, scale, max_pixels)
    pix = page.get_pixmap(matrix=fitz.Matrix(actual_scale, actual_scale))
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def make_grid_image(
    images: list[Image.Image], *, cols: int = 3, padding: int = 30
) -> str:
    """把多张缩略图拼成 cols 列的网格 PNG，行间留 padding 白边，返回 base64。"""
    if not images:
        return ""
    rows_count = (len(images) + cols - 1) // cols
    w = max(img.width for img in images)
    h = max(img.height for img in images)
    grid = Image.new("RGB", (cols * w, rows_count * (h + padding)), "white")
    for i, img in enumerate(images):
        row, col = divmod(i, cols)
        grid.paste(img, (col * w, row * (h + padding) + padding))
    buf = io.BytesIO()
    grid.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")
```

- [ ] **Step 4：运行测试全过**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: 8 条 PASS。

- [ ] **Step 5：补 `make_grid_image` 测试**

在 `tests/test_vl_client.py` 追加：
```python
def test_make_grid_image_empty():
    assert vl_client.make_grid_image([]) == ""


def test_make_grid_image_single():
    img = Image.new("RGB", (50, 60), "red")
    b64 = vl_client.make_grid_image([img], cols=3)
    assert b64
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_make_grid_image_multi_pages_layout():
    """4 张图、3 列 → 应该是 2 行布局。"""
    imgs = [Image.new("RGB", (50, 60), "red") for _ in range(4)]
    b64 = vl_client.make_grid_image(imgs, cols=3, padding=10)
    raw = base64.b64decode(b64)
    grid = Image.open(io.BytesIO(raw))
    # 3 列宽度 = 3*50；2 行高度 = 2*(60+10)
    assert grid.size == (150, 140)
```

最上方 `import` 区域加 `from PIL import Image`。

- [ ] **Step 6：运行测试**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: 11 条全 PASS。

- [ ] **Step 7：commit**

```bash
git add utils/vl_client.py tests/test_vl_client.py
git commit -m "feat(vl_client): add PDF/grid rendering utils with safe pixel cap"
```

---

## Task 5：vl_client — vl_chat HTTP 调用（TDD）

**Files:**
- Modify: `utils/vl_client.py`
- Modify: `tests/test_vl_client.py`

参照 `utils/llm_client.py:chat_completion` 的 httpx 调用 + 重试模式，加全局 semaphore。

- [ ] **Step 1：定义自定义异常**

在 `utils/vl_client.py` import 区域之后、函数之前加：
```python
class VLAPIError(RuntimeError):
    """VL API 调用最终失败（重试耗尽 / 4xx 非 429）。"""
```

- [ ] **Step 2：写 `vl_chat` 测试 — 成功路径**

在 `tests/test_vl_client.py` 末尾追加：
```python
class _MockResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            req = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError("err", request=req, response=httpx.Response(self.status_code))

    def json(self):
        return self._json


@pytest.fixture
def reset_vl_semaphore(monkeypatch):
    """每个测试前重置 vl_client 单例（client + semaphore）。"""
    monkeypatch.setattr(vl_client, "_global_sem", None)
    yield


async def test_vl_chat_success(monkeypatch, reset_vl_semaphore):
    captured = {}

    async def fake_post(self, url, json, headers):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _MockResponse(
            200,
            {"choices": [{"message": {"content": "hello"}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        )

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    resp = await vl_client.vl_chat(messages)
    assert resp["choices"][0]["message"]["content"] == "hello"
    assert resp["usage"]["total_tokens"] == 15
    # URL 应该是 base_url + /chat/completions
    assert captured["url"].endswith("/chat/completions")
```

- [ ] **Step 3：运行测试确认 FAIL**

Run: `uv run pytest tests/test_vl_client.py::test_vl_chat_success -v`
Expected: FAIL（AttributeError 或 ImportError）。

- [ ] **Step 4：实现 `vl_chat`**

在 `utils/vl_client.py` 顶端 import 区域追加：
```python
import asyncio
from typing import Any, Optional

import httpx
from loguru import logger
```

在文件中部加全局信号量与客户端：
```python
_global_sem: asyncio.Semaphore | None = None


def _get_global_sem() -> asyncio.Semaphore:
    global _global_sem
    if _global_sem is None:
        cfg = get_config().vl_model
        _global_sem = asyncio.Semaphore(cfg.global_max_concurrency)
    return _global_sem


async def vl_chat(
    messages: list[dict[str, Any]],
    *,
    max_tokens: Optional[int] = None,
    extra_body: Optional[dict[str, Any]] = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """调用 VL 模型的 OpenAI 兼容 chat/completions 接口。

    返回原始 JSON dict，调用方自己解 choices/usage。

    重试：3 次指数退避（1s/2s/4s）；4xx（除 429）直接抛 VLAPIError。
    全局并发限：vl_model.global_max_concurrency。
    """
    cfg = get_config().vl_model

    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": max_tokens or cfg.max_tokens,
    }
    body_extras = {"chat_template_kwargs": {"enable_thinking": cfg.enable_thinking}}
    if extra_body:
        body_extras.update(extra_body)
    payload.update(body_extras)

    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"

    sem = _get_global_sem()
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with sem:
                async with httpx.AsyncClient(timeout=cfg.timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else None
            if status is not None and 400 <= status < 500 and status != 429:
                raise VLAPIError(f"VL API {status}: {e}") from e
            last_exc = e
            if attempt + 1 == max_retries:
                raise VLAPIError(f"VL API 重试 {max_retries} 次仍失败: {e}") from e
            wait = 2 ** attempt
            logger.warning("vl_chat HTTP {} 第 {}/{} 次重试，{}s 后", status, attempt + 1, max_retries, wait)
            await asyncio.sleep(wait)
        except httpx.RequestError as e:
            last_exc = e
            if attempt + 1 == max_retries:
                raise VLAPIError(f"VL API 网络错误重试 {max_retries} 次: {e}") from e
            wait = 2 ** attempt
            logger.warning("vl_chat RequestError 第 {}/{} 次重试，{}s 后: {}", attempt + 1, max_retries, wait, e)
            await asyncio.sleep(wait)

    raise VLAPIError(f"vl_chat 重试耗尽: {last_exc}")
```

- [ ] **Step 5：运行测试确认通过**

Run: `uv run pytest tests/test_vl_client.py::test_vl_chat_success -v`
Expected: PASS。

- [ ] **Step 6：写 4xx 失败测试**

```python
async def test_vl_chat_4xx_raises_immediately(monkeypatch, reset_vl_semaphore):
    call_count = 0

    async def fake_post(self, url, json, headers):
        nonlocal call_count
        call_count += 1
        return _MockResponse(403)

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(vl_client.VLAPIError):
        await vl_client.vl_chat([{"role": "user", "content": "x"}])
    # 4xx 不应该重试
    assert call_count == 1


async def test_vl_chat_5xx_retries_then_fails(monkeypatch, reset_vl_semaphore):
    call_count = 0

    async def fake_post(self, url, json, headers):
        nonlocal call_count
        call_count += 1
        return _MockResponse(500)

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", lambda *a, **kw: asyncio.sleep(0))  # 跳过实际等待

    with pytest.raises(vl_client.VLAPIError):
        await vl_client.vl_chat([{"role": "user", "content": "x"}], max_retries=3)
    assert call_count == 3
```

- [ ] **Step 7：跑全部测试**

Run: `uv run pytest tests/test_vl_client.py -v`
Expected: 全部 PASS。

- [ ] **Step 8：commit**

```bash
git add utils/vl_client.py tests/test_vl_client.py
git commit -m "feat(vl_client): add vl_chat with retry, 4xx fast-fail, global semaphore"
```

---

## Task 6：vl_service _common 与 _defaults

**Files:**
- Create: `service/vl_service/__init__.py`（先占位空文件）
- Create: `service/vl_service/_common.py`
- Create: `service/vl_service/_defaults.py`
- Test: `tests/test_vl_service.py`

- [ ] **Step 1：写 `parse_vl_json_response` 测试**

创建 `tests/test_vl_service.py`：
```python
"""service/vl_service 包测试。"""

from __future__ import annotations

import pytest

from service.vl_service import _common


def test_parse_vl_json_response_clean_json():
    s = '{"value": "5000万", "reason": "见第3页"}'
    v, r = _common.parse_vl_json_response(s)
    assert v == "5000万"
    assert r == "见第3页"


def test_parse_vl_json_response_markdown_fence():
    s = '```json\n{"value": "abc", "reason": "ok"}\n```'
    v, r = _common.parse_vl_json_response(s)
    assert v == "abc"
    assert r == "ok"


def test_parse_vl_json_response_with_think_tag():
    s = '<think>let me think</think>\n{"value": "X", "reason": "Y"}'
    v, r = _common.parse_vl_json_response(s)
    assert v == "X"
    assert r == "Y"


def test_parse_vl_json_response_value_is_list():
    s = '{"value": ["a", "b"], "reason": "two items"}'
    v, r = _common.parse_vl_json_response(s)
    # list 转 JSON 字符串
    assert v == '["a", "b"]'
    assert r == "two items"


def test_parse_vl_json_response_fallback_to_raw():
    s = "纯文本，无法解析为 JSON"
    v, r = _common.parse_vl_json_response(s)
    assert v == s
    assert r == ""


def test_strip_think_tags():
    s = "before<think>noise</think>after<think>more</think>end"
    assert _common.strip_think_tags(s) == "beforeafterend"


def test_build_image_messages_text_only():
    msgs = _common.build_image_messages(prompt="hello", b64_images=[], system_prompt=None)
    assert msgs == [{"role": "user", "content": "hello"}]


def test_build_image_messages_with_images_and_system():
    msgs = _common.build_image_messages(
        prompt="describe", b64_images=["B64A", "B64B"], system_prompt="be precise"
    )
    assert msgs[0] == {"role": "system", "content": "be precise"}
    assert msgs[1]["role"] == "user"
    content = msgs[1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert "data:image/png;base64,B64A" in content[0]["image_url"]["url"]
    assert content[1]["type"] == "image_url"
    assert content[2] == {"type": "text", "text": "describe"}
```

- [ ] **Step 2：运行测试确认 FAIL**

Run: `uv run pytest tests/test_vl_service.py -v`
Expected: ImportError（`No module named 'service.vl_service'`）。

- [ ] **Step 3：建包结构**

Run: `mkdir -p service/vl_service`

创建 `service/vl_service/__init__.py`（空文件，等 Task 9 再加 re-export）。

创建 `service/vl_service/_defaults.py`：
```python
"""VL 抽取方法的内置 prompt 模板默认值。

来源：docs/VL端到端抽取方法.md §5（vl_progressive 每批 prompt） 与 §6（vl_locate 定位 prompt）
。

用户可在 vl_config 里通过 batch_prompt_template / locate_prompt_template 覆盖。
"""

from __future__ import annotations


# vl_progressive 每批 prompt 模板。占位符：
#   {history}, {field_hints}, {page_label}, {total_pages}
DEFAULT_BATCH_PROMPT = (
    "{history}"
    "你正在逐页阅读一份文档，需要关注以下信息：{field_hints}\n\n"
    "当前是{page_label}（共{total_pages}页）。\n"
    "如果当前页包含上述相关信息，请输出精简摘要（保留关键数字、名称、金额等）。\n"
    "如果当前页无相关信息（如封面、目录、说明性文字），请仅输出\"无相关信息\"。"
)


# vl_locate 第一轮定位 prompt 模板。占位符：
#   {field_hints}, {page_labels}, {position_map}, {grid_rows}, {grid_cols}
DEFAULT_LOCATE_PROMPT = (
    "这张图片是一份文档的缩略图网格（{grid_rows}行×{grid_cols}列），"
    "包含第 {page_labels} 页。\n"
    "位置对应关系：{position_map}\n\n"
    "请判断哪些页面包含以下信息：{field_hints}\n\n"
    "选择标准——选择以下类型的页面：\n"
    "1. 封面/首页（包含企业名称的标题页）\n"
    "2. 正式报表页（资产负债表、利润表、现金流量表等，以完整表格形式呈现）\n"
    "3. 协议/合同的关键条款页（金额、签署方等核心条款）\n"
    "4. 包含汇总数据的表格页（如有明显的数字表格且与所需信息直接相关）\n\n"
    "不要选择：纯文字附注段落、审计意见页、目录页、空白页。\n\n"
    "注意：只能从 [{page_labels}] 中选择，不要返回其他页码。\n"
    "请只返回JSON格式：{{\"found_pages\": [页码数字列表], \"reason\": \"简要说明\"}}\n"
    "如果这几页都不包含相关信息，返回：{{\"found_pages\": [], \"reason\": \"无相关内容\"}}"
)


DEFAULT_MAX_PIXELS = 4_000_000
```

注意 `{{` `}}` 转义（`str.format` 渲染时会输出单个 `{` `}`）。

创建 `service/vl_service/_common.py`：
```python
"""vl_service 内部共享工具。"""

from __future__ import annotations

import json
import re
from typing import Any


_THINK_PATTERN = re.compile(r"<think>[\s\S]*?</think>", re.DOTALL)


def strip_think_tags(text: str) -> str:
    """去掉 Qwen 思考类模型输出的 <think>...</think> 块。"""
    return _THINK_PATTERN.sub("", text).strip()


def parse_vl_json_response(response: str) -> tuple[str, str]:
    """解析 VL 输出为 (value, reason)。

    解析顺序：
    1. 剥 <think> 标签
    2. 尝试从 ```json ... ``` 围栏取 JSON
    3. 尝试 json.loads 整段
    4. 兜底正则找 {"value":...} 子串
    5. 解析失败 → (raw_text, "")
    """
    text = strip_think_tags(response)

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return _extract_value_reason(data)
        except json.JSONDecodeError:
            pass

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _extract_value_reason(data)
    except json.JSONDecodeError:
        pass

    obj = re.search(r"\{[^{}]*\"value\"[^{}]*\}", text, re.DOTALL)
    if obj:
        try:
            data = json.loads(obj.group())
            return _extract_value_reason(data)
        except json.JSONDecodeError:
            pass

    return text.strip(), ""


def _extract_value_reason(data: dict) -> tuple[str, str]:
    raw_value = data.get("value", "")
    if isinstance(raw_value, (list, dict)):
        value = json.dumps(raw_value, ensure_ascii=False)
    else:
        value = str(raw_value).strip()
    reason = str(data.get("reason", "")).strip()
    return value, reason


def build_image_messages(
    *,
    prompt: str,
    b64_images: list[str],
    system_prompt: str | None,
    image_mime: str = "image/png",
) -> list[dict[str, Any]]:
    """构建 OpenAI 兼容的多图消息体。

    - 无图：text-only message
    - 有图：图片块在前，text 在最后
    - system_prompt 非空时插入 system 消息
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if not b64_images:
        messages.append({"role": "user", "content": prompt})
        return messages

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b}"}}
        for b in b64_images
    ]
    content.append({"type": "text", "text": prompt})
    messages.append({"role": "user", "content": content})
    return messages
```

- [ ] **Step 4：运行 _common 测试全过**

Run: `uv run pytest tests/test_vl_service.py -v`
Expected: 8 条 PASS。

- [ ] **Step 5：commit**

```bash
git add service/vl_service/__init__.py service/vl_service/_common.py service/vl_service/_defaults.py tests/test_vl_service.py
git commit -m "feat(vl_service): add _common helpers and default prompt templates"
```

---

## Task 7：vl_service.model — 全量模式实现（TDD）

**Files:**
- Create: `service/vl_service/model.py`
- Modify: `tests/test_vl_service.py`

- [ ] **Step 1：写 `vl_model_extract` 测试**

在 `tests/test_vl_service.py` 末尾追加：
```python
import fitz

from service.vl_service import model as vl_model_module


def _make_pdf_bytes(num_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), f"Page {i + 1}", fontsize=24)
    return doc.tobytes()


async def test_vl_model_extract_success(monkeypatch):
    captured = {}

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        captured["messages"] = messages
        return {
            "choices": [{"message": {"content": '{"value": "abc", "reason": "ok"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(3)
    value, reason, refs = await vl_model_module.vl_model_extract(
        pdf,
        vl_extract_prompt="提取金额，输出 JSON {value, reason}",
        vl_system_prompt=None,
        page_range="1-2",
        max_pixels=200_000,
    )

    assert value == "abc"
    assert reason == "ok"
    assert refs["method"] == "vl_model"
    assert refs["total_pages"] == 3
    assert refs["key_pages"] == [1, 2]  # 1-indexed
    assert refs["vl_total_tokens"] == 15
    # messages 应该有 2 张图（page_range "1-2"）+ 1 段文字
    user_msg = captured["messages"][0]
    assert user_msg["role"] == "user"
    image_blocks = [c for c in user_msg["content"] if c["type"] == "image_url"]
    text_blocks = [c for c in user_msg["content"] if c["type"] == "text"]
    assert len(image_blocks) == 2
    assert len(text_blocks) == 1


async def test_vl_model_extract_empty_pdf(monkeypatch):
    """0 页 PDF 应直接返回空，不调 vl_chat。"""
    called = False

    async def fake_vl_chat(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    # 用空 PDF（0 页），fitz.open() 不允许 0 页，构造一个最小 1 页然后传 page_range="" 来触发
    pdf = _make_pdf_bytes(1)
    value, reason, refs = await vl_model_module.vl_model_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        page_range="",  # 解析出空列表
    )
    assert value == ""
    assert reason == ""
    assert refs["method"] == "vl_model"
    assert refs["key_pages"] == []
    assert called is False
```

- [ ] **Step 2：运行测试确认 ImportError**

Run: `uv run pytest tests/test_vl_service.py::test_vl_model_extract_success -v`
Expected: ImportError（service.vl_service.model 不存在）。

- [ ] **Step 3：实现 `service/vl_service/model.py`**

```python
"""vl_model：标准全量 VL 抽取（指定页一次性塞给 VL）。"""

from __future__ import annotations

from typing import Any

from utils.vl_client import parse_page_range, render_pages_to_b64, vl_chat
import fitz

from service.vl_service._common import build_image_messages, parse_vl_json_response


async def vl_model_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    page_range: str = "all",
    max_pixels: int = 4_000_000,
) -> tuple[str, str, dict[str, Any]]:
    """VL 全量抽取：渲染 page_range 页 → 一次调 VL → 直接产 {value, reason}。

    Args:
        file_bytes: PDF 二进制。
        vl_extract_prompt: 用户配置的最终提示词，必须要求 VL 输出 {value, reason}。
        vl_system_prompt: 可选系统提示。
        page_range: "all" / "1-3,5" 等。
        max_pixels: 单图像素上限。

    Returns:
        (value, reason, source_refs) — source_refs 是 {method, total_pages, key_pages, vl_total_tokens}。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    pages_0idx = parse_page_range(page_range, total_pages)
    refs: dict[str, Any] = {
        "method": "vl_model",
        "total_pages": total_pages,
        "key_pages": [p + 1 for p in pages_0idx],
        "vl_total_tokens": 0,
    }

    if not pages_0idx:
        return "", "", refs

    b64_images = render_pages_to_b64(file_bytes, pages_0idx, scale=2.0, max_pixels=max_pixels)

    messages = build_image_messages(
        prompt=vl_extract_prompt,
        b64_images=b64_images,
        system_prompt=vl_system_prompt,
    )

    resp = await vl_chat(messages)
    raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    usage = resp.get("usage") or {}
    refs["vl_total_tokens"] = usage.get("total_tokens", 0)

    value, reason = parse_vl_json_response(raw)
    return value, reason, refs
```

- [ ] **Step 4：运行测试确认通过**

Run: `uv run pytest tests/test_vl_service.py -v`
Expected: 全 PASS。

- [ ] **Step 5：commit**

```bash
git add service/vl_service/model.py tests/test_vl_service.py
git commit -m "feat(vl_service): implement vl_model_extract"
```

---

## Task 8：vl_service.progressive — 逐批扫描 + 文本聚合（TDD）

**Files:**
- Create: `service/vl_service/progressive.py`
- Modify: `tests/test_vl_service.py`

- [ ] **Step 1：写多场景测试**

在 `tests/test_vl_service.py` 末尾追加：
```python
from service.vl_service import progressive as vl_progressive_module


async def test_vl_progressive_extract_filters_no_info_batches(monkeypatch):
    """前 20 字符含'无相关信息'的批被丢弃，不进入 history。"""
    call_log = []

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        call_log.append(messages)
        idx = len(call_log)
        if idx == 1:
            content = "第1页：投资金额 5000 万元"
        elif idx == 2:
            content = "无相关信息"
        elif idx == 3:
            content = "第3页：股东 张三"
        else:  # 最后聚合
            content = '{"value": "5000万", "reason": "第1页 + 第3页累积"}'
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(3)
    value, reason, refs = await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="基于累积信息提取，返回 {value, reason}",
        vl_system_prompt=None,
        field_hints="投资金额、股东",
        batch_size=1,
    )

    # 4 次调用 = 3 批 + 1 次最终聚合
    assert len(call_log) == 4
    assert value == "5000万"
    assert reason == "第1页 + 第3页累积"
    assert refs["method"] == "vl_progressive"
    assert refs["total_pages"] == 3
    assert refs["key_pages"] is None
    assert refs["batches_with_info"] == 2  # 第2页被丢


async def test_vl_progressive_extract_progress_callback(monkeypatch):
    progress_events = []

    async def fake_vl_chat(messages, **kw):
        idx = len(progress_events) + 1
        if idx <= 2:
            content = f"第{idx}页有信息"
        else:
            content = '{"value": "x", "reason": "y"}'
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 10},
        }

    async def cb(evt):
        progress_events.append(evt)

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    pdf = _make_pdf_bytes(2)
    await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="hint",
        batch_size=1,
        progress_cb=cb,
    )
    # 2 批 → 2 次 batch 进度事件
    assert len(progress_events) == 2
    assert progress_events[0]["page_label"] == "第1页"
    assert progress_events[0]["has_info"] is True


async def test_vl_progressive_extract_custom_batch_template(monkeypatch):
    """自定义模板会替代默认；占位符必须全到位（在调用方校验，这里假设已通过）。"""
    captured_prompts = []

    async def fake_vl_chat(messages, **kw):
        # 提取 user 消息里的 text
        for c in messages[-1]["content"] if isinstance(messages[-1]["content"], list) else []:
            if c.get("type") == "text":
                captured_prompts.append(c["text"])
        idx = len(captured_prompts)
        return {
            "choices": [{"message": {"content": "无相关信息" if idx == 1 else '{"value":"a","reason":"b"}'}}],
            "usage": {"total_tokens": 5},
        }

    monkeypatch.setattr("service.vl_service.progressive.vl_chat", fake_vl_chat)

    custom = "CUSTOM hints={field_hints} label={page_label} total={total_pages} hist={history}"
    pdf = _make_pdf_bytes(1)
    await vl_progressive_module.vl_progressive_extract(
        pdf,
        vl_extract_prompt="final",
        vl_system_prompt=None,
        field_hints="X",
        batch_size=1,
        batch_prompt_template=custom,
    )
    # 第一条 prompt 应该是渲染后的 custom（不含历史）
    assert captured_prompts[0].startswith("CUSTOM hints=X label=第1页 total=1 hist=")
```

- [ ] **Step 2：运行测试确认 FAIL**

Run: `uv run pytest tests/test_vl_service.py::test_vl_progressive_extract_filters_no_info_batches -v`
Expected: ImportError。

- [ ] **Step 3：实现 `service/vl_service/progressive.py`**

```python
"""vl_progressive：逐批扫描 + 伪历史累积 + 最终文本聚合。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from utils.vl_client import render_pages_to_b64, vl_chat
import fitz

from service.vl_service._common import build_image_messages, parse_vl_json_response
from service.vl_service._defaults import DEFAULT_BATCH_PROMPT


_NO_INFO_KEYWORD = "无相关信息"


async def vl_progressive_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    field_hints: str,
    batch_size: int = 2,
    max_pixels: int = 4_000_000,
    batch_prompt_template: str | None = None,
    progress_cb: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """VL 逐批扫描 + 最后一次文本聚合。

    每批：渲染 batch_size 页，VL 自判相关性，输出摘要或"无相关信息"。
    最后：用纯文本（无图）调一次 VL，把累积摘要 + vl_extract_prompt 合并产出 {value, reason}。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    refs: dict[str, Any] = {
        "method": "vl_progressive",
        "total_pages": total_pages,
        "key_pages": None,
        "batches_with_info": 0,
        "vl_total_tokens": 0,
    }

    if total_pages == 0:
        return "", "", refs

    template = batch_prompt_template or DEFAULT_BATCH_PROMPT
    accumulated: list[str] = []

    for batch_start in range(0, total_pages, batch_size):
        batch_pages = list(range(batch_start, min(batch_start + batch_size, total_pages)))
        b64_images = render_pages_to_b64(
            file_bytes, batch_pages, scale=2.0, max_pixels=max_pixels
        )

        page_label = (
            f"第{batch_pages[0] + 1}页"
            if len(batch_pages) == 1
            else f"第{batch_pages[0] + 1}-{batch_pages[-1] + 1}页"
        )

        history = (
            "【已扫描页面的累积信息】：\n" + "\n".join(accumulated) + "\n\n"
            if accumulated
            else ""
        )

        prompt = template.format(
            history=history,
            field_hints=field_hints,
            page_label=page_label,
            total_pages=total_pages,
        )

        messages = build_image_messages(
            prompt=prompt, b64_images=b64_images, system_prompt=vl_system_prompt
        )

        resp = await vl_chat(messages)
        raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        usage = resp.get("usage") or {}
        refs["vl_total_tokens"] += usage.get("total_tokens", 0)

        has_info = _NO_INFO_KEYWORD not in raw[:20]
        if has_info and raw:
            accumulated.append(f"- {page_label}：{raw}")
            refs["batches_with_info"] += 1

        if progress_cb:
            await progress_cb({
                "page_label": page_label,
                "has_info": has_info,
                "summary_preview": raw[:100] if has_info else "",
                "batch_index": batch_start // batch_size,
                "total_batches": (total_pages + batch_size - 1) // batch_size,
            })

    # 最终聚合（文本无图）
    if not accumulated:
        return "", "文档全程无相关信息", refs

    accumulated_text = "\n".join(accumulated)
    final_prompt = (
        f"以下是逐页扫描得到的累积信息：\n{accumulated_text}\n\n{vl_extract_prompt}"
    )
    final_messages = build_image_messages(
        prompt=final_prompt, b64_images=[], system_prompt=vl_system_prompt
    )
    resp = await vl_chat(final_messages)
    raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    usage = resp.get("usage") or {}
    refs["vl_total_tokens"] += usage.get("total_tokens", 0)

    value, reason = parse_vl_json_response(raw)
    return value, reason, refs
```

- [ ] **Step 4：运行测试**

Run: `uv run pytest tests/test_vl_service.py -v`
Expected: 全 PASS。

- [ ] **Step 5：commit**

```bash
git add service/vl_service/progressive.py tests/test_vl_service.py
git commit -m "feat(vl_service): implement vl_progressive_extract"
```

---

## Task 9：vl_service.locate — 缩略图并行定位 + 高清提取（TDD）

**Files:**
- Create: `service/vl_service/locate.py`
- Modify: `tests/test_vl_service.py`

- [ ] **Step 1：写测试**

在 `tests/test_vl_service.py` 末尾追加：
```python
from service.vl_service import locate as vl_locate_module


async def test_vl_locate_extract_filters_hallucinated_pages(monkeypatch):
    """LLM 返回了不在网格范围里的幻觉页码 → 必须被过滤。"""
    pdf = _make_pdf_bytes(6)

    async def fake_vl_chat(messages, *, max_tokens=None, extra_body=None, max_retries=3):
        # 简单根据消息里有没有 "缩略图网格" 区分两轮
        is_locate = False
        if isinstance(messages[-1]["content"], list):
            for c in messages[-1]["content"]:
                if c.get("type") == "text" and "缩略图网格" in c.get("text", ""):
                    is_locate = True
                    break
        if is_locate:
            # 故意返回一个超界页码 99
            return {
                "choices": [{"message": {"content": '{"found_pages": [2, 99], "reason": "x"}'}}],
                "usage": {"total_tokens": 10},
            }
        # 第二轮 extract
        return {
            "choices": [{"message": {"content": '{"value": "FOUND", "reason": "page 2"}'}}],
            "usage": {"total_tokens": 20},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    value, reason, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="基于关键页提取，返回 {value, reason}",
        vl_system_prompt=None,
        field_hints="关键信息",
        grid_pages=6,
    )

    assert value == "FOUND"
    assert refs["method"] == "vl_locate"
    assert refs["total_pages"] == 6
    # 99 被过滤，只剩 2
    assert refs["key_pages"] == [2]


async def test_vl_locate_extract_fallback_when_no_hits(monkeypatch):
    """第一轮一页未命中 → 回退前 fallback_pages 页。"""
    pdf = _make_pdf_bytes(5)

    async def fake_vl_chat(messages, **kw):
        is_locate = False
        if isinstance(messages[-1]["content"], list):
            for c in messages[-1]["content"]:
                if c.get("type") == "text" and "缩略图网格" in c.get("text", ""):
                    is_locate = True
                    break
        if is_locate:
            return {
                "choices": [{"message": {"content": '{"found_pages": [], "reason": "无"}'}}],
                "usage": {"total_tokens": 5},
            }
        return {
            "choices": [{"message": {"content": '{"value": "fallback", "reason": "前 N 页"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        grid_pages=6,
        fallback_pages=3,
    )
    assert refs["key_pages"] == [1, 2, 3]


async def test_vl_locate_extract_truncates_to_limit(monkeypatch):
    """命中超 key_pages_limit 截断，未达阈值全保留。"""
    pdf = _make_pdf_bytes(12)

    async def fake_vl_chat(messages, **kw):
        is_locate = False
        if isinstance(messages[-1]["content"], list):
            for c in messages[-1]["content"]:
                if c.get("type") == "text" and "缩略图网格" in c.get("text", ""):
                    is_locate = True
                    break
        if is_locate:
            # 12 页 grid_pages=6 → 2 个网格；每个网格命中前 4 页
            text = ""
            for c in messages[-1]["content"]:
                if c.get("type") == "text":
                    text = c["text"]
                    break
            # 第一个网格页码 1-6；第二个 7-12
            if "第 1, 2, 3, 4, 5, 6" in text:
                return {"choices": [{"message": {"content": '{"found_pages": [1,2,3,4]}'}}], "usage": {"total_tokens": 5}}
            return {"choices": [{"message": {"content": '{"found_pages": [7,8,9,10]}'}}], "usage": {"total_tokens": 5}}
        return {
            "choices": [{"message": {"content": '{"value": "ok", "reason": "ok"}'}}],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("service.vl_service.locate.vl_chat", fake_vl_chat)

    _, _, refs = await vl_locate_module.vl_locate_extract(
        pdf,
        vl_extract_prompt="x",
        vl_system_prompt=None,
        field_hints="x",
        grid_pages=6,
        key_pages_limit=5,
    )
    # 8 命中 → 排序去重后取前 5
    assert refs["key_pages"] == [1, 2, 3, 4, 7]
```

- [ ] **Step 2：运行测试确认 FAIL**

Run: `uv run pytest tests/test_vl_service.py -v`
Expected: 3 条新测试 ImportError。

- [ ] **Step 3：实现 `service/vl_service/locate.py`**

```python
"""vl_locate：缩略图并行定位 + 关键页高清提取。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

from utils.vl_client import (
    make_grid_image,
    render_hires,
    render_thumbnail,
    vl_chat,
)
import fitz

from service.vl_service._common import (
    build_image_messages,
    parse_vl_json_response,
    strip_think_tags,
)
from service.vl_service._defaults import DEFAULT_LOCATE_PROMPT


async def vl_locate_extract(
    file_bytes: bytes,
    vl_extract_prompt: str,
    vl_system_prompt: str | None,
    *,
    field_hints: str,
    grid_pages: int = 6,
    grid_cols: int = 3,
    max_concurrent: int = 20,
    thumb_scale: float = 0.75,
    key_pages_limit: int = 6,
    fallback_pages: int = 3,
    max_pixels: int = 4_000_000,
    locate_prompt_template: str | None = None,
    progress_cb: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """两轮 VL 抽取：缩略图网格并行定位 → 关键页高清提取。"""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)

    refs: dict[str, Any] = {
        "method": "vl_locate",
        "total_pages": total_pages,
        "key_pages": [],
        "vl_total_tokens": 0,
    }

    if total_pages == 0:
        doc.close()
        return "", "", refs

    template = locate_prompt_template or DEFAULT_LOCATE_PROMPT
    grid_rows = (grid_pages + grid_cols - 1) // grid_cols

    grids: list[tuple[str, list[int]]] = []
    for batch_start in range(0, total_pages, grid_pages):
        page_indices = list(range(batch_start, min(batch_start + grid_pages, total_pages)))
        thumbnails = [render_thumbnail(doc, idx, scale=thumb_scale) for idx in page_indices]
        b64 = make_grid_image(thumbnails, cols=grid_cols)
        grids.append((b64, page_indices))

    total_grids = len(grids)
    sem = asyncio.Semaphore(max_concurrent)

    async def scan_grid(grid_idx: int, b64: str, page_indices: list[int]) -> tuple[list[int], int]:
        page_labels = ", ".join(str(idx + 1) for idx in page_indices)
        position_lines = []
        for i, idx in enumerate(page_indices):
            row, col = divmod(i, grid_cols)
            position_lines.append(f"第{row+1}行第{col+1}列=第{idx+1}页")
        position_map = "；".join(position_lines)

        prompt = template.format(
            field_hints=field_hints,
            page_labels=page_labels,
            position_map=position_map,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
        )

        messages = build_image_messages(
            prompt=prompt, b64_images=[b64], system_prompt=vl_system_prompt
        )

        try:
            async with sem:
                resp = await vl_chat(messages, max_tokens=512)
        except Exception:
            return [], 0

        raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        raw = strip_think_tags(raw)
        usage = resp.get("usage") or {}
        tokens = usage.get("total_tokens", 0)

        valid_set = {idx + 1 for idx in page_indices}
        found: list[int] = []
        try:
            s, e = raw.find("{"), raw.rfind("}")
            if s != -1 and e != -1:
                obj = json.loads(raw[s : e + 1])
                raw_pages = [int(p) for p in obj.get("found_pages", [])]
                found = [p for p in raw_pages if p in valid_set]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        if progress_cb:
            await progress_cb({
                "phase": "locate",
                "grid_idx": grid_idx + 1,
                "total_grids": total_grids,
                "page_labels": page_labels,
                "found_pages": found,
            })

        return [p - 1 for p in found], tokens

    tasks = [scan_grid(i, b64, pages) for i, (b64, pages) in enumerate(grids)]
    results = await asyncio.gather(*tasks)
    for found_0idx, tokens in results:
        refs["vl_total_tokens"] += tokens

    key_pages_0idx = sorted({p for found_0idx, _ in results for p in found_0idx if 0 <= p < total_pages})
    if len(key_pages_0idx) > key_pages_limit:
        key_pages_0idx = key_pages_0idx[:key_pages_limit]

    if not key_pages_0idx:
        key_pages_0idx = list(range(min(fallback_pages, total_pages)))

    refs["key_pages"] = [p + 1 for p in key_pages_0idx]

    if progress_cb:
        await progress_cb({
            "phase": "extract",
            "key_pages": refs["key_pages"],
        })

    # 第二轮：关键页高清提取
    b64_hires = [render_hires(doc, idx, scale=2.0, max_pixels=max_pixels) for idx in key_pages_0idx]
    doc.close()

    extract_messages = build_image_messages(
        prompt=vl_extract_prompt, b64_images=b64_hires, system_prompt=vl_system_prompt
    )
    resp = await vl_chat(extract_messages)
    raw = (resp.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    raw = strip_think_tags(raw)
    usage = resp.get("usage") or {}
    refs["vl_total_tokens"] += usage.get("total_tokens", 0)

    value, reason = parse_vl_json_response(raw)
    return value, reason, refs
```

- [ ] **Step 4：运行测试**

Run: `uv run pytest tests/test_vl_service.py -v`
Expected: 全 PASS。

- [ ] **Step 5：commit**

```bash
git add service/vl_service/locate.py tests/test_vl_service.py
git commit -m "feat(vl_service): implement vl_locate_extract with grid parallel"
```

---

## Task 10：vl_service 包 re-export

**Files:**
- Modify: `service/vl_service/__init__.py`

- [ ] **Step 1：导出三个公开函数**

```python
"""VL 端到端抽取方法包。

三种方法：
- vl_model_extract: 全量模式
- vl_progressive_extract: 逐批扫描
- vl_locate_extract: 缩略图定位 + 高清提取
"""

from service.vl_service.locate import vl_locate_extract
from service.vl_service.model import vl_model_extract
from service.vl_service.progressive import vl_progressive_extract

__all__ = [
    "vl_model_extract",
    "vl_progressive_extract",
    "vl_locate_extract",
]
```

- [ ] **Step 2：smoke import**

Run: `python -c "from service import vl_service; print(vl_service.vl_model_extract, vl_service.vl_progressive_extract, vl_service.vl_locate_extract)"`
Expected: 输出 3 个函数对象。

- [ ] **Step 3：commit**

```bash
git add service/vl_service/__init__.py
git commit -m "feat(vl_service): re-export public API"
```

---

## Task 11：数据模型 — 加列与 enum 扩展

**Files:**
- Modify: `model/tables.py:121-160`

- [ ] **Step 1：在 `model/tables.py` 扩展 `source_type_enum`**

定位到 `ExtractionField` 类（约 121-159 行），把 `source_type` 列：
```python
source_type: Mapped[str] = mapped_column(
    Enum("table", "text", name="source_type_enum"), nullable=False
)
```
改成：
```python
source_type: Mapped[str] = mapped_column(
    Enum("table", "text", "vl", name="source_type_enum"), nullable=False
)
```

- [ ] **Step 2：在 `ExtractionField` 末尾追加 4 列（`__table_args__` 之前）**

```python
    # VL 类专用
    vl_method: Mapped[str | None] = mapped_column(
        Enum("vl_model", "vl_progressive", "vl_locate", name="vl_method_enum"),
        nullable=True,
    )
    vl_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vl_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    vl_extract_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 3：smoke 检查 import 不报错**

Run: `python -c "from model.tables import ExtractionField; print(ExtractionField.__table__.columns.keys())"`
Expected: 列表里包含 `vl_method`, `vl_config`, `vl_system_prompt`, `vl_extract_prompt`。

- [ ] **Step 4：commit**

```bash
git add model/tables.py
git commit -m "model: add 4 vl_* columns to extraction_field, extend source_type enum"
```

---

## Task 12：init_service 自动迁移 vl_* 列与 enum

**Files:**
- Modify: `service/init_service.py:46-110`

注意：现有 init 用 `ALTER TABLE ADD COLUMN` 自动补列；MySQL 的 ENUM 扩展也要单独 ALTER MODIFY。

- [ ] **Step 1：在 `migrations` 列表加新列**

在 `init_database()` 内 `migrations` 列表（约 57-63 行）追加：
```python
        ("extraction_field", "vl_method", "VARCHAR(32) NULL"),
        ("extraction_field", "vl_config", "JSON NULL"),
        ("extraction_field", "vl_system_prompt", "TEXT NULL"),
        ("extraction_field", "vl_extract_prompt", "TEXT NULL"),
```
注：MySQL 的 ENUM 在 ALTER ADD 阶段用 VARCHAR 兼容更稳；SQLAlchemy 模型层声明 ENUM 仅作类型提示，写入时 ORM 会按字符串走。

- [ ] **Step 2：在 `migrations` 后面新增 `source_type` enum 扩展逻辑**

在 `migrations` 循环结束后（约 75 行）、`index_migrations` 之前追加：
```python
        # source_type enum 扩展：旧值 ('table','text') → 新值 ('table','text','vl')
        result = await conn.execute(
            text(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'extraction_field' "
                "AND COLUMN_NAME = 'source_type'"
            )
        )
        col_type = (result.scalar() or "").lower()
        if "'vl'" not in col_type and col_type:
            await conn.execute(
                text(
                    "ALTER TABLE `extraction_field` "
                    "MODIFY COLUMN `source_type` ENUM('table','text','vl') NOT NULL"
                )
            )
            logger.info("已扩展 extraction_field.source_type 枚举：加入 'vl'")
```

- [ ] **Step 3：smoke 测试启动 init**

Run: `python -c "import asyncio; from service.init_service import init_database; asyncio.run(init_database())"`
Expected: 日志输出"已为 extraction_field 表添加 vl_method 列"等共 4 行；以及 source_type enum 扩展（如果之前已存在 extraction_field 表）；不报错。如果是干净环境（没建表），则只输出"数据库表检查完成"。

- [ ] **Step 4：commit**

```bash
git add service/init_service.py
git commit -m "init: auto-migrate vl_* columns and source_type enum"
```

---

## Task 13：Pydantic schemas 扩展

**Files:**
- Modify: `model/schemas.py:154-216`

- [ ] **Step 1：扩展 `SourceTypeEnum` + 新增 `VLMethodEnum`**

定位到 `SourceTypeEnum`（约 156-158 行），改为：
```python
class SourceTypeEnum(str, Enum):
    table = "table"
    text = "text"
    vl = "vl"
```

在 `SearchTypeEnum` 之后追加：
```python
class VLMethodEnum(str, Enum):
    vl_model = "vl_model"
    vl_progressive = "vl_progressive"
    vl_locate = "vl_locate"
```

- [ ] **Step 2：在 `ExtractionFieldCreate` 添加 4 个字段**

定位到 `ExtractionFieldCreate`（约 176-210 行），在 `text_extract_prompt` 之后追加（在两个 validator 之前）：
```python
    # VL 类
    vl_method: Optional[VLMethodEnum] = None
    vl_config: Optional[Dict[str, Any]] = None
    vl_system_prompt: Optional[str] = None
    vl_extract_prompt: Optional[str] = None
```

- [ ] **Step 3：在 `ExtractionFieldCreate` 末尾追加 vl 校验器**

在 `validate_table_prompt` validator 之后追加：
```python
    @field_validator("vl_method")
    @classmethod
    def validate_vl_method_required(cls, v, info):
        if info.data.get("source_type") == SourceTypeEnum.vl and not v:
            raise ValueError("source_type='vl' 时 vl_method 必填")
        return v

    @field_validator("vl_extract_prompt")
    @classmethod
    def validate_vl_extract_prompt(cls, v, info):
        if info.data.get("source_type") == SourceTypeEnum.vl:
            if not v:
                raise ValueError("source_type='vl' 时 vl_extract_prompt 必填")
            lower = v.lower()
            if "value" not in lower or "reason" not in lower:
                raise ValueError(
                    "vl_extract_prompt 必须包含 'value' 与 'reason' 关键字（大小写不敏感），"
                    "因为最终要求 VL 输出 {value, reason} JSON"
                )
        return v

    @field_validator("vl_config")
    @classmethod
    def validate_vl_config_templates(cls, v, info):
        if v is None:
            return v
        method = info.data.get("vl_method")
        if method == VLMethodEnum.vl_progressive:
            tpl = v.get("batch_prompt_template")
            if tpl:
                required = ["{field_hints}", "{page_label}", "{total_pages}", "{history}"]
                missing = [r for r in required if r not in tpl]
                if missing:
                    raise ValueError(f"batch_prompt_template 缺少占位符 {missing}")
        elif method == VLMethodEnum.vl_locate:
            tpl = v.get("locate_prompt_template")
            if tpl:
                required = ["{field_hints}", "{page_labels}", "{position_map}", "{grid_rows}", "{grid_cols}"]
                missing = [r for r in required if r not in tpl]
                if missing:
                    raise ValueError(f"locate_prompt_template 缺少占位符 {missing}")
        return v
```

- [ ] **Step 4：smoke 测试 schema 校验**

Run:
```bash
python -c "
from model.schemas import ExtractionFieldCreate
# 反例：source_type='vl' 但 vl_extract_prompt 缺 value 关键字
try:
    ExtractionFieldCreate(field_id='t1', field_name='x', source_type='vl', vl_method='vl_model', vl_extract_prompt='only reason here')
    print('FAIL: 应该抛错')
except Exception as e:
    print('OK:', str(e)[:60])

# 正例
f = ExtractionFieldCreate(field_id='t2', field_name='x', source_type='vl', vl_method='vl_model', vl_extract_prompt='请输出 value 和 reason')
print('OK:', f.vl_method)
"
```
Expected: 第一行 `OK: ...` 含"vl_extract_prompt 必须包含..."；第二行 `OK: vl_model`。

- [ ] **Step 5：commit**

```bash
git add model/schemas.py
git commit -m "schemas: add VLMethodEnum and vl_* fields with validators"
```

---

## Task 14：extract_vl_field 接入

**Files:**
- Modify: `service/extraction_service.py`

- [ ] **Step 1：在 `service/extraction_service.py` import 区域追加**

定位到顶部 import（约 1-21 行），追加：
```python
from service import vl_service
from utils import vl_client
```

- [ ] **Step 2：在 `extract_text_field` 之后、`run_extraction` 之前插入 `extract_vl_field`**

```python
async def extract_vl_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> Tuple[str, str, Optional[Dict]]:
    """VL 类字段提取：基于 PDF 视觉模型直接产出 {value, reason}。

    Returns:
        (extracted_value, reason, source_refs) 元组。source_refs 形如 {"_vl": {...}}。
    """
    pdf_file = vl_client.pdf_path(file_id)
    if not pdf_file.exists():
        return "", "PDF 原始文件不存在，无法 VL 抽取", None

    try:
        file_bytes = pdf_file.read_bytes()
    except OSError as e:
        return "", f"PDF 文件读取失败: {e}", None

    cfg = field.vl_config or {}
    method = field.vl_method
    default_max_pixels = get_config().vl_model.default_max_pixels

    try:
        if method == "vl_model":
            value, reason, refs = await vl_service.vl_model_extract(
                file_bytes,
                field.vl_extract_prompt or "",
                field.vl_system_prompt,
                page_range=cfg.get("page_range", "all"),
                max_pixels=cfg.get("max_pixels", default_max_pixels),
            )
        elif method == "vl_progressive":
            value, reason, refs = await vl_service.vl_progressive_extract(
                file_bytes,
                field.vl_extract_prompt or "",
                field.vl_system_prompt,
                field_hints=cfg.get("field_hints", ""),
                batch_size=cfg.get("batch_size", 2),
                max_pixels=cfg.get("max_pixels", default_max_pixels),
                batch_prompt_template=cfg.get("batch_prompt_template"),
            )
        elif method == "vl_locate":
            value, reason, refs = await vl_service.vl_locate_extract(
                file_bytes,
                field.vl_extract_prompt or "",
                field.vl_system_prompt,
                field_hints=cfg.get("field_hints", ""),
                grid_pages=cfg.get("grid_pages", 6),
                grid_cols=cfg.get("grid_cols", 3),
                max_concurrent=cfg.get("max_concurrent", 20),
                thumb_scale=cfg.get("thumb_scale", 0.75),
                key_pages_limit=cfg.get("key_pages_limit", 6),
                fallback_pages=cfg.get("fallback_pages", 3),
                max_pixels=cfg.get("max_pixels", default_max_pixels),
                locate_prompt_template=cfg.get("locate_prompt_template"),
            )
        else:
            return "", f"未知 vl_method={method}", None
    except Exception as e:
        logger.error("VL 抽取失败 file_id={} method={} error={}", file_id, method, e)
        return "", f"VL 抽取失败: {e}", None

    return value, reason, {"_vl": refs}
```

`get_config` 引入：在 import 区域加 `from utils.config import get_config`（如果还没有）。检查现有文件，约 17 行已有 `from utils.config import get_config`，无需重复。

- [ ] **Step 3：在 `run_extraction` 与 `run_extraction_stream` 的 dispatcher 加 vl 分支**

定位到 `run_extraction`（约 776-779 行）：
```python
if field.source_type == "table":
    extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
else:
    extracted_value, reason, source_refs = await extract_text_field(file_id, field, session)
```
改为：
```python
if field.source_type == "table":
    extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
elif field.source_type == "vl":
    extracted_value, reason, source_refs = await extract_vl_field(file_id, field, session)
else:
    extracted_value, reason, source_refs = await extract_text_field(file_id, field, session)
```

`run_extraction_stream` 内（约 908-911 行）同样替换。

- [ ] **Step 4：smoke import 检查**

Run: `python -c "from service.extraction_service import extract_vl_field, run_extraction; print('ok')"`
Expected: `ok`。

- [ ] **Step 5：commit**

```bash
git add service/extraction_service.py
git commit -m "feat(extraction): add extract_vl_field and dispatcher branch"
```

---

## Task 15：test_field_extraction_stream 加 vl 分支

**Files:**
- Modify: `service/extraction_service.py`（`test_field_extraction_stream` 函数，约 989 行起）

- [ ] **Step 1：在 `test_field_extraction_stream` 中 source_type 分支前插入 vl 分支**

定位到 `test_field_extraction_stream` 内 `# Step 1: 检索` 区域（约 1006 行），找到原 `if source_type == "table":` 分支（约 1011 行），在其前面插入：

```python
        # ── Step 1: 检索（VL 模式） ──────────────────────
        if source_type == "vl":
            pdf_file = vl_client.pdf_path(file_id)
            if not pdf_file.exists():
                yield {"event": "error", "data": {"step": "pdf_load", "message": "PDF 原始文件不存在"}}
                return
            file_bytes = pdf_file.read_bytes()
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            total_pages = len(doc)
            doc.close()

            yield {
                "event": "pdf_loaded",
                "data": {"total_pages": total_pages, "vl_method": field.vl_method},
            }

            # 调试流挂 progress_cb 把内部进度作为 SSE 事件 yield 出来
            cfg = field.vl_config or {}
            default_max_pixels = get_config().vl_model.default_max_pixels
            method = field.vl_method

            progress_queue: asyncio.Queue = asyncio.Queue()

            async def progress_cb(evt: dict):
                if "phase" in evt:
                    # vl_locate 的 phase=locate / phase=extract
                    await progress_queue.put({"event": f"locate_{evt['phase']}", "data": evt})
                else:
                    # vl_progressive 的批事件
                    await progress_queue.put({"event": "progressive_batch", "data": evt})

            async def run_vl():
                try:
                    if method == "vl_model":
                        return await vl_service.vl_model_extract(
                            file_bytes, field.vl_extract_prompt or "", field.vl_system_prompt,
                            page_range=cfg.get("page_range", "all"),
                            max_pixels=cfg.get("max_pixels", default_max_pixels),
                        )
                    elif method == "vl_progressive":
                        return await vl_service.vl_progressive_extract(
                            file_bytes, field.vl_extract_prompt or "", field.vl_system_prompt,
                            field_hints=cfg.get("field_hints", ""),
                            batch_size=cfg.get("batch_size", 2),
                            max_pixels=cfg.get("max_pixels", default_max_pixels),
                            batch_prompt_template=cfg.get("batch_prompt_template"),
                            progress_cb=progress_cb,
                        )
                    elif method == "vl_locate":
                        return await vl_service.vl_locate_extract(
                            file_bytes, field.vl_extract_prompt or "", field.vl_system_prompt,
                            field_hints=cfg.get("field_hints", ""),
                            grid_pages=cfg.get("grid_pages", 6),
                            grid_cols=cfg.get("grid_cols", 3),
                            max_concurrent=cfg.get("max_concurrent", 20),
                            thumb_scale=cfg.get("thumb_scale", 0.75),
                            key_pages_limit=cfg.get("key_pages_limit", 6),
                            fallback_pages=cfg.get("fallback_pages", 3),
                            max_pixels=cfg.get("max_pixels", default_max_pixels),
                            locate_prompt_template=cfg.get("locate_prompt_template"),
                            progress_cb=progress_cb,
                        )
                    else:
                        raise ValueError(f"未知 vl_method={method}")
                finally:
                    await progress_queue.put(None)  # 哨兵

            vl_task = asyncio.create_task(run_vl())

            # 边跑边 yield 进度事件
            while True:
                evt = await progress_queue.get()
                if evt is None:
                    break
                yield evt

            try:
                value, reason, refs = await vl_task
            except Exception as e:
                yield {"event": "error", "data": {"step": "vl_extract", "message": str(e)}}
                return

            yield {
                "event": "prompt",
                "data": {
                    "system_prompt": field.vl_system_prompt or "",
                    "user_prompt": field.vl_extract_prompt or "",
                },
            }
            yield {
                "event": "result",
                "data": {
                    "extracted_value": value,
                    "reason": reason,
                    "source_refs": {"_vl": refs},
                },
            }
            yield {"event": "done", "data": {}}
            return
```

文件顶部 import 区域追加（如果还没有）：
```python
import fitz  # 已经在 vl_service 里 import，这里 test_field_extraction_stream 单独需要
```

- [ ] **Step 2：smoke import 检查**

Run: `python -c "from service.extraction_service import test_field_extraction_stream; print('ok')"`
Expected: `ok`。

- [ ] **Step 3：commit**

```bash
git add service/extraction_service.py
git commit -m "feat(extraction): add vl branch to test_field_extraction_stream"
```

---

## Task 16：extraction_router /test 加 vl 分支

**Files:**
- Modify: `blue_print/extraction_router.py:177-265`、`281-305`

- [ ] **Step 1：在 `/test` endpoint 加 vl 分支**

定位 `test_extraction` 函数（约 161-265 行），在 `if field.source_type == "table":` 之后、`else:` 之前插入：

```python
        elif field.source_type == "vl":
            from service.extraction_service import extract_vl_field
            value, reason, refs = await extract_vl_field(file_id, field, db)
            search_results = [{
                "type": "vl_meta",
                "method": refs["_vl"]["method"] if refs else None,
                "key_pages": refs["_vl"].get("key_pages") if refs else None,
                "vl_total_tokens": refs["_vl"].get("vl_total_tokens") if refs else 0,
                "batches_with_info": refs["_vl"].get("batches_with_info") if refs else None,
            }] if refs else []
            extracted_value = value
            llm_input = field.vl_extract_prompt or ""
            llm_output = extracted_value
```

- [ ] **Step 2：在 mode 2（临时 config 构造）补 vl_* 字段透传**

定位 mode 2 的 `field = ExtractionField(...)` 构造（在 `test_extraction` 约 188-203 行 + `test_extraction_stream` 约 287-303 行），各加 4 个字段：
```python
            vl_method=config.get("vl_method"),
            vl_config=config.get("vl_config"),
            vl_system_prompt=config.get("vl_system_prompt"),
            vl_extract_prompt=config.get("vl_extract_prompt"),
```

- [ ] **Step 3：`list_fields` 与 `upsert_field` 透传 4 字段**

`list_fields`（约 37-69 行）在 `ExtractionFieldResponse(...)` 构造里加：
```python
                vl_method=f.vl_method,
                vl_config=f.vl_config,
                vl_system_prompt=f.vl_system_prompt,
                vl_extract_prompt=f.vl_extract_prompt,
```

`upsert_field`（约 72-133 行）的 `existing.xxx = field.xxx` 与 `new_field = ExtractionField(...)` 各加 4 行：
```python
existing.vl_method = field.vl_method
existing.vl_config = field.vl_config
existing.vl_system_prompt = field.vl_system_prompt
existing.vl_extract_prompt = field.vl_extract_prompt
```
新增分支：
```python
            vl_method=field.vl_method,
            vl_config=field.vl_config,
            vl_system_prompt=field.vl_system_prompt,
            vl_extract_prompt=field.vl_extract_prompt,
```

- [ ] **Step 4：smoke 检查**

Run: `python -c "from blue_print.extraction_router import router; print(router.routes)"`
Expected: 列表里能看到 `/extraction/test` 等路由，无 import 错误。

- [ ] **Step 5：commit**

```bash
git add blue_print/extraction_router.py
git commit -m "feat(extraction_router): add vl branch to /test and CRUD passthrough"
```

---

## Task 17：file_router 上传写盘 + 删除联动

**Files:**
- Modify: `blue_print/file_router.py`

- [ ] **Step 1：找到上传 endpoint 写盘点**

打开 `blue_print/file_router.py`，定位 `file_content_bytes = await file.read()` 行（约 198 行），及紧随其后的尺寸检查。

- [ ] **Step 2：在尺寸检查通过后加写盘**

定位到约 199-202 行：
```python
file_content_bytes = await file.read()
if len(file_content_bytes) > cfg.max_file_size:
    raise HTTPException(...)
```

之后插入：
```python
# 持久化原始 PDF 字节，VL 抽取依赖
try:
    from utils import vl_client
    pdf_target = vl_client.pdf_path(file_id)
    pdf_target.parent.mkdir(parents=True, exist_ok=True)
    pdf_target.write_bytes(file_content_bytes)
except Exception as e:
    logger.warning("写盘 PDF 失败（不阻断 pipeline）: file_id={} error={}", file_id, e)
```

注意：`file_id` 这时已经生成（参考现有逻辑里 `generate_file_id(...)` 调用点）。如果 `file_id` 在尺寸检查之后才生成，把写盘块挪到 `file_id = ...` 之后即可。

- [ ] **Step 3：找单文件删除 endpoint，加联动清理**

定位 `DELETE /file/{file_id}` 处（搜索 `@router.delete("/{file_id}")` 或类似）。在删除 DB 记录处之前或之后追加：
```python
try:
    from utils import vl_client
    vl_client.pdf_path(file_id).unlink(missing_ok=True)
except Exception as e:
    logger.warning("清理 PDF 失败 file_id={}: {}", file_id, e)
```

- [ ] **Step 4：批量删除 endpoint 同样加**

定位批量删除（搜索 `batch_delete` 或 `BatchDeleteRequest`）。在循环删除每个 `file_id` 时追加同样的 unlink。

- [ ] **Step 5：smoke import 检查**

Run: `python -c "from blue_print.file_router import router; print(len(router.routes))"`
Expected: 输出路由数（>0），无报错。

- [ ] **Step 6：commit**

```bash
git add blue_print/file_router.py
git commit -m "feat(file_router): persist raw PDF on upload, cleanup on delete"
```

---

## Task 18：doctype 级联删除联动 + init 孤儿清理

**Files:**
- Modify: `blue_print/doctype_router.py`
- Modify: `service/init_service.py`

- [ ] **Step 1：找文档类型级联删除路径**

打开 `blue_print/doctype_router.py`，搜索 `force=true` 的级联删除分支。这个分支会删除文档类型下所有 file 记录及其 file_content/file_table 等。

- [ ] **Step 2：在删除 file 记录的循环里加 PDF unlink**

在每次循环开始 / 结束处追加（用 `from utils import vl_client`）：
```python
try:
    vl_client.pdf_path(file_id).unlink(missing_ok=True)
except Exception as e:
    logger.warning("doctype 级联清理 PDF 失败 file_id={}: {}", file_id, e)
```

- [ ] **Step 3：在 `service/init_service.py` 加孤儿清理函数**

在 `cleanup_garbage_data` 之后、`run_init` 之前追加：

```python
async def cleanup_orphan_pdfs(session: AsyncSession) -> None:
    """清理 uploads/ 下不在 files 表中的孤儿 PDF。"""
    from utils import vl_client

    storage_dir = vl_client._get_pdf_storage_dir()
    if not storage_dir.exists():
        return

    stmt = select(File.file_id)
    result = await session.execute(stmt)
    valid_ids = {row[0] for row in result.fetchall()}

    removed = 0
    for pdf_file in storage_dir.glob("*.pdf"):
        file_id = pdf_file.stem
        if file_id not in valid_ids:
            try:
                pdf_file.unlink()
                removed += 1
            except OSError as e:
                logger.warning("删除孤儿 PDF 失败 {}: {}", pdf_file, e)
    if removed > 0:
        logger.info("清理孤儿 PDF: {} 个", removed)
```

- [ ] **Step 4：在 `run_init` 末尾调用**

定位 `run_init` 函数（约 253-272 行），在 `cleanup_garbage_data(session)` 之后追加：
```python
        await cleanup_orphan_pdfs(session)
```

- [ ] **Step 5：smoke import 检查**

Run: `python -c "from service.init_service import cleanup_orphan_pdfs; print(cleanup_orphan_pdfs)"`
Expected: 输出函数对象。

- [ ] **Step 6：commit**

```bash
git add service/init_service.py blue_print/doctype_router.py
git commit -m "feat: doctype cascade and init orphan PDF cleanup"
```

---

## Task 19：file_router 集成测试

**Files:**
- Test: `tests/test_file_router_vl_storage.py`

- [ ] **Step 1：写写盘 + 删除测试**

```python
"""上传写盘 + 删除联动测试。"""

from __future__ import annotations

import io

import fitz
import pytest

from utils import vl_client


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


async def test_delete_removes_pdf(client, fresh_uploads, monkeypatch, tmp_path):
    """DELETE /file/{id} 应该删除 uploads/{id}.pdf。"""
    # 直接造一个 file 记录 + uploads/ 文件
    from model.tables import File as FileModel
    from model.database import get_session_factory

    fake_id = "abc123"
    pdf_file = fresh_uploads / f"{fake_id}.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(FileModel(file_id=fake_id, file_name="t.pdf", file_size=10, progress="complete"))
        await s.commit()

    resp = await client.delete(f"/file/{fake_id}")
    assert resp.status_code == 200
    assert not pdf_file.exists()
```

- [ ] **Step 2：运行测试**

Run: `uv run pytest tests/test_file_router_vl_storage.py -v`
Expected: 2 条 PASS（如果 DB 不可用，跳过或修 fixture）。

- [ ] **Step 3：commit**

```bash
git add tests/test_file_router_vl_storage.py
git commit -m "test: upload writes PDF, delete removes PDF"
```

---

## Task 20：extraction_router /test 集成测试

**Files:**
- Test: `tests/test_extraction_router_vl.py`

- [ ] **Step 1：写 /test 模式 2（临时 config）VL 调用测试**

```python
"""extraction_router VL 分支测试。"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import fitz
import pytest

from utils import vl_client


@pytest.fixture
def fake_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(vl_client, "_get_pdf_storage_dir", lambda: tmp_path)
    yield tmp_path


async def test_extraction_test_vl_mode_with_temp_config(client, fake_uploads, monkeypatch):
    """/extraction/test 接受 source_type=vl 的临时 config。"""
    file_id = "vl_test_001"

    # 造一份 PDF
    doc = fitz.open()
    doc.new_page().insert_text((20, 20), "amount: 5000", fontsize=12)
    pdf_bytes = doc.tobytes()
    (fake_uploads / f"{file_id}.pdf").write_bytes(pdf_bytes)

    # mock VL chat
    async def fake_vl_chat(messages, **kw):
        return {
            "choices": [{"message": {"content": '{"value": "5000", "reason": "见首页"}'}}],
            "usage": {"total_tokens": 30},
        }

    monkeypatch.setattr("service.vl_service.model.vl_chat", fake_vl_chat)

    # 还需要 file 记录（用空记录让 extract_vl_field 能跑；它实际读 type_id 用，VL 不依赖）
    from model.tables import File as FileModel
    from model.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as s:
        s.add(FileModel(file_id=file_id, file_name="x.pdf", file_size=100, progress="complete"))
        await s.commit()

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
```

- [ ] **Step 2：运行测试**

Run: `uv run pytest tests/test_extraction_router_vl.py -v`
Expected: PASS。

- [ ] **Step 3：commit**

```bash
git add tests/test_extraction_router_vl.py
git commit -m "test: /extraction/test vl mode with temp config"
```

---

## Task 21：UI — source_type 下拉与 onChange

**Files:**
- Modify: `ui/js/ruleConfig.js`

- [ ] **Step 1：在 source_type 下拉加 vl 选项**

定位 `<select class="form-select" id="fm-source-type"`（约 367-378 行），在 `<option value="table">` 之后加：
```html
                        <option value="vl" ${field.source_type === 'vl' ? 'selected' : ''}>VL（PDF 视觉模型）</option>
```

- [ ] **Step 2：在 buildFieldForm 中插入 VL section 占位**

定位到 `<div id="fm-text-section">...</div>` 之后（约 460 行附近），追加：
```html
            <div id="fm-vl-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">VL 配置</div>
                <div class="form-group">
                    <label class="form-label">VL 方法</label>
                    <select class="form-select" id="fm-vl-method" onchange="RuleConfig.onVLMethodChange(this.value)">
                        <option value="vl_model" ${(field.vl_method || 'vl_locate') === 'vl_model' ? 'selected' : ''}>vl_model（全量）</option>
                        <option value="vl_progressive" ${field.vl_method === 'vl_progressive' ? 'selected' : ''}>vl_progressive（逐批扫描）</option>
                        <option value="vl_locate" ${(field.vl_method || 'vl_locate') === 'vl_locate' ? 'selected' : ''}>vl_locate（定位+提取）</option>
                    </select>
                </div>
                <div id="fm-vl-config-area">
                    ${this.buildVLConfigFields(field.vl_method || 'vl_locate', field.vl_config || {})}
                </div>
                <div class="form-group">
                    <label class="form-label">System prompt（可选）</label>
                    <textarea class="form-textarea" id="fm-vl-system-prompt" rows="3" placeholder="可选，VL 调用的系统提示">${Utils.escapeHtml(field.vl_system_prompt || '')}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Final extract prompt</label>
                    <textarea class="form-textarea" id="fm-vl-extract-prompt" rows="6" placeholder="必须含 value/reason 关键字，要求 VL 直接输出 {&quot;value&quot;:..., &quot;reason&quot;:...} JSON">${Utils.escapeHtml(field.vl_extract_prompt || '')}</textarea>
                </div>
            </div>
```

- [ ] **Step 3：扩展 onSourceTypeChange**

定位 `onSourceTypeChange(type)`（约 628-635 行），改为：
```javascript
    onSourceTypeChange(type) {
        const tableSection = document.getElementById('fm-table-section');
        const textSection = document.getElementById('fm-text-section');
        const vlSection = document.getElementById('fm-vl-section');
        if (!tableSection || !textSection || !vlSection) return;

        tableSection.style.display = type === 'table' ? 'block' : 'none';
        textSection.style.display = type === 'text' ? 'block' : 'none';
        vlSection.style.display = type === 'vl' ? 'block' : 'none';
    },
```

- [ ] **Step 4：smoke 在浏览器加载页面**

启动 dev server `python app.py`，浏览器开 `http://localhost:5019/ui`，新建 / 编辑字段，切换 source_type → 应见 VL 配置区显示 / 隐藏。

- [ ] **Step 5：commit**

```bash
git add ui/js/ruleConfig.js
git commit -m "ui: add source_type=vl option and section toggle"
```

---

## Task 22：UI — VL config 表单 build / collect

**Files:**
- Modify: `ui/js/ruleConfig.js`

- [ ] **Step 1：实现 buildVLConfigFields**

在 `buildSearchConfigFields` 之后（约 626 行结尾后）追加：
```javascript
    buildVLConfigFields(method, vlConfig) {
        vlConfig = vlConfig || {};
        let html = '';

        switch (method) {
            case 'vl_model':
                html = `
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Page range</label>
                            <input class="form-input" id="fm-vl-page-range" value="${Utils.escapeHtml(vlConfig.page_range || 'all')}" placeholder="all 或 1-3,5">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Max pixels</label>
                            <input class="form-input" id="fm-vl-max-pixels" type="number" value="${vlConfig.max_pixels ?? 4000000}" min="100000">
                        </div>
                    </div>
                `;
                break;

            case 'vl_progressive':
                html = `
                    <div class="form-group">
                        <label class="form-label">Field hints（提示要找的字段）</label>
                        <input class="form-input" id="fm-vl-field-hints" value="${Utils.escapeHtml(vlConfig.field_hints || '')}" placeholder="例：投资金额、签署日期、股东姓名">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Batch size</label>
                            <input class="form-input" id="fm-vl-batch-size" type="number" value="${vlConfig.batch_size ?? 2}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Max pixels</label>
                            <input class="form-input" id="fm-vl-max-pixels" type="number" value="${vlConfig.max_pixels ?? 4000000}" min="100000">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">自定义 batch prompt 模板（留空用默认）</label>
                        <textarea class="form-textarea" id="fm-vl-batch-prompt-template" rows="6" placeholder="必须含占位符 {field_hints} {page_label} {total_pages} {history}">${Utils.escapeHtml(vlConfig.batch_prompt_template || '')}</textarea>
                    </div>
                `;
                break;

            case 'vl_locate':
                html = `
                    <div class="form-group">
                        <label class="form-label">Field hints</label>
                        <input class="form-input" id="fm-vl-field-hints" value="${Utils.escapeHtml(vlConfig.field_hints || '')}" placeholder="例：资产总额、负债总额、净利润">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Grid pages</label>
                            <input class="form-input" id="fm-vl-grid-pages" type="number" value="${vlConfig.grid_pages ?? 6}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Grid cols</label>
                            <input class="form-input" id="fm-vl-grid-cols" type="number" value="${vlConfig.grid_cols ?? 3}" min="1">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Max concurrent</label>
                            <input class="form-input" id="fm-vl-max-concurrent" type="number" value="${vlConfig.max_concurrent ?? 20}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Thumb scale</label>
                            <input class="form-input" id="fm-vl-thumb-scale" type="number" step="0.05" value="${vlConfig.thumb_scale ?? 0.75}" min="0.1" max="2.0">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Key pages limit</label>
                            <input class="form-input" id="fm-vl-key-pages-limit" type="number" value="${vlConfig.key_pages_limit ?? 6}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Fallback pages</label>
                            <input class="form-input" id="fm-vl-fallback-pages" type="number" value="${vlConfig.fallback_pages ?? 3}" min="0">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Max pixels</label>
                        <input class="form-input" id="fm-vl-max-pixels" type="number" value="${vlConfig.max_pixels ?? 4000000}" min="100000">
                    </div>
                    <div class="form-group">
                        <label class="form-label">自定义 locate prompt 模板（留空用默认）</label>
                        <textarea class="form-textarea" id="fm-vl-locate-prompt-template" rows="8" placeholder="必须含占位符 {field_hints} {page_labels} {position_map} {grid_rows} {grid_cols}">${Utils.escapeHtml(vlConfig.locate_prompt_template || '')}</textarea>
                    </div>
                `;
                break;
        }
        return html;
    },

    onVLMethodChange(method) {
        const area = document.getElementById('fm-vl-config-area');
        if (!area) return;
        const config = (this.state.editingField && this.state.editingField.vl_method === method)
            ? (this.state.editingField.vl_config || {})
            : {};
        area.innerHTML = this.buildVLConfigFields(method, config);
    },

    collectVLConfig(method) {
        const config = {};
        const getVal = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
        const getInt = (id, def) => { const el = document.getElementById(id); return el ? (parseInt(el.value) || def) : def; };
        const getFloat = (id, def) => { const el = document.getElementById(id); return el ? (parseFloat(el.value) || def) : def; };

        switch (method) {
            case 'vl_model':
                config.page_range = getVal('fm-vl-page-range') || 'all';
                config.max_pixels = getInt('fm-vl-max-pixels', 4000000);
                break;
            case 'vl_progressive':
                config.field_hints = getVal('fm-vl-field-hints');
                config.batch_size = getInt('fm-vl-batch-size', 2);
                config.max_pixels = getInt('fm-vl-max-pixels', 4000000);
                {
                    const tpl = getVal('fm-vl-batch-prompt-template');
                    if (tpl) config.batch_prompt_template = tpl;
                }
                break;
            case 'vl_locate':
                config.field_hints = getVal('fm-vl-field-hints');
                config.grid_pages = getInt('fm-vl-grid-pages', 6);
                config.grid_cols = getInt('fm-vl-grid-cols', 3);
                config.max_concurrent = getInt('fm-vl-max-concurrent', 20);
                config.thumb_scale = getFloat('fm-vl-thumb-scale', 0.75);
                config.key_pages_limit = getInt('fm-vl-key-pages-limit', 6);
                config.fallback_pages = getInt('fm-vl-fallback-pages', 3);
                config.max_pixels = getInt('fm-vl-max-pixels', 4000000);
                {
                    const tpl = getVal('fm-vl-locate-prompt-template');
                    if (tpl) config.locate_prompt_template = tpl;
                }
                break;
        }
        return config;
    },
```

- [ ] **Step 2：在 collectFieldFormData 加 vl 分支**

定位 `collectFieldFormData`（约 760 行附近），找到收集 source_type 与赋默认值的代码块，扩展为：
```javascript
        const sourceType = document.getElementById('fm-source-type').value;
        const data = {
            field_id: document.getElementById('fm-field-id').value.trim(),
            field_name: document.getElementById('fm-field-name').value.trim(),
            type_id: this.state.currentTypeId || 'default',
            source_type: sourceType,
            enabled: document.getElementById('fm-enabled').checked ? 1 : 0,
            priority: parseInt(document.getElementById('fm-priority').value) || 0,
            // table 类
            table_name_pattern: null,
            table_match_type: null,
            table_match_keywords: null,
            table_match_max_results: null,
            table_system_prompt: null,
            table_extract_prompt: null,
            // text 类
            search_type: null,
            search_config: null,
            text_system_prompt: null,
            text_extract_prompt: null,
            // vl 类
            vl_method: null,
            vl_config: null,
            vl_system_prompt: null,
            vl_extract_prompt: null,
        };
```

接着在 `if (sourceType === 'table') { ... } else { ... }` 之后追加：
```javascript
        if (sourceType === 'vl') {
            data.vl_method = document.getElementById('fm-vl-method').value;
            data.vl_config = this.collectVLConfig(data.vl_method);
            data.vl_system_prompt = document.getElementById('fm-vl-system-prompt').value.trim() || null;
            data.vl_extract_prompt = document.getElementById('fm-vl-extract-prompt').value.trim();
        }
```

注意把 text/table 分支结构调整成 if/else if/else 三分支或独立 if 块（视当前代码结构而定，原代码可能是 if (table) {} else { /* text */ }）。

- [ ] **Step 3：smoke 在浏览器测试**

启动 dev server，新建一个 source_type=vl + vl_method=vl_locate 的字段，提交保存，DB 里看 `extraction_field` 表 vl_method/vl_config 等列已写入。

- [ ] **Step 4：commit**

```bash
git add ui/js/ruleConfig.js
git commit -m "ui: build/collect vl_config form per method"
```

---

## Task 23：UI — 调试面板 vl 事件渲染 + 列表展示 + 校验

**Files:**
- Modify: `ui/js/ruleConfig.js`

- [ ] **Step 1：调试面板加 vl 事件分支**

定位调试面板事件处理（搜索 `event === 'search_results'` 或 `case 'search_results'`，约 1150-1300 行区间）。在已有事件 case 之间追加：

```javascript
            } else if (event === 'pdf_loaded') {
                debugLog.innerHTML += `<div class="debug-event">📄 PDF 加载完成 · 共 ${data.total_pages} 页 · 方法 ${data.vl_method}</div>`;
            } else if (event === 'progressive_batch') {
                const icon = data.has_info ? '✓' : '✗';
                const cls = data.has_info ? 'has-info' : 'no-info';
                debugLog.innerHTML += `<div class="debug-event ${cls}">[${data.page_label}] ${icon} ${data.has_info ? Utils.escapeHtml(data.summary_preview || '') : '无相关'}</div>`;
            } else if (event === 'locate_locate') {
                debugLog.innerHTML += `<div class="debug-event">网格 ${data.grid_idx}/${data.total_grids} 页码 ${Utils.escapeHtml(data.page_labels)} → 命中 [${(data.found_pages || []).join(', ')}]</div>`;
            } else if (event === 'locate_extract') {
                debugLog.innerHTML += `<div class="debug-event">关键页确定：[${(data.key_pages || []).join(', ')}]，开始第二轮高清提取</div>`;
            }
```

- [ ] **Step 2：字段列表 source_type 列展示 vl 方法**

定位字段列表渲染（约 195 行附近），找到展示 `source_type` 的位置。把：
```javascript
${f.source_type}
```
替换为：
```javascript
${f.source_type === 'vl' ? `VL · ${f.vl_method || ''}` : f.source_type}
```

- [ ] **Step 3：前端校验加 vl 分支**

定位 `validateFieldFormData` 或表单提交前校验函数。在原有 source_type=table/text 校验旁加：
```javascript
if (data.source_type === 'vl') {
    if (!data.vl_method) {
        return '请选择 VL 方法';
    }
    if (!data.vl_extract_prompt) {
        return 'Final extract prompt 不能为空';
    }
    const lower = data.vl_extract_prompt.toLowerCase();
    if (!lower.includes('value') || !lower.includes('reason')) {
        return 'Final extract prompt 必须包含 value 与 reason 关键字';
    }
}
```

- [ ] **Step 4：smoke 在浏览器测试调试面板**

新建一个 vl_progressive 字段并跑 stream 调试，应当看到 PDF loaded → 多个 batch 行 → prompt → result。

- [ ] **Step 5：commit**

```bash
git add ui/js/ruleConfig.js
git commit -m "ui: vl event rendering, list display, validation"
```

---

## Task 24：CLAUDE.md 文档更新

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1：在 "Extraction System" 节追加 VL 子节**

定位 CLAUDE.md 的 `### Extraction System` 节，在末尾追加：

```markdown
- **vl** - 三种基于 VL 视觉模型的端到端 PDF 抽取。直接读 `uploads/{file_id}.pdf`，跳过 MinerU 解析的 Markdown：
  - `vl_model`：指定页全部塞 VL 一次出 JSON。配置 `page_range`。
  - `vl_progressive`：分批扫描 + 伪历史累积 + 最后文本聚合。配置 `field_hints`、`batch_size`，可自定义 `batch_prompt_template`。
  - `vl_locate`：缩略图网格并行定位 + 关键页高清提取。配置 `field_hints`、`grid_pages`、`max_concurrent`，可自定义 `locate_prompt_template`。
  - VL 直接产出 `{value, reason}` JSON，**不**走文本 LLM 二次抽取。
  - 全局并发 `vl_model.global_max_concurrency`（默认 8）通过 `utils/vl_client.py` 的 asyncio.Semaphore 治理。
  - PDF 字节由 `blue_print/file_router.py` 在上传时持久化到 `uploads/{file_id}.pdf`，由 DELETE / 批量删除 / 文档类型级联删除联动清理；启动时 `cleanup_orphan_pdfs` 兜底。
```

- [ ] **Step 2：在 "Configuration" 节添加 `vl_model:` 一行**

定位 `## Configuration` 节末尾，把列出的 sections 列表追加 `vl_model`。

- [ ] **Step 3：commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): document VL extraction methods"
```

---

## Task 25：最终回归

- [ ] **Step 1：跑所有测试**

Run: `uv run pytest -v`
Expected: 全部 PASS。

- [ ] **Step 2：手动跑一个端到端 vl_locate 用例**

启动 dev server，上传一份多页 PDF，新建 source_type=vl + vl_method=vl_locate 的字段（field_hints 写实际要找的内容、prompt 含 value/reason），调试流验证：
- pdf_loaded 事件
- 多个 locate_locate 网格事件
- locate_extract 事件
- prompt + result

- [ ] **Step 3：检查异步主流程 callback 不漏 stage_done**

如果项目有 callback test server（看 `tests/test_callback_server.py`），手动跑一次带 vl 字段的 pipeline，curl 接收端，确认每个 vl 字段一次 `field_done`、阶段一次 `stage_done`，**没有**多余的 progressive_batch / locate_grid 推送到 callback_url。

---

## 自审

**Spec 覆盖：** 逐节核对 spec → task：
- §1 数据模型 → Task 11、12、13 ✓
- §2 配置与客户端 → Task 1、2、3、4、5 ✓
- §3 Service 层 → Task 6、7、8、9、10、14 ✓
- §4 Pipeline / 文件生命周期 → Task 17、18 ✓
- §5 路由与调试流 → Task 15、16、20 ✓
- §6 前端 UI → Task 21、22、23 ✓
- §7 错误处理 → Task 14（PDF 不存在）、Task 5（VL API）、Task 9（hallucination filter）、Task 13（schema 校验）✓
- §8 测试 → Task 3-5、6-9、19、20 + Task 25 回归 ✓
- §9 落地清单 → 全覆盖 ✓
- §10 out-of-scope → 不涉及 ✓

**Placeholder 扫描：** 全部步骤都给了具体代码 / 命令 / 文件路径。无 TBD / TODO / "处理边界情况" 等模糊用语。

**类型一致：** `parse_vl_json_response` 三处出现一致；`vl_chat` 签名一致；`extract_vl_field` 与 `run_extraction` dispatcher 一致；`source_refs` 形状（`{"_vl": {...}}`）spec ↔ extract_vl_field ↔ /test 一致。

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-05-10-vl-extraction-methods.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
