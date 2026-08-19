# 系统设置接口 /settings

> 对应服务版本 0.3.0

设置页的运行时配置读写接口。受**管理员短期会话**保护：先 `POST /settings/login` 拿到 `settings_session` Cookie，再访问配置接口。

配置项含义见 [guides/configuration.md](../guides/configuration.md)。

**会话 Cookie**

| 属性 | 值 |
|---|---|
| 名称 | `settings_session` |
| 作用域 | `path=/settings` |
| 标志 | `HttpOnly`、`SameSite=strict`、`Secure` 由 `settings.secure_cookie` 控制 |
| 有效期 | `settings.session_minutes` 分钟 |

浏览器请求需带 `credentials: 'include'`；curl 需用 `-c` / `-b` 保存并回传 cookie jar。

## 设置页登录

校验管理员密码并下发会话 Cookie。

- 方法路径：`POST /settings/login`
- 认证：无（本接口即认证入口）

**请求体**

<!-- AUTOGEN:request-body POST /settings/login -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| password | string | 是 | — |  |
<!-- /AUTOGEN:request-body -->

**请求示例（curl）**

```bash
curl -X POST http://localhost:5019/settings/login \
  -H "Content-Type: application/json" \
  -c /tmp/settings_cookie.txt \
  -d '{"password": "your-admin-password"}'
```

**响应体**

<!-- AUTOGEN:response POST /settings/login status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| authenticated | boolean | 是 | 恒为 true（失败走错误码） |
<!-- /AUTOGEN:response -->

```jsonc
{ "code": 200, "message": "登录成功", "data": { "authenticated": true } }
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 登录成功，已下发 Cookie | ResponseWrapper |
| 401 | 密码错误 | `{"detail": "..."}` |
| 429 | 登录尝试过于频繁（按客户端 IP 限流） | `{"detail": "..."}` |

## 设置会话状态

查询当前 Cookie 是否仍有效，供前端决定展示登录框还是设置表单。

- 方法路径：`GET /settings/session`
- 认证：无——**未登录也返回 200**，用 `authenticated=false` 表达，不返回 401

**响应体**

<!-- AUTOGEN:response GET /settings/session status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| authenticated | boolean | 是 | 当前会话是否有效 |
<!-- /AUTOGEN:response -->

```jsonc
{ "code": 200, "message": "success", "data": { "authenticated": false } }
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无论是否已登录） | ResponseWrapper |

## 读取运行时配置

返回可在设置页编辑的配置分组及当前值。

- 方法路径：`GET /settings/config`
- 认证：需要 `settings_session` Cookie

> **密钥不返回明文**：`SECRET_PATHS` 覆盖的字段（各模型与网络搜索的 `api_key`）一律呈现为 `{"configured": true|false}`，只告知是否已配置。

**请求示例（curl）**

```bash
curl -b /tmp/settings_cookie.txt http://localhost:5019/settings/config
```

**响应体**

<!-- AUTOGEN:response GET /settings/config status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| version | string | 是 | 配置指纹，PATCH 时作为 base_version 回传 |
| config | object | 是 | 可编辑分组及当前值（密钥呈现为 configured 布尔） |
| readonly | object | 是 | 各分组的只读字段清单 |
<!-- /AUTOGEN:response -->

```jsonc
{
  "code": 200,
  "message": "success",
  "data": {
    "version": "9f2c...",              // 配置指纹，PATCH 时原样回传作 base_version
    "config": {
      "concurrency": { "global_llm": 16, "task_extraction": 4, "global_pipeline": 4 },
      "extraction": { "base_url": "https://...", "model": "qwen-plus",
                      "api_key": { "configured": true } },   // 密钥只给状态
      "embedding":  { "base_url": "https://...", "model": "text-embedding-v4",
                      "api_key": { "configured": true } }
    },
    "readonly": { "embedding": ["embedding_dim", "batch_size", "timeout", "retry_count"] }
  }
}
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 401 | 未登录或会话过期 | `{"detail": "设置会话已过期，请重新登录"}` |
| 500 | 配置文件读取失败 | `{"detail": "..."}` |

## 更新运行时配置

按分组增量更新配置，**立即生效**并写回 `configs/config.yaml`。

- 方法路径：`PATCH /settings/config`
- 认证：需要 `settings_session` Cookie

> **乐观锁**：`base_version` 必须等于最近一次 `GET /settings/config` 返回的 `version`。他人在此期间改过配置则返回 409，前端需重新拉取后再提交。

> **即时生效范围**：`concurrency` 分组的改动会同步 `replace` 到 limiter 注册表，正在等待的任务按新限额放行；其余分组在下次读取 `get_config()` 时生效。

**请求体**

<!-- AUTOGEN:request-body PATCH /settings/config -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| base_version | string | 是 | — |  |
| changes | object | 否 | — |  |
| secrets | object | 否 | — |  |
<!-- /AUTOGEN:request-body -->

`changes` 为 `{分组: {字段: 值}}`，只允许开放分组与字段，否则 422。
`secrets` 为 `{路径: {action, value}}`：

| action | 含义 |
|---|---|
| `keep` | 保持原值不变（`value` 可省略） |
| `replace` | 写入 `value` |
| `clear` | 清空该密钥 |

**请求示例（curl）**

```bash
curl -X PATCH http://localhost:5019/settings/config \
  -H "Content-Type: application/json" \
  -b /tmp/settings_cookie.txt \
  -d '{
        "base_version": "9f2c...",
        "changes": { "concurrency": { "task_extraction": 8, "global_pipeline": 6 } },
        "secrets":  { "extraction.api_key": { "action": "keep" } }
      }'
```

**响应体**

<!-- AUTOGEN:response PATCH /settings/config status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| version | string | 是 | 更新后的新配置指纹 |
| config | object | 是 | 更新后的配置（密钥仍不返回明文） |
| readonly | object | 是 | 各分组的只读字段清单 |
<!-- /AUTOGEN:response -->

```jsonc
{
  "code": 200,
  "message": "配置已保存并即时生效",
  "data": {
    "version": "3a71...",       // 更新后的新指纹，下次 PATCH 用它
    "config":   { "...": "同 GET /settings/config" },
    "readonly": { "embedding": ["embedding_dim", "batch_size", "timeout", "retry_count"] }
  }
}
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 保存成功 | ResponseWrapper |
| 401 | 未登录或会话过期 | `{"detail": "..."}` |
| 409 | `base_version` 与当前配置不一致（并发修改） | `{"detail": "..."}` |
| 422 | 分组/字段不允许修改，或值不通过校验 | `{"detail": "..."}` |
| 500 | 写入配置文件失败 | `{"detail": "..."}` |

## 退出设置页

吊销会话令牌并清除 Cookie。

- 方法路径：`POST /settings/logout`
- 认证：无（无有效 Cookie 时也返回 200，重复调用不报错）

**响应体**

<!-- AUTOGEN:response POST /settings/logout status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| authenticated | boolean | 是 | 恒为 false |
<!-- /AUTOGEN:response -->

```jsonc
{ "code": 200, "message": "已退出设置", "data": { "authenticated": false } }
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（幂等） | ResponseWrapper |
