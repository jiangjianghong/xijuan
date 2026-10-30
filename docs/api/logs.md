# 应用日志接口 /log

> 对应服务版本 0.3.0

查看服务运行日志（`logs/app_*.log`），供运维排查。均为只读接口。

## 应用日志文件列表

列出 `logs/` 下可查看的 `app_*.log`，按修改时间倒序（最新在前）。

- 方法路径：`GET /log/files`
- 认证：需要有效凭证；具体权限见 [登录与接口访问控制](auth.md#权限边界)

**响应体**

<!-- AUTOGEN:response GET /log/files status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| current | string | 是 | 最新日志文件名（可空） |
| items | array[object] | 是 | 文件列表 |
<!-- /AUTOGEN:response -->

```jsonc
{
  "code": 200,
  "message": "success",
  "data": {
    "current": "app_2026-07-21.log",     // 最新文件名，无日志时为 null
    "items": [ { "name": "app_2026-07-21.log", "size": 20480, "modified_at": "2026-07-21T10:00:00" } ]
  }
}
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无日志时 `current` 为 null） | ResponseWrapper |

## 读取最近日志

读取指定日志文件末尾若干行。

- 方法路径：`GET /log/recent`
- 认证：需要有效凭证；具体权限见 [登录与接口访问控制](auth.md#权限边界)

**查询参数**

<!-- AUTOGEN:query-params GET /log/recent -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file | string | 否 | — |  |
| lines | integer | 否 | 200 |  |
| level | string | 否 | — |  |
<!-- /AUTOGEN:query-params -->

**请求示例（curl）**

```bash
curl "http://localhost:5019/log/recent?lines=100&level=ERROR"
```

**响应体**

<!-- AUTOGEN:response GET /log/recent status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file | string | 是 | 日志文件名（可空） |
| lines | array[object] | 是 | 日志行 |
<!-- /AUTOGEN:response -->

```jsonc
{
  "code": 200,
  "message": "success",
  "data": {
    "file": "app_2026-07-21.log",
    "lines": [
      { "level": "INFO", "line": "...", "timestamp": "2026-07-21T10:00:00",
        "type_id": "default", "file_id": "a1b2c3...", "message": "解析完成", "offset": 123 }
    ]
  }
}
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无日志文件时 `file` 为 null、`lines` 为 `[]`） | ResponseWrapper |
| 400 | 文件名不合法 / 等级不合法 | `{"detail": "..."}` |
| 404 | 指定日志文件不存在 | `{"detail": "..."}` |

## 实时日志流（SSE）

SSE 实时推送日志：先回放末尾 `tail` 行，再持续跟随追加内容；未指定 `file` 时自动跟随最新 `app_*.log`（轮转时切换）。

- 方法路径：`GET /log/stream`
- 认证：需要有效凭证；具体权限见 [登录与接口访问控制](auth.md#权限边界)

**查询参数**

<!-- AUTOGEN:query-params GET /log/stream -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file | string | 否 | — |  |
| tail | integer | 否 | 200 |  |
| level | string | 否 | — |  |
<!-- /AUTOGEN:query-params -->

响应为 `text/event-stream`。SSE 事件：
- `ready` — 连接建立
- `line` — 单行日志，`data` 同 `/log/recent` 的行结构
- `rotated` — 已切到新文件
- `heartbeat` — 心跳
- `auth_required` — 管理员会话失效，流随即关闭，请重新登录

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | SSE 流 | text/event-stream |
| 400 | 文件名 / 等级不合法 | `{"detail": "..."}` |
| 404 | 指定的日志文件不存在 | `{"detail": "..."}` |

## 业务调用日志 /invocations

以下接口同样只允许管理员会话，Key 和 IP 白名单不授予日志权限。

`GET /invocations` 按 `page/page_size/kind/status/q/type_id` 查询正式业务调用摘要，返回 `{items,total,page,page_size}`。列表不包含输入原文与完整结果。

<!-- AUTOGEN:query-params GET /invocations -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| page | integer | 否 | 1 | 页码，从 1 开始。 |
| page_size | integer | 否 | 20 | 每页条数。 |
| kind | string | 否 |  |  |
| status | string | 否 |  |  |
| q | string | 否 |  |  |
| type_id | string | 否 |  | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:query-params -->

`GET /invocations/{log_id}` 返回调用详情，包含脱敏参数、阶段时间与结果。不存在返回 404。

<!-- AUTOGEN:response GET /invocations/{log_id} status=200 -->
无结构化字段（见示例 / 权威页）
<!-- /AUTOGEN:response -->

`GET /invocations/{log_id}/result.json` 下载抽取与分析结果 JSON；不包含原始输入与解析中间产物。调用不存在返回 404，尚无结果返回 409。

<!-- AUTOGEN:response GET /invocations/{log_id}/result.json status=200 -->
无（该接口 data 为空 / 见备注）
<!-- /AUTOGEN:response -->
