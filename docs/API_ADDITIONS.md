# API 文档新增内容

本文件包含需要添加到 API_FULL_REFERENCE.md 的所有新接口文档。

---

## 插入位置1: 在 3.19 之后添加 3.20

### 3.20 `GET /file/stats` 文件处理统计

处理统计（概览 KPI / 状态分布 / 类型排行 / 处理趋势 / 阶段耗时）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/stats` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileStatsResponse>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `range` | string | 否 | `d30` | 时间窗口：`d7`/`d30`/`d90`/`all` |

**说明：**

- 不受顶部项目、文档类型选择器影响（统计全部类型）
- `range` 时间窗口按 `files.create_time` 过滤，**统一作用于全部 6 组指标**
- 共 4 条聚合查询（状态分布、类型×项目、趋势、阶段耗时），无 N+1
- 返回数据包含：
  - **概览 KPI**：总文件数、处理中、成功、失败
  - **状态分布**：各状态的文件数量
  - **类型排行**：各文档类型的文件数和占比
  - **处理趋势**：按时间粒度（小时/天/周）统计的处理量
  - **阶段耗时**：各阶段的平均、P50、P95 耗时

请求示例：

```bash
curl "http://localhost:5019/file/stats?range=d30"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "overview": {
      "total": 1250,
      "processing": 15,
      "completed": 1180,
      "failed": 55
    },
    "status_distribution": [
      {"status": "complete", "count": 1180},
      {"status": "extracting", "count": 8},
      {"status": "parsing_failed", "count": 35}
    ],
    "type_ranking": [
      {"type_id": "default", "type_name": "默认类型", "count": 800, "percentage": 64.0},
      {"type_id": "contract", "type_name": "合同", "count": 300, "percentage": 24.0}
    ],
    "trends": [
      {"period": "2026-10-01", "count": 45},
      {"period": "2026-10-02", "count": 52}
    ],
    "stage_durations": {
      "parsing": {"avg_ms": 12500, "p50_ms": 10200, "p95_ms": 25000},
      "extracting": {"avg_ms": 8300, "p50_ms": 7500, "p95_ms": 15000}
    }
  }
}
```

---

## 插入位置2: 在 5.7 之后添加 5.8

### 5.8 `GET /analysis/tasks/{task_id}` 查询独立分析任务

查询 `/analysis/run` 异步模式创建的分析任务状态和结果。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/analysis/tasks/{task_id}` |
| 请求体 | 无 |
| 响应 | `AnalysisTaskQueryResponse` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `task_id` | string | 是 | 任务 ID，由 `/analysis/run` 返回 |

**说明：**

- 任务状态：`queued` / `analyzing` / `complete` / `analysis_failed`
- 所有 async 任务均保存状态及完整结果，与 `callback_url` 和 `persist` 无关
- 已完成结果在服务重启后保留，中断任务会标记为失败
- 任务记录暂不自动过期
- `sync/stream` 模式和功能上线前的任务编号不可查询，返回 404

请求示例：

```bash
curl "http://localhost:5019/analysis/tasks/task_abc123"
```

响应示例（进行中）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "task_abc123",
    "status": "analyzing",
    "result": null,
    "error": null,
    "created_at": "2026-10-14T10:30:00",
    "updated_at": "2026-10-14T10:30:05"
  }
}
```

响应示例（完成）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "task_abc123",
    "status": "complete",
    "result": {
      "total": 3,
      "succeeded": 3,
      "failed": 0,
      "results": [
        {
          "rule_id": "RULE_001",
          "rule_name": "金额合规性",
          "result": true,
          "reason": "金额在允许范围内",
          "success": true
        }
      ]
    },
    "error": null,
    "created_at": "2026-10-14T10:30:00",
    "updated_at": "2026-10-14T10:30:08"
  }
}
```

---

## 插入位置3: 在 6.8 之后添加 6.9-6.12（文档类型参数管理）

### 6.9 `GET /doctype/{type_id}/params` 查询类型入参定义

查询文档类型的入参定义列表。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/doctype/{type_id}/params` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<TypeParamItem[]>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |

**说明：**

- 入参定义用于在上传文件时传递动态参数
- 字段和规则配置中可使用 `<param>参数标识</param>` 占位符引用参数值
- 按 `priority` 升序、`param_key` 排序返回

请求示例：

```bash
curl "http://localhost:5019/doctype/contract/params"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "param_key": "contract_type",
      "param_name": "合同类型",
      "description": "用于区分采购合同、销售合同等",
      "default_value": "采购",
      "required": 1,
      "priority": 1
    },
    {
      "param_key": "review_date",
      "param_name": "审核日期",
      "description": "合同审核的基准日期",
      "default_value": "",
      "required": 0,
      "priority": 2
    }
  ]
}
```

---

### 6.10 `POST /doctype/{type_id}/params` 新增/更新类型入参

按 `param_key` 新增或更新一条入参定义。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/doctype/{type_id}/params` |
| 请求体 | `TypeParamUpsert` |
| 响应 | `ResponseWrapper<TypeParamItem>` |
| 副作用 | 插入或更新 `type_param` 表记录 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `param_key` | string | 是 | 参数标识，字母数字下划线，1-64字符 |
| `param_name` | string | 是 | 参数显示名称 |
| `description` | string | 否 | 参数说明 |
| `default_value` | string | 否 | 默认值 |
| `required` | integer | 否 | 是否必填，0/1，默认0 |
| `priority` | integer | 否 | 排序优先级，默认0 |

**说明：**

- 如果 `param_key` 已存在，则更新；否则新增
- `param_key` 只能包含字母、数字、下划线
- 类型不存在返回 404

请求示例：

```bash
curl -X POST "http://localhost:5019/doctype/contract/params" \
  -H "Content-Type: application/json" \
  -d '{
    "param_key": "supplier_name",
    "param_name": "供应商名称",
    "description": "用于合同甲方字段的默认匹配",
    "default_value": "",
    "required": 0,
    "priority": 3
  }'
```

响应示例：

```json
{
  "code": 0,
  "message": "参数已保存",
  "data": {
    "param_key": "supplier_name",
    "param_name": "供应商名称",
    "description": "用于合同甲方字段的默认匹配",
    "default_value": "",
    "required": 0,
    "priority": 3
  }
}
```

---

### 6.11 `DELETE /doctype/{type_id}/params/{param_key}` 删除类型入参

删除一条入参定义。

| 项 | 说明 |
|---|---|
| 方法 | `DELETE` |
| 路径 | `/doctype/{type_id}/params/{param_key}` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper` |
| 副作用 | 删除 `type_param` 表记录 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |
| `param_key` | string | 是 | 参数标识 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `force` | boolean | 否 | `false` | 是否强制删除（即使被引用） |

**说明：**

- 如果参数仍被字段或规则引用，且 `force=false`，返回 409
- `force=true` 强制删除，引用处的占位符会渲染为空字符串
- 入参不存在返回 404

请求示例：

```bash
curl -X DELETE "http://localhost:5019/doctype/contract/params/supplier_name?force=true"
```

响应示例：

```json
{
  "code": 0,
  "message": "参数已删除",
  "data": null
}
```

---

### 6.12 `GET /doctype/{type_id}/config_version` 查询配置版本

查询类型配置的当前版本标识（基于字段和规则的最后更新时间生成）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/doctype/{type_id}/config_version` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ConfigVersionResponse>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |

**说明：**

- 版本标识由字段和规则的最后更新时间戳生成（Unix毫秒）
- 用于前端判断配置是否变更，决定是否重新加载
- 类型不存在返回 404

请求示例：

```bash
curl "http://localhost:5019/doctype/contract/config_version"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "type_id": "contract",
    "version": "1728892345678",
    "field_count": 15,
    "rule_count": 8,
    "last_updated": "2026-10-14T15:32:25"
  }
}
```

---

## 插入位置4: 新增第8章 系统设置接口

## 8. 系统设置接口 `/settings`

系统设置接口提供运行时配置的读取和更新能力，受管理员会话保护。

**会话机制：**

- 密码在 `config.yaml` 的 `settings.password` 配置，明文存储
- 会话默认有效期 30 分钟（`settings.session_minutes`）
- 使用 HttpOnly + SameSite=Strict Cookie
- 会话数据存储在单进程内存中
- 空密码禁用登录（所有写接口返回 401）

**安全注意：**

- 仅在可信内网环境使用
- 生产环境建议通过反向代理限制访问
- 密钥字段（`api_key`等）只写不读，读取时返回 `{configured: true/false}`

---

### 8.1 `POST /settings/login` 管理员登录

验证密码并创建会话。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/settings/login` |
| 请求体 | `LoginRequest` |
| 响应 | `ResponseWrapper<{authenticated: boolean}>` |
| 副作用 | 创建会话 Cookie |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `password` | string | 是 | 管理员密码 |

**说明：**

- 密码错误返回 401
- 登录频繁失败会触发限流（5分钟内最多 10 次尝试），返回 429
- 成功后设置 `settings_session` Cookie

请求示例：

```bash
curl -X POST "http://localhost:5019/settings/login" \
  -H "Content-Type: application/json" \
  -d '{"password": "admin123"}'
```

响应示例：

```json
{
  "code": 0,
  "message": "登录成功",
  "data": {
    "authenticated": true
  }
}
```

---

### 8.2 `GET /settings/session` 查询会话状态

检查当前会话是否有效。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/settings/session` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<{authenticated: boolean}>` |
| 副作用 | 无 |

**说明：**

- 无需登录即可调用
- 返回当前会话是否有效

请求示例：

```bash
curl "http://localhost:5019/settings/session" \
  --cookie "settings_session=xxx"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "authenticated": true
  }
}
```

---

### 8.3 `GET /settings/config` 读取运行时配置

读取当前运行时配置（脱敏版本）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/settings/config` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ConfigReadResponse>` |
| 副作用 | 无 |
| 权限 | 需要有效会话 |

**说明：**

- 只返回白名单配置段：`mineru`、`chunking`、`embedding`、`extraction`、`table_name_validation`、`analysis`、`vl_model`、`web_search`、`storage`
- 密钥字段返回 `{configured: true/false}` 而非实际值
- `embedding` 只返回 `base_url`、`api_key`、`model_name`
- 会话无效返回 401

请求示例：

```bash
curl "http://localhost:5019/settings/config" \
  --cookie "settings_session=xxx"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "version": "abc123def456",
    "config": {
      "mineru": {
        "api_url": "http://mineru:8080",
        "polling_interval": 2.0,
        "max_wait_time": 600
      },
      "embedding": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": {"configured": true},
        "model_name": "text-embedding-v4"
      },
      "extraction": {
        "max_concurrency": 5,
        "enabled_sources": ["table", "text", "vl"]
      }
    }
  }
}
```

---

### 8.4 `PATCH /settings/config` 更新运行时配置

更新运行时配置并即时生效。

| 项 | 说明 |
|---|---|
| 方法 | `PATCH` |
| 路径 | `/settings/config` |
| 请求体 | `SettingsPatchRequest` |
| 响应 | `ResponseWrapper<ConfigUpdateResponse>` |
| 副作用 | 更新 `config.yaml` 文件和内存配置 |
| 权限 | 需要有效会话 |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `base_version` | string | 是 | 基于的配置版本（用于检测冲突） |
| `changes` | object | 否 | 要修改的配置字段（嵌套路径） |
| `secrets` | object | 否 | 密钥字段操作（keep/replace/clear） |

**说明：**

- `base_version` 不匹配返回 409（配置已被他人修改）
- `changes` 支持嵌套路径，如 `{"extraction": {"max_concurrency": 10}}`
- `secrets` 格式：`{"embedding.api_key": {"action": "replace", "value": "sk-xxx"}}`
- 只允许修改白名单配置段
- 使用 ruamel.yaml 保留注释和格式
- 原子性替换文件，更新成功后即时生效
- 新调用立即使用新配置，进行中的任务不受影响

请求示例：

```bash
curl -X PATCH "http://localhost:5019/settings/config" \
  --cookie "settings_session=xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "base_version": "abc123def456",
    "changes": {
      "extraction": {
        "max_concurrency": 10
      },
      "chunking": {
        "chunk_size": 1000
      }
    },
    "secrets": {
      "embedding.api_key": {
        "action": "replace",
        "value": "sk-newkey123"
      }
    }
  }'
```

响应示例：

```json
{
  "code": 0,
  "message": "配置已保存并即时生效",
  "data": {
    "version": "def456ghi789",
    "updated_fields": ["extraction.max_concurrency", "chunking.chunk_size", "embedding.api_key"],
    "restart_required": false
  }
}
```

错误示例（版本冲突）：

```json
{
  "code": 409,
  "message": "配置版本冲突：当前版本为 def456ghi789，提交的基础版本为 abc123def456",
  "data": null
}
```

---

### 8.5 `POST /settings/logout` 退出登录

撤销当前会话。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/settings/logout` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<{authenticated: boolean}>` |
| 副作用 | 删除会话 Cookie |

**说明：**

- 撤销服务端会话并删除 Cookie
- 无会话时调用也返回成功

请求示例：

```bash
curl -X POST "http://localhost:5019/settings/logout" \
  --cookie "settings_session=xxx"
```

响应示例：

```json
{
  "code": 0,
  "message": "已退出设置",
  "data": {
    "authenticated": false
  }
}
```

---

## 插入位置5: 新增第9章 运行时监控接口

## 9. 运行时监控接口 `/runtime`

运行时监控接口提供进程内并发池状态和压力历史的只读查询。

---

### 9.1 `GET /runtime/concurrency` 查询并发池状态

返回当前 worker 的并发池快照与压力历史。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/runtime/concurrency` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ConcurrencySnapshot>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `window` | string | 否 | `60s` | 压力趋势时间窗口：`60s`/`5m`/`30m` |

**说明：**

- 返回当前并发池状态：各阶段的运行中任务数、队列长度
- 返回指定时间窗口内的压力历史（采样点）
- 用于诊断并发瓶颈和负载情况
- 单进程部署时只反映当前进程状态

请求示例：

```bash
curl "http://localhost:5019/runtime/concurrency?window=5m"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timestamp": "2026-10-14T16:30:00",
    "pools": {
      "parsing": {
        "active": 2,
        "queued": 5,
        "max_concurrency": 3
      },
      "extracting": {
        "active": 8,
        "queued": 12,
        "max_concurrency": 10
      },
      "analyzing": {
        "active": 5,
        "queued": 3,
        "max_concurrency": 8
      }
    },
    "history": [
      {
        "timestamp": "2026-10-14T16:25:00",
        "total_active": 10,
        "total_queued": 15
      },
      {
        "timestamp": "2026-10-14T16:30:00",
        "total_active": 15,
        "total_queued": 20
      }
    ]
  }
}
```

---

## 插入位置6: 新增第10章 调用日志接口

## 10. 调用日志接口 `/invocations`

调用日志接口记录和查询正式业务调用（文件处理、独立分析）的完整生命周期。

**记录范围：**

- `POST /file/parse` 上传处理
- `POST /file/{id}/retry/{stage}` 重试
- `POST /analysis/run` 独立分析

**不记录：**

- 调试接口（`/extraction/test`、`/analysis/test`）
- 配置查询（`/extraction/fields`、`/analysis/rules`）
- 单纯的状态查询

**字段说明：**

- `kind`：`file_parse` / `file_retry` / `independent_analysis`
- `status`：`queued` / `running` / `complete` / `failed`
- `parameters`：调用时的关键参数（mode、stage、items 等）
- `result`：完整的 extraction + analysis 结果（仅 complete 状态）
- `stage_times`：各阶段的开始/结束时间

---

### 10.1 `GET /invocations` 分页查询调用日志

分页查询调用日志列表（不包含大字段）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/invocations` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<InvocationListResponse>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `page` | integer | 否 | `1` | 页码 |
| `page_size` | integer | 否 | `20` | 每页条数，最大100 |
| `kind` | string | 否 | `""` | 按调用类型过滤 |
| `status` | string | 否 | `""` | 按状态过滤 |
| `type_id` | string | 否 | `""` | 按文档类型过滤 |
| `q` | string | 否 | `""` | 模糊搜索（file_name、file_id、task_id、log_id） |

**说明：**

- 列表不返回 `request_input`、`stage_times`、`result` 大字段
- 按 `created_at DESC, log_id DESC` 排序
- 支持模糊搜索，自动转义通配符

请求示例：

```bash
curl "http://localhost:5019/invocations?page=1&page_size=20&kind=file_parse&status=complete"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "log_id": "log_abc123",
        "kind": "file_parse",
        "status": "complete",
        "file_id": "file_def456",
        "task_id": null,
        "type_id": "default",
        "file_name": "合同.pdf",
        "file_size": 1048576,
        "mode": "async",
        "stage": null,
        "source": null,
        "item_count": 0,
        "biz_ids": [],
        "created_at": "2026-10-14T10:00:00",
        "started_at": "2026-10-14T10:00:01",
        "finished_at": "2026-10-14T10:02:30",
        "updated_at": "2026-10-14T10:02:30",
        "duration_ms": 150000,
        "error": null
      }
    ],
    "total": 1250,
    "page": 1,
    "page_size": 20
  }
}
```

---

### 10.2 `GET /invocations/{log_id}` 查询调用详情

查询单个调用的完整详情（包含大字段）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/invocations/{log_id}` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<InvocationDetail>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `log_id` | string | 是 | 调用日志 ID |

**说明：**

- 返回完整详情，包括 `request_input`、`parameters`、`stage_times`、`result`
- 对于进行中的文件处理（`status=queued/running`），实时从 `files` 表读取阶段时间
- 已结束的调用只返回当次调用的快照
- 不存在返回 404

请求示例：

```bash
curl "http://localhost:5019/invocations/log_abc123"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "log_id": "log_abc123",
    "kind": "file_parse",
    "status": "complete",
    "file_id": "file_def456",
    "file_name": "合同.pdf",
    "type_id": "default",
    "created_at": "2026-10-14T10:00:00",
    "finished_at": "2026-10-14T10:02:30",
    "duration_ms": 150000,
    "request_input": {
      "file_name": "合同.pdf",
      "type_id": "default",
      "mode": "async",
      "params": {"contract_type": "采购"}
    },
    "parameters": {
      "mode": "async",
      "callback_url": "https://example.com/webhook",
      "callback_mode": "full"
    },
    "stage_times": {
      "parsing": {"start": "2026-10-14T10:00:01", "end": "2026-10-14T10:00:15"},
      "tableing": {"start": "2026-10-14T10:00:15", "end": "2026-10-14T10:00:20"},
      "extracting": {"start": "2026-10-14T10:01:30", "end": "2026-10-14T10:02:15"}
    },
    "result": {
      "extraction": [
        {"field_id": "F001", "field_name": "合同金额", "value": "500000"}
      ],
      "analysis": [
        {"rule_id": "R001", "rule_name": "金额合规", "result": true}
      ]
    },
    "error": null
  }
}
```

---

### 10.3 `GET /invocations/{log_id}/result.json` 下载调用结果

下载调用结果的 JSON 文件（只包含 extraction + analysis）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/invocations/{log_id}/result.json` |
| 请求体 | 无 |
| 响应 | JSON 文件下载 |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `log_id` | string | 是 | 调用日志 ID |

**说明：**

- 返回 `Content-Disposition: attachment` 触发浏览器下载
- 文件名格式：`invocation-{log_id}.json`
- 只包含 `extraction` 和 `analysis` 结果，不包含原始输入、解析中间产物
- 日志不存在返回 404
- 调用尚未完成（无 result）返回 409

请求示例：

```bash
curl "http://localhost:5019/invocations/log_abc123/result.json" -O
```

响应内容（invocation-log_abc123.json）：

```json
{
  "extraction": [
    {
      "field_id": "F001",
      "field_name": "合同金额",
      "value": "500000",
      "reason": "从第3页表格中提取",
      "pages": [3],
      "source_pages": [3],
      "source_refs": {
        "table": {"table_index": 0, "table_name": "价格明细表"}
      }
    }
  ],
  "analysis": [
    {
      "rule_id": "R001",
      "rule_name": "金额合规性",
      "result": true,
      "reason": "金额在允许范围内",
      "input_values": {"合同金额": "500000"},
      "source_refs": {
        "合同金额": {"table": {"table_index": 0}}
      }
    }
  ]
}
```

---

## 章节编号调整说明

添加了第 8、9、10 章后，原有章节编号需要调整：

- 原 **8. 日志接口** → **11. 日志接口**
- 原 **9. 异步回调契约** → **12. 异步回调契约**
- 原 **10. SSE 流式事件契约** → **13. SSE 流式事件契约**
- 原 **11. 枚举与关键结构速查** → **14. 枚举与关键结构速查**
- 原 **12. 附录** → **15. 附录**

## 接口总览表格更新

需要将第2章的接口总览表格更新为：

| 分组 | 数量 | 说明 |
|---|---:|---|
| `/file` | 20 | 文件上传、处理、重试、内容、结果、统计、删除 |
| `/extraction` | 7 | 字段配置、默认提示词、同步/流式调试 |
| `/analysis` | 8 | 分析规则、同步/流式调试、独立分析执行、任务查询 |
| `/doctype` | 18 | 文档类型、参数管理、复制、导入导出、模板、项目归类 |
| `/search` | 1 | 向量检索 |
| `/settings` | 5 | 运行时配置管理、会话认证 |
| `/runtime` | 1 | 并发池状态监控 |
| `/invocations` | 3 | 调用日志查询和结果下载 |
| `/log` | 3 | 应用日志读取和实时流 |

并在详细表格中添加：

```
| GET | `/file/stats` | 文件处理统计 |
| GET | `/analysis/tasks/{task_id}` | 查询独立分析任务 |
| GET | `/doctype/{type_id}/params` | 查询类型入参定义 |
| POST | `/doctype/{type_id}/params` | 新增/更新类型入参 |
| DELETE | `/doctype/{type_id}/params/{param_key}` | 删除类型入参 |
| GET | `/doctype/{type_id}/config_version` | 查询配置版本 |
| POST | `/settings/login` | 管理员登录 |
| GET | `/settings/session` | 查询会话状态 |
| GET | `/settings/config` | 读取运行时配置 |
| PATCH | `/settings/config` | 更新运行时配置 |
| POST | `/settings/logout` | 退出登录 |
| GET | `/runtime/concurrency` | 查询并发池状态 |
| GET | `/invocations` | 分页查询调用日志 |
| GET | `/invocations/{log_id}` | 查询调用详情 |
| GET | `/invocations/{log_id}/result.json` | 下载调用结果 |
```
