# 启动配置自检（startup config check）设计

**日期**：2026-05-12
**作者**：jiangxihong + Claude
**状态**：草案，待评审

## 1. 背景与目标

服务启动时只验证了"数据库表能不能建出来"和"Milvus collection 在不在"，但下游依赖（MinerU 解析服务、向量化模型、3 份 LLM 配置、VL 视觉模型）只有在第一个真实业务请求到来时才会暴露问题——配置笔误、密钥失效、外部服务掉线，往往要 PM 试用时才被发现。

本设计在 `run_init()` 末尾追加一组只读探活，**让所有外部依赖在 lifespan 阶段就给出一次明确的 PASS/FAIL**，输出对齐表格到启动日志。

**非目标**：不做持续健康检查，不暴露 `/health` API，不阻塞启动，不提供开关。

## 2. 范围

| 类别 | 被测对象 | 检查方式 |
|---|---|---|
| 基础设施 | MySQL | `SHOW TABLES` 返回行数 |
| 基础设施 | Milvus | `connect()` + `utility.list_collections()`，断言目标 collection 在其中 |
| 基础设施 | MinerU | `httpx.GET base_url`，任何 HTTP 响应（含 4xx）即贯通 |
| 模型 | 向量化模型 (`embedding`) | `get_embeddings(["ping"])`，断言返回维度 == `embedding.embedding_dim` |
| 模型 | 抽取 LLM (`extraction.llm_*`) | `chat_completion("ping", ...)` 显式传 base_url/model/api_key/max_retries=1 |
| 模型 | 表格名校验 LLM (`table_name_validation.llm_*`) | 同上，传 `table_name_validation.*` 三参（即使值与 extraction 重复也照发） |
| 模型 | VL 视觉模型 (`vl_model.*`) | 读 `tests/base64_pic.md`（已是 `data:image/png;base64,...`），构造 OpenAI vision messages 调 `vl_chat`，断言 `choices[0].message.content` 非空。这一项同时覆盖了 vl_model 的连通性+鉴权+模型 |

合计 **7 项**：3 基础设施 + 4 模型（embedding + 2 个 LLM + 1 个 VL）。`vl_model` 不再单独跑纯文本 chat ping——图片调用本身就走的同一条 chat/completions 路径，能成功即代表整套 vl_model 配置 OK。

## 3. 架构

### 3.1 新增文件

`service/startup_check.py`：

```
@dataclass CheckResult
    name: str
    ok: bool
    elapsed_ms: int
    detail: str
    error: str | None

async def check_mysql() -> CheckResult
async def check_milvus() -> CheckResult
async def check_mineru() -> CheckResult
async def check_embedding() -> CheckResult
async def check_llm(name, base_url, model, api_key) -> CheckResult
async def check_vl() -> CheckResult
async def _run_one(name: str, fn: Callable) -> CheckResult
async def run_startup_checks() -> list[CheckResult]
def _format_table(results: list[CheckResult]) -> str
```

### 3.2 接入点

`service/init_service.py::run_init()` 在所有现有逻辑（建库建表、Milvus collection、状态恢复、垃圾清理、孤儿 PDF 清理）之后追加：

```python
from service.startup_check import run_startup_checks
await run_startup_checks()
```

放在最后是因为 mysql/milvus check 的语义前提是这些资源已经被准备好。

### 3.3 并发与超时

- 7 个 check 用 `asyncio.gather(..., return_exceptions=True)` 并发。
- 每项硬超时 **10 秒**（用 `asyncio.wait_for`），到点判 fail。
- LLM/VL 类 check 强制 `max_retries=1`，避免现有重试退避把启动时间拖到分钟级。
- 预期总耗时 ≈ 单项最长耗时 ≈ 2~3s。

### 3.4 错误处理

`_run_one` 是所有异常的边界：

```python
async def _run_one(name, fn):
    start = time.monotonic()
    try:
        return await asyncio.wait_for(fn(), timeout=10)
    except asyncio.TimeoutError:
        logger.warning("启动检查 {} 超时（>10s）", name)
        return CheckResult(name, ok=False, elapsed_ms=10000, detail="", error="TimeoutError")
    except Exception as e:
        logger.exception("启动检查 {} 异常", name)
        return CheckResult(name, ok=False, elapsed_ms=int((time.monotonic()-start)*1000),
                           detail="", error=f"{type(e).__name__}: {e}")
```

`run_startup_checks` 本身再套一层 try/except 兜底，连汇总渲染失败都只 `logger.exception` 一行，绝不阻塞 lifespan。

## 4. 输出格式

启动检查完成后用 loguru 一次性打印 ASCII 表格 + 汇总行：

```
┌──────────────────────┬──────┬───────┬─────────────────────────────────────┐
│ Check                │ OK   │  ms   │ Detail                              │
├──────────────────────┼──────┼───────┼─────────────────────────────────────┤
│ mysql                │  ✓   │   12  │ db=wanzi_prase2_001, tables=12      │
│ milvus               │  ✓   │   83  │ collections=3, target=...base ✓     │
│ mineru               │  ✓   │  102  │ GET http://...:7078 → 404 (ok)      │
│ embedding            │  ✓   │  541  │ dim=1024 ✓                          │
│ extraction_llm       │  ✗   │ 10003 │ TimeoutError                        │
│ table_validation_llm │  ✓   │  742  │ model=qwen3.5-122b, reply=2 chars   │
│ vl_model             │  ✓   │ 1893  │ model=qwen3.5-122b, reply=37 chars  │
└──────────────────────┴──────┴───────┴─────────────────────────────────────┘
启动检查完成: 6/7 通过, 总耗时 ~1.9s
```

- 失败行额外配合一条 `WARNING` 日志（含 error 字段全文）。
- 异常 traceback 已经由 `_run_one` 里的 `logger.exception` 留底。

## 5. 关键实现细节

- **`tests/base64_pic.md` 路径解析**：用 `Path(__file__).resolve().parent.parent / "tests" / "base64_pic.md"` 锚定项目根，不依赖 cwd；启动检查跑不起来时 fallback 到 fail 而非崩溃。
- **VL messages 构造**：
  ```python
  data_url = Path(...).read_text(encoding="utf-8").strip()
  messages = [{"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": data_url}},
      {"type": "text", "text": "请用一句话描述这张图片"},
  ]}]
  resp = await vl_chat(messages, max_retries=1)
  reply = resp["choices"][0]["message"]["content"]
  ```
- **LLM check 必须显式传参**：现有 `chat_completion` 默认回退到 `extraction.*` 配置，3 份 LLM check 必须把 `base_url/model/api_key` 三参显式传入，否则会把 `table_validation_llm` / `vl_model` 测成 extraction。
- **Milvus check 不能调 `ensure_collection`**（会建集合）；只用 `utility.list_collections()` 只读探查。
- **MinerU "贯通"判定**：`httpx.AsyncClient(timeout=5).get(base_url)`，任何返回（含 404/405）都算 ok；只有 `httpx.RequestError` 或 `asyncio.TimeoutError` 算 fail。

## 6. 测试

`tests/test_startup_check.py`：

| 用例 | 覆盖点 |
|---|---|
| `test_check_result_dataclass` | dataclass 字段、默认值 |
| `test_format_table_smoke` | 表格渲染不抛、行数与输入一致 |
| `test_run_one_swallows_exception` | mock 一个抛 RuntimeError 的 check fn，确认 `_run_one` 返回 ok=False 且不向上抛 |
| `test_run_one_respects_timeout` | mock 一个 sleep(15) 的 check fn，确认 10s 后被判 fail |
| `test_run_startup_checks_does_not_raise_on_any_failure` | gather 中混入异常项，确认最外层不抛 |

不为 7 个真实 check 写 IO 测试——它们的事实测试就是每次启动服务时的实跑。

## 7. 配置改动

无。本设计不引入任何 yaml 字段，也不读环境变量。

## 8. 风险与权衡

- **加 2~3s 启动时间**：可接受。pytest 用 `httpx.AsyncClient` + `ASGITransport` 起 lifespan 时会触发；若后续单测变慢明显再补一个 `STARTUP_CHECK_ENABLED` env override。
- **MinerU "GET 任何响应都算 ok" 偏宽松**：能区分"网络通不通"但不能区分"vllm 后端起没起"。这是用户明确选择，避免每次启动都实跑一次 PDF 解析。
- **VL 图片测试依赖 `tests/base64_pic.md` 存在**：若文件被误删 check 会 fail，但 fail 信息会清楚指出"找不到文件"，运维容易定位。
