# 字段提取配置接口 /extraction

> 对应服务版本 0.3.0

管理字段提取配置（table / text / vl 三类源）与调试。详细配置配方（各源类型的 `search_config` / `vl_config` / 占位符规则、端到端范例、错误排查）见 [extraction-config 指南](../guides/extraction-config.md)，本页只做接口参考。

## 列出字段配置

按 `priority` 升序返回字段配置。`type_id` 为空返回全量，非空按精确匹配过滤。

- 方法路径：`GET /extraction/fields`
- 认证：无（内网部署）

**查询参数**

<!-- AUTOGEN:query-params GET /extraction/fields -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_id | string | 否 |  | 按文档类型精确过滤字段配置；空串返回全部。 |
<!-- /AUTOGEN:query-params -->

**响应体**

<!-- AUTOGEN:response GET /extraction/fields status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| field_id | string | 否 |  |
| type_id | string | 是 |  |
| field_name | string | 否 |  |
| source_type | SourceTypeEnum | 否 |  |
| enabled | integer | 是 |  |
| priority | integer | 是 |  |
| use_llm | integer | 是 |  |
| table_name_pattern | string | 是 |  |
| table_match_type | TableMatchTypeEnum | 是 |  |
| table_match_keywords | array[string] | 是 |  |
| table_match_max_results | integer | 是 |  |
| table_system_prompt | string | 是 |  |
| table_extract_prompt | string | 是 |  |
| search_type | SearchTypeEnum | 是 |  |
| search_config | object | 是 | 结构详见 [search_config](../reference/data-model.md#extraction_field) |
| text_system_prompt | string | 是 |  |
| text_extract_prompt | string | 是 |  |
| vl_method | VLMethodEnum | 是 |  |
| vl_config | object | 是 | 结构详见 [vl_config](../reference/data-model.md#extraction_field) |
| vl_system_prompt | string | 是 |  |
| vl_extract_prompt | string | 是 |  |
| created_at | string | 是 |  |
| updated_at | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 新增/更新字段配置（upsert）

按 `field_id`（**全局唯一**）upsert。

- 方法路径：`POST /extraction/fields`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /extraction/fields -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| field_id | string | 是 | — | 字段 ID，匹配 `^[a-zA-Z0-9_]+$`（最长 100），**全局唯一**。 |
| type_id | string | 否 | default | 归属文档类型，默认 `default`。 |
| field_name | string | 是 | — | 字段显示名（最长 200）。 |
| source_type | SourceTypeEnum | 是 | — | 来源类型：`table` / `text` / `vl`（决定下面哪组字段生效）。 |
| enabled | integer | 否 | 1 | 是否启用（1/0）。 |
| priority | integer | 否 | 0 | 执行优先级，数字越小越先（升序）。 |
| use_llm | integer | 否 | 1 |  |
| table_name_pattern | string | 否 | — | [table] 表名匹配模式（配合 `table_match_type`）。 |
| table_match_type | TableMatchTypeEnum | 否 | — | [table] 匹配方式：`exact` / `fuzzy` / `contains` / `llm`。 |
| table_match_keywords | array[string] | 否 | — | [table] 匹配关键词列表。 |
| table_match_max_results | integer | 否 | — | [table] 最多命中表数。 |
| table_system_prompt | string | 否 | — | [table] LLM system prompt（可空）。 |
| table_extract_prompt | string | 否 | — | [table] 抽取 prompt；`source_type=table` 时须含至少一个 `<search_result>标签</search_result>`。 |
| search_type | SearchTypeEnum | 否 | — | [text] 检索方式：`context` / `section` / `rule` / `chunk_db` / `vector_db` / `page`。 |
| search_config | object | 否 | — | [text] 检索配置（自由 JSON，键随 `search_type` 不同）：
- `context`：`keywords`[必] / `context_before`(200) / `context_after`(200) / `max_results`(5) / `sort_order`(asc)
- `section`：`section_pattern` / `section_match_type`|`match_type`(contains) / `threshold`(0.8) / `max_results`(3) / `sort_order`(asc)
- `rule`：`keywords`[必] / `stop_words`(默认中文标点集) / `direction`(forward) / `min_length`(2) / `max_length`(200) / `max_results`(5) / `sort_order`(asc)
- `chunk_db`：`keywords`[必] / `keyword_filter` / `max_results`|`top_k`(10) / `sort_order`(asc)
- `vector_db`：`query_text` / `top_k`(5) / `score_threshold`
- `page`：`page_range`（如 `"1-3"` / `"all"` / `"2"`）/ `max_length`(30000，末尾截断)（结构详见 [search_config](../reference/data-model.md#extraction_field)） |
| text_system_prompt | string | 否 | — | [text] LLM system prompt（可空）。 |
| text_extract_prompt | string | 否 | — | [text] 抽取 prompt；`source_type=text` 时须含至少一个 `<search_result>标签</search_result>`。 |
| vl_method | VLMethodEnum | 否 | — | [vl] 方法：`vl_model` / `vl_progressive` / `vl_locate`；`source_type=vl` 时必填。 |
| vl_config | object | 否 | — | [vl] VL 配置（自由 JSON，键随 `vl_method` 不同）：
- `vl_model`：`page_range`(默认 `"all"`，指定页一次性塞 VL)
- `vl_progressive`：`field_hints`("") / `batch_size`(2) / 可选 `batch_prompt_template`（占位符 `{history}` `{field_hints}` `{page_label}` `{total_pages}`）
- `vl_locate`：`field_hints`("") / `grid_pages`(6) / `grid_cols`(3) / `max_concurrent`(20) / 可选 `locate_prompt_template`（占位符 `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}`）（结构详见 [vl_config](../reference/data-model.md#extraction_field)） |
| vl_system_prompt | string | 否 | — | [vl] LLM system prompt（可空）。 |
| vl_extract_prompt | string | 否 | — | [vl] 抽取 prompt；`source_type=vl` 时必填，且须含 `value` 与 `reason` 关键字（大小写不敏感）。 |
<!-- /AUTOGEN:request-body -->

```jsonc
{
  "field_id": "company_name",
  "type_id": "financial_report",
  "field_name": "公司名称",
  "source_type": "text",
  "search_type": "context",
  "search_config": { "keywords": ["公司名称"], "context_after": 200, "max_results": 3 },
  "text_extract_prompt": "从内容提取公司名称：\n<search_result>命中片段</search_result>\n输出 {value, reason}。"
}
```

**请求示例（curl）**

```bash
curl -X POST http://localhost:5019/extraction/fields \
  -H "Content-Type: application/json" -d @field.json
```

**响应体**

<!-- AUTOGEN:response POST /extraction/fields status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| field_id | string | 是 | 字段 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已创建 / 已更新 | ResponseWrapper（`message` 区分） |
| 409 | `field_id` 已被其它 `type_id` 占用 | ResponseWrapper |
| 422 | 提示词缺 `<search_result>` 占位符 / vl 缺 `value`·`reason` 等校验失败 | Pydantic 错误体 |

> 校验规则详见 [extraction-config 指南](../guides/extraction-config.md)。`use_llm=0` 时放宽提示词必填。

## 删除字段配置

**硬删除**字段配置本身；该字段历史写入的 `extraction_result` **不级联清理**。

- 方法路径：`DELETE /extraction/fields/{field_id}`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params DELETE /extraction/fields/{field_id} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| field_id | string | 是 | 字段配置 ID（全局唯一，匹配 `^[a-zA-Z0-9_]+$`，最长 100）。 |
<!-- /AUTOGEN:path-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已删除 | ResponseWrapper（`data: null`） |
| 404 | 字段不存在 | ResponseWrapper |

## 检查字段 ID 是否存在

只读探测 `field_id` 是否已被占用（保存前查重）。**全局**查存在性，故 `exists=true` 也可能是被其它类型占用。

- 方法路径：`GET /extraction/fields/{field_id}/check`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /extraction/fields/{field_id}/check -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| field_id | string | 是 | 字段配置 ID（全局唯一，匹配 `^[a-zA-Z0-9_]+$`，最长 100）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /extraction/fields/{field_id}/check status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| exists | boolean | 是 | 是否已存在 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 无论是否存在 | ResponseWrapper（`data.exists`） |

## 字段提取调试（同步）

传 `field_id`（用已存配置）或 `config`（临时配置）二选一，均需 `file_id`。返回检索结果、渲染后 prompt、LLM/VL 输出、解析后的值。

- 方法路径：`POST /extraction/test`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /extraction/test -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file_id | string | 是 | — | 目标文件 ID（须已完成 parsing，文本类需 `file_content`，VL 类需 `uploads/{file_id}.pdf`）。 |
| field_id | string | 否 | — | 已保存字段配置 ID；与 `config` 二选一。 |
| config | object | 否 | — | 临时字段配置 dict（结构同 `ExtractionFieldCreate`）；与 `field_id` 二选一。 |
<!-- /AUTOGEN:request-body -->

```jsonc
{ "file_id": "a1b2c3...", "field_id": "company_name" }
```

**响应体**

<!-- AUTOGEN:response POST /extraction/test status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| search_results | array[object] | 是 |  |
| llm_input | string | 是 |  |
| llm_output | string | 是 |  |
| extracted_value | string | 是 |  |
| reason | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | 既未传 `field_id` 也未传 `config` | ResponseWrapper |
| 404 | 字段或文件内容不存在 | ResponseWrapper |
| 500 | 提取异常 | ResponseWrapper |

> `search_results` 形态随 `source_type` / `search_type` 不同；VL 字段的 `llm_output` 即 `extracted_value`（直出 JSON）。

## 字段提取流式调试（SSE）

SSE 分步推送：检索结果 → prompt → LLM/VL 响应 → 最终结果。入参与 `/extraction/test` 相同。

- 方法路径：`POST /extraction/test/stream`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /extraction/test/stream -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file_id | string | 是 | — | 目标文件 ID（须已完成 parsing，文本类需 `file_content`，VL 类需 `uploads/{file_id}.pdf`）。 |
| field_id | string | 否 | — | 已保存字段配置 ID；与 `config` 二选一。 |
| config | object | 否 | — | 临时字段配置 dict（结构同 `ExtractionFieldCreate`）；与 `field_id` 二选一。 |
<!-- /AUTOGEN:request-body -->

响应为 `text/event-stream`，事件清单见 [sse.md](sse.md)。VL 进度事件（`pdf_loaded` / `progressive_batch` / `locate_locate` / `locate_extract`）**仅** SSE 推送。

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | SSE 流 | text/event-stream |
| 400 / 404 | 同 `/extraction/test` | ResponseWrapper |
