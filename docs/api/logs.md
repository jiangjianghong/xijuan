# 应用日志接口 /log

> 对应服务版本 0.3.0

查看服务运行日志（`logs/app_*.log`），供运维排查。均为只读接口。

## 应用日志文件列表

列出 `logs/` 下可查看的 `app_*.log`，按修改时间倒序（最新在前）。

- 方法路径：`GET /log/files`
- 认证：无（内网部署）

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
- 认证：无（内网部署）

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
- 认证：无（内网部署）

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

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | SSE 流 | text/event-stream |
| 400 | 文件名 / 等级不合法 | `{"detail": "..."}` |
| 404 | 指定的日志文件不存在 | `{"detail": "..."}` |
