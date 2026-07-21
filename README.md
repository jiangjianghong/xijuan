# 析卷 AI

基于 MinerU + LLM 的 PDF 文档处理 Pipeline，支持解析、AI 表格名称校验、向量化、字段提取和逻辑分析。

**核心能力**：PDF 解析 → AI 表格名称校验 → 文本分块 → 向量化存储 → 字段提取 → 逻辑分析

## 功能特性

- **PDF 解析**：基于 MinerU，支持复杂表格和图文混排
- **AI 表格名称校验**：LLM 自动识别表格标题，支持并发处理，名称截断 30 字
- **智能分块**：递归字符分割，表格保持完整性
- **向量检索**：Milvus 存储，支持 5 种文本检索 + 4 种表格匹配
- **字段提取（文本 / 表格类）**：LLM 驱动，可配置 9 种检索/匹配规则后送 LLM 抽取
- **字段提取（VL 类）**：基于 PDF 视觉模型端到端抽取，3 种方法——`vl_model`（全量）/ `vl_progressive`（逐批扫描）/ `vl_locate`（缩略图定位+高清提取），由 VL 直出 `{value, reason}` JSON，不经文本 LLM 二次抽取
- **逻辑分析**：支持判断类（LLM）和计算类（numexpr）规则
- **三种执行模式**：async（异步）、sync（同步）、stream（SSE 流式）
- **细粒度流式事件**：支持逐字段、逐规则实时推送提取和分析进度

## 处理流程

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                   完整处理流程                                        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│   PDF上传 ──► 解析 ──► 表格校验 ──► 分块 ──► 向量化 ──► 提取 ──► 分析 ──► 完成      │
│              (MinerU)   (LLM)    (递归分割) (Embedding)  (LLM)   (LLM/计算)          │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

| 阶段 | 输入 | 输出 | 核心技术 |
|------|------|------|----------|
| 解析 | PDF 文件 | Markdown + 表格 | MinerU API |
| 表格校验 | Markdown 中的 HTML 表格 | 带名称的表格列表 | LLM 并发提取表名 |
| 分块 | Markdown 文本 | 文本块列表 | 递归字符分割 |
| 向量化 | 文本块 | 向量数据 | Embedding API + Milvus |
| 提取 | 向量/文本检索结果（text/table）或 `uploads/{file_id}.pdf`（vl） | 字段值 | LLM + 5 种检索方式 / VL 视觉模型 |
| 分析 | 提取字段值 | 判断/计算结果 | LLM / numexpr |

> 字段提取支持 `table` / `text` / `vl` 三类。前两类经检索后送文本 LLM；`vl` 类直接读上传时持久化的 `uploads/{file_id}.pdf`，由 VL 模型一步输出 JSON。

## 快速开始

### 环境要求

- Python 3.12+
- MySQL 8.0+
- Milvus 2.x
- MinerU 服务（外部依赖）

### 安装

```bash
# 克隆项目
git clone <repository-url>
cd wanz_prase2_001

# 安装依赖（使用 uv）
uv sync

# 配置：编辑 configs/config.yaml，配置数据库、Milvus、MinerU 等连接信息

# 启动服务（开发热重载）
python app.py
# 或： uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload
```

### 验证

```bash
# 上传 PDF 文件（流式模式）
curl -X POST "http://localhost:5019/file/parse?mode=stream" \
  -F "file=@test.pdf"

# 查询处理状态
curl "http://localhost:5019/file/{file_id}/status"

# 获取提取结果
curl "http://localhost:5019/file/{file_id}/extraction"
```

## 配置说明

关键配置项（`configs/config.yaml`）：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server.port` | 服务端口 | 5019 |
| `mineru.base_url` | MinerU 服务地址 | - |
| `mineru.parse_timeout` | 解析超时（秒） | 1200 |
| `chunking.chunk_size` | 分块大小（字符） | 512 |
| `embedding.model_name` | Embedding 模型 | text-embedding-v4 |
| `extraction.llm_model` | 提取用 LLM 模型 | qwen-max |
| `table_name_validation.llm_model` | 表格名称校验 LLM 模型 | qwen3.5-35b-a3b |
| `table_name_validation.max_concurrency` | 表格校验并发数 | 20 |
| `vl_model.model` | VL 视觉模型（用于 vl 类字段，不配则 vl 字段不可用） | qwen-vl-max |
| `vl_model.global_max_concurrency` | VL 调用全局并发上限（asyncio.Semaphore 治理） | 8 |
| `vl_model.pdf_storage_dir` | 原始 PDF 持久化目录（vl 抽取依赖） | uploads |

完整配置参考 [配置手册](docs/guides/configuration.md)

## API 概览

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/file/parse` | 上传并处理 PDF（支持 sync/async/stream） |
| GET | `/file/{file_id}/status` | 查询处理状态 |
| GET | `/file/{file_id}/tables` | 获取表格列表（含名称和页码） |
| GET | `/file/{file_id}/extraction` | 获取字段提取结果 |
| GET | `/file/{file_id}/analysis` | 获取逻辑分析结果 |
| POST | `/file/{file_id}/retry/{stage}` | 从指定阶段重试（支持 sync/async/stream） |
| POST | `/extraction/test` | 字段提取调试 |
| POST | `/analysis/test` | 逻辑分析调试 |

完整 API 文档参考 [接口文档](docs/README.md)；启动后可访问交互式 Swagger UI：`http://localhost:5019/docs`

## 项目结构

```
wanz_prase2_001/
├── app.py                  # 入口文件（FastAPI + uvicorn）
├── configs/
│   └── config.yaml         # 配置文件
├── blue_print/             # 路由层
│   ├── file_router.py      # 文件处理接口
│   ├── extraction_router.py # 字段提取接口
│   └── analysis_router.py  # 逻辑分析接口
├── service/                # 业务逻辑
│   ├── pipeline_service.py # 管线编排（串联所有阶段）
│   ├── parse_service.py    # PDF 解析（MinerU）
│   ├── table_service.py    # AI 表格名称校验（LLM）
│   ├── chunk_service.py    # 文本分块
│   ├── embedding_service.py # 向量化
│   ├── extraction_service.py # 字段提取（含 table/text/vl 分发）
│   ├── analysis_service.py # 逻辑分析
│   └── vl_service/         # VL 端到端抽取（model / progressive / locate）
├── model/                  # 数据模型
│   ├── tables.py           # SQLAlchemy ORM 模型
│   ├── schemas.py          # Pydantic 请求/响应模型
│   └── database.py         # 数据库连接管理
├── ui/                     # 前端界面
├── uploads/                # 原始 PDF 持久化（vl 抽取依赖；.gitignore 已排除）
└── utils/                  # 工具类
    ├── llm_client.py       # LLM 调用
    ├── milvus_client.py    # Milvus 客户端
    ├── vl_client.py        # VL HTTP 客户端 + PDF 渲染（PyMuPDF / Pillow）
    └── config.py           # 配置加载
```

## 文档

完整文档见 **[docs/README.md](docs/README.md)**（导航枢纽）。`docs/openapi.json` 与活的 `/docs` Swagger UI 是精确 schema 权威；手写文档分四层：

- **接口参考** [docs/api/](docs/api/) — 各资源接口（含 curl / 请求·响应字段表 / 错误码）、[异步回调](docs/api/callbacks.md)、[SSE 事件](docs/api/sse.md)
- **指南** — [字段提取配置](docs/guides/extraction-config.md) / [逻辑分析配置](docs/guides/analysis-config.md) / [source_refs 溯源](docs/guides/source-refs.md) / [config.yaml 配置](docs/guides/configuration.md)
- **数据参考** — [数据模型 / 表结构](docs/reference/data-model.md) / [枚举与 progress 状态机](docs/reference/enums.md)
- **架构** — [MinerU 集成](docs/architecture/mineru-integration.md)
