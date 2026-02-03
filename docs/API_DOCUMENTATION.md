# 文档解析与逻辑分析系统 - API 接口文档

> **版本**: 0.1.0
> **技术栈**: FastAPI + SQLAlchemy + MySQL + Milvus + Aliyun DashScope

---

## 目录

1. [基础信息](#1-基础信息)
2. [通用响应格式](#2-通用响应格式)
3. [文件处理接口 `/file/*`](#3-文件处理接口)
4. [字段提取配置接口 `/extraction/*`](#4-字段提取配置接口)
5. [逻辑分析配置接口 `/analysis/*`](#5-逻辑分析配置接口)
6. [向量检索接口 `/search`](#6-向量检索接口)
7. [数据模型说明](#7-数据模型说明)
8. [枚举值说明](#8-枚举值说明)
9. [错误码说明](#9-错误码说明)

---

## 1. 基础信息

| 项目 | 说明 |
|------|------|
| Base URL | `http://{host}:5019` |
| 协议 | HTTP |
| 数据格式 | JSON（`Content-Type: application/json`） |
| 文件上传 | `multipart/form-data` |
| 认证方式 | 无（当前版本） |

---

## 2. 通用响应格式

所有接口均返回统一的 `ResponseWrapper` 结构：

```json
{
  "code": 200,
  "message": "success",
  "data": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | `int` | 状态码，200 表示成功 |
| `message` | `string` | 描述信息 |
| `data` | `any` | 响应数据，具体格式因接口而异 |

---

## 3. 文件处理接口

### 3.1 提交文件解析

提交文件进行解析，支持同步、异步、流式三种处理模式。

- **URL**: `POST /file/parse`
- **Content-Type**: `multipart/form-data`

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|------|--------|------|
| `file` | form-data | `File` | 是 | - | 上传的文件（最大 100MB） |
| `mode` | query | `string` | 否 | `"async"` | 处理模式：`sync` / `async` / `stream` |

**请求示例**

```bash
curl -X POST "http://localhost:5019/file/parse?mode=async" \
  -F "file=@/path/to/document.pdf"
```

**响应示例（async 模式）**

```json
{
  "code": 200,
  "message": "文件已提交处理（异步）",
  "data": {
    "file_id": "a1b2c3d4e5f6"
  }
}
```

**响应示例（sync 模式）**

```json
{
  "code": 200,
  "message": "文件处理完成",
  "data": {
    "file_id": "a1b2c3d4e5f6"
  }
}
```

**响应示例（stream 模式）**

返回 `text/event-stream` 格式的 SSE 流，逐步推送处理进度事件。

**SSE 事件类型说明**

| 事件名 | 阶段 | 说明 |
|--------|------|------|
| `parsing_start` | 解析 | 开始 MinerU 解析 |
| `parsing` | 解析 | MinerU 解析完成 |
| `content_saved` | 解析 | MD 内容已存储 |
| `md_content` | 解析 | **MD 文档内容**（包含完整 Markdown 文本） |
| `tables_extracted` | 解析 | 表格提取完成 |
| `chunking_start` | 分块 | 开始分块 |
| `chunking` | 分块 | 分块完成 |
| `chunks_saving` | 分块 | 开始存储分块 |
| `chunks_saved` | 分块 | 分块已存储到数据库 |
| `embedding_start` | 向量化 | 开始向量化 |
| `embedding` | 向量化 | 向量化完成 |
| `milvus_submitting` | 向量化 | 开始提交向量到 Milvus |
| `milvus_submitted` | 向量化 | 向量已提交到 Milvus |
| `tasks_loading` | 提取/分析 | 开始获取提取/分析任务 |
| `tasks_loaded` | 提取/分析 | 已获取提取/分析任务 |
| `extraction_start` | 字段提取 | 开始关键词提取 |
| `field_extracted` | 字段提取 | **单个字段提取完成**（每个字段独立发送） |
| `extraction` | 字段提取 | 全部关键词提取完成 |
| `analysis_start` | 逻辑分析 | 开始逻辑分析 |
| `rule_analyzed` | 逻辑分析 | **单条规则分析完成**（每条规则独立发送） |
| `analysis` | 逻辑分析 | 全部逻辑分析完成 |
| `complete` | 完成 | 文件处理完成 |
| `error` | 错误 | 处理过程中发生错误 |

**SSE 事件数据格式示例**

```
event: parsing
data: {"file_id": "abc123", "stage": "parsing", "message": "MinerU 解析完成", "content_length": 5000}

event: md_content
data: {"file_id": "abc123", "stage": "md_content", "message": "MD 文档内容", "content": "# 1 公司简介\n\n某某公司..."}

event: field_extracted
data: {"file_id": "abc123", "stage": "field_extracted", "message": "字段提取完成: 公司名称", "field_id": "company_name", "field_name": "公司名称", "extracted_value": "某某科技有限公司", "reason": "从第一段提取", "success": true, "current": 1, "total": 5}

event: rule_analyzed
data: {"file_id": "abc123", "stage": "rule_analyzed", "message": "规则分析完成: 是否盈利", "rule_id": "is_profitable", "rule_name": "是否盈利", "rule_type": "judge", "result_value": "true", "input_values": {"net_profit": "5000000"}, "reason": "净利润大于0", "success": true, "current": 1, "total": 3}

event: complete
data: {"file_id": "abc123", "stage": "complete", "message": "文件处理完成"}
```

**特殊逻辑说明**

| 文件状态 | 行为 |
|----------|------|
| 正在处理中（`parsing`/`chunking`/`embedding`/`extracting`/`analyzing`） | 返回 409，拒绝重复提交 |
| 已完成（`complete`） | 直接返回已完成状态 |
| 失败状态（`*_failed`） | 自动清理并从对应阶段重试 |

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 提交成功或处理完成 |
| 400 | 文件大小超过限制 |
| 409 | 文件正在处理中，不可重复提交 |

---

### 3.2 查询文件处理进度

- **URL**: `GET /file/{file_id}/status`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl "http://localhost:5019/file/a1b2c3d4e5f6/status"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "a1b2c3d4e5f6",
    "file_name": "report.pdf",
    "file_size": 2048576,
    "progress": "complete",
    "error": null,
    "create_time": "2025-01-15T10:30:00",
    "updated_at": "2025-01-15T10:32:00"
  }
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 查询成功 |
| 404 | 文件不存在 |

---

### 3.3 删除文件

删除文件及所有关联数据（MySQL 记录 + Milvus 向量数据）。

- **URL**: `DELETE /file/{file_id}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl -X DELETE "http://localhost:5019/file/a1b2c3d4e5f6"
```

**响应示例**

```json
{
  "code": 200,
  "message": "文件已删除",
  "data": null
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 删除成功 |
| 404 | 文件不存在 |

---

### 3.4 从指定阶段重试

从指定的处理阶段开始异步重试。

- **URL**: `POST /file/{file_id}/retry/{stage}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |
| `stage` | `string` | 是 | 重试阶段，可选值：`chunking` / `embedding` / `extracting` / `analyzing` |

**请求示例**

```bash
curl -X POST "http://localhost:5019/file/a1b2c3d4e5f6/retry/embedding"
```

**响应示例**

```json
{
  "code": 200,
  "message": "已从 embedding 阶段开始重试",
  "data": null
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 重试已提交 |
| 400 | 无效的阶段名称 |
| 404 | 文件不存在 |

---

### 3.5 重试字段提取

快捷重试字段提取阶段（等同于 `POST /file/{file_id}/retry/extracting`）。

- **URL**: `POST /file/{file_id}/retry/extracting`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl -X POST "http://localhost:5019/file/a1b2c3d4e5f6/retry/extracting"
```

**响应示例**

```json
{
  "code": 200,
  "message": "已从 extracting 阶段开始重试",
  "data": null
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 重试已提交 |
| 404 | 文件不存在 |

---

### 3.6 重试逻辑分析

快捷重试逻辑分析阶段（等同于 `POST /file/{file_id}/retry/analyzing`）。

- **URL**: `POST /file/{file_id}/retry/analyzing`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl -X POST "http://localhost:5019/file/a1b2c3d4e5f6/retry/analyzing"
```

**响应示例**

```json
{
  "code": 200,
  "message": "已从 analyzing 阶段开始重试",
  "data": null
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 重试已提交 |
| 404 | 文件不存在 |

---

### 3.7 获取文件表格列表

获取文件中提取出的所有表格数据。

- **URL**: `GET /file/{file_id}/tables`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl "http://localhost:5019/file/a1b2c3d4e5f6/tables"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2c3d4e5f6",
      "table_index": 0,
      "total_table": 3,
      "table_name": "财务报表",
      "table_content": "| 项目 | 金额 |\n|------|------|\n| 收入 | 1000 |"
    }
  ]
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 查询成功（无表格时返回空数组） |

---

### 3.8 获取文件分块列表

获取文件的文本分块数据。

- **URL**: `GET /file/{file_id}/chunks`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl "http://localhost:5019/file/a1b2c3d4e5f6/chunks"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2c3d4e5f6",
      "chunk_id": "chunk_001",
      "chunk_index": 0,
      "total_chunks": 15,
      "chunk_content": "这是文档的第一个分块内容..."
    }
  ]
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 查询成功（无分块时返回空数组） |

---

### 3.9 获取字段提取结果

获取文件的所有字段提取结果。

- **URL**: `GET /file/{file_id}/extraction`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl "http://localhost:5019/file/a1b2c3d4e5f6/extraction"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2c3d4e5f6",
      "field_id": "company_name",
      "extracted_value": "某某科技有限公司",
      "reason": "从文档第一段'公司名称：某某科技有限公司'中提取"
    },
    {
      "file_id": "a1b2c3d4e5f6",
      "field_id": "total_revenue",
      "extracted_value": "1500000",
      "reason": "从利润表第3行'营业总收入'列提取"
    }
  ]
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 查询成功（无结果时返回空数组） |

---

### 3.10 获取逻辑分析结果

获取文件的所有逻辑分析结果。

- **URL**: `GET /file/{file_id}/analysis`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 文件 ID |

**请求示例**

```bash
curl "http://localhost:5019/file/a1b2c3d4e5f6/analysis"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2c3d4e5f6",
      "rule_id": "revenue_check",
      "result_value": "true",
      "input_values": {
        "total_revenue": "1500000",
        "threshold": "1000000"
      },
      "reason": "营业总收入1500000大于阈值1000000，判断为达标"
    }
  ]
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 查询成功（无结果时返回空数组） |

---

## 4. 字段提取配置接口

### 4.1 获取字段配置列表

获取所有已启用的字段提取配置，按优先级排序。

- **URL**: `GET /extraction/fields`

**请求示例**

```bash
curl "http://localhost:5019/extraction/fields"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "field_id": "company_name",
      "field_name": "公司名称",
      "source_type": "text",
      "enabled": 1,
      "priority": 0,
      "table_name_pattern": null,
      "table_match_type": null,
      "table_extract_prompt": null,
      "search_type": "context",
      "search_config": {"keyword": "公司"},
      "text_extract_prompt": "请从以下文本中提取公司名称",
      "created_at": "2025-01-15T10:00:00",
      "updated_at": "2025-01-15T10:00:00"
    },
    {
      "field_id": "total_revenue",
      "field_name": "营业总收入",
      "source_type": "table",
      "enabled": 1,
      "priority": 1,
      "table_name_pattern": "利润表",
      "table_match_type": "fuzzy",
      "table_extract_prompt": "请从表格中提取营业总收入金额",
      "search_type": null,
      "search_config": null,
      "text_extract_prompt": null,
      "created_at": "2025-01-15T10:00:00",
      "updated_at": "2025-01-15T10:00:00"
    }
  ]
}
```

---

### 4.2 新增/更新字段配置

根据 `field_id` 执行 upsert 操作：存在则更新，不存在则新增。

- **URL**: `POST /extraction/fields`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `field_id` | `string` | 是 | - | 字段 ID（字母数字下划线，最长 100） |
| `field_name` | `string` | 是 | - | 字段名称（最长 200） |
| `source_type` | `string` | 是 | - | 数据源类型：`table` / `text` |
| `enabled` | `int` | 否 | `1` | 是否启用：1=启用, 0=禁用 |
| `priority` | `int` | 否 | `0` | 优先级（数值越小越优先） |
| `table_name_pattern` | `string` | 否 | `null` | 表格名称匹配模式（`source_type=table` 时使用） |
| `table_match_type` | `string` | 否 | `null` | 表格匹配方式：`exact` / `fuzzy` / `contains` / `llm` |
| `table_extract_prompt` | `string` | 否 | `null` | 表格提取 Prompt |
| `search_type` | `string` | 否 | `null` | 文本检索方式：`context` / `section` / `rule` / `chunk_db` / `vector_db` |
| `search_config` | `object` | 否 | `null` | 检索配置参数 |
| `text_extract_prompt` | `string` | 否 | `null` | 文本提取 Prompt |

**请求示例（表格类字段）**

```json
{
  "field_id": "total_revenue",
  "field_name": "营业总收入",
  "source_type": "table",
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "检索到的表格如下：\n<search_result>利润表</search_result>\n\n请从表格中提取营业总收入金额，仅返回数值。"
}
```

**请求示例（文本类字段 - 单关键词）**

```json
{
  "field_id": "company_name",
  "field_name": "公司名称",
  "source_type": "text",
  "priority": 0,
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称"],
    "context_before": 100,
    "context_after": 200
  },
  "text_extract_prompt": "从以下内容中提取公司全称：\n<search_result>公司名称</search_result>\n\n请返回完整的公司名称。"
}
```

**请求示例（文本类字段 - 多关键词）**

```json
{
  "field_id": "company_info",
  "field_name": "公司基本信息",
  "source_type": "text",
  "priority": 0,
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "注册地址", "法定代表人"],
    "context_before": 100,
    "context_after": 200
  },
  "text_extract_prompt": "请从以下内容中提取公司信息：\n\n关于公司名称的内容：\n<search_result>公司名称</search_result>\n\n关于注册地址的内容：\n<search_result>注册地址</search_result>\n\n关于法定代表人的内容：\n<search_result>法定代表人</search_result>\n\n请提取：1.公司全称 2.详细地址 3.法人姓名"
}
```

**占位符说明**

| 占位符格式 | 说明 |
|-----------|------|
| `<search_result>标签</search_result>` | 搜索结果占位符，标签不可为空 |
| 文本类字段标签 | 使用 search_config.keywords 中的关键词 |
| 表格类字段标签 | 使用 table_name_pattern 匹配的表格名称 |
| 无匹配结果时 | 替换为 `（未找到 '标签' 的相关内容）` |

**响应示例（新增）**

```json
{
  "code": 200,
  "message": "字段配置已创建",
  "data": {
    "field_id": "total_revenue"
  }
}
```

**响应示例（更新）**

```json
{
  "code": 200,
  "message": "字段配置已更新",
  "data": {
    "field_id": "total_revenue"
  }
}
```

---

### 4.3 删除字段配置

软删除字段提取配置（将 `enabled` 设为 0）。

- **URL**: `DELETE /extraction/fields/{field_id}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `field_id` | `string` | 是 | 字段 ID |

**请求示例**

```bash
curl -X DELETE "http://localhost:5019/extraction/fields/total_revenue"
```

**响应示例**

```json
{
  "code": 200,
  "message": "字段配置已禁用",
  "data": null
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 删除（禁用）成功 |
| 404 | 字段配置不存在 |

---

### 4.4 检查字段 ID 是否存在

- **URL**: `GET /extraction/fields/{field_id}/check`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `field_id` | `string` | 是 | 字段 ID |

**请求示例**

```bash
curl "http://localhost:5019/extraction/fields/total_revenue/check"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "exists": true
  }
}
```

---

### 4.5 字段提取调试

调试字段提取逻辑，支持两种模式：使用已保存配置或临时配置。

- **URL**: `POST /extraction/test`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 目标文件 ID |
| `field_id` | `string` | 否* | 模式 1：使用已保存的字段配置 |
| `config` | `object` | 否* | 模式 2：使用临时配置 |

> \* `field_id` 和 `config` 必须提供其中一个。

**请求示例（模式 1：使用已保存配置）**

```json
{
  "file_id": "a1b2c3d4e5f6",
  "field_id": "company_name"
}
```

**请求示例（模式 2：使用临时配置）**

```json
{
  "file_id": "a1b2c3d4e5f6",
  "config": {
    "field_name": "测试字段",
    "source_type": "text",
    "search_type": "context",
    "search_config": {"keyword": "利润"},
    "text_extract_prompt": "请提取净利润数值"
  }
}
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "search_results": [
      {
        "table_name": "利润表",
        "table_content": "| 项目 | 金额 |\n| 净利润 | 500000 |"
      }
    ],
    "llm_input": "请从表格中提取营业总收入金额",
    "llm_output": "1500000",
    "extracted_value": "1500000",
    "reason": "从利润表第3行'营业总收入'列提取得到数值1500000"
  }
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 调试成功 |
| 400 | 未提供 `field_id` 或 `config` |
| 404 | 字段配置不存在 / 文件内容不存在 |
| 500 | 提取过程异常 |

---

## 5. 逻辑分析配置接口

### 5.1 获取分析规则列表

获取所有已启用的逻辑分析规则，按优先级排序。

- **URL**: `GET /analysis/rules`

**请求示例**

```bash
curl "http://localhost:5019/analysis/rules"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "rule_id": "revenue_check",
      "rule_name": "营收达标检查",
      "rule_type": "judge",
      "expression": "请判断营业总收入 <field_result>total_revenue</field_result> 是否大于 1000000",
      "depend_fields": ["total_revenue"],
      "enabled": 1,
      "priority": 0,
      "created_at": "2025-01-15T10:00:00",
      "updated_at": "2025-01-15T10:00:00"
    },
    {
      "rule_id": "profit_rate",
      "rule_name": "利润率计算",
      "rule_type": "calc",
      "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
      "depend_fields": ["net_profit", "total_revenue"],
      "enabled": 1,
      "priority": 1,
      "created_at": "2025-01-15T10:00:00",
      "updated_at": "2025-01-15T10:00:00"
    }
  ]
}
```

---

### 5.2 新增/更新分析规则

根据 `rule_id` 执行 upsert 操作：存在则更新，不存在则新增。

- **URL**: `POST /analysis/rules`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `rule_id` | `string` | 是 | - | 规则 ID（字母数字下划线，最长 100） |
| `rule_name` | `string` | 是 | - | 规则名称（最长 200） |
| `rule_type` | `string` | 是 | - | 规则类型：`judge`（判断） / `calc`（计算） |
| `expression` | `string` | 是 | - | 表达式（使用 `<field_result>字段标识</field_result>` 引用提取字段） |
| `depend_fields` | `string[]` | 否 | `null` | 依赖的字段 ID 列表 |
| `enabled` | `int` | 否 | `1` | 是否启用：1=启用, 0=禁用 |
| `priority` | `int` | 否 | `0` | 优先级（数值越小越优先） |

**请求示例（判断类规则）**

```json
{
  "rule_id": "revenue_check",
  "rule_name": "营收达标检查",
  "rule_type": "judge",
  "expression": "请判断营业总收入 <field_result>total_revenue</field_result> 是否大于 1000000",
  "depend_fields": ["total_revenue"]
}
```

**请求示例（计算类规则）**

```json
{
  "rule_id": "profit_rate",
  "rule_name": "利润率计算",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"]
}
```

**字段占位符说明**

| 占位符格式 | 说明 |
|-----------|------|
| `<field_result>字段标识</field_result>` | 字段结果占位符，标签不可为空 |
| 字段标识 | 使用 depend_fields 中声明的 field_id |
| 无提取结果时 | 替换为 `（未找到字段 '字段标识' 的提取结果）` |
```

**响应示例（新增）**

```json
{
  "code": 200,
  "message": "规则配置已创建",
  "data": {
    "rule_id": "revenue_check"
  }
}
```

**响应示例（更新）**

```json
{
  "code": 200,
  "message": "规则配置已更新",
  "data": {
    "rule_id": "revenue_check"
  }
}
```

---

### 5.3 删除分析规则

软删除逻辑分析规则（将 `enabled` 设为 0）。

- **URL**: `DELETE /analysis/rules/{rule_id}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `rule_id` | `string` | 是 | 规则 ID |

**请求示例**

```bash
curl -X DELETE "http://localhost:5019/analysis/rules/revenue_check"
```

**响应示例**

```json
{
  "code": 200,
  "message": "规则配置已禁用",
  "data": null
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 删除（禁用）成功 |
| 404 | 规则配置不存在 |

---

### 5.4 检查规则 ID 是否存在

- **URL**: `GET /analysis/rules/{rule_id}/check`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `rule_id` | `string` | 是 | 规则 ID |

**请求示例**

```bash
curl "http://localhost:5019/analysis/rules/revenue_check/check"
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "exists": true
  }
}
```

---

### 5.5 逻辑分析调试

调试逻辑分析规则，支持两种模式：使用已保存配置或临时配置。

- **URL**: `POST /analysis/test`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 目标文件 ID |
| `rule_id` | `string` | 否* | 模式 1：使用已保存的规则配置 |
| `config` | `object` | 否* | 模式 2：使用临时配置 |

> \* `rule_id` 和 `config` 必须提供其中一个。

**请求示例（模式 1：使用已保存配置）**

```json
{
  "file_id": "a1b2c3d4e5f6",
  "rule_id": "revenue_check"
}
```

**请求示例（模式 2：使用临时配置）**

```json
{
  "file_id": "a1b2c3d4e5f6",
  "config": {
    "rule_type": "calc",
    "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
    "depend_fields": ["net_profit", "total_revenue"]
  }
}
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "input_values": {
      "net_profit": "500000",
      "total_revenue": "1500000"
    },
    "expression_resolved": "500000 / 1500000 * 100",
    "result_value": "33.33",
    "reason": "计算公式: 500000 / 1500000 * 100 = 33.33"
  }
}
```

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 调试成功 |
| 400 | 未提供 `rule_id` 或 `config` |
| 404 | 规则配置不存在 |
| 500 | 分析过程异常 |

---

## 6. 向量检索接口

### 6.1 向量相似度检索

基于 Milvus 向量数据库进行语义相似度检索。

- **URL**: `POST /search`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | `string` | 是 | - | 检索查询文本 |
| `file_id` | `string` | 否 | `null` | 限定检索范围的文件 ID |
| `top_k` | `int` | 否 | `10` | 返回结果数量 |
| `score_threshold` | `float` | 否 | `null` | 分数阈值（L2 距离，越小越相似） |

**请求示例**

```json
{
  "query": "公司营业收入情况",
  "file_id": "a1b2c3d4e5f6",
  "top_k": 5,
  "score_threshold": 0.8
}
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "chunk_id": "chunk_003",
      "file_id": "a1b2c3d4e5f6",
      "chunk_index": 3,
      "chunk_content": "公司2024年度营业收入为1500万元，同比增长15%...",
      "score": 0.25
    },
    {
      "chunk_id": "chunk_007",
      "file_id": "a1b2c3d4e5f6",
      "chunk_index": 7,
      "chunk_content": "营业收入构成中，主营业务收入占比85%...",
      "score": 0.38
    }
  ]
}
```

---

## 7. 数据模型说明

### 7.1 数据库表结构

#### files - 文件主表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `file_id` | VARCHAR(64) | PK | 文件唯一标识 |
| `file_name` | VARCHAR(512) | NOT NULL | 文件名称 |
| `file_size` | BIGINT | DEFAULT 0 | 文件大小（字节） |
| `create_time` | DATETIME | DEFAULT NOW() | 创建时间 |
| `start_parsing_time` | DATETIME | NULLABLE | 开始解析时间 |
| `end_parsing_time` | DATETIME | NULLABLE | 解析完成时间 |
| `start_chunking_time` | DATETIME | NULLABLE | 开始分块时间 |
| `end_chunking_time` | DATETIME | NULLABLE | 分块完成时间 |
| `start_embedding_time` | DATETIME | NULLABLE | 开始向量化时间 |
| `end_embedding_time` | DATETIME | NULLABLE | 向量化完成时间 |
| `end_extracting_time` | DATETIME | NULLABLE | 字段提取完成时间 |
| `end_analyzing_time` | DATETIME | NULLABLE | 逻辑分析完成时间 |
| `progress` | VARCHAR(32) | DEFAULT "parsing" | 处理进度状态 |
| `error` | TEXT | NULLABLE | 错误信息 |
| `updated_at` | DATETIME | AUTO UPDATE | 最后更新时间 |

#### file_content - 文件内容表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `file_id` | VARCHAR(64) | PK | 文件 ID |
| `file_content` | LONGTEXT | NOT NULL | 文件解析后的全文内容 |

#### file_table - 文件表格表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `file_id` | VARCHAR(64) | PK (复合) | 文件 ID |
| `table_index` | INT | PK (复合) | 表格序号 |
| `total_table` | INT | DEFAULT 0 | 表格总数 |
| `table_name` | VARCHAR(500) | DEFAULT "" | 表格名称 |
| `table_content` | LONGTEXT | NOT NULL | 表格内容 |

#### file_chunk - 文件分块表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `file_id` | VARCHAR(64) | PK (复合) | 文件 ID |
| `chunk_id` | VARCHAR(64) | PK (复合) | 分块 ID |
| `chunk_index` | INT | DEFAULT 0 | 分块序号 |
| `total_chunks` | INT | DEFAULT 0 | 分块总数 |
| `chunk_content` | TEXT | NOT NULL | 分块内容 |

#### extraction_field - 字段提取配置表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `field_id` | VARCHAR(100) | PK | 字段 ID |
| `field_name` | VARCHAR(200) | NOT NULL | 字段名称 |
| `source_type` | ENUM("table","text") | NOT NULL | 数据源类型 |
| `enabled` | TINYINT | DEFAULT 1 | 是否启用 |
| `priority` | INT | DEFAULT 0 | 优先级 |
| `created_at` | DATETIME | DEFAULT NOW() | 创建时间 |
| `updated_at` | DATETIME | AUTO UPDATE | 更新时间 |
| `table_name_pattern` | VARCHAR(500) | NULLABLE | 表格名称匹配模式 |
| `table_match_type` | ENUM("exact","fuzzy","contains","llm") | NULLABLE | 表格匹配方式 |
| `table_extract_prompt` | TEXT | NULLABLE | 表格提取 Prompt |
| `search_type` | ENUM("context","section","rule","chunk_db","vector_db") | NULLABLE | 文本检索方式 |
| `search_config` | JSON | NULLABLE | 检索配置 |
| `text_extract_prompt` | TEXT | NULLABLE | 文本提取 Prompt |

#### analysis_rule - 逻辑分析规则表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `rule_id` | VARCHAR(100) | PK | 规则 ID |
| `rule_name` | VARCHAR(200) | NOT NULL | 规则名称 |
| `rule_type` | ENUM("judge","calc") | NOT NULL | 规则类型 |
| `expression` | TEXT | NOT NULL | 表达式 |
| `depend_fields` | JSON | NULLABLE | 依赖字段 ID 列表 |
| `enabled` | TINYINT | DEFAULT 1 | 是否启用 |
| `priority` | INT | DEFAULT 0 | 优先级 |
| `created_at` | DATETIME | DEFAULT NOW() | 创建时间 |
| `updated_at` | DATETIME | AUTO UPDATE | 更新时间 |

#### extraction_result - 提取结果表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `file_id` | VARCHAR(64) | PK (复合) | 文件 ID |
| `field_id` | VARCHAR(100) | PK (复合) | 字段 ID |
| `extracted_value` | TEXT | DEFAULT "" | 提取值 |
| `reason` | TEXT | NULLABLE | 提取理由/依据 |

#### analysis_result - 分析结果表

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `file_id` | VARCHAR(64) | PK (复合) | 文件 ID |
| `rule_id` | VARCHAR(100) | PK (复合) | 规则 ID |
| `result_value` | VARCHAR(500) | DEFAULT "" | 分析结果 |
| `input_values` | JSON | NULLABLE | 输入字段值 |
| `reason` | TEXT | NULLABLE | 分析理由/依据 |

---

## 8. 枚举值说明

### 8.1 SourceTypeEnum - 数据源类型

| 值 | 说明 |
|------|------|
| `table` | 从文档表格中提取 |
| `text` | 从文本内容中提取 |

### 8.2 TableMatchTypeEnum - 表格匹配方式

| 值 | 说明 |
|------|------|
| `exact` | 精确匹配表格名称 |
| `fuzzy` | 模糊匹配表格名称 |
| `contains` | 表格名称包含指定模式 |
| `llm` | 使用 LLM 进行语义匹配 |

### 8.3 SearchTypeEnum - 文本检索方式

| 值 | 说明 |
|------|------|
| `context` | 上下文检索 |
| `section` | 章节检索 |
| `rule` | 规则检索 |
| `chunk_db` | 数据库分块检索（MySQL） |
| `vector_db` | 向量数据库检索（Milvus） |

### 8.4 RuleTypeEnum - 分析规则类型

| 值 | 说明 |
|------|------|
| `judge` | 判断类规则，返回布尔结果（true/false） |
| `calc` | 计算类规则，返回数值结果（精度：2 位小数） |

### 8.5 文件处理进度状态

| 值 | 说明 |
|------|------|
| `parsing` | 正在解析文件 |
| `parsing_failed` | 文件解析失败 |
| `chunking` | 正在分块 |
| `chunking_failed` | 分块失败 |
| `embedding` | 正在向量化 |
| `embedding_failed` | 向量化失败 |
| `extracting` | 正在提取字段 |
| `extracting_failed` | 字段提取失败 |
| `analyzing` | 正在逻辑分析 |
| `analyzing_failed` | 逻辑分析失败 |
| `complete` | 处理完成 |

---

## 9. 错误码说明

### 9.1 HTTP 状态码

| 状态码 | 说明 | 场景 |
|--------|------|------|
| 200 | 成功 | 请求处理成功 |
| 400 | 请求错误 | 参数校验失败、文件超过大小限制、无效的阶段名称 |
| 404 | 资源不存在 | 文件/字段配置/规则配置不存在 |
| 409 | 冲突 | 文件正在处理中，不可重复提交 |
| 422 | 参数校验错误 | Pydantic 模型校验失败（FastAPI 自动返回） |
| 500 | 服务器内部错误 | 提取/分析过程异常 |

### 9.2 业务错误响应格式

当返回非 200 状态码时，响应体格式为：

**ResponseWrapper 格式（code=400）**

```json
{
  "code": 400,
  "message": "文件大小超过限制 (100MB)",
  "data": null
}
```

**HTTPException 格式（FastAPI 默认）**

```json
{
  "detail": "文件不存在"
}
```

### 9.3 Pydantic 校验错误格式（422）

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["body", "field_id"],
      "msg": "String should match pattern '^[a-zA-Z0-9_]+$'",
      "input": "invalid-id!",
      "ctx": {"pattern": "^[a-zA-Z0-9_]+$"}
    }
  ]
}
```

---

## 附录：接口总览

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | POST | `/file/parse` | 提交文件解析 |
| 2 | GET | `/file/{file_id}/status` | 查询处理进度 |
| 3 | DELETE | `/file/{file_id}` | 删除文件 |
| 4 | POST | `/file/{file_id}/retry/{stage}` | 从指定阶段重试 |
| 5 | POST | `/file/{file_id}/retry/extracting` | 重试字段提取 |
| 6 | POST | `/file/{file_id}/retry/analyzing` | 重试逻辑分析 |
| 7 | GET | `/file/{file_id}/tables` | 获取表格列表 |
| 8 | GET | `/file/{file_id}/chunks` | 获取分块列表 |
| 9 | GET | `/file/{file_id}/extraction` | 获取提取结果 |
| 10 | GET | `/file/{file_id}/analysis` | 获取分析结果 |
| 11 | GET | `/extraction/fields` | 获取字段配置列表 |
| 12 | POST | `/extraction/fields` | 新增/更新字段配置 |
| 13 | DELETE | `/extraction/fields/{field_id}` | 删除字段配置 |
| 14 | GET | `/extraction/fields/{field_id}/check` | 检查字段 ID 是否存在 |
| 15 | POST | `/extraction/test` | 字段提取调试 |
| 16 | GET | `/analysis/rules` | 获取分析规则列表 |
| 17 | POST | `/analysis/rules` | 新增/更新分析规则 |
| 18 | DELETE | `/analysis/rules/{rule_id}` | 删除分析规则 |
| 19 | GET | `/analysis/rules/{rule_id}/check` | 检查规则 ID 是否存在 |
| 20 | POST | `/analysis/test` | 逻辑分析调试 |
| 21 | POST | `/search` | 向量相似度检索 |
