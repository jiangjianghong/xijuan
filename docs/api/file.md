# 文件处理接口 /file

> 对应服务版本 0.3.0

上传 PDF、驱动六阶段管线、查询进度与各阶段结果。`file_id` 由 `(type_id, file_name, 时间戳, 随机)` 取 SHA256[:32]，**每次上传都是新 ID、不去重**；失败请用 retry，不要重新上传。

## 提交文件解析

上传文件并启动 6 阶段管线。`mode` = `async`（默认，立即返回）/ `sync`（阻塞至完成）/ `stream`（SSE）。

- 方法路径：`POST /file/parse`
- 认证：无（内网部署）
- Content-Type：`multipart/form-data`
- 幂等/并发：每次上传生成新 `file_id`，不去重；PDF 同步落 `uploads/{file_id}.pdf`（供 VL）

**查询参数**

<!-- AUTOGEN:query-params POST /file/parse -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| mode | string | 否 | async | 处理模式：`async`（默认，后台任务立即返回）/ `sync`（阻塞至完成）/ `stream`（SSE 流）。其它值按 `sync` 处理。（可选: async / sync / stream） |
| type_id | string | 否 | default | 归属文档类型，默认 `default`；决定使用哪套字段/规则配置。 |
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时 2.5s，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
<!-- /AUTOGEN:query-params -->

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file | file | 是 | PDF 文件（受 `mineru.max_file_size` 限制，默认 100MB） |

**请求示例（curl）**

```bash
curl -X POST "http://localhost:5019/file/parse?type_id=default&mode=async" \
  -F "file=@report.pdf"
```

**响应体**

<!-- AUTOGEN:response POST /file/parse status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 新建文件 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已受理（async）/ 完成（sync） | ResponseWrapper |
| 400 | 文件大小超限 | ResponseWrapper（`code:400`） |

> `callback_url` 在 async/sync 生效（每阶段 + 每条 field_done/rule_done + stage_done 都 POST，见 [callbacks.md](callbacks.md)）；stream 忽略它，走 [SSE](sse.md)。`mode` 非法值按 sync 处理。

## 分页查询文件列表

按 `create_time DESC` 分页。`status`（即 `progress`）与 `type_id` 精确匹配，空串不过滤。

- 方法路径：`GET /file/list`
- 认证：无（内网部署）

**查询参数**

<!-- AUTOGEN:query-params GET /file/list -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| page | integer | 否 | 1 | 页码，从 1 开始（默认 1）。 |
| page_size | integer | 否 | 20 | 每页条数（默认 20）。 |
| status | string | 否 |  | 按 `progress` 精确过滤：`parsing` / `tableing` / `chunking` / `embedding` / `extracting` / `analyzing` / `complete` 及对应 `*_failed`；空串不过滤。 |
| type_id | string | 否 |  | 按文档类型精确过滤；空串返回全部类型。 |
<!-- /AUTOGEN:query-params -->

**响应体**

<!-- AUTOGEN:response GET /file/list status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| items | array[FileListItem] | 否 |  |
| total | integer | 否 |  |
| page | integer | 否 |  |
| page_size | integer | 否 |  |
| total_pages | integer | 否 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 处理中文件队列

返回所有「处理中」（非 complete、非 `*_failed`）的文件，前端处理队列的唯一数据源（JOIN 出 `type_name` / `project_id`）。

- 方法路径：`GET /file/processing`
- 认证：无（内网部署）

**响应体**

<!-- AUTOGEN:response GET /file/processing status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| file_name | string | 否 |  |
| progress | string | 否 |  |
| type_id | string | 是 |  |
| type_name | string | 是 |  |
| project_id | string | 是 |  |
| create_time | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 文件片段上下文查询

按请求体 `file_id` + `query`（关键词或 Markdown 文本片段）在整篇 Markdown 精确查找，返回命中上下文窗口、页码，并可选返回全部分块。**`file_id` 在请求体，不在 URL。**

- 方法路径：`POST /file/context_query`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /file/context_query -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file_id | string | 是 | — |  |
| query | string | 是 | — |  |
| query_type | string | 否 | keyword |  |
| context_before | integer | 否 | 200 |  |
| context_after | integer | 否 | 200 |  |
| case_sensitive | boolean | 否 | False |  |
| include_all_chunks | boolean | 否 | True |  |
<!-- /AUTOGEN:request-body -->

```jsonc
{ "file_id": "a1b2...", "query": "注册资本", "context_before": 50, "context_after": 200, "include_all_chunks": true }
```

**响应体**

<!-- AUTOGEN:response POST /file/context_query status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| query | string | 否 |  |
| query_type | string | 否 |  |
| matched | boolean | 否 |  |
| match_count | integer | 否 |  |
| matches | array[FileContextMatchItem] | 是 |  |
| chunks | array[FileContextChunkItem] | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（未命中 `matched:false`） | ResponseWrapper |
| 404 | 文件内容不存在 / 未解析完成 | ResponseWrapper |

> `matches[].bboxes` 为块级 PDF 框，用于高亮定位；结构详见 [source-refs](../guides/source-refs.md)。

## 批量删除文件

删除所有 `file_id` 关联的 MySQL 记录、Milvus 向量、`uploads/{file_id}.pdf`。不存在的 ID 进 `failed_ids`。

- 方法路径：`DELETE /file/batch`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body DELETE /file/batch -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| file_ids | array[string] | 是 | — | 要删除的文件 ID 列表（不存在的会进入响应 `failed_ids`）。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response DELETE /file/batch status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| deleted_count | integer | 否 |  |
| failed_ids | array[string] | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 查询文件处理进度

返回当前 `progress` 与最近 `error`。每阶段起止时间戳请走 `/detail`。

- 方法路径：`GET /file/{file_id}/status`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/status -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/status status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| file_name | string | 否 |  |
| file_size | integer | 否 |  |
| progress | string | 否 |  |
| type_id | string | 是 |  |
| error | string | 是 |  |
| create_time | string | 是 |  |
| updated_at | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 404 | 文件不存在 | ResponseWrapper |

## 文件完整详情

在 `/status` 基础上额外返回六阶段全套起止时间戳，可算每阶段耗时。

- 方法路径：`GET /file/{file_id}/detail`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/detail -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/detail status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| file_name | string | 否 |  |
| file_size | integer | 否 |  |
| progress | string | 否 |  |
| type_id | string | 是 |  |
| error | string | 是 |  |
| create_time | string | 是 |  |
| updated_at | string | 是 |  |
| start_parsing_time | string | 是 |  |
| end_parsing_time | string | 是 |  |
| start_tableing_time | string | 是 |  |
| end_tableing_time | string | 是 |  |
| start_chunking_time | string | 是 |  |
| end_chunking_time | string | 是 |  |
| start_embedding_time | string | 是 |  |
| end_embedding_time | string | 是 |  |
| start_extracting_time | string | 是 |  |
| end_extracting_time | string | 是 |  |
| start_analyzing_time | string | 是 |  |
| end_analyzing_time | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 404 | 文件不存在 | ResponseWrapper |

## 删除文件

级联删除 `files` / `file_content` / `file_table` / `file_chunk` / 结果（MySQL 立即提交），Milvus 与 PDF 后台清理。

- 方法路径：`DELETE /file/{file_id}`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params DELETE /file/{file_id} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已删除 | ResponseWrapper |
| 404 | 文件不存在 | ResponseWrapper |

## 从指定阶段重试

清掉指定阶段及下游数据后从该阶段重跑。有效 `stage`：`tableing` / `chunking` / `embedding` / `extracting` / `analyzing`（兼容旧别名 `table_name_validating`→`tableing`）。

- 方法路径：`POST /file/{file_id}/retry/{stage}`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params POST /file/{file_id}/retry/{stage} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
| stage | string | 是 | 重试起点阶段：`tableing` / `chunking` / `embedding` / `extracting` / `analyzing`（兼容旧别名 `table_name_validating` → `tableing`）。该阶段及下游数据会被清理后重跑。 |
<!-- /AUTOGEN:path-params -->

**查询参数**

<!-- AUTOGEN:query-params POST /file/{file_id}/retry/{stage} -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| mode | string | 否 | async | 处理模式：`async`（默认）/ `sync` / `stream`。（可选: async / sync / stream） |
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时 2.5s，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
<!-- /AUTOGEN:query-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已受理 | ResponseWrapper |
| 400 | 无效阶段名 | ResponseWrapper |
| 404 | 文件不存在 | ResponseWrapper |

> `mode`（async/sync/stream）与 `callback_url` 语义同 `/file/parse`；stream 事件序列见 [sse.md](sse.md)。

## 快捷重试：字段提取

等价于 `retry/{stage}` 中 `stage=extracting`，内部转发。

- 方法路径：`POST /file/{file_id}/retry/extracting`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params POST /file/{file_id}/retry/extracting -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**查询参数**

<!-- AUTOGEN:query-params POST /file/{file_id}/retry/extracting -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| mode | string | 否 | async | 处理模式：`async`（默认）/ `sync` / `stream`。（可选: async / sync / stream） |
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时 2.5s，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
<!-- /AUTOGEN:query-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已受理 | ResponseWrapper |
| 404 | 文件不存在 | ResponseWrapper |

## 快捷重试：逻辑分析

等价于 `retry/{stage}` 中 `stage=analyzing`，内部转发。

- 方法路径：`POST /file/{file_id}/retry/analyzing`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params POST /file/{file_id}/retry/analyzing -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**查询参数**

<!-- AUTOGEN:query-params POST /file/{file_id}/retry/analyzing -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| mode | string | 否 | async | 处理模式：`async`（默认）/ `sync` / `stream`。（可选: async / sync / stream） |
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时 2.5s，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
<!-- /AUTOGEN:query-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已受理 | ResponseWrapper |
| 404 | 文件不存在 | ResponseWrapper |

## 重算页码映射

用已落库的 md + middle_json 重建 `page_mapping` 写回，供存量文件免重传刷新逐页锚点 / bbox。

- 方法路径：`POST /file/{file_id}/recompute_page_mapping`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params POST /file/{file_id}/recompute_page_mapping -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 404 | 文件内容不存在 | ResponseWrapper |

## 文件表格列表

返回 `file_table` 按 `table_index` 升序的全部表格（`table_name` 由 tableing 阶段 LLM 识别）。

- 方法路径：`GET /file/{file_id}/tables`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/tables -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/tables status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| table_index | integer | 否 |  |
| total_table | integer | 否 |  |
| table_name | string | 否 |  |
| table_content | string | 否 |  |
| page_num | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无表格 `[]`，非 404） | ResponseWrapper |

## 文件分块列表

返回 `file_chunk` 按 `chunk_index` 升序的全部分块（表格作为独立 chunk 不拆分）。

- 方法路径：`GET /file/{file_id}/chunks`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/chunks -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/chunks status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| chunk_id | string | 否 |  |
| chunk_index | integer | 否 |  |
| total_chunks | integer | 否 |  |
| chunk_content | string | 否 |  |
| page_num | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无分块 `[]`） | ResponseWrapper |

## 文件章节大纲

正则解析 Markdown 章节标题，与 `search_type=section` 同一套切片口径。

- 方法路径：`GET /file/{file_id}/outline`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/outline -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/outline status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| index | integer | 是 | 序号 |
| number | string | 是 | 章节号 |
| title | string | 是 | 标题 |
| content | string | 是 | 章节切片正文 |
| start_pos | integer | 是 | 起始偏移 |
| end_pos | integer | 是 | 结束偏移 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无内容 `[]`） | ResponseWrapper |

## 按页返回 Markdown 内容

基于 `page_mapping` 把整篇 Markdown 逐页切分，按页码升序返回。

- 方法路径：`GET /file/{file_id}/content`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/content -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/content status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| page_num | integer | 是 | 页码 |
| content | string | 是 | 该页 markdown |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无 `page_mapping` 返 `[]`） | ResponseWrapper |

## 字段提取结果

返回 `extraction_result` 全表行（JOIN 字段名）。`source_refs` 含检索原文 / bbox / 模型自报页码等，结构详见 [source-refs](../guides/source-refs.md)。

- 方法路径：`GET /file/{file_id}/extraction`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/extraction -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/extraction status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| field_id | string | 否 |  |
| field_name | string | 是 |  |
| extracted_value | string | 否 |  |
| reason | string | 是 |  |
| source_refs | object | 是 | 结构详见 [source_refs](../guides/source-refs.md) |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 逻辑分析结果

返回 `analysis_result` 全表行（JOIN 规则名，含 `input_values`）。judge 启用网络搜索时 `source_refs` 含 `_web_search`。

- 方法路径：`GET /file/{file_id}/analysis`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/analysis -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /file/{file_id}/analysis status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| file_id | string | 否 |  |
| rule_id | string | 否 |  |
| rule_name | string | 是 |  |
| result_value | string | 否 |  |
| input_values | object | 是 | 结构详见 [input_values](../reference/data-model.md#analysis_result) |
| reason | string | 是 |  |
| source_refs | object | 是 | 结构详见 [source_refs](../guides/source-refs.md) |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 下载原始 PDF

下发 `uploads/{file_id}.pdf` 原始字节，供前端定位预览。

- 方法路径：`GET /file/{file_id}/pdf`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /file/{file_id}/pdf -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file_id | string | 是 | 目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。 |
<!-- /AUTOGEN:path-params -->

响应为二进制 `application/pdf`（Content-Disposition inline）。

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | PDF 字节 | application/pdf |
| 404 | 无落盘 PDF（历史文件 / 已被保留策略清理） | `{"detail": "..."}` |
