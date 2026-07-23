# 逻辑分析配置接口 /analysis

> 对应服务版本 0.3.0

管理逻辑分析规则（judge / calc / custom 三类）与调试。详细配置配方（表达式占位符 `<field_result>`、`web_search` 网络搜索、`depend_fields` 依赖、custom 的 `is_formatted` / `output_schema`）见 [analysis-config 指南](../guides/analysis-config.md)。

## 列出分析规则

按 `priority` 升序返回规则。`type_id` 为空返回全量，非空按精确匹配过滤。

- 方法路径：`GET /analysis/rules`
- 认证：无（内网部署）

**查询参数**

<!-- AUTOGEN:query-params GET /analysis/rules -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_id | string | 否 |  | 按文档类型精确过滤规则；空串返回全部。 |
<!-- /AUTOGEN:query-params -->

**响应体**

<!-- AUTOGEN:response GET /analysis/rules status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| rule_id | string | 否 |  |
| type_id | string | 是 |  |
| rule_name | string | 否 |  |
| rule_type | RuleTypeEnum | 否 |  |
| expression | string | 否 |  |
| system_prompt | string | 是 |  |
| depend_fields | array[string] | 是 |  |
| web_search | object | 是 | 结构详见 [web_search](../guides/analysis-config.md) |
| is_formatted | integer | 是 |  |
| output_schema | array[object] | 是 |  |
| enabled | integer | 是 |  |
| priority | integer | 是 |  |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 新增/更新分析规则（upsert）

按 `rule_id`（**全局唯一**）upsert。

- 方法路径：`POST /analysis/rules`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /analysis/rules -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| rule_id | string | 是 | — | 规则 ID，匹配 `^[a-zA-Z0-9_]+$`（最长 100），**全局唯一**。 |
| type_id | string | 否 | default | 归属文档类型，默认 `default`。 |
| rule_name | string | 是 | — | 规则显示名（最长 200）。 |
| rule_type | RuleTypeEnum | 是 | — | 规则类型：`judge`（LLM 判断）/ `calc`（numexpr 计算）/ `custom`（LLM 自由生成，返回 `{value, reason}`）。 |
| expression | string | 是 | — | 表达式 / 提示词，须含至少一个 `<field_result>字段ID</field_result>` 占位符（渲染时替换为字段提取值）。 |
| system_prompt | string | 否 | — | [judge/custom] 调控 LLM 的 system prompt；`calc` 类型忽略。 |
| depend_fields | array[string] | 否 | — | 依赖的字段 ID 列表（用于取值并填充占位符）。 |
| web_search | object | 否 | — | [judge/custom] 网络搜索配置（自由 JSON）：`{enabled: bool, query: str, count?: int, freshness?: str}`。启用时判断前先调博查搜索，`query` 支持 `<field_result>字段ID</field_result>` 占位符，搜索结果文本替换 `expression` 中的 `<web_search_result/>` 占位符（启用时必须存在）。搜索失败不致命（占位符替换为失败提示继续判断）。（结构详见 [web_search](../guides/analysis-config.md)） |
| is_formatted | integer | 否 | 0 | [custom] 格式化输出开关（0/1，默认 0）。0=模型返回纯文本 `value`；1=按 `output_schema` 返回结构化 JSON 字符串。仅 `custom` 生效。 |
| output_schema | array[object] | 否 | — | [custom] 格式化输出的字段树（`is_formatted=1` 时必填）。节点结构 `{key, type, example?, desc?, children?}`，`type` ∈ `string`/`number`/`boolean`/`object`/`array`，`object`/`array` 须含非空 `children`；渲染为结构说明 + 示例 JSON 注入提示词。 |
| enabled | integer | 否 | 1 | 是否启用（1/0）。 |
| priority | integer | 否 | 0 | 执行优先级（升序）。 |
<!-- /AUTOGEN:request-body -->

```jsonc
{
  "rule_id": "assets_positive",
  "type_id": "financial_report",
  "rule_name": "资产为正",
  "rule_type": "calc",
  "expression": "<field_result>total_assets</field_result> > 0",
  "depend_fields": ["total_assets"]
}
```

`custom`（格式化输出）示例——`output_schema` 定义结构，模型据此产出 `value`：

```jsonc
{
  "rule_id": "shareholder_summary",
  "type_id": "financial_report",
  "rule_name": "股东结构摘要",
  "rule_type": "custom",
  "expression": "根据以下信息汇总股东结构：<field_result>shareholders</field_result>",
  "depend_fields": ["shareholders"],
  "is_formatted": 1,
  "output_schema": [
    { "key": "总股东数", "type": "number", "example": "3" },
    { "key": "主要股东", "type": "array", "children": [
      { "key": "名称", "type": "string", "example": "张三" },
      { "key": "持股比例", "type": "string", "example": "51%" }
    ]}
  ]
}
```

**响应体**

<!-- AUTOGEN:response POST /analysis/rules status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| rule_id | string | 是 | 规则 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已创建 / 已更新 | ResponseWrapper |
| 409 | `rule_id` 已被其它 `type_id` 占用 | ResponseWrapper |
| 422 | `expression` 缺 `<field_result>` 占位符 / 启用 `web_search` 时的校验失败 | Pydantic 错误体 |

> `system_prompt` 对 `judge` / `custom` 生效；`calc` 用 `numexpr` 计算，结果按 `analysis.calc_precision`（默认 2 位）保留小数。`custom` 走 LLM 自由生成 `{value, reason}`，`is_formatted=1` 时 `value` 为按 `output_schema` 组织的结构化 JSON 字符串；开启格式化但 `output_schema` 为空 / 结构非法 → **422**。

## 删除分析规则

**硬删除**规则本身；历史 `analysis_result` **不级联清理**。

- 方法路径：`DELETE /analysis/rules/{rule_id}`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params DELETE /analysis/rules/{rule_id} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| rule_id | string | 是 | 分析规则 ID（全局唯一，匹配 `^[a-zA-Z0-9_]+$`，最长 100）。 |
<!-- /AUTOGEN:path-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已删除 | ResponseWrapper（`data: null`） |
| 404 | 规则不存在 | ResponseWrapper |

## 检查规则 ID 是否存在

只读探测 `rule_id` 是否已被占用（保存前查重）。**全局**查存在性。

- 方法路径：`GET /analysis/rules/{rule_id}/check`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /analysis/rules/{rule_id}/check -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| rule_id | string | 是 | 分析规则 ID（全局唯一，匹配 `^[a-zA-Z0-9_]+$`，最长 100）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /analysis/rules/{rule_id}/check status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| exists | boolean | 是 | 是否已存在 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 无论是否存在 | ResponseWrapper（`data.exists`） |

## 逻辑分析调试（同步）

传 `rule_id`（用已存规则）或 `config`（临时配置）二选一，均需 `file_id`。依赖字段值取自该 `file_id` 已有的 `extraction_result`。

- 方法路径：`POST /analysis/test`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /analysis/test -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file_id | string | 是 | — | 目标文件 ID（其 `extraction_result` 提供依赖字段值）。 |
| rule_id | string | 否 | — | 已保存规则 ID；与 `config` 二选一。 |
| config | object | 否 | — | 临时规则配置 dict（`rule_type` / `expression` / `system_prompt` / `depend_fields`；custom 另含 `is_formatted` / `output_schema`）；与 `rule_id` 二选一。 |
<!-- /AUTOGEN:request-body -->

```jsonc
{ "file_id": "a1b2c3...", "rule_id": "assets_positive" }
```

**响应体**

<!-- AUTOGEN:response POST /analysis/test status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| input_values | object | 是 | 依赖字段取值（结构详见 [input_values](../reference/data-model.md#analysis_result)） |
| expression_resolved | string | 是 | 占位符替换后的表达式 |
| result_value | string | 是 | 结果 |
| reason | string | 是 | 理由 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | 既未传 `rule_id` 也未传 `config` | ResponseWrapper |
| 404 | 规则不存在 | ResponseWrapper |
| 500 | 分析异常 | ResponseWrapper |

## 逻辑分析流式调试（SSE）

SSE 分步推送：`input_values` → `resolved_expression` →（judge / custom：[`web_search`] → `prompt` → `llm_response`）→ `result` → `done`。入参与 `/analysis/test` 相同。

- 方法路径：`POST /analysis/test/stream`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /analysis/test/stream -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file_id | string | 是 | — | 目标文件 ID（其 `extraction_result` 提供依赖字段值）。 |
| rule_id | string | 否 | — | 已保存规则 ID；与 `config` 二选一。 |
| config | object | 否 | — | 临时规则配置 dict（`rule_type` / `expression` / `system_prompt` / `depend_fields`；custom 另含 `is_formatted` / `output_schema`）；与 `rule_id` 二选一。 |
<!-- /AUTOGEN:request-body -->

响应为 `text/event-stream`，事件清单见 [sse.md](sse.md)。

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | SSE 流 | text/event-stream |
| 400 / 404 | 同 `/analysis/test` | ResponseWrapper |

## 独立逻辑分析执行

接收外部传入的 `field_values`（不读文件提取结果、不写 `analysis_result`），按 `type_id` 加载启用规则执行。支持 `sync` / `async` / `stream`，可批量 `items`。

- 方法路径：`POST /analysis/run`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /analysis/run -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| mode | AnalysisRunModeEnum | 是 | — |  |
| callback_url | string | 否 | — |  |
| items | array[AnalysisRunItem] | 是 | — |  |
<!-- /AUTOGEN:request-body -->

```jsonc
{
  "mode": "sync",
  "items": [
    { "type_id": "financial_report", "biz_id": "doc-001",
      "field_values": { "total_assets": "1000" } }
  ]
}
```

**响应体**

<!-- AUTOGEN:response POST /analysis/run status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| total_items | integer | 否 | item 总数 |
| items | array[AnalysisRunItemResult] | 是 | 逐 item 结果 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | sync 完成 / async 已受理 | ResponseWrapper |
| 422 | `async` 模式缺 `callback_url` / 校验失败 | Pydantic 错误体 |

> 要求每条 item 的 `field_values` 覆盖规则 `depend_fields`；items 间并发，单 item 内按 `priority, rule_id` 顺序执行。`async` 用 `task_id` 通过 `callback_url` 推送 `rule_done` / `task_done` / `task_failed`（见 [callbacks.md](callbacks.md)），`stream` 走 SSE（见 [sse.md](sse.md)）。
