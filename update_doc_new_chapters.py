import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 步骤 5: 添加新的第 8、9、10 章 ==========
new_chapters = """
## 8. 系统设置接口 `/settings`

运行时配置管理和会话认证接口。

### 8.1 `POST /settings/login` 管理员登录

使用密码进行管理员认证。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/settings/login` |
| 请求体 | `LoginRequest` |
| 响应 | `ResponseWrapper` |
| 副作用 | 创建会话并设置 Cookie |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `password` | string | 是 | 管理员密码 |

**说明：**

- 密码在 `configs/config.yaml` 的 `settings.password` 中配置（明文）
- 配置为空字符串时禁用登录保护
- 会话默认 30 分钟，使用 HttpOnly + SameSite=Strict Cookie
- 会话存储在单进程内存中，重启后失效
- 密码错误返回 401

请求示例：

```bash
curl -X POST "http://localhost:5019/settings/login" \
  -H "Content-Type: application/json" \
  -d '{"password": "your_admin_password"}'
```

---

### 8.2 `GET /settings/session` 查询会话状态

检查当前会话是否有效。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/settings/session` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<SessionStatus>` |
| 副作用 | 无 |

**说明：**

- 未认证或会话过期返回 `{"authenticated": false}`
- 有效会话返回过期时间

请求示例：

```bash
curl "http://localhost:5019/settings/session" \
  --cookie "session=<session_token>"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "authenticated": true,
    "expires_at": "2026-10-14T16:30:00"
  }
}
```

---

### 8.3 `GET /settings/config` 读取运行时配置

读取当前运行时配置，敏感字段（如 API key）只返回是否已配置。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/settings/config` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ConfigSnapshot>` |
| 副作用 | 无 |
| 权限 | 需要登录 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `sections` | string | 否 | 全部 | 逗号分隔的配置节列表 |

**说明：**

- 允许读取的节：`mineru`, `chunking`, `embedding`, `extraction`, `table_name_validation`, `analysis`, `vl_model`, `web_search`, `storage`
- 敏感字段（`api_key`, `password` 等）返回 `{"configured": true/false}`
- `embedding` 只返回 `base_url`, `api_key`, `model_name`
- 未登录返回 401

请求示例：

```bash
curl "http://localhost:5019/settings/config?sections=embedding,vl_model" \
  --cookie "session=<session_token>"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "embedding": {
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": {"configured": true},
      "model_name": "text-embedding-v4"
    },
    "vl_model": {
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": {"configured": true},
      "model_name": "qwen-vl-max-latest",
      "global_max_concurrency": 8
    }
  }
}
```

---

### 8.4 `PATCH /settings/config` 更新运行时配置

热更新运行时配置，原子性写入 YAML 文件并重载内存配置。

| 项 | 说明 |
|---|---|
| 方法 | `PATCH` |
| 路径 | `/settings/config` |
| 请求体 | `ConfigUpdate` |
| 响应 | `ResponseWrapper<ConfigSnapshot>` |
| 副作用 | 更新配置文件和内存配置，重建 VL 并发信号量 |
| 权限 | 需要登录 |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `updates` | object | 是 | 配置节更新字典 |

**说明：**

- 允许更新的节同 `GET /settings/config`
- 敏感字段支持三种操作：
  - `{"keep": true}` - 保持原值
  - `{"value": "new_secret"}` - 替换为新值
  - `{"clear": true}` - 清空
- 使用 `ruamel.yaml` 保留 YAML 注释和顺序
- 先验证完整配置（Pydantic），再原子替换文件
- 更新后立即生效，正在执行的任务不受影响
- 配置错误返回 400，未登录返回 401

请求示例：

```bash
curl -X PATCH "http://localhost:5019/settings/config" \
  -H "Content-Type: application/json" \
  --cookie "session=<session_token>" \
  -d '{
    "updates": {
      "vl_model": {
        "api_key": {"value": "sk-new-key"},
        "global_max_concurrency": 12
      },
      "embedding": {
        "api_key": {"keep": true},
        "model_name": "text-embedding-v5"
      }
    }
  }'
```

---

### 8.5 `POST /settings/logout` 退出登录

注销当前会话。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/settings/logout` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper` |
| 副作用 | 删除会话并清除 Cookie |

**说明：**

- 清除服务端会话记录
- 设置过期 Cookie 通知客户端清除
- 未登录时调用也返回成功

请求示例：

```bash
curl -X POST "http://localhost:5019/settings/logout" \
  --cookie "session=<session_token>"
```

---

## 9. 运行时监控接口 `/runtime`

查询系统运行时状态。

### 9.1 `GET /runtime/concurrency` 查询并发池状态

查询 VL 模型全局并发池的当前状态。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/runtime/concurrency` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ConcurrencyStatus>` |
| 副作用 | 无 |

**说明：**

- 返回 VL 模型调用的并发上限、当前活跃数、等待队列长度
- 上限由 `vl_model.global_max_concurrency` 配置（默认 8）
- 通过 `utils/vl_client.py` 的 `asyncio.Semaphore` 控制

请求示例：

```bash
curl "http://localhost:5019/runtime/concurrency"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "max_concurrency": 8,
    "active_count": 3,
    "waiting_count": 0,
    "timestamp": "2026-10-14T15:45:30"
  }
}
```

---

## 10. 调用日志接口 `/invocations`

LLM 和 VL 模型调用日志查询接口。

### 10.1 `GET /invocations` 分页查询调用日志

分页查询 LLM/VL 调用历史记录。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/invocations` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<InvocationLogPage>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `page` | integer | 否 | 1 | 页码（从 1 开始） |
| `page_size` | integer | 否 | 20 | 每页条数（1-100） |
| `file_id` | string | 否 | - | 按文件 ID 过滤 |
| `model_type` | string | 否 | - | 按模型类型过滤（`llm`/`vl`/`embedding`） |
| `status` | string | 否 | - | 按状态过滤（`success`/`error`） |
| `start_time` | string | 否 | - | 起始时间（ISO 8601） |
| `end_time` | string | 否 | - | 结束时间（ISO 8601） |

**说明：**

- 按调用时间倒序返回
- 每条记录包含：调用 ID、文件 ID、模型类型、状态、tokens、耗时、创建时间
- 不包含完整请求/响应内容（使用 `GET /invocations/{log_id}` 查看详情）

请求示例：

```bash
curl "http://localhost:5019/invocations?page=1&page_size=20&model_type=llm&status=success"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "log_id": "inv_abc123",
        "file_id": "file_xyz",
        "model_type": "llm",
        "model_name": "qwen-max",
        "status": "success",
        "prompt_tokens": 1500,
        "completion_tokens": 300,
        "total_tokens": 1800,
        "duration_ms": 2345,
        "created_at": "2026-10-14T15:30:00"
      }
    ],
    "total": 156,
    "page": 1,
    "page_size": 20
  }
}
```

---

### 10.2 `GET /invocations/{log_id}` 查询调用详情

查询单条调用记录的完整信息（包含请求和响应内容）。

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
| `log_id` | string | 是 | 调用记录 ID |

**说明：**

- 返回完整的请求参数和响应内容
- 对于 VL 调用，包含 base64 图片（可能很大）
- 错误调用包含错误信息和堆栈
- 记录不存在返回 404

请求示例：

```bash
curl "http://localhost:5019/invocations/inv_abc123"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "log_id": "inv_abc123",
    "file_id": "file_xyz",
    "model_type": "llm",
    "model_name": "qwen-max",
    "status": "success",
    "request": {
      "messages": [{"role": "user", "content": "..."}],
      "temperature": 0.1
    },
    "response": {
      "choices": [{"message": {"content": "..."}}]
    },
    "prompt_tokens": 1500,
    "completion_tokens": 300,
    "duration_ms": 2345,
    "created_at": "2026-10-14T15:30:00"
  }
}
```

---

### 10.3 `GET /invocations/{log_id}/result.json` 下载调用结果

以 JSON 文件形式下载调用记录（用于存档或分析）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/invocations/{log_id}/result.json` |
| 请求体 | 无 |
| 响应 | `application/json` 文件流 |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `log_id` | string | 是 | 调用记录 ID |

**说明：**

- 直接返回 JSON 文件，浏览器会触发下载
- 文件名格式：`invocation_{log_id}.json`
- 内容与 `GET /invocations/{log_id}` 相同
- 记录不存在返回 404

请求示例：

```bash
curl "http://localhost:5019/invocations/inv_abc123/result.json" -O
```

---
"""

# 在 "## 8. 日志接口" 之前插入新章节
pattern = r'(---\s*\n)(## 8\. 日志接口 `/log`)'
content = re.sub(pattern, r'\1' + new_chapters + r'\n\2', content)

print("已添加第 8、9、10 章")

# 更新原 8-12 章的编号为 11-15 章
content = content.replace('## 8. 日志接口 `/log`', '## 11. 日志接口 `/log`')
content = content.replace('## 9. 异步回调契约', '## 12. 异步回调契约')
content = content.replace('## 10. SSE 流式事件契约', '## 13. SSE 流式事件契约')
content = content.replace('## 11. 枚举与关键结构速查', '## 14. 枚举与关键结构速查')
content = content.replace('### 11.1 枚举', '### 14.1 枚举')
content = content.replace('### 11.2 source_refs 结构要点', '### 14.2 source_refs 结构要点')
content = content.replace('### 11.3 pages 与 source_pages', '### 14.3 pages 与 source_pages')
content = content.replace('### 11.4 占位符规则', '### 14.4 占位符规则')
content = content.replace('## 12. 附录', '## 15. 附录')

print("已更新原 8-12 章的编号为 11-15 章")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤5完成: 已添加新的第 8、9、10 章并更新原章节编号")
