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

按 `type_id` 加载启用规则批量执行逻辑判断 / 计算。字段值可由调用方直接传入（`source=values`，默认），也可取自某个文件已落库的提取结果（`source=file`）。支持 `sync` / `async` / `stream`，可批量 `items`。

### 先理解 `items` 和 `results`

该接口同时支持“批量业务输入”和“每个业务输入执行多条规则”，因此使用两层数组：

| 层级 | 代表什么 | 与哪一层对应 |
|---|---|---|
| 请求 `items[]` | 待分析的业务对象列表；每个元素是一组字段值或一个文件 | 一个 item 可以是一份合同、一张订单、一份报表等 |
| 响应 `data.items[]` | 每个业务对象的执行汇总 | 响应 `data.items[i]` 对应请求 `items[i]` |
| 响应 `data.items[i].results[]` | 第 `i` 个业务对象实际执行的逐规则结果 | 每个元素对应一条规则，不是另一个业务对象 |

映射关系：

```text
请求 items[0]（业务对象 doc-001）
  -> 响应 data.items[0]（doc-001 的执行汇总）
       -> results[0]（doc-001 的第 1 条规则结果）
       -> results[1]（doc-001 的第 2 条规则结果）

请求 items[1]（业务对象 doc-002）
  -> 响应 data.items[1]（doc-002 的执行汇总）
       -> results[0]（doc-002 的第 1 条规则结果）
       -> results[1]（doc-002 的第 2 条规则结果）
```

即使只提交一个业务对象，请求仍写成 `items: [{...}]`；即使某个对象只执行一条规则，其结果仍写成 `results: [{...}]`。没有规则实际执行时，`results` 为 `[]`。

- 方法路径：`POST /analysis/run`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /analysis/run -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| mode | AnalysisRunModeEnum | 是 | — | 执行模式：`sync` 同步返回 / `async` 后台跑并回调 / `stream` SSE 流式 |
| source | AnalysisRunSourceEnum | 否 | values | 字段值来源：`values`（默认）用请求里的 `field_values`；`file` 读各 item `file_id` 已落库的 `extraction_result` |
| persist | boolean | 否 | False | 是否把结果 upsert 进 `analysis_result`；仅 `source=file` 可用，**不改 `files.progress`** |
| callback_url | string | 否 | — | `async` 模式必填，用于推送 `rule_done` / `task_done` / `task_failed` |
| items | array[AnalysisRunItem] | 是 | — | 待分析的业务输入列表，至少 1 项。一个 item 代表一个业务对象；item 与其规则均并发执行，响应 `data.items[]` 按请求顺序逐项对应。即使只分析一个对象也必须传数组。 |
<!-- /AUTOGEN:request-body -->

`items[]`（`AnalysisRunItem`）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_id | string | 视 `source` | — | `source=values` 必填；`source=file` 可省，取 `files.type_id` |
| biz_id | string | 是 | — | 调用方业务 ID，原样回传（与 `file_id` 无关） |
| field_values | object | 视 `source` | `{}` | `{field_id: value}` 映射；`source=values` 必填，`source=file` 禁传 |
| rule_ids | array[string] \| null | 否 | `null` | 规则白名单，语义见下表 |
| file_id | string \| null | 视 `source` | `null` | `source=file` 必填、`source=values` 禁传；取该文件已落库的 `extraction_result` 作字段值 |

**取值来源**

| `source` | 字段值来源 | `field_values` | `file_id` | `persist` |
|---|---|:--:|:--:|:--:|
| `values`（默认） | 请求里的 `field_values` | 必填 | 禁传 | 不可用 |
| `file` | 该 `file_id` 的 `extraction_result` | 禁传 | 必填 | 可用 |

`source=file` 时：`type_id` 取 `files.type_id`（请求传了且不一致 → 该 item 报错）；`source_refs` 会并入各依赖字段的提取溯源，键为 `field_id`，与 `_web_search` 等元数据键同级（与管线版一致）。`persist=true` 把结果 upsert 进 `analysis_result`，但**不改 `files.progress`** —— 管线状态机只由 pipeline / retry 维护。

读库集中在 items 并发之前、`persist` 写库在并发之后（`AsyncSession` 非并发安全）。

`rule_ids` 三种取值：

| 取值 | 执行范围 | 依赖字段没盖全时 |
|---|---|---|
| 不传 / `null` | 该类型全部启用规则 | 静默跳过，不出现在 `results` 里 |
| `[]` | 不执行任何规则 | — |
| `["a", "b"]` | 只跑点名的规则 | 产出 `success=false` 结果（`reason` 列出缺失字段），计入 `total` / `failed` |

```jsonc
{
  "mode": "sync",
  "items": [
    { "type_id": "financial_report", "biz_id": "doc-001",
      "field_values": { "total_assets": "1000" } },
    // 只跑指定的两条规则
    { "type_id": "financial_report", "biz_id": "doc-002",
      "field_values": { "total_assets": "1000" },
      "rule_ids": ["assets_positive", "assets_ratio"] }
  ]
}
```

`source=file`：不传 `field_values`，字段值取自该文件已落库的提取结果，并把结果落库。

```jsonc
{
  "mode": "sync",
  "source": "file",
  "persist": true,
  "items": [
    { "biz_id": "doc-001", "file_id": "3f2a...",
      "rule_ids": ["profit_margin"] }
  ]
}
```

**响应体**

<!-- AUTOGEN:response POST /analysis/run status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| total_items | integer | 否 | 本批业务输入总数，等于请求 `items` 长度，也等于响应 `items` 长度。 |
| items | array[AnalysisRunItemResult] | 是 | 逐业务输入的执行汇总列表。响应 `items[i]` 对应请求 `items[i]`，不是逐规则列表；逐规则列表位于各元素的 `results[]`。 |
<!-- /AUTOGEN:response -->

`items[]`（`AnalysisRunItemResult`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| item_index | integer | 与请求 `items` 的零基下标一致（并发执行但响应顺序保持） |
| biz_id | string | 请求中的业务 ID 原样回传，用于关联输入和结果 |
| type_id | string | 文档类型 |
| total | integer | 当前业务对象实际执行的规则数，等于 `results` 长度 |
| succeeded | integer | `results[]` 中 `success=true` 的数量 |
| failed | integer | `results[]` 中 `success=false` 的数量；`succeeded + failed = total` |
| results | array[AnalysisRunRuleResult] | 当前业务对象的逐规则结果；一条规则也仍是数组，无规则执行时为 `[]` |
| unknown_rule_ids | array[string] | 点名了但该类型下不存在的 rule_id；不点名时恒为空。显式点名的禁用规则仍会执行 |
| error | string \| null | `source=file` 的 item 级错误（文件不存在 / `type_id` 与文件不一致 / 该文件无提取结果）；正常为 `null`。此时 `total` 为 0、`results` 为空，同批其它 item 不受影响 |

`results[]`（`AnalysisRunRuleResult`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| rule_id / rule_name | string | 本条结果对应的规则 ID / 名称 |
| rule_type | string | `judge` / `calc` / `custom` |
| result | string | 规则结果；执行失败时通常为空字符串 |
| reason | string | 规则理由或执行失败原因 |
| input_values | object | 本条规则实际使用的依赖字段值 `{field_id: value}` |
| source_refs | object \| null | 依赖字段溯源；`source=values` 通常为 `null`，`source=file` 可带提取溯源 |
| success | boolean | 本条规则是否执行成功，不等同于 judge 规则的 true/false 业务结论 |
| index | integer | 本条规则在当前业务对象中的执行序号，从 1 开始 |
| total | integer | 当前业务对象实际执行的规则总数，与外层 item 的 `total` 相同 |

**两条业务输入、每条执行两条规则的同步响应示例**

```json
{
  "code": 200,
  "message": "逻辑分析完成",
  "data": {
    "total_items": 2,
    "items": [
      {
        "item_index": 0,
        "biz_id": "doc-001",
        "type_id": "financial_report",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "results": [
          {
            "rule_id": "profit_positive",
            "rule_name": "是否盈利",
            "rule_type": "judge",
            "result": "true",
            "reason": "净利润大于 0。",
            "input_values": {"net_profit": "5000000"},
            "source_refs": null,
            "success": true,
            "index": 1,
            "total": 2
          },
          {
            "rule_id": "profit_margin",
            "rule_name": "净利率",
            "rule_type": "calc",
            "result": "0.0333333333",
            "reason": "计算成功",
            "input_values": {"net_profit": "5000000", "revenue": "150000000"},
            "source_refs": null,
            "success": true,
            "index": 2,
            "total": 2
          }
        ],
        "unknown_rule_ids": [],
        "error": null
      },
      {
        "item_index": 1,
        "biz_id": "doc-002",
        "type_id": "financial_report",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "results": [
          {
            "rule_id": "profit_positive",
            "rule_name": "是否盈利",
            "rule_type": "judge",
            "result": "false",
            "reason": "净利润小于 0。",
            "input_values": {"net_profit": "-100000"},
            "source_refs": null,
            "success": true,
            "index": 1,
            "total": 2
          },
          {
            "rule_id": "profit_margin",
            "rule_name": "净利率",
            "rule_type": "calc",
            "result": "-0.0125",
            "reason": "计算成功",
            "input_values": {"net_profit": "-100000", "revenue": "8000000"},
            "source_refs": null,
            "success": true,
            "index": 2,
            "total": 2
          }
        ],
        "unknown_rule_ids": [],
        "error": null
      }
    ]
  }
}
```

三种模式取得最终结果的位置不同，但最终数据结构相同：

| mode | 最终结果位置 |
|---|---|
| `sync` | HTTP 响应的 `data` |
| `async` | 回调 `event=task_done` 的 `data` |
| `stream` | SSE `task_done` 事件负载的 `data` |

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | sync 完成 / async 已受理 | ResponseWrapper |
| 422 | `async` 模式缺 `callback_url` / 校验失败 | Pydantic 错误体 |
| 422 | `source=file` 缺 `file_id`、`source=file` 传了 `field_values`、`source=values` 传了 `file_id`、`persist=true` 但 `source≠file` | Pydantic 错误体 |

> 校验分层：能从请求体判断的问题返回 **422**；需查库才知道的问题（文件不存在 / `type_id` 不一致 / 无提取结果）记在 item 级 `error` 字段并返回 **200**，不让一个坏 item 拖垮整批。

> 显式点名时无视 `enabled` 开关；点名了该类型下不存在的 rule_id **不报错**，收进 `unknown_rule_ids` 回传，需调用方自行检查。items 间并发，单 item 内按 `priority, rule_id` 顺序执行。`async` 用 `task_id` 通过 `callback_url` 推送 `rule_done` / `task_done` / `task_failed`（见 [callbacks.md](callbacks.md)），`stream` 走 SSE（见 [sse.md](sse.md)）。
