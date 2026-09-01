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
| type_id | string | 否 | default | 归属文档类型，默认 `default`；决定使用哪套字段/规则配置。**必须已存在于 `doc_type`**，否则返回 HTTP 400。 |
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时默认 2.5s，由 `callback.timeout` 配置，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
<!-- /AUTOGEN:query-params -->

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| file | file | 是 | PDF 文件（受 `mineru.max_file_size` 限制，默认 100MB） |
| params | string | 否 | 该类型的入参实参，**JSON 对象字符串**。见下方「入参」小节。 |

**入参（`params`）**

若该 `type_id` 定义了入参（`GET /doctype/{type_id}/params`），可在此传入实参；字段与规则配置里的 `<param>参数标识</param>` 占位符会被替换成对应值。

放在 form 而非 query：参数值可能是一段中文说明而不只是个日期，query 串有网关长度上限，中文值还要 URL 编码。

落库的是**合并后的完整快照**（`默认值 ← 传入值覆盖`）而非仅传入部分，写入 `files.input_params`。因此 `POST /file/{file_id}/retry/{stage}` 会沿用当时的值，无需重传；代价是之后改了参数默认值，retry 不会用新默认值 —— 可复现性优先于时效性。

四类入参错误一律 **HTTP 400 且不建档、不写盘**（校验在 `generate_file_id` 之前）：传参错误是调用方的 bug，放过去的代价是跑完 MinerU 与几十次 LLM 调用之后，几个字段悄悄用空串抽出了错的结果。

```bash
curl -X POST "http://localhost:5019/file/parse?type_id=contract&mode=async" \
  -F "file=@report.pdf" \
  -F 'params={"current_date":"2026-08-31","year":"2025"}'
```

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
| 400 | `type_id` 在 `doc_type` 中不存在 | `{detail:"文档类型不存在: xxx"}`（真正的 HTTP 400） |
| 400 | `params` 不是合法 JSON | `{detail:"params 不是合法 JSON: ..."}` |
| 400 | `params` 顶层不是对象 | `{detail:"params 必须是 JSON 对象"}` |
| 400 | `params` 某个值是对象或数组（占位符只能替换成文本） | `{detail:"params.xxx 必须是标量..."}` |
| 400 | `params` 含该类型未定义的 key | `{detail:"未知入参: xxx；该类型已定义的入参为: ..."}` |
| 400 | 缺少 `required=1` 且无 `default_value` 的入参 | `{detail:"缺少必填入参: xxx"}` |

> `type_id` 只校验存在性，不校验 `enabled`——被禁用的类型仍可上传。校验在建档与写盘之前，拒绝时不产生 `files` 记录与 `uploads/*.pdf`。

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
| items | array[FileListItem] | 否 | 当前页文件列表 |
| total | integer | 否 | 总条数 |
| page | integer | 否 | 当前页码（从 1 起） |
| page_size | integer | 否 | 每页条数 |
| total_pages | integer | 否 | 总页数 |
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
| file_id | string | 否 | 文件唯一 ID |
| file_name | string | 否 | 原始文件名 |
| progress | string | 否 | 处理进度 |
| type_id | string | 是 | 归属文档类型 |
| type_name | string | 是 | 类型名（JOIN doc_type，可空） |
| project_id | string | 是 | 所属项目 ID（可空） |
| create_time | string | 是 | 创建时间 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 全局处理统计

统计页（前端点击左上角「析卷 AI」标题进入）的唯一数据源，一次返回 6 组聚合结果。

**口径为全局**：不接受 `type_id` / `project_id` 过滤，统计库中全部文件，与顶部项目 / 文档类型选择器无关。固定 4 条聚合查询（状态分布、类型×项目、按天趋势、阶段耗时），无 N+1。

两个易误读的口径：

1. `overview.avg_total_seconds` 是 `start_parsing_time` → `end_analyzing_time` 的墙钟时长，**包含阶段之间的排队等待**，因此通常远大于 `stage_durations` 各阶段均值之和。
2. `stage_durations[].samples` 只计该阶段起止时间**双端非空**的文件。重试会把目标阶段及下游时间戳重置为 NULL，这些文件在重跑完成前不计入；只有 end 没有 start 的历史遗留行同样不计入。

`trend` 只返回**有数据的日期**，空白日期需消费方自行补零（前端 `Statistics.fillTrend` 即做此事）。

- 方法路径：`GET /file/stats`
- 认证：无（内网部署）

**查询参数**

<!-- AUTOGEN:query-params GET /file/stats -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| range | StatsRangeEnum | 否 | 30d |  |
<!-- /AUTOGEN:query-params -->

**响应体**

<!-- AUTOGEN:response GET /file/stats status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| overview | StatsOverview | 否 | 概览 KPI |
| status_distribution | array[StatsCountItem] | 是 | 按 progress 的分布（按数量降序） |
| by_project | array[StatsCountItem] | 是 | 按项目的分布（按数量降序，未分组归入 `__ungrouped__`） |
| by_type | array[StatsCountItem] | 是 | 按文档类型的分布（按数量降序） |
| trend | array[StatsTrendItem] | 是 | 近 `trend_days` 天的按天趋势（升序，仅含有数据的日期） |
| stage_durations | array[StatsStageItem] | 是 | 六阶段耗时，按管线执行顺序固定返回 6 项 |
| range | StatsRangeEnum | 是 |  |
| granularity | string | 是 |  |
| start_time | string | 是 |  |
| end_time | string | 是 |  |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（无数据时各数组为空、`overview` 全 0） | ResponseWrapper |

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
| file_id | string | 否 | 文件唯一 ID |
| query | string | 否 | 查询词 |
| query_type | string | 否 | 查询类型（keyword/text_fragment） |
| matched | boolean | 否 | 是否有命中 |
| match_count | integer | 否 | 命中数 |
| matches | array[FileContextMatchItem] | 是 | 命中列表 |
| chunks | array[FileContextChunkItem] | 是 | 全部分块（include_all_chunks=true 时含命中标记） |
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
| deleted_count | integer | 否 | 成功删除数 |
| failed_ids | array[string] | 是 | 删除失败/不存在的 file_id 列表 |
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
| file_id | string | 否 | 文件唯一 ID |
| file_name | string | 否 | 原始文件名 |
| file_size | integer | 否 | 文件字节数 |
| progress | string | 否 | 处理进度（见 progress 状态机） |
| type_id | string | 是 | 归属文档类型 |
| error | string | 是 | 最近失败的错误信息（可空） |
| create_time | string | 是 | 创建时间 |
| updated_at | string | 是 | 最近更新时间 |
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
| file_id | string | 否 | 文件唯一 ID |
| file_name | string | 否 | 原始文件名 |
| file_size | integer | 否 | 文件字节数 |
| progress | string | 否 | 处理进度 |
| type_id | string | 是 | 归属文档类型 |
| input_params | object | 是 |  |
| error | string | 是 | 错误信息（可空） |
| create_time | string | 是 | 创建时间 |
| updated_at | string | 是 | 最近更新时间 |
| start_parsing_time | string | 是 | 解析开始 |
| end_parsing_time | string | 是 | 解析结束 |
| start_tableing_time | string | 是 | 表名校验开始 |
| end_tableing_time | string | 是 | 表名校验结束 |
| start_chunking_time | string | 是 | 分块开始 |
| end_chunking_time | string | 是 | 分块结束 |
| start_embedding_time | string | 是 | 向量化开始 |
| end_embedding_time | string | 是 | 向量化结束 |
| start_extracting_time | string | 是 | 抽取开始 |
| end_extracting_time | string | 是 | 抽取结束 |
| start_analyzing_time | string | 是 | 分析开始 |
| end_analyzing_time | string | 是 | 分析结束 |
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
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时默认 2.5s，由 `callback.timeout` 配置，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
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
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时默认 2.5s，由 `callback.timeout` 配置，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
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
| callback_url | string | 否 | — | 可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时默认 2.5s，由 `callback.timeout` 配置，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。 |
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
| file_id | string | 否 | 文件唯一 ID |
| table_index | integer | 否 | 表序号（从 0） |
| total_table | integer | 否 | 表总数 |
| table_name | string | 否 | 表名（tableing 阶段 LLM 识别，截断 30 字） |
| table_content | string | 否 | 表格 HTML 内容 |
| page_num | string | 是 | 所在页（可能为范围如 3-4，可空） |
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
| file_id | string | 否 | 文件唯一 ID |
| chunk_id | string | 否 | 分块 ID |
| chunk_index | integer | 否 | 分块序号 |
| total_chunks | integer | 否 | 分块总数 |
| chunk_content | string | 否 | 分块正文 |
| page_num | string | 是 | 所在页（可空） |
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
| file_id | string | 否 | 文件唯一 ID |
| field_id | string | 否 | 字段 ID |
| field_name | string | 是 | 字段名（配置删除则 null） |
| extracted_value | string | 否 | 抽取值 |
| reason | string | 是 | 抽取理由（可空） |
| pages | array[integer] | 是 | 模型自报参考页（1-indexed int 数组）；VL / use_llm=0 / 模型未返回时为 [] |
| source_pages | array[integer] | 是 | 可用页码：pages 优先、程序命中页兜底。键恒存在，无命中时为 []。区间已展开，不含 "12-15" 形式 |
| source_refs | object | 是 | 溯源（结构见 source-refs 指南）（结构详见 [source_refs](../guides/source-refs.md)） |
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
| file_id | string | 否 | 文件唯一 ID |
| rule_id | string | 否 | 规则 ID |
| rule_name | string | 是 | 规则名（配置删除则 null） |
| result_value | string | 否 | 分析结果 |
| input_values | object | 是 | 依赖字段取值（结构详见 [input_values](../reference/data-model.md#analysis_result)） |
| reason | string | 是 | 判断/计算理由（可空） |
| source_refs | object | 是 | 溯源（judge 启用网络搜索时含 _web_search）（结构详见 [source_refs](../guides/source-refs.md)） |
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
