# VL 端到端抽取方法 — 完整移植文档

> 自包含文档：另一个项目 / 另一个 AI 拿到这一份就能跑，不需要访问当前代码库。
>
> 来源：`plugins/vl_plugin.py`（633 行）+ 必要工具函数。

---

## 0. 一句话总览

**3 种 VL 端到端抽取方法**，全部基于"PDF → PyMuPDF 渲染成 PNG → base64 → OpenAI 兼容多图 API → VL 模型返回结构化文本"这一通用链路，区别在**怎么选页 / 怎么调度 API**：

| 方法 | 思路 | 调用次数 | 是否并发 | 适用 |
|---|---|---|---|---|
| `vl_model` | 一把梭：指定页全部塞给 VL | 1 次 | — | 短文档、全相关 |
| `vl_progressive` | 逐批 + 伪历史累积，模型自判相关性 | N / batch | 串行 | 长文档、相关页分散 |
| `vl_locate` | 两轮：缩略图网格并行定位 → 关键页高清提取 | (页数/grid) + 1 | 并行 | 长文档、要快速定位 |

---

## 1. 依赖

```
pymupdf       # import fitz, PDF 渲染
Pillow        # 图片拼网格
openai        # AsyncOpenAI 客户端（OpenAI 兼容 API）
loguru        # 日志（可换标准 logging）
```

`pip install pymupdf pillow openai loguru` 即可。

---

## 2. VL 模型配置（OpenAI 兼容）

本项目用 Dashscope 的 Qwen-VL 系列。任何 OpenAI 兼容的 VL 端点都能跑：

```yaml
vl_model:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key: "<你的 KEY>"
  model: "qwen3.5-27b"      # 或 qwen-vl-max / qwen2.5-vl-72b-instruct 等
  temperature: 0.1
  max_tokens: 4096
  timeout: 180
  enable_thinking: false    # 思考模型设 true 会输出 <think> 标签
```

客户端单例（避免每次重建连接池）：

```python
from openai import AsyncOpenAI

_vl_client: AsyncOpenAI | None = None

def get_vl_client() -> AsyncOpenAI:
    global _vl_client
    if _vl_client is None:
        _vl_client = AsyncOpenAI(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="<KEY>",
            timeout=180,
        )
    return _vl_client
```

---

## 3. 通用工具函数（5 个，全部独立、无项目耦合）

```python
from __future__ import annotations

import base64
import io
import fitz  # pymupdf
from PIL import Image


def parse_page_range(page_range: str, total_pages: int) -> list[int]:
    """解析 "1-2,5" / "all" 这类字符串，返回 0-indexed 页码列表."""
    if page_range == "all":
        return list(range(total_pages))
    pages: list[int] = []
    for part in page_range.split(","):
        part = part.strip()
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            pages.extend(range(int(start_s) - 1, int(end_s)))
        else:
            pages.append(int(part) - 1)
    return [p for p in pages if 0 <= p < total_pages]


def render_pages_to_base64(file_content: bytes, pages: list[int]) -> list[str]:
    """渲染 PDF 指定页为 2x PNG，返回 base64 字符串列表（用于 vl_model 模式）."""
    doc = fitz.open(stream=file_content, filetype="pdf")
    try:
        results: list[str] = []
        for page_idx in pages:
            page = doc.load_page(page_idx)
            mat = fitz.Matrix(2, 2)  # 2x 缩放
            pixmap = page.get_pixmap(matrix=mat)
            png_bytes = pixmap.tobytes("png")
            results.append(base64.b64encode(png_bytes).decode("ascii"))
        return results
    finally:
        doc.close()


def render_page_thumbnail(doc: fitz.Document, page_idx: int, scale: float = 0.75) -> Image.Image:
    """渲染单页为低清 PIL Image（用于 vl_locate 第一轮缩略图）."""
    page = doc.load_page(page_idx)
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_page_hires(doc: fitz.Document, page_idx: int, scale: float = 2.0) -> str:
    """渲染单页为高清 base64 PNG（用于 vl_locate 第二轮精准提取）."""
    page = doc.load_page(page_idx)
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def make_grid_image(images: list[Image.Image], cols: int = 3) -> str:
    """把多张缩略图拼成 cols 列网格，返回 base64 PNG（行间留 30px 白边）."""
    if not images:
        return ""
    rows_count = (len(images) + cols - 1) // cols
    w = max(img.width for img in images)
    h = max(img.height for img in images)
    padding = 30
    grid = Image.new("RGB", (cols * w, rows_count * (h + padding)), "white")
    for i, img in enumerate(images):
        row, col = divmod(i, cols)
        grid.paste(img, (col * w, row * (h + padding) + padding))
    buf = io.BytesIO()
    grid.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")
```

---

## 4. 方法 1 — `vl_model`（标准全量模式）

**思路**：按 `page_range` 渲染指定页（2× 缩放）→ 一次性把所有图片打包进一条 `messages` → VL 模型直接产出结构化 Markdown。

**适用**：页数少且全部相关（封面页、登记证明、承诺书、股权架构等 ≤ 几页）。

**缺点**：页数多时 token 爆炸；无关页（目录、附注）也送进模型造成噪声。

```python
from typing import Any
from loguru import logger
import fitz
import time

DEFAULT_VL_PROMPT = (
    "请将以上文档图片中的所有文字内容提取为结构化文本，保持原始格式和层级关系。"
    "如有表格，请用Markdown表格格式输出。"
)


async def vl_model_extract(
    file_content: bytes,
    page_range: str = "all",
    vl_prompt: str | None = None,
    *,
    model: str = "qwen3.5-27b",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    enable_thinking: bool = False,
) -> dict[str, Any]:
    """标准全量 VL 抽取.

    Returns:
        {"text": str, "metadata": {...}}
    """
    vl_prompt = vl_prompt or DEFAULT_VL_PROMPT
    start = time.monotonic()

    doc = fitz.open(stream=file_content, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    pages = parse_page_range(page_range, total_pages)
    if not pages:
        return {"text": "", "metadata": {"parse_method": "vl_model", "parse_seconds": 0}}

    b64_images = render_pages_to_base64(file_content, pages)

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}
        for b in b64_images
    ]
    content.append({"type": "text", "text": vl_prompt})

    client = get_vl_client()
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )

    result_text = response.choices[0].message.content or ""
    usage = response.usage
    return {
        "text": result_text,
        "metadata": {
            "parse_method": "vl_model",
            "parse_seconds": round(time.monotonic() - start, 3),
            "vl_prompt_tokens": usage.prompt_tokens if usage else 0,
            "vl_completion_tokens": usage.completion_tokens if usage else 0,
            "vl_total_tokens": usage.total_tokens if usage else 0,
        },
    }
```

---

## 5. 方法 2 — `vl_progressive`（逐批扫描 + 伪历史累积）

**思路**：把 PDF 按 `batch_size`（默认 2）分批，**串行**调 VL；每批 prompt 中拼入"已扫描页面累积摘要"作为伪历史；模型自判相关性：

- 相关 → 输出精简摘要（保留数字 / 名称 / 金额）
- 无关 → 仅输出 `"无相关信息"`（前 20 字符内含此关键字则丢弃）

最终把所有有效摘要按页码顺序拼接为 `document_text`。

**适用**：长文档但相关页分散，希望用 LLM 的语义判断代替规则筛选。

**缺点**：**串行**慢；伪历史让后面的 prompt 越来越长。

```python
async def vl_progressive_extract(
    file_content: bytes,
    field_hints: str,                # "我要找的是什么"，例如 "投资金额、签署日期、股东姓名"
    batch_size: int = 2,
    progress_callback=None,          # 可选 async 回调，每批完成时调用
    *,
    model: str = "qwen3.5-27b",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    start = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0

    doc = fitz.open(stream=file_content, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    if total_pages == 0:
        return {"text": "", "metadata": {"parse_method": "vl_progressive", "parse_seconds": 0}}

    accumulated_summaries: list[str] = []
    client = get_vl_client()

    for batch_start in range(0, total_pages, batch_size):
        batch_pages = list(range(batch_start, min(batch_start + batch_size, total_pages)))
        b64_images = render_pages_to_base64(file_content, batch_pages)

        page_label = (
            f"第{batch_pages[0] + 1}页"
            if len(batch_pages) == 1
            else f"第{batch_pages[0] + 1}-{batch_pages[-1] + 1}页"
        )

        history_text = ""
        if accumulated_summaries:
            history_text = "【已扫描页面的累积信息】：\n" + "\n".join(accumulated_summaries) + "\n\n"

        prompt = (
            f"{history_text}"
            f"你正在逐页阅读一份文档，需要关注以下信息：{field_hints}\n\n"
            f"当前是{page_label}（共{total_pages}页）。\n"
            "如果当前页包含上述相关信息，请输出精简摘要（保留关键数字、名称、金额等）。\n"
            '如果当前页无相关信息（如封面、目录、说明性文字），请仅输出"无相关信息"。'
        )

        content: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}
            for b in b64_images
        ]
        content.append({"type": "text", "text": prompt})

        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        result_text = (response.choices[0].message.content or "").strip()
        usage = response.usage
        if usage:
            total_prompt_tokens += usage.prompt_tokens
            total_completion_tokens += usage.completion_tokens

        # 关键：模型自判，前 20 字符不含"无相关信息"才记入伪历史
        has_info = "无相关信息" not in result_text[:20]
        if has_info and result_text:
            accumulated_summaries.append(f"- {page_label}：{result_text}")

        if progress_callback:
            await progress_callback({
                "current_page": batch_pages[-1] + 1,
                "total_pages": total_pages,
                "batch": page_label,
                "has_info": has_info,
                "summary_preview": result_text[:100] if has_info else "",
            })

    document_text = "\n".join(accumulated_summaries) if accumulated_summaries else "文档中未找到相关信息"

    return {
        "text": document_text,
        "metadata": {
            "parse_method": "vl_progressive",
            "parse_seconds": round(time.monotonic() - start, 3),
            "vl_prompt_tokens": total_prompt_tokens,
            "vl_completion_tokens": total_completion_tokens,
            "vl_total_tokens": total_prompt_tokens + total_completion_tokens,
            "vl_total_pages": total_pages,
            "vl_scanned_batches": (total_pages + batch_size - 1) // batch_size,
            "vl_summaries_count": len(accumulated_summaries),
        },
    }
```

---

## 6. 方法 3 — `vl_locate`（两轮：缩略图并行定位 + 高清精准提取）

**思路**：解决 `vl_progressive` 串行慢的问题，分两轮：

### 第一轮 — 并行定位
1. 全部页面渲染为低清缩略图（默认 0.75× 缩放）
2. 每 `grid_pages` 页（默认 6）拼成一张 3 列网格图（行间 30px 白边）
3. prompt 里告诉模型 "第 R 行第 C 列 = 第 X 页" 的**位置映射**，约束输出页码必须来自 `[页码列表]`
4. `asyncio.Semaphore`（默认并发 20）+ `asyncio.gather` **并行**扫描所有网格
5. 每次调用带**指数退避重试**（最多 3 次：1s → 2s → 4s）
6. 模型返回 JSON `{"found_pages": [...], "reason": "..."}`，用集合做**幻觉过滤**（剔除不在当前网格页码范围内的返回）
7. 汇总命中页 → 排序去重 → 截断保留前 4-6 页

### 第二轮 — 高清提取
- 对定位到的关键页用 2× 缩放重新渲染
- 一次性发给 VL，prompt 围绕 `field_hints` 提取结构化文本
- 若第一轮一页没命中 → 回退扫描前 3 页

**适用**：长文档（财务报表、投资协议正本等几十页），需要快速定位 3-5 个关键页。

```python
import asyncio
import json
import re


async def vl_locate_extract(
    file_content: bytes,
    field_hints: str,
    grid_pages: int = 6,
    max_concurrent: int = 20,
    progress_callback=None,
    *,
    model: str = "qwen3.5-27b",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    enable_thinking: bool = False,
) -> dict[str, Any]:
    start = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0

    doc = fitz.open(stream=file_content, filetype="pdf")
    total_pages = len(doc)

    if total_pages == 0:
        doc.close()
        return {"text": "", "metadata": {"parse_method": "vl_locate", "parse_seconds": 0}}

    cfg_extra = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
    client = get_vl_client()

    # ── 第一轮：缩略图网格并行定位 ──
    grid_cols = 3
    grid_rows = (grid_pages + grid_cols - 1) // grid_cols

    grids: list[tuple[str, list[int]]] = []  # (b64_grid, page_indices_0idx)
    for batch_start in range(0, total_pages, grid_pages):
        page_indices = list(range(batch_start, min(batch_start + grid_pages, total_pages)))
        thumbnails = [render_page_thumbnail(doc, idx) for idx in page_indices]
        b64 = make_grid_image(thumbnails, cols=grid_cols)
        grids.append((b64, page_indices))

    total_grids = len(grids)
    sem = asyncio.Semaphore(max_concurrent)
    locate_start = time.monotonic()

    async def scan_grid(grid_idx: int, b64: str, page_indices: list[int]) -> list[int]:
        nonlocal total_prompt_tokens, total_completion_tokens

        page_labels = ", ".join(str(idx + 1) for idx in page_indices)
        # 位置映射：告诉模型网格中每个位置对应的页码
        pos_hints = []
        for i, idx in enumerate(page_indices):
            row, col = divmod(i, grid_cols)
            pos_hints.append(f"第{row+1}行第{col+1}列=第{idx+1}页")
        pos_text = "；".join(pos_hints)

        prompt = (
            f"这张图片是一份文档的缩略图网格（{grid_rows}行×{grid_cols}列），"
            f"包含第 {page_labels} 页。\n"
            f"位置对应关系：{pos_text}\n\n"
            f"请判断哪些页面包含以下信息：{field_hints}\n\n"
            "选择标准——选择以下类型的页面：\n"
            "1. 封面/首页（包含企业名称的标题页）\n"
            "2. 正式报表页（资产负债表、利润表、现金流量表等，以完整表格形式呈现）\n"
            "3. 协议/合同的关键条款页（金额、签署方等核心条款）\n"
            "4. 包含汇总数据的表格页（如有明显的数字表格且与所需信息直接相关）\n\n"
            "不要选择：纯文字附注段落、审计意见页、目录页、空白页。\n\n"
            f"注意：只能从 [{page_labels}] 中选择，不要返回其他页码。\n"
            '请只返回JSON格式：{"found_pages": [页码数字列表], "reason": "简要说明"}\n'
            '如果这几页都不包含相关信息，返回：{"found_pages": [], "reason": "无相关内容"}'
        )

        messages = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}]

        # 指数退避重试 3 次
        resp = None
        for attempt in range(3):
            try:
                async with sem:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=512,  # 定位只要短 JSON
                        extra_body=cfg_extra,
                    )
                break
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return []

        if resp is None:
            return []

        text = (resp.choices[0].message.content or "").strip()
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.DOTALL).strip()

        usage = resp.usage
        if usage:
            total_prompt_tokens += usage.prompt_tokens
            total_completion_tokens += usage.completion_tokens

        # 解析 JSON + 幻觉过滤
        valid_range = set(idx + 1 for idx in page_indices)
        found: list[int] = []
        try:
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e != -1:
                obj = json.loads(text[s:e + 1])
                raw = [int(p) for p in obj.get("found_pages", [])]
                found = [p for p in raw if p in valid_range]  # 关键：过滤幻觉页码
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        if progress_callback:
            await progress_callback({
                "phase": "locate",
                "current_grid": grid_idx + 1,
                "total_grids": total_grids,
                "pages": page_labels,
                "found_pages": found,
                "total_pages": total_pages,
            })

        return [p - 1 for p in found]  # 转回 0-indexed

    tasks = [scan_grid(i, b64, pages) for i, (b64, pages) in enumerate(grids)]
    results = await asyncio.gather(*tasks)

    key_pages = sorted(set(p for r in results for p in r if 0 <= p < total_pages))
    # 截断最多 4-6 页（这里取前 6）
    if len(key_pages) > 4:
        key_pages = key_pages[:6]

    locate_seconds = round(time.monotonic() - locate_start, 3)

    # ── 第二轮：关键页高清精准提取 ──
    if not key_pages:
        # 一页都没命中 → 回退前 3 页
        key_pages = list(range(min(3, total_pages)))

    b64_hires = [render_page_hires(doc, idx) for idx in key_pages]
    page_labels_str = ", ".join(str(idx + 1) for idx in key_pages)

    if progress_callback:
        await progress_callback({
            "phase": "extract",
            "key_pages": [p + 1 for p in key_pages],
            "total_pages": total_pages,
        })

    extract_prompt = (
        f"以下是一份文档中的关键页面（第 {page_labels_str} 页）。\n"
        f"请从中提取以下信息：{field_hints}\n\n"
        "请将所有找到的信息以结构化文本输出，保留关键数字、名称、金额等。"
        "如有表格，请用Markdown表格格式输出。"
    )

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}
        for b in b64_hires
    ]
    content.append({"type": "text", "text": extract_prompt})

    extract_start = time.monotonic()
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=cfg_extra,
    )

    result_text = (resp.choices[0].message.content or "").strip()
    result_text = re.sub(r"<think>[\s\S]*?</think>", "", result_text, flags=re.DOTALL).strip()
    extract_seconds = round(time.monotonic() - extract_start, 3)

    usage = resp.usage
    if usage:
        total_prompt_tokens += usage.prompt_tokens
        total_completion_tokens += usage.completion_tokens

    doc.close()

    return {
        "text": result_text,
        "metadata": {
            "parse_method": "vl_locate",
            "parse_seconds": round(time.monotonic() - start, 3),
            "vl_prompt_tokens": total_prompt_tokens,
            "vl_completion_tokens": total_completion_tokens,
            "vl_total_tokens": total_prompt_tokens + total_completion_tokens,
            "vl_total_pages": total_pages,
            "vl_locate_grids": total_grids,
            "vl_locate_seconds": locate_seconds,
            "vl_extract_seconds": extract_seconds,
            "vl_key_pages": [p + 1 for p in key_pages],
        },
    }
```

---

## 7. 调用示例

```python
import asyncio

async def main():
    with open("xxx.pdf", "rb") as f:
        pdf_bytes = f.read()

    # ① 短文档：全量
    r1 = await vl_model_extract(pdf_bytes, page_range="1-2")
    print(r1["text"])

    # ② 长文档、相关页分散：逐批扫描
    r2 = await vl_progressive_extract(
        pdf_bytes,
        field_hints="投资金额、签署日期、股东姓名、注册资本",
        batch_size=2,
    )
    print(r2["text"])

    # ③ 长文档、要快速定位：两轮模式
    r3 = await vl_locate_extract(
        pdf_bytes,
        field_hints="资产总额、负债总额、净利润、所有者权益",
        grid_pages=6,
        max_concurrent=20,
    )
    print(r3["text"])
    print(r3["metadata"]["vl_key_pages"])  # 看模型定位到了哪几页

asyncio.run(main())
```

---

## 8. 三种方法对比速查

| 维度 | `vl_model` | `vl_progressive` | `vl_locate` |
|---|---|---|---|
| 入口函数 | `vl_model_extract` | `vl_progressive_extract` | `vl_locate_extract` |
| 调用次数 | 1 次 | N / batch_size 次 | (页数 / grid_pages) + 1 次 |
| 是否并发 | 否（一次调用） | **串行** | **并行**（信号量限流 20） |
| 是否需要 `field_hints` | 否 | 是 | 是 |
| 是否带累积上下文 | 否 | 伪历史累积 | 否（两轮独立） |
| 适合场景 | 短文档、全相关 | 长文档、要 LLM 自行筛选 | 长文档、定位精准抽取 |
| 主要风险 | token 爆炸 | 速度慢、prompt 膨胀 | 缩略图过糊导致定位失败 |
| token 三件套 metadata | ✓ | ✓ | ✓ |
| 进度回调支持 | — | ✓ | ✓（分 `locate` / `extract` 两阶段） |

---

## 9. 关键工程细节（移植时不要简化掉）

1. **2× 缩放**渲染高清图（`fitz.Matrix(2, 2)`），太小会糊导致 OCR 错。
2. **0.75× 缩放**渲染缩略图（`vl_locate` 第一轮），定位足够，省 token。
3. **网格行间 30px 白边**：避免相邻页面贴在一起被 VL 误识别成连续内容。
4. **位置映射 prompt**（`第 R 行第 C 列 = 第 X 页`）+ **集合幻觉过滤**：是 `vl_locate` 防 VL 模型乱编页码的核心，缺一不可。
5. **关键页截断**：第一轮命中超过 4 页就截断到前 6 页（见 `vl_locate_extract` 中的 `if len(key_pages) > 4`），避免第二轮 token 超限。
6. **`<think>` 标签清理**：Qwen 思考类模型输出会带 `<think>...</think>`，必须用正则剥掉再做 JSON 解析。
7. **重试 3 次 + 指数退避**：网络 / 限流抖动时 `vl_locate` 第一轮单网格失败不影响整体。
8. **回退前 3 页**：`vl_locate` 第一轮一页没命中时的兜底。
9. **`enable_thinking` 透传**：通过 `extra_body={"chat_template_kwargs": {"enable_thinking": ...}}` 控制，Dashscope / vLLM 都认这个参数。
10. **`asyncio.Semaphore`** 必须套在 `await client.chat.completions...` 外面，否则限流不生效。

---

## 10. 图片压缩 / 尺寸控制（**移植时必看**）

本项目**没有显式 JPEG 压缩、没有像素上限保护**，只通过 `fitz.Matrix` 的 scale 因子和 PNG `optimize=True` 控制图片大小。这套做法在小 PDF 上没问题，但**移植到新项目时一定要看完这一节**，否则容易踩坑。

### 10.1 当前项目的做法（一句话）

| 用途 | 函数 | 缩放 | 输出格式 | 特点 |
|---|---|---|---|---|
| `vl_model` 全量送图 | `render_pages_to_base64` | **2.0×** | PNG（无 optimize） | 高清，token 贵 |
| `vl_locate` 第一轮缩略图 | `render_page_thumbnail` | **0.75×** | PIL Image（不直接出文件） | 低清，仅用于定位 |
| `vl_locate` 缩略图拼网格 | `make_grid_image` | — | PNG（**`optimize=True`**） | 拼接后压缩一次 |
| `vl_locate` 第二轮高清 | `render_page_hires` | **2.0×** | PNG（无 optimize） | 高清，只对关键页 |

**没有 JPEG**：所有图都用 PNG，因为文字截图 PNG 比 JPEG 更清晰（JPEG 对锐利边缘会糊），文字 OCR 准确率高很多。

**没有像素上限**：PDF 的原始 DPI 不一样（扫描件常见 300-600 DPI，再 ×2 就是 600-1200 DPI），单页可能渲染出 5000×7000 像素的巨图。

### 10.2 这套做法的实际问题

#### 问题 1：高 DPI 源 PDF 直接撑爆
扫描件 PDF 以 600 DPI 输入 + 2× 缩放 → 单页 ≈ 4960×7016 像素 → PNG ≈ 8-15 MB → base64 后 ≈ 11-20 MB。
- 大部分 VL 模型有**单图最大像素**限制：Qwen-VL 默认 `max_pixels = 1280 × 28 × 28 ≈ 100 万`，硬上限 `12,845,056 ≈ 1280 万`。
- 超限要么报 400，要么 API 自己 resize（你不知道它怎么 resize 的，效果不可控）。

#### 问题 2：base64 体积膨胀 33%
HTTP body 里塞的是 base64，比二进制大 33%。一份 20 页 PDF 的 `vl_model` 全量请求可能上百 MB，超 OpenAI SDK 默认 timeout（180s）很常见。

#### 问题 3：`vl_locate` 网格拼接也会变巨图
默认 `grid_pages=6`、3 列 2 行，0.75× 缩略图 → 单元 ≈ 1240×1755 → 网格 ≈ 3720×3540 像素。一份 60 页 PDF 拼出 10 张这种大网格，第一轮就发 10 个 12 MB+ 的图。

#### 问题 4：token 成本
VL 模型按图片像素计 token（Qwen-VL 大约每 28×28 = 1 个 token），1000 万像素的图 ≈ 12700 token，比文字 prompt 贵几十倍。

### 10.3 推荐的压缩 / 尺寸保护方案（移植时加上）

下面是一个**带像素上限 + 可选 JPEG 降级**的安全渲染函数，建议替换掉原来的 `render_pages_to_base64`：

```python
import base64
import io
from PIL import Image
import fitz


def render_page_safe(
    doc: fitz.Document,
    page_idx: int,
    target_scale: float = 2.0,
    max_pixels: int = 4_000_000,        # 400 万像素上限，Qwen-VL 安全区
    jpeg_quality: int | None = None,    # 设为 85 改用 JPEG（仅彩色扫描件用）
) -> str:
    """渲染单页，带像素上限保护，返回 base64 字符串.

    - 先按 target_scale 渲染
    - 若像素超 max_pixels，按比例降 scale 重渲
    - jpeg_quality=None 输出 PNG（文字推荐）；设数值则输出 JPEG（彩色扫描件可用）
    """
    page = doc.load_page(page_idx)

    # 先估算原始大小
    rect = page.rect
    base_w = rect.width
    base_h = rect.height

    # 计算实际可用 scale
    target_pixels = (base_w * target_scale) * (base_h * target_scale)
    if target_pixels > max_pixels:
        scale = (max_pixels / (base_w * base_h)) ** 0.5
    else:
        scale = target_scale

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))

    if jpeg_quality is None:
        # PNG 路径：文字清晰，推荐默认
        img_bytes = pix.tobytes("png")
    else:
        # JPEG 路径：彩色扫描件可显著省体积（约 80%）
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        img_bytes = buf.getvalue()

    return base64.b64encode(img_bytes).decode("ascii")
```

然后在调用 VL API 时，根据格式调整 `image_url` 的 MIME 类型：

```python
mime = "image/jpeg" if jpeg_quality else "image/png"
content.append({
    "type": "image_url",
    "image_url": {"url": f"data:{mime};base64,{b64}"},
})
```

### 10.4 各模式的尺寸建议（实战参数）

| 模式 | 推荐 `target_scale` | 推荐 `max_pixels` | 推荐格式 |
|---|---|---|---|
| `vl_model` 短文档全量 | 2.0 | 400 万 | PNG |
| `vl_locate` 缩略图 | 0.75（保持当前） | 100 万 | PNG（拼网格用 `optimize=True`） |
| `vl_locate` 高清提取 | 2.0 | 400 万 | PNG |
| 彩色扫描件（无矢量文字） | 2.0 | 600 万 | JPEG q=85（省 80% 体积） |
| 纯文字 PDF（矢量文字） | 1.5 即可 | 200 万 | PNG（矢量渲染 1.5x 已经足够清晰） |

**经验法则**：先试 PNG + 2x，如果 base64 超 5 MB 再考虑：
1. 先降 `max_pixels`（保护下限别糊）
2. 仍超就改 JPEG（仅限彩色扫描件，纯文字别用 JPEG）
3. 还超就拆批（vl_progressive / vl_locate）

### 10.5 `vl_locate` 网格拼接的额外建议

如果 `grid_pages=6` 拼出的网格超 4 MB，按以下顺序优化：
1. **降缩略图 scale**：0.75 → 0.5（识别率轻微下降但够用）
2. **缩小网格页数**：`grid_pages=6` → `grid_pages=4`（2×2 网格更稳）
3. **`make_grid_image` 改 JPEG q=80**：因为缩略图本来就糊，JPEG 损失可忽略

### 10.6 一句话总结

**当前项目走的是"PNG + 高 scale + 不限制"的简单路线，在小 PDF 上最高质量，但移植到不可控来源的 PDF 时必须加 `max_pixels` 上限保护，可选 JPEG 降级**。前面 `vl_locate` 默认值（`grid_pages=6`、`max_concurrent=20`）是基于实际项目调出来的，扫描件场景建议下调一档（4 / 10）。

---

## 11. 选型建议

- 文档 ≤ 5 页 + 全部相关 → `vl_model`
- 文档 ≤ 30 页 + 相关页占比高（>50%）→ `vl_model`（直接全量更省事）
- 文档几十页 + 关键信息集中在某几页 → `vl_locate`（实战首选）
- 文档很长 + 想保留 LLM 全文阅读的语义判断 → `vl_progressive`（慢，本项目实战中较少用）

`field_hints` 写法建议：用逗号分隔的具体字段名，例如 `"资产总额、负债总额、净利润、所有者权益"`，比泛泛的"财务数据"效果好得多。
