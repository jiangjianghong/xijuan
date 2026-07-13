# 析卷 AI - API 接口文档

> **版本**: 0.3.0
> **技术栈**: FastAPI + SQLAlchemy (async) + MySQL + Milvus + DashScope (Qwen / Qwen-VL)
> **管线阶段**: parsing → tableing → chunking → embedding → extracting → analyzing → complete

---

## 目录

1. [基础信息](#1-基础信息)
2. [通用响应格式](#2-通用响应格式)
3. [文档类型接口 `/doctype/*`](#3-文档类型接口-doctype)
4. [文件处理接口 `/file/*`](#4-文件处理接口-file)
5. [字段提取配置接口 `/extraction/*`](#5-字段提取配置接口-extraction)
6. [逻辑分析配置接口 `/analysis/*`](#6-逻辑分析配置接口-analysis)
7. [向量检索接口 `/search`](#7-向量检索接口-search)
8. [异步回调约定](#8-异步回调约定)
9. [SSE 事件清单](#9-sse-事件清单)
10. [数据模型说明](#10-数据模型说明)
11. [枚举值说明](#11-枚举值说明)
12. [错误码说明](#12-错误码说明)
13. [附录：接口总览](#13-附录接口总览)

---

## 1. 基础信息

| 项目 | 说明 |
|------|------|
| Base URL | `http://{host}:5019` |
| 协议 | HTTP |
| 数据格式 | JSON（`Content-Type: application/json`） |
| 文件上传 | `multipart/form-data` |
| SSE 流 | `text/event-stream` |
| 认证方式 | 无（当前版本） |
| 文件 ID 生成 | `SHA256[:32]( (type_id, file_name, time.time_ns(), secrets.token_hex(8)) )` —— 每次上传都是新 ID，不做去重 |
| 默认文档类型 | `default`（系统启动时自动创建，不可删除） |

---

## 2. 通用响应格式

所有业务接口统一返回 `ResponseWrapper`：

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

> 部分校验/4xx 错误使用 FastAPI 默认的 `HTTPException`，响应体格式为 `{"detail": "..."}`，详见第 12 节。

---

## 3. 文档类型接口 `/doctype`

文档类型用于隔离不同格式文件的抽取字段与逻辑规则配置。每个文件归属唯一 `type_id`（默认 `default`），抽取字段与规则按 `type_id` 隔离，**配置不跨类型共享**。

类型有**血缘 + 项目**两个维度。血缘：`is_template`（模板标记，由 [3.7](#37-副本模板提升--取消) promote/demote 切换）+ `parent_type_id`（复制来源，由 [3.4](#34-复制配置同实例跨类型) `copy_from` 自动记录）。项目：`project_id`（可空=未分组）对「模板 + 其血缘下游」分类，一个类型属 ≤1 个项目，`default` 恒未分组，详见 [3.9](#39-项目分类)。顶部导航两级——先选项目，文档类型下拉只显示该项目下的类型。

### 3.1 列出 / 搜索文档类型

- **URL**: `GET /doctype/list`

**查询参数**（全部可选；不传任何参数即旧行为）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `q` | `string` | - | 模糊搜索 `type_id` / `type_name` |
| `scope` | `string` | `all` | 范围过滤：`all` / `template`（模板，含默认类型）/ `copy`（副本，即非模板且非默认） |
| `project_id` | `string` | - | 项目过滤：具体 `project_id` 只返成员；`__ungrouped__` 只返未分组（含默认类型） |
| `page` | `int` | - | 页码，≥1 |
| `page_size` | `int` | - | 每页条数，1–500 |
| `sort` | `string` | `created_at` | 排序：`created_at`（倒序）/ `type_name`（升序）；默认类型恒置顶 |

**响应形态**

- **传齐 `page` + `page_size`** → `data` 为分页对象 `{ "items": [...], "total": N }`
- **否则** → `data` 为数组（**向后兼容**，旧调用方无需改动）

> 文件/字段/规则计数对当前结果集用 3 条 `GROUP BY` 聚合，不再逐类型查询（修复 N+1）。

**单项字段**（在原有基础上新增 `is_template` / `parent_type_id` / `project_id` / `project_name`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_default` | `int` | 1=默认类型 |
| `is_template` | `int` | 1=模板 |
| `parent_type_id` | `string \| null` | 复制来源类型（`copy_from` 自动记录） |
| `project_id` | `string \| null` | 归属项目（`null`=未分组） |
| `project_name` | `string \| null` | 归属项目名称（`project_id` 为空则为 `null`） |
| `file_count` / `field_count` / `rule_count` | `int` | 关联计数 |

**分页响应示例**（`?scope=copy&page=1&page_size=20`）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "type_id": "annual_report",
        "type_name": "年报",
        "description": "上市公司年报",
        "is_default": 0,
        "enabled": 1,
        "is_template": 0,
        "parent_type_id": "annual_report_tpl",
        "created_at": "2025-02-01T10:00:00",
        "updated_at": "2025-02-01T10:00:00",
        "file_count": 30,
        "field_count": 15,
        "rule_count": 5
      }
    ],
    "total": 42
  }
}
```

**数组响应示例**（不传分页参数）

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "type_id": "default",
      "type_name": "默认类型",
      "description": null,
      "is_default": 1,
      "enabled": 1,
      "is_template": 0,
      "parent_type_id": null,
      "created_at": "2025-01-15T10:00:00",
      "updated_at": "2025-01-15T10:00:00",
      "file_count": 12,
      "field_count": 8,
      "rule_count": 3
    }
  ]
}
```

---

### 3.2 新增/更新文档类型

按 `type_id` upsert：存在则更新（不能改 `is_default`），不存在则新增。

- **URL**: `POST /doctype`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `type_id` | `string` | 是 | - | 类型 ID，正则 `^[a-zA-Z0-9_-]+$`，最长 64 |
| `type_name` | `string` | 是 | - | 类型名称，最长 200 |
| `description` | `string` | 否 | `null` | 类型描述 |
| `enabled` | `int` | 否 | `1` | 是否启用：1=启用, 0=禁用 |

**响应示例**

```json
{
  "code": 200,
  "message": "类型已创建",
  "data": {"type_id": "annual_report"}
}
```

---

### 3.3 删除文档类型

- **URL**: `DELETE /doctype/{type_id}`

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `force` | `bool` | `false` | 是否级联删除该类型下所有文件、字段、规则、Milvus 向量、`uploads/{file_id}.pdf` |

**约束**

- 默认类型（`is_default=1`）禁止删除 → 400
- 非默认类型有关联文件/字段/规则但未传 `force=true` → 409

**响应示例（force=true）**

```json
{
  "code": 200,
  "message": "类型已删除",
  "data": {
    "type_id": "annual_report",
    "deleted_files": 30,
    "deleted_fields": 15,
    "deleted_rules": 5
  }
}
```

---

### 3.4 复制配置（同实例跨类型）

从源类型复制字段+规则到目标类型，**生成全新 ID** 的独立副本（编辑互不影响）。复制时字段名 / 规则名保持不变，新 `field_id` / `rule_id` 基于源 ID 自动编号，例如 `amount` → `amount_0002`；再次复制为 `amount_0003`。

- **URL**: `POST /doctype/{type_id}/copy_from`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `source_type_id` | `string` | 是 | - | 源类型 ID（不能等于目标 `type_id`） |
| `field_ids` | `string[] \| null` | 否 | `null`（全部） | 仅复制指定字段；不传 / `null` 表示全部，空数组 `[]` 表示不复制字段 |
| `rule_ids` | `string[] \| null` | 否 | `null`（全部） | 仅复制指定规则；不传 / `null` 表示全部，空数组 `[]` 表示不复制规则 |
| `on_conflict` | `string` | 否 | `"rename"` | 同源 ID 副本冲突策略：`rename` 自动取下一个 `_000N`；`skip` 在目标类型已有该源 ID 的副本时跳过 |

**响应示例**

```json
{
  "code": 200,
  "message": "配置复制完成",
  "data": {
    "copied_fields": 8,
    "skipped_fields": 0,
    "copied_rules": 3,
    "skipped_rules": 0,
    "missing_dependencies": ["利润率检查::net_profit"]
  }
}
```

`missing_dependencies` 列出**规则 depend_fields** 中未随本次复制一起复制的源字段 ID（格式 `规则名::源field_id`），不会自动跳过规则，调用方需自行处理。规则依赖按源 `field_id` 精确重映射到本次生成的新 `field_id`，不再按字段名猜测。

**副作用（自动记录血缘）**：复制成功后，若目标类型不是默认类型，则记录 `parent_type_id = source_type_id`（血缘来源）。

---

### 3.5 导出配置（跨实例迁移）

把指定类型的字段+规则序列化为 JSON 载荷，可保存后通过 `POST /doctype/import` 恢复到其他环境。

- **URL**: `GET /doctype/{type_id}/export`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "type_id": "annual_report",
    "type_name": "年报",
    "description": "上市公司年报",
    "version": 1,
    "fields": [
      {
        "field_id": "company_name",
        "field_name": "公司名称",
        "source_type": "text",
        "enabled": 1,
        "priority": 0,
        "search_type": "context",
        "search_config": {"keywords": ["公司名称"]},
        "text_extract_prompt": "...",
        "table_name_pattern": null,
        "table_match_type": null,
        "table_match_keywords": null,
        "table_match_max_results": null,
        "table_system_prompt": null,
        "table_extract_prompt": null,
        "text_system_prompt": null,
        "vl_method": null,
        "vl_config": null,
        "vl_system_prompt": null,
        "vl_extract_prompt": null
      }
    ],
    "rules": [
      {
        "rule_id": "revenue_check",
        "rule_name": "营收达标检查",
        "rule_type": "judge",
        "expression": "请判断营业总收入 <field_result>total_revenue</field_result> 是否大于 1000000",
        "system_prompt": null,
        "depend_field_names": ["营业总收入"],
        "enabled": 1,
        "priority": 0
      }
    ]
  }
}
```

> 规则依赖以 **`field_name`** 序列化（不依赖原 `field_id`），导入时按字段名重映射。

---

### 3.6 导入配置

- **URL**: `POST /doctype/import`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `payload` | `ExportPayload` | 是 | - | 由 `GET /doctype/{type_id}/export` 产生的载荷 |
| `target_type_id` | `string` | 否 | `payload.type_id` | 目标类型 ID。**空时取 payload 中的值** |
| `create_type_if_missing` | `bool` | 否 | `true` | 目标类型不存在时是否自动创建 |
| `on_conflict` | `string` | 否 | `"rename"` | 同名冲突策略：`skip` / `rename` |

**响应示例**

```json
{
  "code": 200,
  "message": "配置导入完成",
  "data": {
    "target_type_id": "annual_report_v2",
    "created_type": true,
    "copied_fields": 8,
    "skipped_fields": 0,
    "copied_rules": 3,
    "skipped_rules": 0,
    "missing_dependencies": []
  }
}
```

字段 `field_id` 始终重新生成（避免与全局 `field_id` 唯一约束冲突）。

---

### 3.7 副本↔模板：提升 / 取消

把普通/副本类型标记为「模板」（或取消标记）。**模板 + 默认类型**才会出现在顶部类型选择器与 `scope=template` 过滤里；提升不影响 `parent_type_id` 血缘。

- **URL**: `POST /doctype/{type_id}/promote`（标记为模板）
- **URL**: `POST /doctype/{type_id}/demote`（取消模板标记）

**约束**

- 类型不存在 → 404
- 默认类型（`is_default=1`）→ 400（无需 / 不可操作）

**响应示例**

```json
{ "code": 200, "message": "已标记为模板", "data": {"type_id": "annual_report"} }
```

---

### 3.8 批量删除文档类型

逐条删除多个类型，单条失败不影响其余；默认类型、不存在、有数据但未 `force` 的会被**跳过并记录原因**（不抛 HTTP 错误）。

- **URL**: `POST /doctype/batch_delete`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `type_ids` | `string[]` | 是 | - | 待删除的类型 ID 列表 |
| `force` | `bool` | 否 | `false` | 是否级联删除文件/字段/规则/Milvus/PDF（同单删的 `force`） |

**响应示例**

```json
{
  "code": 200,
  "message": "批量删除完成：成功 2/4",
  "data": {
    "deleted": 2,
    "results": [
      {"type_id": "t1", "ok": true, "deleted_files": 3, "deleted_fields": 2, "deleted_rules": 1},
      {"type_id": "default", "ok": false, "reason": "默认类型不可删除"},
      {"type_id": "no_such", "ok": false, "reason": "类型不存在"},
      {"type_id": "t2", "ok": true, "deleted_files": 0, "deleted_fields": 0, "deleted_rules": 0}
    ]
  }
}
```

> 与单删 `DELETE /doctype/{type_id}` 共用底层删除逻辑（`_delete_one_type`）。

---

### 3.9 项目分类

项目（`project` 表）把「模板 + 其血缘下游」归为一组，便于管理大量类型。一个类型归属 ≤1 个项目（`project_id`，可空=未分组），`default` 恒未分组。

**核心语义**

- **归类级联血缘**：把某类型归入项目时，其所有 `parent_type_id` 传递后代一并带入同一项目（抓模板即整条血缘进组）。
- **复制继承**：`copy_from` / 派生出的新类型未分组时，继承源类型的项目。
- 删项目只解除成员归属（`project_id` 置空），**不删除类型**。

#### 3.9.1 列出项目

- **URL**: `GET /doctype/projects`

返回 `data` 为数组，每项 `{project_id, project_name, description, type_count, created_at, updated_at}`；`type_count` 为该项目下类型数。

#### 3.9.2 新增 / 改名项目

- **URL**: `POST /doctype/projects`（按 `project_id` upsert）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | `string` | 是 | 匹配 `^[a-zA-Z0-9_-]+$`（最长 64），作 upsert 主键 |
| `project_name` | `string` | 是 | 项目名称（最长 200） |
| `description` | `string` | 否 | 描述 |

#### 3.9.3 删除项目

- **URL**: `DELETE /doctype/projects/{project_id}`

成员类型的 `project_id` 置空（变未分组）后删除项目本身；项目不存在 → 404。

#### 3.9.4 批量归类到项目

- **URL**: `POST /doctype/batch_assign_project`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type_ids` | `string[]` | 是 | 待归类类型（各自血缘后代一并带入） |
| `project_id` | `string \| null` | 是 | 目标项目；`null`=移出（未分组） |

`project_id` 非空但项目不存在 → 404；默认类型跳过（恒未分组）。返回 `data={requested, affected, project_id}`，`affected` 为实际写入数（含级联带入的后代）。

```json
{ "code": 200, "message": "批量归类完成", "data": {"requested": 1, "affected": 3, "project_id": "annual_reports"} }
```

---

## 4. 文件处理接口 `/file`

### 4.1 提交文件解析

- **URL**: `POST /file/parse`
- **Content-Type**: `multipart/form-data`

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|------|--------|------|
| `file` | form-data | `File` | 是 | - | 上传的文件（受 `mineru.max_file_size` 限制，默认 100MB） |
| `mode` | query | `string` | 否 | `"async"` | 处理模式：`sync` / `async` / `stream` |
| `type_id` | query | `string` | 否 | `"default"` | 归属的文档类型，决定使用哪一套字段/规则 |
| `callback_url` | query | `string` | 否 | - | 异步回调地址，详见第 8 节 |

**响应示例（async / sync）**

```json
{
  "code": 200,
  "message": "文件已提交处理（异步）",
  "data": {"file_id": "a1b2c3d4e5f6..."}
}
```

**响应（stream 模式）** 返回 `text/event-stream`，事件序列见第 9 节。

**重要约束**

- `file_id` 由 `(type_id, file_name, 当前纳秒时间戳, 随机盐)` SHA256[:32] 生成。**每次上传都产生新的 `file_id`，没有服务端去重**。
- 同时持久化原始 PDF 字节到 `uploads/{file_id}.pdf`，供 VL 抽取使用；写盘失败不阻断主流程。
- 失败重试请使用 `POST /file/{file_id}/retry/{stage}`，**不要**重新上传（会产生新记录）。

**状态码**

| 状态码 | 说明 |
|--------|------|
| 200 | 提交成功 / 处理完成 |
| 400 | 文件大小超限（`code=400` 包在 `ResponseWrapper` 中） |

---

### 4.2 文件分页列表

- **URL**: `GET /file/list`

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | `int` | `1` | 页码（从 1 开始） |
| `page_size` | `int` | `20` | 每页条数 |
| `status` | `string` | `""` | 按 `progress` 精确匹配过滤（见第 11.6 节） |
| `type_id` | `string` | `""` | 按 `type_id` 精确匹配过滤 |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "file_id": "a1b2...",
        "file_name": "report.pdf",
        "file_size": 2048576,
        "progress": "complete",
        "type_id": "annual_report",
        "error": null,
        "create_time": "2025-01-15T10:30:00"
      }
    ],
    "total": 30,
    "page": 1,
    "page_size": 20,
    "total_pages": 2
  }
}
```

---

### 4.3 批量删除文件

- **URL**: `DELETE /file/batch`
- **Content-Type**: `application/json`

**请求体**

```json
{"file_ids": ["a1b2...", "c3d4..."]}
```

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "deleted_count": 2,
    "failed_ids": []
  }
}
```

不存在的 `file_id` 会进入 `failed_ids`；Milvus / PDF 文件清理失败仅记录 warning，不影响 MySQL 删除。

---

### 4.4 查询文件状态

- **URL**: `GET /file/{file_id}/status`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "a1b2...",
    "file_name": "report.pdf",
    "file_size": 2048576,
    "progress": "complete",
    "type_id": "annual_report",
    "error": null,
    "create_time": "2025-01-15T10:30:00",
    "updated_at": "2025-01-15T10:32:00"
  }
}
```

**状态码**: 200 / 404（文件不存在）

---

### 4.5 文件完整详情

返回所有阶段的开始/结束时间戳，可计算阶段耗时。

- **URL**: `GET /file/{file_id}/detail`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "a1b2...",
    "file_name": "report.pdf",
    "file_size": 2048576,
    "progress": "complete",
    "type_id": "annual_report",
    "error": null,
    "create_time": "2025-01-15T10:30:00",
    "updated_at": "2025-01-15T10:35:00",
    "start_parsing_time": "2025-01-15T10:30:00",
    "end_parsing_time": "2025-01-15T10:31:20",
    "start_tableing_time": "2025-01-15T10:31:20",
    "end_tableing_time": "2025-01-15T10:31:30",
    "start_chunking_time": "2025-01-15T10:31:30",
    "end_chunking_time": "2025-01-15T10:31:45",
    "start_embedding_time": "2025-01-15T10:31:45",
    "end_embedding_time": "2025-01-15T10:32:00",
    "end_extracting_time": "2025-01-15T10:34:10",
    "end_analyzing_time": "2025-01-15T10:35:00"
  }
}
```

**状态码**: 200 / 404

---

### 4.6 删除文件

删除文件及所有关联数据：MySQL（files / file_content / file_table / file_chunk / extraction_result / analysis_result）、Milvus 向量、`uploads/{file_id}.pdf`。

- **URL**: `DELETE /file/{file_id}`

**响应示例**

```json
{"code": 200, "message": "文件已删除", "data": null}
```

**状态码**: 200 / 404

---

### 4.7 从指定阶段重试

清掉指定阶段及其下游已有数据，从该阶段重跑。

- **URL**: `POST /file/{file_id}/retry/{stage}`

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `file_id` | `string` | 文件 ID |
| `stage` | `string` | 阶段：`tableing` / `chunking` / `embedding` / `extracting` / `analyzing`。兼容旧别名：`table_name_validating` → `tableing` |

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | `string` | `"async"` | 处理模式：`async` / `stream` / `sync` |
| `callback_url` | `string` | - | 回调地址（仅 async/sync 模式使用） |

**响应示例（async）**

```json
{"code": 200, "message": "已从 embedding 阶段开始重试", "data": null}
```

**stream 模式** 返回 SSE 流，事件类型与 `POST /file/parse?mode=stream` 一致。

**状态码**: 200 / 400（无效阶段名）/ 404（文件不存在）

---

### 4.8 快捷重试：字段提取 / 逻辑分析

完全等价于 `POST /file/{file_id}/retry/extracting` 和 `POST /file/{file_id}/retry/analyzing`。

- **URL**: `POST /file/{file_id}/retry/extracting`
- **URL**: `POST /file/{file_id}/retry/analyzing`

查询参数与响应同 4.7。

---

### 4.9 文件表格列表

- **URL**: `GET /file/{file_id}/tables`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2...",
      "table_index": 0,
      "total_table": 3,
      "table_name": "财务报表",
      "table_content": "<table>...</table>",
      "page_num": "12"
    }
  ]
}
```

无数据时返回 `[]`。

---

### 4.10 文件分块列表

- **URL**: `GET /file/{file_id}/chunks`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2...",
      "chunk_id": "chunk_001",
      "chunk_index": 0,
      "total_chunks": 15,
      "chunk_content": "...",
      "page_num": "3-4"
    }
  ]
}
```

---

### 4.10.1 文件片段上下文查询

按请求体中的 `file_id` 和关键词 / MinerU Markdown 片段查询命中上下文、片段页码，并返回该文件全部分块。该接口不把 `file_id` 放在 URL 中。

- **URL**: `POST /file/context_query`

**请求体**

```json
{
  "file_id": "a1b2...",
  "query": "合同金额",
  "query_type": "keyword",
  "context_before": 200,
  "context_after": 200,
  "case_sensitive": false,
  "include_all_chunks": true
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---:|---:|---:|---|
| `file_id` | string | 是 | 无 | 文件 ID，项目内统一字段名 |
| `query` | string | 是 | 无 | 关键词或 MinerU Markdown 中的文本片段 |
| `query_type` | string | 否 | `keyword` | `keyword` / `text_fragment`，MVP 均按精确文本查找 |
| `context_before` | int | 否 | `200` | 命中位置前返回的字符数 |
| `context_after` | int | 否 | `200` | 命中位置后返回的字符数 |
| `case_sensitive` | bool | 否 | `false` | 是否大小写敏感 |
| `include_all_chunks` | bool | 否 | `true` | 是否返回该文件全部 `file_chunk` 分块 |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "a1b2...",
    "query": "合同金额",
    "query_type": "keyword",
    "matched": true,
    "match_count": 1,
    "matches": [
      {
        "match_index": 1,
        "keyword": "合同金额",
        "position": 1234,
        "match_start_pos": 1234,
        "match_end_pos": 1238,
        "context_start_pos": 1034,
        "context_end_pos": 1438,
        "context": "...合同金额...",
        "page_num": "5",
        "bboxes": []
      }
    ],
    "chunks": [
      {
        "file_id": "a1b2...",
        "chunk_id": "chunk_001",
        "chunk_index": 0,
        "total_chunks": 15,
        "chunk_content": "...",
        "start_pos": 0,
        "end_pos": 512,
        "page_num": "3-4",
        "hit": false,
        "hit_count": 0
      }
    ]
  }
}
```

说明：

- `page_num` 按命中片段本身计算，不按上下文窗口计算。
- `chunks` 默认返回该文件全部分块；每个分块通过 `hit/hit_count` 标记是否包含命中片段。
- 文件内容不存在或尚未解析完成时返回 404。

---

### 4.11 文件大纲（章节切片）

正则解析 Markdown 章节标题（与抽取阶段 `search_type=section` 用同一套口径）。

- **URL**: `GET /file/{file_id}/outline`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "index": 0,
      "number": "1",
      "title": "公司简介",
      "content": "# 1 公司简介\n...",
      "start_pos": 0,
      "end_pos": 1532
    }
  ]
}
```

文件不存在或内容为空时返回 `[]`（不返回 404）。

---

### 4.11.1 按页 Markdown 内容

基于 `parsing` 阶段落库的 `page_mapping`，把整篇 Markdown 逐页切分，按页码升序返回。

- **URL**: `GET /file/{file_id}/content`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {"page_num": 1, "content": "# 公司简介\n..."},
    {"page_num": 2, "content": "## 主营业务\n..."}
  ]
}
```

说明：

- 首页并入首个块锚点前的前导内容，末页切到文末，纯空白页跳过。
- 文件不存在 / 内容为空 / 无 `page_mapping`（存量老文件重解析前无逐页锚点）返回 `[]`（不返回 404），与 `/tables`、`/chunks`、`/outline` 一致。

---

### 4.12 字段提取结果

- **URL**: `GET /file/{file_id}/extraction`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2...",
      "field_id": "company_name",
      "field_name": "公司名称",
      "extracted_value": "某某科技有限公司",
      "reason": "从文档第一段提取",
      "source_refs": {
        "公司名称": [
          {
            "type": "context",
            "start_pos": 120,
            "end_pos": 420,
            "page_num": "1",
            "text": "……公司名称：某某科技有限公司……",
            "bboxes": [
              {"page_num": 1, "bbox": [88.0, 72.5, 507.3, 96.1], "page_size": [595.0, 842.0]}
            ]
          }
        ],
        "_texts": {"公司名称": "……拼接后实际注入占位符的完整文本……"}
      }
    }
  ]
}
```

> `field_name` 来自 `extraction_field` 表的 `field_name` 列（LEFT JOIN），若字段配置已被删除则为 `null`。
>
> `source_refs` 返回完整参考块（与回调 `field_done` / `stage_done` 透出的内容一致）：按占位符 label 分组——table 类固定 `_tables` 键、text 类按检索关键词分组（section 用 section_title，page 检索固定 `page_content`）、VL 类形如 `{"_vl": {method, total_pages, key_pages, vl_total_tokens, ...}}`。每条 ref 含 `type` / `start_pos` / `end_pos` / `page_num`（字符串，可能为 `"3-5"` 范围）/ `text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀）；text 5 种检索（context/section/rule/chunk_db/vector_db）与 table 类的 ref 另带 `bboxes = [{page_num, bbox, page_size}]`（MinerU 块级框，左上原点、与 `page_size` 同单位，供前端 pdf.js 跳页画框；**非空才挂键**），page 检索与 VL 类不挂；顶层 `_texts` = `{label: 拼接后实际注入占位符的完整文本}`。
>
> **容错**：提取失败字段的 `source_refs` 为 `null`；存量老数据无 `text` / `_texts` / `bboxes` 键（老文件 page_mapping 无 bbox，重新解析后才有），消费方读取时需容错。

---

### 4.13 逻辑分析结果

- **URL**: `GET /file/{file_id}/analysis`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "a1b2...",
      "rule_id": "revenue_check",
      "rule_name": "营收达标检查",
      "result_value": "true",
      "input_values": {"total_revenue": "1500000"},
      "reason": "营业总收入1500000大于阈值1000000"
    }
  ]
}
```

> `rule_name` 来自 `analysis_rule` 表的 `rule_name` 列（LEFT JOIN），若规则配置已被删除则为 `null`。

---

### 4.14 原始 PDF 下载

下发上传时持久化的 `uploads/{file_id}.pdf` 原始字节，供管理 UI 提取结果 tab 的 pdf.js 定位预览（配合 `source_refs.bboxes` 跳页画框）。

- **URL**: `GET /file/{file_id}/pdf`

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `file_id` | `string` | 文件 ID，须完整匹配白名单正则 `[\w-]+` |

**响应**

- 成功：PDF 原始字节，`Content-Type: application/pdf`，`Content-Disposition: inline; filename="{file_id}.pdf"`（浏览器内联预览）。

**404 情形**（均返回 `{"detail": "原始 PDF 不存在"}`）

- `file_id` 不匹配白名单正则；
- `uploads/{file_id}.pdf` 不存在——包括**历史文件**（VL PDF 持久化机制上线前上传的文件没有落盘 PDF）。

**安全约束**：`file_id` 经 `re.fullmatch(r"[\w-]+")` 白名单校验后才拼接路径，防止路径穿越（Windows 下 `..%5C` 反斜杠可逃出存储目录）。PDF 文件由上传接口落盘，随文件删除/批量删除/文档类型级联删除联动清理，启动时 `cleanup_orphan_pdfs` 兜底。

---

## 5. 字段提取配置接口 `/extraction`

### 5.1 列出字段配置

- **URL**: `GET /extraction/fields`

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type_id` | `string` | `""` | 按 `type_id` 过滤；空表示全量 |

返回数组按 `priority` 升序。每条字段的字段说明见 5.2。

---

### 5.2 新增/更新字段配置

按 `field_id` upsert（全局唯一）。若已存在记录归属于其他 `type_id`，返回 **409**。

- **URL**: `POST /extraction/fields`
- **Content-Type**: `application/json`

**通用字段**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `field_id` | `string` | 是 | - | 全局唯一，正则 `^[a-zA-Z0-9_]+$`，最长 100 |
| `type_id` | `string` | 否 | `"default"` | 归属的文档类型 |
| `field_name` | `string` | 是 | - | 字段名称，最长 200 |
| `source_type` | `string` | 是 | - | `table` / `text` / `vl` |
| `enabled` | `int` | 否 | `1` | 是否启用 |
| `priority` | `int` | 否 | `0` | 越小越优先 |

**`source_type=table` 时**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `table_name_pattern` | `string` | 否 | 表格名匹配模式 |
| `table_match_type` | `string` | 否 | `exact` / `fuzzy` / `contains` / `llm` |
| `table_match_keywords` | `string[]` | 否 | `llm` 匹配时的关键词列表（辅助 LLM 决策） |
| `table_match_max_results` | `int` | 否 | 匹配上限 |
| `table_system_prompt` | `string` | 否 | 表格提取的 system prompt |
| `table_extract_prompt` | `string` | 否 | 表格提取 prompt，**必须**包含至少一个 `<search_result>标签</search_result>` 占位符 |

**`source_type=text` 时**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `search_type` | `string` | 否 | `context` / `section` / `rule` / `chunk_db` / `vector_db` / `page` |
| `search_config` | `object` | 否 | 检索配置（结构随 `search_type` 不同） |
| `text_system_prompt` | `string` | 否 | 文本提取的 system prompt |
| `text_extract_prompt` | `string` | 否 | 文本提取 prompt，**必须**包含至少一个 `<search_result>标签</search_result>` 占位符 |

**`source_type=vl` 时**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `vl_method` | `string` | **是** | `vl_model` / `vl_progressive` / `vl_locate` |
| `vl_config` | `object` | 否 | 方法相关参数（见下方表） |
| `vl_system_prompt` | `string` | 否 | VL 调用的 system prompt |
| `vl_extract_prompt` | `string` | **是** | 最终提取 prompt，**必须**包含 `value` 和 `reason` 关键字（大小写不敏感），因为 VL 要输出 `{value, reason}` JSON |

**`vl_config` 按方法参数**

| `vl_method` | 字段 | 类型 | 默认值 | 说明 |
|-------------|------|------|--------|------|
| `vl_model` | `page_range` | string | `"all"` | `"all"` 或 `"1-3"` / `"1-3,5"` |
| `vl_model` | `max_pixels` | int | `4000000` | 单图像素上限 |
| `vl_progressive` | `field_hints` | string | - | 自然语言提示要找的字段 |
| `vl_progressive` | `batch_size` | int | `2` | 每批塞 VL 的页数 |
| `vl_progressive` | `max_pixels` | int | `4000000` | 单图像素上限 |
| `vl_progressive` | `batch_prompt_template` | string | 内置默认 | 必含占位符 `{field_hints}` `{page_label}` `{total_pages}` `{history}` |
| `vl_locate` | `field_hints` | string | - | 自然语言提示 |
| `vl_locate` | `grid_pages` | int | `6` | 每张网格图包含的页数 |
| `vl_locate` | `grid_cols` | int | `3` | 网格列数 |
| `vl_locate` | `max_concurrent` | int | `20` | 第一轮并行上限（与全局 semaphore 取小） |
| `vl_locate` | `thumb_scale` | float | `0.75` | 缩略图缩放系数 |
| `vl_locate` | `key_pages_limit` | int | `6` | 第一轮命中的关键页上限 |
| `vl_locate` | `fallback_pages` | int | `3` | 第一轮未命中时回退前 N 页 |
| `vl_locate` | `max_pixels` | int | `4000000` | 第二轮高清重渲染像素上限 |
| `vl_locate` | `locate_prompt_template` | string | 内置默认 | 必含占位符 `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}` |

> 模板字面 `{ }` 需写成 `{{ }}` 转义（后端 `str.format()` 渲染）。

**占位符规则（text / table）**

| 占位符 | 解释 |
|--------|------|
| `<search_result>标签</search_result>` | 抽取 prompt 中的检索结果占位符，标签**不可为空** |
| 文本类标签 | 通常对应 `search_config.keywords` 中的关键词 |
| 表格类标签 | 通常对应 `table_name_pattern` 匹配出的表格名 |
| 无匹配结果 | 替换为 `（未找到 '标签' 的相关内容）` |

VL 类字段**不**使用 `<search_result>` 占位符 —— 直接读 `uploads/{file_id}.pdf`，由视觉模型一次输出 `{value, reason}` JSON，不再走文本 LLM 二次抽取；`source_refs` 形如 `{"_vl": {method, total_pages, key_pages, vl_total_tokens, batches_with_info}}`，**无**检索原文（`text`/`_texts`）也**无** `bboxes`（不可画框）——前端定位仅靠 `_vl.key_pages` 跳页（`vl_progressive` 的 `key_pages` 为 `null`，不可定位）。

**请求示例（表格类）**

```json
{
  "field_id": "total_revenue",
  "type_id": "annual_report",
  "field_name": "营业总收入",
  "source_type": "table",
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "检索到的表格：\n<search_result>利润表</search_result>\n\n请提取营业总收入金额，仅返回数值。"
}
```

**请求示例（文本类，多关键词）**

```json
{
  "field_id": "company_info",
  "type_id": "default",
  "field_name": "公司基本信息",
  "source_type": "text",
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "注册地址", "法定代表人"],
    "context_before": 100,
    "context_after": 200
  },
  "text_extract_prompt": "公司名称：\n<search_result>公司名称</search_result>\n\n注册地址：\n<search_result>注册地址</search_result>\n\n法人：\n<search_result>法定代表人</search_result>\n\n请提取：1.公司全称 2.详细地址 3.法人姓名"
}
```

**请求示例（VL `vl_locate`）**

```json
{
  "field_id": "total_assets_vl",
  "type_id": "annual_report",
  "field_name": "资产总额",
  "source_type": "vl",
  "vl_method": "vl_locate",
  "vl_config": {
    "field_hints": "资产总额、负债总额、净利润",
    "grid_pages": 6,
    "grid_cols": 3,
    "key_pages_limit": 6,
    "fallback_pages": 3
  },
  "vl_extract_prompt": "请从以上高清财报页中提取「资产总额」。\n只返回 JSON：{\"value\": \"金额（含单位）\", \"reason\": \"看到的页码与位置\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

**响应示例**

```json
{"code": 200, "message": "字段配置已创建", "data": {"field_id": "total_revenue"}}
```

**状态码**: 200 / 409（`field_id` 已被其他 `type_id` 占用）/ 422（Pydantic 校验失败）

---

### 5.3 删除字段配置

**硬删除**（直接从 `extraction_field` 表删除，不再走 enabled=0 的软删除路径）。

- **URL**: `DELETE /extraction/fields/{field_id}`

**响应示例**

```json
{"code": 200, "message": "字段配置已删除", "data": null}
```

**状态码**: 200 / 404

---

### 5.4 检查字段 ID 是否存在

- **URL**: `GET /extraction/fields/{field_id}/check`

```json
{"code": 200, "message": "success", "data": {"exists": true}}
```

---

### 5.5 字段提取调试（同步）

支持两种模式：使用已保存的 `field_id` 或 传入临时 `config`。

- **URL**: `POST /extraction/test`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 目标文件 ID |
| `field_id` | `string` | 二选一 | 模式 1：使用已保存配置 |
| `config` | `object` | 二选一 | 模式 2：临时配置（结构同 5.2 请求体，不含 `field_id`/`type_id`） |

**响应（普通字段）**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "search_results": [
      {"table_name": "利润表", "table_content": "..."}
    ],
    "llm_input": "检索到的表格...",
    "llm_output": "1500000",
    "extracted_value": "1500000",
    "reason": "从利润表第3行提取"
  }
}
```

**响应（VL 字段）**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "search_results": [
      {
        "type": "vl_meta",
        "method": "vl_locate",
        "key_pages": [12, 13, 15],
        "vl_total_tokens": 8421,
        "batches_with_info": null
      }
    ],
    "llm_input": "请从以上高清财报页中提取「资产总额」...",
    "llm_output": "1234567890 元",
    "extracted_value": "1234567890 元",
    "reason": "见第 12 页资产负债表合计行"
  }
}
```

VL 字段 `llm_output` 与 `extracted_value` 一致（VL 直出 JSON，不再二次抽取）。

**状态码**: 200 / 400（缺 `field_id`/`config`） / 404（字段配置或文件内容不存在）/ 500（提取异常）

---

### 5.6 字段提取流式调试（SSE）

- **URL**: `POST /extraction/test/stream`
- **Content-Type**: `application/json`
- **响应类型**: `text/event-stream`

请求体同 5.5。事件序列详见第 9.2 节。

---

## 6. 逻辑分析配置接口 `/analysis`

### 6.1 列出分析规则

- **URL**: `GET /analysis/rules`

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type_id` | `string` | `""` | 按 `type_id` 过滤；空表示全量 |

按 `priority` 升序。每条规则字段同 6.2。

---

### 6.2 新增/更新分析规则

按 `rule_id` upsert（全局唯一）。若已存在记录归属于其他 `type_id`，返回 **409**。

- **URL**: `POST /analysis/rules`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `rule_id` | `string` | 是 | - | 全局唯一，正则 `^[a-zA-Z0-9_]+$`，最长 100 |
| `type_id` | `string` | 否 | `"default"` | 归属的文档类型 |
| `rule_name` | `string` | 是 | - | 规则名称，最长 200 |
| `rule_type` | `string` | 是 | - | `judge` / `calc` |
| `expression` | `string` | 是 | - | 表达式，**必须**包含至少一个 `<field_result>字段ID</field_result>` 占位符 |
| `system_prompt` | `string` | 否 | `null` | judge 类型可自定义 LLM system prompt |
| `depend_fields` | `string[]` | 否 | `null` | 依赖的字段 ID 列表 |
| `enabled` | `int` | 否 | `1` | 是否启用 |
| `priority` | `int` | 否 | `0` | 越小越优先 |

**请求示例（judge）**

```json
{
  "rule_id": "revenue_check",
  "type_id": "annual_report",
  "rule_name": "营收达标检查",
  "rule_type": "judge",
  "expression": "请判断营业总收入 <field_result>total_revenue</field_result> 是否大于 1000000",
  "system_prompt": "你是一个专业的财务分析师，仅依据提供的数值判断。",
  "depend_fields": ["total_revenue"]
}
```

**请求示例（calc）**

```json
{
  "rule_id": "profit_rate",
  "type_id": "annual_report",
  "rule_name": "利润率计算",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"]
}
```

**占位符规则**

| 占位符 | 解释 |
|--------|------|
| `<field_result>字段ID</field_result>` | 字段结果占位符，标签**不可为空** |
| 字段 ID | 必须是 `depend_fields` 列表中存在的某个 `field_id` |
| 无提取结果 | 替换为 `（未找到字段 '字段ID' 的提取结果）` |

**响应示例**

```json
{"code": 200, "message": "规则配置已创建", "data": {"rule_id": "revenue_check"}}
```

**状态码**: 200 / 409（`rule_id` 已被其他 `type_id` 占用）/ 422

---

### 6.3 删除分析规则

**硬删除**。

- **URL**: `DELETE /analysis/rules/{rule_id}`

```json
{"code": 200, "message": "规则配置已删除", "data": null}
```

---

### 6.4 检查规则 ID 是否存在

- **URL**: `GET /analysis/rules/{rule_id}/check`

```json
{"code": 200, "message": "success", "data": {"exists": true}}
```

---

### 6.5 逻辑分析调试（同步）

- **URL**: `POST /analysis/test`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | `string` | 是 | 目标文件 ID |
| `rule_id` | `string` | 二选一 | 模式 1：使用已保存规则 |
| `config` | `object` | 二选一 | 模式 2：临时配置（含 `rule_type` / `expression` / `system_prompt` / `depend_fields`） |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "input_values": {"net_profit": "500000", "total_revenue": "1500000"},
    "expression_resolved": "500000 / 1500000 * 100",
    "result_value": "33.33",
    "reason": "计算公式: 500000 / 1500000 * 100 = 33.33"
  }
}
```

**状态码**: 200 / 400 / 404 / 500

---

### 6.6 逻辑分析流式调试（SSE）

- **URL**: `POST /analysis/test/stream`
- **响应类型**: `text/event-stream`

请求体同 6.5。事件序列详见第 9.3 节。

---

## 7. 向量检索接口 `/search`

- **URL**: `POST /search`
- **Content-Type**: `application/json`

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | `string` | 是 | - | 检索文本 |
| `file_id` | `string` | 否 | `null` | 限定检索范围 |
| `top_k` | `int` | 否 | `10` | 返回条数 |
| `score_threshold` | `float` | 否 | `null` | L2 距离上限（越小越相似），超过则过滤 |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "chunk_id": "chunk_003",
      "file_id": "a1b2...",
      "chunk_index": 3,
      "total_chunks": 15,
      "chunk_content": "公司2024年度营业收入为1500万元...",
      "start_pos": 1200,
      "end_pos": 1450,
      "page_num": "5",
      "score": 0.25
    }
  ]
}
```

---

## 8. 异步回调约定

当 `POST /file/parse` 或 `POST /file/{file_id}/retry/{stage}` 携带 `callback_url` 时（仅 `mode=async`/`sync`），管线会向该地址 POST JSON 通知。

| 通知类型 | 说明 |
|---------|------|
| 阶段入口 | 每个阶段开始时各 1 次 |
| `field_done` / `rule_done` | 抽取/分析每完成 1 个字段/规则触发 1 次 |
| `stage_done` | 每个阶段完整数据合并下发 1 次 |

**通用 schema**

```json
{
  "file_id": "...",
  "status": "parsing|tableing|chunking|embedding|extracting|analyzing|complete",
  "event": "field_done|rule_done|stage_done",
  "data": { /* 见下 */ }
}
```

> `event` 字段缺省时表示阶段入口通知；老消费者只读 `status` 不受影响。

**`stage_done.data` 按阶段**

| stage | data 内容 |
|-------|----------|
| `parsing` | `{content, middle_json, page_mapping}`（完整 MD） |
| `tableing` | `{total, tables: [{file_id, table_index, total_table, table_name, table_content, start_pos, end_pos, page_num}]}` |
| `chunking` | `{total, chunks: [{file_id, chunk_id, chunk_index, total_chunks, chunk_content, start_pos, end_pos, page_num}]}` |
| `embedding` | **无 data**（仅作完成信号；向量量太大不下发，需查 Milvus） |
| `extracting` | `{total, succeeded, failed, results: [field_done.data ...]}` |
| `analyzing` | `{total, succeeded, failed, results: [rule_done.data ...]}` |

**单字段/单规则事件**

```json
{
  "file_id": "...", "status": "extracting", "event": "field_done",
  "data": {"field_id", "field_name", "value", "reason",
           "source_refs", "success": true, "index": 5, "total": 12}
}
```

```json
{
  "file_id": "...", "status": "analyzing", "event": "rule_done",
  "data": {"rule_id", "rule_name", "rule_type", "result", "reason",
           "input_values", "source_refs", "success": true, "index": 3, "total": 8}
}
```

**完整事件序列**

```
parsing                    → parsing + stage_done(MD)
tableing                   → tableing + stage_done(tables)
chunking                   → chunking + stage_done(chunks)
embedding                  → embedding + stage_done(无 data)
extracting + field_done×N  → extracting + stage_done(results)
analyzing  + rule_done×N   → analyzing  + stage_done(results)
complete
```

**调用约束**

- 每次回调超时 **2.5s**，失败仅 warning，**不影响主管线**。
- VL 类字段的"进度事件"（progressive_batch / locate_locate / locate_extract）**仅 SSE 推送**，**不**走 `callback_url`。

---

## 9. SSE 事件清单

所有 SSE 响应均为 `text/event-stream`，单条事件格式：

```
event: <event_name>
data: <json>

```

### 9.1 `POST /file/parse?mode=stream` 与 `POST /file/{file_id}/retry/{stage}?mode=stream`

| 事件 | 阶段 | 说明 |
|------|------|------|
| `resume` | 重试入口 | 仅 retry 流，标识从哪个阶段继续 |
| `parsing_start` / `parsing` | 解析 | MinerU 解析开始 / 完成 |
| `content_saved` / `md_content` | 解析 | MD 已落库 / 推送完整 MD 文本 |
| `tableing_start` / `tableing` | 表格识别 | 表格抽取与 LLM 命名开始 / 完成 |
| `chunking_start` / `chunking` / `chunks_saving` / `chunks_saved` | 分块 | 分块各阶段 |
| `embedding_start` / `embedding` / `milvus_submitting` / `milvus_submitted` | 向量化 | 向量化与 Milvus 写入 |
| `tasks_loading` / `tasks_loaded` | 提取/分析 | 加载字段/规则任务 |
| `extraction_start` / `field_extracted` / `extraction` | 字段提取 | 单字段事件含 `field_id` / `field_name` / `extracted_value` / `reason` / `success` / `current` / `total` |
| `analysis_start` / `rule_analyzed` / `analysis` | 逻辑分析 | 单规则事件含 `rule_id` / `rule_name` / `rule_type` / `result_value` / `input_values` / `reason` / `success` / `current` / `total` |
| `complete` | 完成 | 全流程结束 |
| `error` | 错误 | 任意阶段失败时推送 |

---

### 9.2 `POST /extraction/test/stream`

| 步骤 | event | data 字段 |
|------|-------|-----------|
| 1（table/text）| `search_results` | `source_type`, `search_type`, `matched_tables` / `results_by_label`, `results` |
| 1（vl）| `pdf_loaded` | `total_pages`, `vl_method` |
| 2（vl_progressive）| `progressive_batch` | `page_label`, `has_info`, `summary_preview`, `batch_index`, `total_batches` |
| 2（vl_locate）| `locate_locate` | `phase`, `grid_idx`, `total_grids`, `page_labels`, `found_pages` |
| 3（vl_locate）| `locate_extract` | `phase`, `key_pages` |
| 2-3（仅 `source_type=table` 且 `table_match_type=llm`）| `match_llm` | `step`, `prompt` / `llm_response` / `matched_indices` / `error` |
| 4 | `prompt` | `system_prompt`, `user_prompt` |
| 5（table/text）| `llm_response` | `raw_response` |
| 6 | `result` | `extracted_value`, `reason`, `source_refs` |
| 7 | `done` | `{}` |
| —  | `error` | `step`, `message` |

> VL 字段不会推 `search_results` / `llm_response`；table/text 字段不会推 `pdf_loaded` / `progressive_batch` / `locate_locate` / `locate_extract`。

---

### 9.3 `POST /analysis/test/stream`

**Judge 类型**

| 步骤 | event | data 字段 |
|------|-------|-----------|
| 1 | `input_values` | `input_values`, `depend_fields` |
| 2 | `resolved_expression` | `original_expression`, `resolved_expression` |
| 3 | `prompt` | `system_prompt`, `user_prompt` |
| 4 | `llm_response` | `raw_response` |
| 5 | `result` | `result_value`, `reason` |
| 6 | `done` | `{}` |
| — | `error` | `message` |

**Calc 类型**（无 `prompt` / `llm_response`）

| 步骤 | event | data 字段 |
|------|-------|-----------|
| 1 | `input_values` | `input_values`, `depend_fields` |
| 2 | `resolved_expression` | `original_expression`, `resolved_expression` |
| 3 | `result` | `result_value`, `reason` |
| 4 | `done` | `{}` |

---

## 10. 数据模型说明

### 10.1 `doc_type`

| 列 | 类型 | 说明 |
|---|------|------|
| `type_id` | VARCHAR(64) PK | 类型 ID |
| `type_name` | VARCHAR(200) NOT NULL | 类型名称 |
| `description` | TEXT | 描述 |
| `is_default` | TINYINT DEFAULT 0 | 1=默认类型，不可删 |
| `is_template` | TINYINT DEFAULT 0 | 1=模板（选择器展示 / `scope=template`），由 promote/demote 切换 |
| `parent_type_id` | VARCHAR(64) NULL | 复制来源类型，`copy_from` 自动记录（索引 `ix_doc_type_parent_type_id`） |
| `project_id` | VARCHAR(64) NULL | 归属项目（`null`=未分组），索引 `ix_doc_type_project_id` |
| `enabled` | TINYINT DEFAULT 1 | 启用标志 |
| `created_at` / `updated_at` | DATETIME | 时间戳 |

> **`project` 表**：`project_id`(PK) / `project_name` / `description` / `created_at` / `updated_at`。项目对「模板 + 血缘下游」分类；删项目只把成员 `doc_type.project_id` 置空，不删类型。

### 10.2 `files`

| 列 | 类型 | 说明 |
|---|------|------|
| `file_id` | VARCHAR(64) PK | 文件 ID（SHA256[:32]） |
| `type_id` | VARCHAR(64) NOT NULL DEFAULT 'default' | 归属类型，索引 |
| `file_name` | VARCHAR(512) | 文件名 |
| `file_size` | BIGINT | 字节数 |
| `progress` | VARCHAR(32) DEFAULT 'parsing' | 进度状态（见 11.6） |
| `error` | TEXT | 错误信息 |
| `create_time` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 自动更新 |
| `start_parsing_time` / `end_parsing_time` | DATETIME | 解析时间戳 |
| `start_tableing_time` / `end_tableing_time` | DATETIME | 表格阶段时间戳 |
| `start_chunking_time` / `end_chunking_time` | DATETIME | 分块时间戳 |
| `start_embedding_time` / `end_embedding_time` | DATETIME | 向量化时间戳 |
| `end_extracting_time` | DATETIME | 提取完成时间 |
| `end_analyzing_time` | DATETIME | 分析完成时间 |

### 10.3 `file_content`

| 列 | 类型 | 说明 |
|---|------|------|
| `file_id` | VARCHAR(64) PK | |
| `file_content` | LONGTEXT NOT NULL | MinerU 输出的完整 Markdown |
| `middle_json` | LONGTEXT | MinerU 中间 JSON |
| `page_mapping` | JSON | MD 偏移量→PDF 页号/bbox 映射，每项 `{start_pos, end_pos, page_num, bbox, page_size}`（bbox/page_size 老数据不带），是 `source_refs.bboxes` 的来源 |

### 10.4 `file_table`

| 列 | 类型 | 说明 |
|---|------|------|
| `file_id`, `table_index` | 复合 PK | |
| `total_table` | INT | 表格总数 |
| `table_name` | VARCHAR(500) | LLM 解析出的表名（截断 30 字） |
| `table_content` | LONGTEXT | `<table>...</table>` HTML |
| `start_pos` / `end_pos` | INT | 在原文中的偏移 |
| `page_num` | VARCHAR(20) | PDF 页号（可能是范围如 `"3-4"`） |

### 10.5 `file_chunk`

| 列 | 类型 | 说明 |
|---|------|------|
| `file_id`, `chunk_id` | 复合 PK | |
| `chunk_index` / `total_chunks` | INT | 序号与总数 |
| `chunk_content` | TEXT | 分块文本 |
| `start_pos` / `end_pos` | INT | 偏移 |
| `page_num` | VARCHAR(20) | PDF 页号 |

### 10.6 `extraction_field`

| 列 | 类型 | 说明 |
|---|------|------|
| `field_id` | VARCHAR(100) PK | 全局唯一 |
| `type_id` | VARCHAR(64) NOT NULL DEFAULT 'default' | 索引 |
| `field_name` | VARCHAR(200) NOT NULL | |
| `source_type` | ENUM('table','text','vl') NOT NULL | |
| `enabled` | TINYINT DEFAULT 1 | |
| `priority` | INT DEFAULT 0 | |
| `table_name_pattern` | VARCHAR(500) | |
| `table_match_type` | ENUM('exact','fuzzy','contains','llm') | |
| `table_match_keywords` | JSON | LLM 匹配关键词 |
| `table_match_max_results` | INT | |
| `table_system_prompt` / `table_extract_prompt` | TEXT | |
| `search_type` | ENUM('context','section','rule','chunk_db','vector_db','page') | |
| `search_config` | JSON | |
| `text_system_prompt` / `text_extract_prompt` | TEXT | |
| `vl_method` | ENUM('vl_model','vl_progressive','vl_locate') | |
| `vl_config` | JSON | |
| `vl_system_prompt` / `vl_extract_prompt` | TEXT | |
| `created_at` / `updated_at` | DATETIME | |

### 10.7 `analysis_rule`

| 列 | 类型 | 说明 |
|---|------|------|
| `rule_id` | VARCHAR(100) PK | 全局唯一 |
| `type_id` | VARCHAR(64) NOT NULL DEFAULT 'default' | 索引 |
| `rule_name` | VARCHAR(200) NOT NULL | |
| `rule_type` | ENUM('judge','calc') NOT NULL | |
| `expression` | TEXT NOT NULL | 含 `<field_result>` 占位符 |
| `system_prompt` | TEXT | judge 类型可用 |
| `depend_fields` | JSON | `field_id` 列表 |
| `enabled` / `priority` | TINYINT / INT | |
| `created_at` / `updated_at` | DATETIME | |

### 10.8 `extraction_result`

| 列 | 类型 | 说明 |
|---|------|------|
| `file_id`, `field_id` | 复合 PK | |
| `extracted_value` | TEXT | |
| `reason` | TEXT | LLM 给出的理由 |
| `source_refs` | JSON | 参考块/页码/检索原文（`text`/`_texts`）/`bboxes`（块级 PDF 框）/VL 元数据（`_vl`）；失败时 NULL |

### 10.9 `analysis_result`

| 列 | 类型 | 说明 |
|---|------|------|
| `file_id`, `rule_id` | 复合 PK | |
| `result_value` | VARCHAR(500) | |
| `input_values` | JSON | 解析时的依赖字段值 |
| `reason` | TEXT | |
| `source_refs` | JSON | 依赖字段的参考块 |

---

## 11. 枚举值说明

### 11.1 `SourceType`

| 值 | 说明 |
|----|------|
| `table` | 从已提取的表格中匹配并 LLM 抽取 |
| `text` | 从 Markdown 文本检索后 LLM 抽取 |
| `vl` | 直接对原 PDF 走 VL 视觉模型抽取 |

### 11.2 `TableMatchType`

| 值 | 说明 |
|----|------|
| `exact` | 精确匹配 |
| `fuzzy` | 模糊匹配 |
| `contains` | 包含匹配 |
| `llm` | LLM 语义匹配（可携带 `table_match_keywords`） |

### 11.3 `SearchType`

| 值 | 说明 |
|----|------|
| `context` | 关键词命中 + 前后上下文 |
| `section` | 章节标题匹配 |
| `rule` | 关键词起点 + 停止词边界 |
| `chunk_db` | MySQL 内分块检索 |
| `vector_db` | Milvus 语义检索 |
| `page` | 按 `page_range` 直接切 Markdown 喂 LLM；占位符固定为 `<search_result>page_content</search_result>`，可配 `max_length` 末尾截断 |

### 11.4 `VLMethod`

| 值 | 说明 |
|----|------|
| `vl_model` | 指定页全部塞 VL 一次出 JSON |
| `vl_progressive` | 分批扫描 + 伪历史累积 + 最后文本聚合 |
| `vl_locate` | 缩略图网格并行定位 + 关键页高清提取 |

### 11.5 `RuleType`

| 值 | 说明 |
|----|------|
| `judge` | LLM 判断，返回 `true`/`false`（也可能是 LLM 自由文本判断结果） |
| `calc` | `numexpr` 计算表达式，按 `analysis.calc_precision`（默认 2 位）保留小数 |

### 11.6 文件处理进度 `progress`

成功路径：`parsing` → `tableing` → `chunking` → `embedding` → `extracting` → `analyzing` → `complete`

每个 `*ing` 状态都有对应的 `*_failed` 失败态：

| 值 | 说明 |
|----|------|
| `parsing` / `parsing_failed` | 解析（MinerU） |
| `tableing` / `tableing_failed` | 表格识别（LLM 命名） |
| `chunking` / `chunking_failed` | 分块 |
| `embedding` / `embedding_failed` | 向量化 + Milvus 写入 |
| `extracting` / `extracting_failed` | 字段提取 |
| `analyzing` / `analyzing_failed` | 逻辑分析 |
| `complete` | 处理完成 |

> 启动时 `init_service` 会把所有 `*ing` 状态强制改为 `*_failed`（崩溃恢复）。兼容旧值 `table_name_validating` → `tableing`。

---

## 12. 错误码说明

### 12.1 HTTP 状态码

| 状态码 | 场景 |
|--------|------|
| 200 | 成功 |
| 400 | 参数错误：文件超大、阶段名无效、`target_type_id` 缺失、源/目标类型相同等 |
| 404 | 资源不存在（文件、字段、规则、类型） |
| 409 | 冲突：`field_id`/`rule_id` 被其他 `type_id` 占用、未传 `force=true` 删除非空类型 |
| 422 | Pydantic 校验失败（FastAPI 自动返回） |
| 500 | 服务端异常（提取/分析过程） |

### 12.2 业务错误体（`ResponseWrapper` 风格）

```json
{"code": 400, "message": "文件大小超过限制 (100MB)", "data": null}
```

### 12.3 HTTPException 错误体

```json
{"detail": "文件不存在"}
```

### 12.4 Pydantic 校验错误（422）

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

## 13. 附录：接口总览

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | GET | `/doctype/list` | 列出 / 搜索文档类型（可分页/筛选） |
| 2 | POST | `/doctype` | 新增/更新文档类型 |
| 3 | DELETE | `/doctype/{type_id}` | 删除文档类型（可级联） |
| 4 | POST | `/doctype/{type_id}/copy_from` | 同实例跨类型复制配置（记录血缘） |
| 5 | GET | `/doctype/{type_id}/export` | 导出配置 JSON |
| 6 | POST | `/doctype/import` | 导入配置 JSON |
| 7 | POST | `/doctype/{type_id}/promote` | 标记为模板 |
| 8 | POST | `/doctype/{type_id}/demote` | 取消模板标记 |
| 9 | POST | `/doctype/batch_delete` | 批量删除类型（逐条返回结果） |
| 10 | GET | `/doctype/projects` | 列出项目（含 `type_count`） |
| 11 | POST | `/doctype/projects` | 新增/改名项目（upsert） |
| 12 | DELETE | `/doctype/projects/{project_id}` | 删除项目（成员转未分组，不删类型） |
| 13 | POST | `/doctype/batch_assign_project` | 批量归类到项目（级联血缘） |
| 14 | POST | `/file/parse` | 提交文件解析（sync/async/stream） |
| 15 | GET | `/file/list` | 分页查询文件 |
| 16 | DELETE | `/file/batch` | 批量删除 |
| 17 | GET | `/file/{file_id}/status` | 查询处理进度 |
| 18 | GET | `/file/{file_id}/detail` | 完整详情（含所有时间字段） |
| 19 | DELETE | `/file/{file_id}` | 删除文件及关联数据 |
| 20 | POST | `/file/{file_id}/retry/{stage}` | 从指定阶段重试 |
| 21 | POST | `/file/{file_id}/retry/extracting` | 重试字段提取 |
| 22 | POST | `/file/{file_id}/retry/analyzing` | 重试逻辑分析 |
| 23 | GET | `/file/{file_id}/tables` | 表格列表 |
| 24 | GET | `/file/{file_id}/chunks` | 分块列表 |
| 25 | POST | `/file/context_query` | 文件片段上下文查询（请求体传 `file_id`） |
| 26 | GET | `/file/{file_id}/outline` | 章节大纲 |
| 27 | GET | `/file/{file_id}/content` | 按页 Markdown 内容 |
| 28 | GET | `/file/{file_id}/extraction` | 字段提取结果 |
| 29 | GET | `/file/{file_id}/analysis` | 逻辑分析结果 |
| 30 | GET | `/file/{file_id}/pdf` | 原始 PDF 下载（定位预览用） |
| 31 | GET | `/extraction/fields` | 字段配置列表（可 `type_id` 过滤） |
| 32 | POST | `/extraction/fields` | 新增/更新字段配置 |
| 33 | DELETE | `/extraction/fields/{field_id}` | 删除字段配置（硬删） |
| 34 | GET | `/extraction/fields/{field_id}/check` | 检查 ID 是否存在 |
| 35 | POST | `/extraction/test` | 字段提取调试 |
| 36 | POST | `/extraction/test/stream` | 字段提取流式调试（SSE） |
| 37 | GET | `/analysis/rules` | 规则配置列表（可 `type_id` 过滤） |
| 38 | POST | `/analysis/rules` | 新增/更新规则 |
| 39 | DELETE | `/analysis/rules/{rule_id}` | 删除规则（硬删） |
| 40 | GET | `/analysis/rules/{rule_id}/check` | 检查 ID 是否存在 |
| 41 | POST | `/analysis/test` | 逻辑分析调试 |
| 42 | POST | `/analysis/test/stream` | 逻辑分析流式调试（SSE） |
| 43 | POST | `/search` | 向量相似度检索 |
