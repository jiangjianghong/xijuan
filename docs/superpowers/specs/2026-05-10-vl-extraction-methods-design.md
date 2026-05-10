# VL 端到端抽取方法集成设计

- 日期：2026-05-10
- 范围：在现有 `extraction_field` 体系上新增第三种来源类型 `vl`，支持 `vl_model` / `vl_progressive` / `vl_locate` 三种基于 VL 视觉模型的端到端 PDF 抽取方法。
- 来源材料：`docs/VL端到端抽取方法.md`
- 现状基线：`docs/design.md`、`CLAUDE.md` "Extraction System" 节，及代码 `service/extraction_service.py`、`model/tables.py`、`blue_print/extraction_router.py`、`ui/js/ruleConfig.js`

---

## 0. 背景

当前抽取体系两类来源：
- `table` —— 4 种表名匹配（exact/fuzzy/contains/llm）+ 表格内容塞入 `<search_result>` 占位符 → LLM 产出 `{value, reason}`。
- `text` —— 5 种文本检索（context/section/rule/chunk_db/vector_db）→ `<search_result>` → LLM。

两者都依赖 MinerU 解析后的 Markdown。对于扫描件、含复杂表格 / 印章 / 手写、关键信息分散在几十页 PDF 的情形，MinerU + 文本检索效果有限。

VL 方法直接读 PDF 渲染图，跳过 MinerU，三种调度策略：

| 方法 | 思路 | 适用 |
|---|---|---|
| `vl_model` | 一把梭：指定页全部塞给 VL | 短文档、全相关 |
| `vl_progressive` | 逐批扫描 + 伪历史累积 + 最后聚合 | 长文档、相关页分散 |
| `vl_locate` | 缩略图网格并行定位 → 关键页高清提取 | 长文档、要快速定位 |

## 1. 数据模型与配置形态

### 1.1 `extraction_field` 表新增 4 列

| 列 | 类型 | 说明 |
|---|---|---|
| `vl_method` | `Enum('vl_model','vl_progressive','vl_locate')` nullable | 仅 `source_type='vl'` 时使用 |
| `vl_config` | JSON nullable | 方法专属参数（含 `field_hints`、可选自定义中间 prompt 模板） |
| `vl_system_prompt` | TEXT nullable | 可选系统提示 |
| `vl_extract_prompt` | TEXT nullable | 最终结构化提示，要求 VL 直接输出 `{value, reason}` JSON |

`source_type` enum 由 `(table, text)` 扩展为 `(table, text, vl)`。

### 1.2 `vl_config` 形状

```jsonc
// vl_model
{
  "page_range": "all" | "1-3,5",
  "max_pixels": 4000000
}

// vl_progressive
{
  "field_hints": "投资金额、签署日期",
  "batch_size": 2,
  "max_pixels": 4000000,
  "batch_prompt_template": null      // 可选；null=用内置默认模板
}

// vl_locate
{
  "field_hints": "资产总额、净利润",
  "grid_pages": 6,
  "grid_cols": 3,
  "max_concurrent": 20,
  "thumb_scale": 0.75,
  "key_pages_limit": 6,
  "fallback_pages": 3,
  "max_pixels": 4000000,
  "locate_prompt_template": null     // 可选
}
```

### 1.3 中间 prompt 模板的占位符契约

模板用 Python `str.format(**kwargs)` 渲染。用户填模板时**必须**包含下表的全部占位符，否则保存阶段 422。

| 方法 | 模板键 | 必填占位符 |
|---|---|---|
| `vl_progressive` | `batch_prompt_template` | `{field_hints}`, `{page_label}`, `{total_pages}`, `{history}` |
| `vl_locate` | `locate_prompt_template` | `{field_hints}`, `{page_labels}`, `{position_map}`, `{grid_rows}`, `{grid_cols}` |
| `vl_model` | —（无中间 prompt） | — |

默认模板放在 `service/vl_service/_defaults.py`，照搬来源材料 §5/§6 原文。

### 1.4 Pydantic 校验（`model/schemas.py`）

- `SourceTypeEnum` 增加 `vl`
- 新增 `VLMethodEnum`：`vl_model` / `vl_progressive` / `vl_locate`
- `ExtractionFieldCreate` 增加 4 个 vl_* 字段
- `source_type='vl'` 时：`vl_method` 必填、`vl_extract_prompt` 必填
- `vl_extract_prompt` **不**强制 `<search_result>` 占位符（VL 直接调，不做替换）
- **弱校验**：`vl_extract_prompt` 必须包含 `value` 与 `reason` 两个关键字（**大小写不敏感的子串匹配**；纯中文写 prompt 也请显式保留这两个英文 key 名以通过校验，因为最终要求 VL 输出的就是 `{"value": ..., "reason": ...}` JSON）
- 模板键存在时校验上表的占位符全部到位

### 1.5 `source_refs` 写入约定

统一形状：三种方法都写 `method` / `total_pages` / `key_pages` / `vl_total_tokens`；附加方法专属 extras。

```jsonc
// 写入 extraction_result.source_refs
{
  "_vl": {
    "method": "vl_locate",            // 三种方法都有
    "total_pages": 50,                // 三种方法都有
    "key_pages": [3, 7, 12],          // 三种方法都有，1-indexed；vl_progressive 设为 null
    "vl_total_tokens": 38421,         // 三种方法都有
    "batches_with_info": 4            // 仅 vl_progressive
  }
}
```

`key_pages` 各方法语义：
- `vl_model`：`parse_page_range(page_range, total_pages)` 转回 1-indexed 后的所有页
- `vl_locate`：第二轮高清提取的页（1-indexed）
- `vl_progressive`：`null`（逐页扫描没有"key_pages"概念）

`_vl` 键名仿照表格类的 `_tables`。

## 2. 配置与 VL 客户端

### 2.1 `configs/config.yaml` 新增 `vl_model:` 节

```yaml
vl_model:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key: "sk-..."
  model: "qwen-vl-max"
  temperature: 0.1
  max_tokens: 4096
  timeout: 180
  enable_thinking: false
  global_max_concurrency: 8         # 全局 chat.completions 并发上限
  default_max_pixels: 4000000       # 单图像素上限（来源材料 §10.3 安全区）
  pdf_storage_dir: "uploads"        # PDF 持久化目录（相对项目根目录；绝对路径也接受）
```

`utils/config.py` 加 `VLModelConfig` 模型挂到根 config。

### 2.2 `utils/vl_client.py` 新模块

公开接口（结构对照现有 `utils/llm_client.py` + `utils/milvus_client.py`）：

```python
async def vl_chat(messages, *, max_tokens=None, extra_body=None) -> ChatCompletion
    # 单点调用；内部走全局 semaphore + 指数退避重试 3 次（1s/2s/4s）
    # 不做 JSON 解析（解析在 vl_service 里做）

def render_pages_to_b64(file_bytes, pages, *, scale=2.0, max_pixels=None,
                        jpeg_quality=None) -> list[str]
def render_thumbnail(doc, page_idx, *, scale=0.75) -> PIL.Image
def render_hires(doc, page_idx, *, scale=2.0, max_pixels=None) -> str  # b64
def make_grid_image(images, *, cols=3, padding=30) -> str  # b64
def parse_page_range(page_range: str, total_pages: int) -> list[int]
def pdf_path(file_id: str) -> Path                    # 返回 uploads/{file_id}.pdf 绝对路径
```

实现要点：
- `_vl_client: AsyncOpenAI | None` 单例
- 全局 `asyncio.Semaphore(global_max_concurrency)`，套在 `await client.chat.completions.create` 外面
- 渲染函数采用来源材料 §10.3 的 `render_page_safe` 思路：默认 PNG + `max_pixels` 上限保护
- `<think>` 标签清理放到调用方（vl_service 里），不在 client 层做

### 2.3 新增依赖

`pyproject.toml` 增加：
- `pymupdf`
- `pillow`

## 3. Service 层

### 3.1 包结构 `service/vl_service/`

```
service/
  vl_service/
    __init__.py        # re-export 三个公开函数
    _defaults.py       # DEFAULT_BATCH_PROMPT / DEFAULT_LOCATE_PROMPT
    _common.py         # parse_vl_json_response / strip_think_tags / build_image_messages
    model.py           # vl_model_extract
    progressive.py     # vl_progressive_extract
    locate.py          # vl_locate_extract
```

调用方 `from service import vl_service` → `vl_service.vl_model_extract(...)`。

### 3.2 三个公开函数签名

```python
async def vl_model_extract(
    file_bytes: bytes, vl_extract_prompt: str, vl_system_prompt: str | None,
    *, page_range: str = "all", max_pixels: int = 4_000_000,
) -> tuple[str, str, dict]:
    """→ (value, reason, source_refs_data)"""

async def vl_progressive_extract(
    file_bytes, vl_extract_prompt, vl_system_prompt, *,
    field_hints: str, batch_size: int = 2, max_pixels: int = 4_000_000,
    batch_prompt_template: str | None = None,
    progress_cb: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, dict]:
    """N 批扫描 + 1 次最终文本聚合（无图）"""

async def vl_locate_extract(
    file_bytes, vl_extract_prompt, vl_system_prompt, *,
    field_hints: str, grid_pages: int = 6, grid_cols: int = 3,
    max_concurrent: int = 20, thumb_scale: float = 0.75,
    key_pages_limit: int = 6, fallback_pages: int = 3,
    max_pixels: int = 4_000_000, locate_prompt_template: str | None = None,
    progress_cb=None,
) -> tuple[str, str, dict]:
    """第一轮并行定位 + 第二轮高清提取"""
```

### 3.3 Prompt 拼接策略

| 方法 | 中间 prompt | 最终 prompt（用户控制） |
|---|---|---|
| `vl_model` | — | 直接 `vl_extract_prompt`，与所有页图同发 |
| `vl_progressive` | `batch_prompt_template`（自动 / 用户）注入 `field_hints / page_label / total_pages / history` | 文本聚合：`f"以下是逐页扫描得到的累积信息：{accumulated}\n\n{vl_extract_prompt}"`，无图 |
| `vl_locate` | `locate_prompt_template`（自动 / 用户）注入 `field_hints / page_labels / position_map / grid_rows / grid_cols`，强约束 `found_pages` 必须来自页码列表 | 直接 `vl_extract_prompt`，与 key_pages 高清图同发 |

### 3.4 关键工程细节（移植自来源材料 §9，必须保留）

1. 2× 缩放渲染高清图，太小会糊
2. 0.75× 缩放渲染 vl_locate 第一轮缩略图
3. 网格行间 30px 白边
4. 位置映射 prompt + 集合幻觉过滤（`vl_locate` 防 VL 乱编页码的核心）
5. 关键页截断：第一轮所有网格命中页 → `sorted(set(...))` 去重升序 → 取前 `key_pages_limit` 个；超 `key_pages_limit` 才截断，未达阈值的全保留
6. `<think>` 标签正则清理后再做 JSON 解析
7. 重试 3 次 + 指数退避（在 `vl_client.vl_chat` 里实现）
8. `vl_locate` 第一轮一页未命中 → 回退前 `fallback_pages` 页
9. `enable_thinking` 通过 `extra_body={"chat_template_kwargs": {"enable_thinking": ...}}` 传递
10. `asyncio.Semaphore` 必须套在 `client.chat.completions.create` 外（否则限流不生效）
11. 单图渲染加 `max_pixels` 保护，避免高 DPI 扫描件撑爆

### 3.5 `extract_vl_field`（加到 `service/extraction_service.py`）

```python
async def extract_vl_field(file_id, field, session) -> tuple[str, str, dict | None]:
    pdf = vl_client.pdf_path(file_id)
    if not pdf.exists():
        return "", "PDF 原始文件不存在，无法 VL 抽取", None
    file_bytes = pdf.read_bytes()
    cfg = field.vl_config or {}
    method = field.vl_method

    if method == "vl_model":
        v, r, refs = await vl_service.vl_model_extract(
            file_bytes, field.vl_extract_prompt, field.vl_system_prompt,
            page_range=cfg.get("page_range", "all"),
            max_pixels=cfg.get("max_pixels", DEFAULT_MAX_PIXELS),
        )
    elif method == "vl_progressive":
        v, r, refs = await vl_service.vl_progressive_extract(
            file_bytes, field.vl_extract_prompt, field.vl_system_prompt,
            field_hints=cfg.get("field_hints", ""),
            batch_size=cfg.get("batch_size", 2),
            max_pixels=cfg.get("max_pixels", DEFAULT_MAX_PIXELS),
            batch_prompt_template=cfg.get("batch_prompt_template"),
        )
    elif method == "vl_locate":
        v, r, refs = await vl_service.vl_locate_extract(
            file_bytes, field.vl_extract_prompt, field.vl_system_prompt,
            field_hints=cfg.get("field_hints", ""),
            grid_pages=cfg.get("grid_pages", 6),
            grid_cols=cfg.get("grid_cols", 3),
            max_concurrent=cfg.get("max_concurrent", 20),
            thumb_scale=cfg.get("thumb_scale", 0.75),
            key_pages_limit=cfg.get("key_pages_limit", 6),
            fallback_pages=cfg.get("fallback_pages", 3),
            max_pixels=cfg.get("max_pixels", DEFAULT_MAX_PIXELS),
            locate_prompt_template=cfg.get("locate_prompt_template"),
        )
    else:
        return "", f"未知 vl_method={method}", None

    return v, r, {"_vl": refs}
```

### 3.6 `run_extraction` dispatcher 增加分支

`service/extraction_service.py` 现有 if/elif（约 776-779 行）扩展：

```python
if field.source_type == "table":
    extracted_value, reason, source_refs = await extract_table_field(file_id, field, session)
elif field.source_type == "vl":
    extracted_value, reason, source_refs = await extract_vl_field(file_id, field, session)
else:
    extracted_value, reason, source_refs = await extract_text_field(file_id, field, session)
```

`run_extraction_stream` 同步加分支。

## 4. Pipeline 与文件生命周期

### 4.1 PDF 持久化（上传时）

`blue_print/file_router.py` 上传处理处（约 198-300 行）：

1. `file_content_bytes = await file.read()` 后，立即写盘到 `vl_client.pdf_path(file_id)`，**await** 写盘后再触发 BackgroundTask 解析（写盘是几十 MB 本地 IO，~ms 级）。
2. 写盘失败 → 记录 warning 日志但**不阻断**上传 / 解析。
3. 同 `file_id`（重复上传同名文件，命中 SHA256 哈希）覆盖写。

### 4.2 PDF 删除（联动）

以下路径全部追加 `vl_client.pdf_path(file_id).unlink(missing_ok=True)`：
- `DELETE /file/{file_id}`
- `POST /file/batch_delete`
- 文档类型级联删除（`POST /doctype/{type_id}/delete?force=true`）

### 4.3 启动孤儿清理

`service/init_service.py` 启动时扫 `uploads/` 下所有 `.pdf` 文件名（去掉后缀即 `file_id`），与 `files` 表 `file_id` 集合做差集，差集里的删除。

反向（DB 有记录但磁盘无文件）不强制处理：VL 抽取时回写"PDF 不存在"，符合 §3.5 已确认行为。

### 4.4 `.gitignore`

加一行：`uploads/`

### 4.5 不新增 pipeline 阶段

VL 在 `extracting` 阶段被动触发，与现有 source_type=table/text 字段并列。pipeline 状态机、callback 契约、stage_done 事件结构**不变**。

## 5. 路由层与调试流

### 5.1 `/extraction/test`（同步调试）

`blue_print/extraction_router.py:207-251` 加 vl 分支：

```python
elif field.source_type == "vl":
    extracted_value, reason, refs = await extract_vl_field(file_id, field, db)
    search_results = [{
        "type": "vl_meta",
        "method": refs["_vl"]["method"],
        "key_pages": refs["_vl"].get("key_pages"),
        "vl_total_tokens": refs["_vl"].get("vl_total_tokens"),
        "batches_with_info": refs["_vl"].get("batches_with_info"),
    }] if refs else []
    llm_input = field.vl_extract_prompt or ""
    llm_output = extracted_value
```

`/test` 临时 config 模式（约 287-303 行）`field = ExtractionField(...)` 构造补 `vl_method` / `vl_config` / `vl_system_prompt` / `vl_extract_prompt` 4 字段。

### 5.2 `/extraction/test/stream`（SSE 调试）

`service/extraction_service.py:test_field_extraction_stream` 加 vl 分支，4 步骤事件：

| Step | vl_model | vl_progressive | vl_locate |
|---|---|---|---|
| 1 检索 | `pdf_loaded`（page_range_resolved, total_pages） | `pdf_loaded` + 每批 `progressive_batch`（page_label, has_info, summary_preview） | `pdf_loaded` + 每个网格 `locate_grid`（grid_idx, found_pages） |
| 2 prompt | `prompt`（system_prompt, final_user_prompt） | `prompt`（最终聚合 prompt + 每批 prompt 截首样例） | `prompt`（locate prompt 截样例 + final extract prompt） |
| 3 LLM | `llm_response`（raw_response） | `llm_response`（最终聚合的 response） | `llm_response`（最终 extract 的 response） |
| 4 解析 | `result`（value, reason） | `result` | `result` |

`progress_cb` 通过本地 async queue 转成 yield 流，保持 vl_service 入口纯函数（tuple 返回）。

### 5.3 异步主流程的回调契约

VL 字段在 `run_extraction` + `notify_callback` 路径下与 table/text 字段**完全一致**：每字段一次 `field_done`、阶段结束一次 `stage_done`。

vl_service 内部的 `progress_cb`（per-batch / per-grid）**只在调试 stream 时挂**，主流程不挂，不污染外部 callback_url 消费者。

### 5.4 `/extraction/fields`（CRUD）

`upsert_field`、`list_fields` 透传 4 个 vl_* 字段，无业务逻辑变化。校验依赖 §1.4 Pydantic schema。

## 6. 前端 UI

### 6.1 `ui/js/ruleConfig.js` 改动

**(a) 来源类型下拉加 vl**（约 367-378 行）：

```html
<option value="text">文本</option>
<option value="table">表格</option>
<option value="vl">VL（PDF 视觉模型）</option>
```

`onSourceTypeChange(type)` 增加 `vl` 分支，显示 `#fm-vl-section`，隐藏其他。

**(b) 新增 VL 配置区**（与 table/text section 同级）：

```
┌─ VL 配置 ────────────────────────────┐
│  VL 方法  [vl_locate ▾]  ← 切换驱动下方表单 │
│  ─── vl_locate 时显示 ───            │
│  Field hints   [____________________] │
│  Grid pages    [6]   Grid cols  [3]   │
│  Max concurrent[20]  Thumb scale[0.75]│
│  Key pages limit[6]  Fallback   [3]   │
│  Max pixels    [4000000]              │
│  自定义 locate prompt 模板（多行 textarea）│
│  ─── vl_progressive 时显示 ───        │
│  Field hints   [____________________] │
│  Batch size    [2]   Max pixels [4000000]│
│  自定义 batch prompt 模板（多行 textarea）│
│  ─── vl_model 时显示 ───              │
│  Page range    [all]  Max pixels[4000000]│
│  ─── 共用 ───                         │
│  System prompt (可选)  [______________]│
│  Final extract prompt  [______________]│
│  └ 必须含 value/reason 关键字（弱校验）  │
└──────────────────────────────────────┘
```

自定义模板**默认展开**（不折叠）。`onVLMethodChange(method)` 切换三组方法专属字段的 display；System / Final prompt 始终显示。

`buildVLConfigFields(method, vlConfig)` + `collectVLConfig(method)` 各按方法分支组装/收集，参照现有 `buildSearchConfigFields` / `collectSearchConfig` 模式。

**(c) 调试面板适配 VL 流事件**（约 1083 行 `buildDebugPanel` 周边）：

新增 3 个事件渲染分支：
- `pdf_loaded` → 顶部 chip 显示 `total_pages={n}`
- `progressive_batch` → 增量追加一行 `[第N页] ✓有信息 / ✗无相关` + 摘要预览
- `locate_grid` → 增量追加一行 `网格 {idx}/{total} → 命中 [{found_pages}]`

`prompt` / `llm_response` / `result` 事件复用现有渲染。

**(d) 字段列表显示**（约 195 行 fields 表格渲染处）：source_type 列把 `vl` 渲染为 `VL · {vl_method}`，例如 `VL · vl_locate`。

**(e) 前端校验**（`validateFieldFormData`）：
- source_type=vl 时 `vl_method` 必选
- `vl_extract_prompt` 必填且包含 `value` 与 `reason` 关键字（与后端 Pydantic 一致）

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| PDF 文件不存在（uploads/ 缺失） | `extract_vl_field` 返回 `("", "PDF 原始文件不存在", None)`；`run_extraction` 写空值并继续下一字段 |
| `vl_method` 缺失或非法 | 同上，reason="未知 vl_method=xxx" |
| VL API 异常（超时/限流/4xx） | `vl_client.vl_chat` 内重试 3 次（指数退避 1s/2s/4s）；终失败抛 `VLAPIError` → `run_extraction` 主循环 try/except 捕获，字段 reason 记录异常，继续 |
| `vl_progressive` 单批失败 | 该批跳过，不计入 `accumulated_summaries`；不影响最终聚合 |
| `vl_locate` 单网格失败 | 该网格返回空 found_pages；不影响其他网格的并行扫描 |
| VL 输出非 JSON 或无 value/reason | `parse_vl_json_response` 兜底：`value=raw_text`、`reason=""`（与现有 `parse_llm_json_response` 同语义） |
| Pydantic 校验失败（保存配置） | 422 + 字段级错误信息（FastAPI 自动） |
| 模板缺占位符（保存配置） | 422 + "缺少占位符 {xxx}"（schema 校验） |

## 8. 测试

`tests/` 目录新增（沿用现有 pytest-asyncio 模式）：

- `tests/test_vl_service_unit.py` —— `monkeypatch` 把 `utils.vl_client.vl_chat` 替换为可控 mock：
  - vl_model：单次调用成功 / JSON 解析失败兜底 / API 异常
  - vl_progressive：N 批次（含部分批"无相关信息"）+ 最终聚合 / `field_hints` 注入 / 自定义 batch_prompt_template 占位符替换
  - vl_locate：模拟多网格并行（部分返回幻觉页码 → 验证集合过滤） / 第二轮高清提取 / 一页未命中 → fallback_pages 兜底
- `tests/test_extraction_vl_integration.py` —— 用最小 PDF（fixture）跑 `extract_vl_field` 端到端（仍 mock vl_chat），验证 `source_refs._vl` 写入正确
- `tests/test_extraction_router_vl.py` —— `/extraction/test`（同步）+ `/extraction/test/stream`（SSE）VL 分支返回结构
- `tests/test_file_router_vl_storage.py` —— 上传后 `uploads/{file_id}.pdf` 存在；DELETE 后消失；批量删除 / 文档类型级联删除联动清理
- `tests/test_init_vl_orphan.py` —— 启动时 uploads/ 孤儿 PDF 被清理

## 9. 落地清单（实现阶段会拆为独立任务）

1. 依赖：`pyproject.toml` 加 `pymupdf`、`pillow`
2. 配置：`utils/config.py` 加 `VLModelConfig`；`configs/config.yaml` 加 `vl_model:` 节
3. PDF 持久化：`utils/vl_client.py` `pdf_path()` 工具；`file_router.py` 上传写盘 + 删除联动；`init_service.py` 孤儿清理
4. VL 客户端：`utils/vl_client.py` 单例 + 全局 semaphore + 渲染 5 工具
5. Service：`service/vl_service/` 包（_defaults / _common / model / progressive / locate / __init__）
6. 数据模型：`model/tables.py` 加 4 列 + enum；写迁移说明（`ALTER TABLE` 手工命令，因为 SQLAlchemy 不自动迁 enum）
7. Schema：`model/schemas.py` 加 `VLMethodEnum` / 字段 / 校验
8. 提取集成：`service/extraction_service.py` 加 `extract_vl_field` + dispatcher 分支 + `test_field_extraction_stream` vl 分支
9. 路由：`blue_print/extraction_router.py` `/test` 加 vl 分支
10. 前端：`ui/js/ruleConfig.js` 下拉、表单、调试面板、列表渲染、前端校验
11. `.gitignore`：加 `uploads/`
12. 测试：上述 5 个 test 文件
13. 文档：`CLAUDE.md` "Extraction System" 节补 vl 部分（来源类型、配置形态、PDF 存储、并发治理）

## 10. 不在本设计范围内（明确 out-of-scope）

- 跨字段 VL 输出缓存（已确认不做：每字段独立调 VL）
- VL 在 parsing 阶段替代 MinerU 的可能性（属"另一种解析路线"，不在本次 source_type=vl 集成范围）
- 多文档类型间共享 PDF 存储或去重（每个 file_id 一份独立 PDF，复用现有 file_id 哈希）
- VL 模型自动选型 / 多 VL provider 抽象（本次只接一个 OpenAI 兼容 endpoint）
