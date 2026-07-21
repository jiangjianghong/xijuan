# 接口总览与公共约定

> 对应服务版本 0.3.0

本页是所有接口的公共约定。精确 schema 以 `docs/openapi.json`（及活的 `/docs` Swagger UI）为权威，本手写文档是语义 / 示例 / 流程的权威。

## 基础信息

- Base URL：`http://localhost:5019`（按部署调整）
- 版本：`0.3.0`
- 交互式 API 文档（Swagger UI）：`GET /docs`
- 机器可读规格：`docs/openapi.json`（OpenAPI 3.1）
- 全部接口清单见 [README 接口总览](../README.md#接口总览)

## 处理管线

PDF 经六阶段管线沉淀为结构化结果：

```
parsing → tableing → chunking → embedding → extracting → analyzing → complete
```

每阶段失败置 `progress = <stage>_failed` 并写入 `error`。阶段与状态机详见 [enums](../reference/enums.md#progress-状态机)。

## 通用响应信封

除 SSE 流、`multipart` 上传、二进制下载外，所有接口统一返回 `ResponseWrapper`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | integer | 业务状态码，成功 200；业务校验失败（如文件超限）可能在 HTTP 200 里返回非 200 的 `code` |
| `message` | string | 人类可读结果说明 |
| `data` | any | 业务负载，类型随接口而定（对象 / 数组 / null） |

```jsonc
{ "code": 200, "message": "success", "data": {} }
```

## 认证

当前**无鉴权**（面向内网部署）。对外暴露时应在网关层加鉴权；外部集成方请与运维确认接入方式。本文档在各接口保留「认证」行以备后续启用。

## 分页约定

分页接口统一返回信封：

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | array | 当前页数据 |
| `total` | integer | 总条数 |
| `page` | integer | 当前页码（从 1 开始） |
| `page_size` | integer | 每页条数 |
| `total_pages` | integer | 总页数 |

**例外**：`GET /doctype/list` 是唯一的形态切换——**传齐 `page` + `page_size` 才返回 `{items, total}`，否则原样返回数组**（向后兼容旧调用方）。详见 [doctype](doctype.md)。

## 错误码总表

| HTTP | 含义 | 响应体 |
|---|---|---|
| 200 | 成功（业务失败可能体现在 `code`） | ResponseWrapper |
| 400 | 请求参数非法 / 业务前置校验失败 | ResponseWrapper 或 `{"detail": "..."}` |
| 404 | 资源不存在 | ResponseWrapper 或 `{"detail": "..."}` |
| 409 | 唯一性冲突（`field_id` / `rule_id` 被其它 `type_id` 占用） | ResponseWrapper |
| 422 | 请求体 Pydantic 校验失败 | Pydantic 错误体 |
| 500 | 服务内部异常 | ResponseWrapper 或 `{"detail": "..."}` |

**业务错误体（ResponseWrapper 风格）**

```jsonc
{ "code": 400, "message": "文件大小超过限制 (100MB)", "data": null }
```

**HTTPException 错误体**

```jsonc
{ "detail": "原始 PDF 不存在" }
```

**Pydantic 校验错误（422）**

```jsonc
{
  "detail": [
    { "type": "value_error", "loc": ["body", "expression"],
      "msg": "expression 必须包含至少一个 <field_result>字段标识</field_result> 占位符" }
  ]
}
```
