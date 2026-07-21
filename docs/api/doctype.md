# 文档类型接口 /doctype

> 对应服务版本 0.3.0

文档类型（`type_id`）隔离不同格式文件的字段 / 规则配置。类型还有**血缘**（`is_template` + `parent_type_id`）与**项目**（`project_id`）两个维度。配置不跨类型共享，共享靠显式复制 / 导入。

## 列出 / 搜索文档类型

列出文档类型，每项附 `file_count` / `field_count` / `rule_count` 计数。

- 方法路径：`GET /doctype/list`
- 认证：无（内网部署）

**查询参数**

<!-- AUTOGEN:query-params GET /doctype/list -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| q | string | 否 | — | 模糊搜索关键词，对 `type_id` / `type_name` 做 `LIKE %q%`；省略不过滤。 |
| scope | string | 否 | all | 范围过滤：`all` 全部 / `template` 模板（含默认类型）/ `copy` 副本（既非模板也非默认）。（可选: all / template / copy） |
| project_id | string | 否 | — | 项目过滤：具体 `project_id` 只返该项目成员；`__ungrouped__` 只返未分组（含默认类型）；省略不过滤。 |
| page | integer | 否 | — | 页码（从 1 开始）。**与 `page_size` 同时传入才启用分页**，返回 `{items, total}`；否则返回数组。 |
| page_size | integer | 否 | — | 每页条数（1–500）。**与 `page` 同时传入才启用分页**。 |
| sort | string | 否 | created_at | 排序字段：`created_at`（降序，默认）或 `type_name`（升序）。默认类型恒置顶，不受影响。（可选: created_at / type_name） |
<!-- /AUTOGEN:query-params -->

**响应体**

<!-- AUTOGEN:response GET /doctype/list status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| type_id | string | 否 | 类型 ID |
| type_name | string | 否 | 类型名 |
| description | string | 是 | 描述（可空） |
| max_parse_pages | integer | 是 | 最大解析页数（可空） |
| enable_embedding | integer | 是 | 是否启用向量化 |
| is_default | integer | 是 | 是否默认类型 |
| enabled | integer | 是 | 是否启用 |
| is_template | integer | 是 | 是否模板 |
| parent_type_id | string | 是 | 复制来源类型 ID（血缘，可空） |
| project_id | string | 是 | 所属项目 ID（可空） |
| project_name | string | 是 | 所属项目名（可空） |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

> **返回形态切换**：传齐 `page` + `page_size` → `data = {items, total}`（分页）；否则 → `data = [...]`（数组，向后兼容）。默认类型恒置顶。

## 新增/更新文档类型（upsert）

按 `type_id` upsert。新建时 `is_default` 固定 0；更新时只改 `type_name` / `description` / `enabled`，保留 `is_default`。`is_template` / `parent_type_id` 不经本接口设置。

- 方法路径：`POST /doctype`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /doctype -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_id | string | 是 | — | 类型 ID，匹配 `^[a-zA-Z0-9_-]+$`（最长 64）；作为 upsert 主键。 |
| type_name | string | 是 | — | 类型显示名（最长 200）。 |
| description | string | 否 | — | 类型描述（可空）。 |
| max_parse_pages | integer | 否 | — |  |
| enable_embedding | integer | 否 | 1 |  |
| enabled | integer | 否 | 1 | 是否启用，1 启用 / 0 停用。 |
| project_id | string | 否 | — | 归属项目 ID；**仅新建时生效**（更新已存在类型时忽略，项目归属由 `batch_assign_project` 管理）。`null`=未分组。 |
<!-- /AUTOGEN:request-body -->

```jsonc
{ "type_id": "financial_report", "type_name": "财务报告", "description": "上市公司年报", "enabled": 1 }
```

**请求示例（curl）**

```bash
curl -X POST http://localhost:5019/doctype -H "Content-Type: application/json" \
  -d '{"type_id":"financial_report","type_name":"财务报告"}'
```

**响应体**

<!-- AUTOGEN:response POST /doctype status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 新建/更新的类型 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已创建 / 已更新 | ResponseWrapper |
| 422 | `type_id` 不匹配 `^[a-zA-Z0-9_-]+$` 等 | Pydantic 错误体 |

## 更新文档类型（可改 type_id）

更新基础配置（`type_name` / `description` / `max_parse_pages` / `enable_embedding` / `enabled`）。`project_id` 更新时忽略。请求体 `type_id` 与路径不同即**改名并级联**（把该类型下 `files` / `extraction_field` / `analysis_rule` 的 `type_id`、子类型的 `parent_type_id` 全部改到新值）。

- 方法路径：`PUT /doctype/{type_id}`
- 认证：无（内网部署）
- Content-Type：`application/json`

**路径参数**

<!-- AUTOGEN:path-params PUT /doctype/{type_id} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**请求体**

<!-- AUTOGEN:request-body PUT /doctype/{type_id} -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_id | string | 是 | — | 类型 ID，匹配 `^[a-zA-Z0-9_-]+$`（最长 64）；作为 upsert 主键。 |
| type_name | string | 是 | — | 类型显示名（最长 200）。 |
| description | string | 否 | — | 类型描述（可空）。 |
| max_parse_pages | integer | 否 | — |  |
| enable_embedding | integer | 否 | 1 |  |
| enabled | integer | 否 | 1 | 是否启用，1 启用 / 0 停用。 |
| project_id | string | 否 | — | 归属项目 ID；**仅新建时生效**（更新已存在类型时忽略，项目归属由 `batch_assign_project` 管理）。`null`=未分组。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response PUT /doctype/{type_id} status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| old_type_id | string | 是 | 原类型 ID |
| type_id | string | 是 | 新类型 ID |
| renamed | boolean | 是 | 是否发生改名 |
| updated_files | integer | 是 | 改名级联的文件数 |
| updated_fields | integer | 是 | 改名级联的字段数 |
| updated_rules | integer | 是 | 改名级联的规则数 |
| updated_children | integer | 是 | 改名级联的子类型数 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | 原 type_id 为空 / 默认类型改名 | ResponseWrapper |
| 404 | 类型不存在 | ResponseWrapper |
| 409 | 新 type_id 已存在 | ResponseWrapper |

## 删除文档类型（单个）

**默认类型不可删**。非默认类型有关联数据时：不传 `force` → 409 + 数量提示；`force=true` → 级联删除文件、`file_content/table/chunk`、结果、Milvus 向量、PDF、字段、规则。

- 方法路径：`DELETE /doctype/{type_id}`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params DELETE /doctype/{type_id} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**查询参数**

<!-- AUTOGEN:query-params DELETE /doctype/{type_id} -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| force | boolean | 否 | False | 为 `true` 时级联删除该类型下所有文件、配置、Milvus 向量与 PDF；为 `false`（默认）且有关联数据则返回 409。 |
<!-- /AUTOGEN:query-params -->

**响应体**

<!-- AUTOGEN:response DELETE /doctype/{type_id} status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 被删类型 ID |
| deleted_files | integer | 是 | 级联删除文件数 |
| deleted_fields | integer | 是 | 级联删除字段数 |
| deleted_rules | integer | 是 | 级联删除规则数 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 已删除 | ResponseWrapper |
| 400 | 默认类型 | ResponseWrapper |
| 404 | 类型不存在 | ResponseWrapper |
| 409 | 有数据未 `force` | ResponseWrapper |

## 批量删除文档类型

对 `type_ids` 逐条复用单删逻辑，**单条失败不中断**，最后一次性提交。

- 方法路径：`POST /doctype/batch_delete`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /doctype/batch_delete -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_ids | array[string] | 是 | — | 要删除的类型 ID 列表（默认类型 / 不存在 / 有数据未 force 的会被跳过）。 |
| force | boolean | 否 | False | 是否级联删除类型下的文件与配置；默认 `false`。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response POST /doctype/batch_delete status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| results | array[object] | 是 | 逐条删除结果 |
| deleted | integer | 是 | 成功删除条数 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 完成（逐条 `ok` / `reason`） | ResponseWrapper |

## 同实例跨类型复制配置

把源类型字段 + 规则复制到当前 `type_id`，生成全新 ID 的**独立副本**。规则 `depend_fields` 按源 `field_id` 重映射到新副本 ID；未一起复制的依赖记入 `missing_dependencies`。

- 方法路径：`POST /doctype/{type_id}/copy_from`
- 认证：无（内网部署）
- Content-Type：`application/json`

**路径参数**

<!-- AUTOGEN:path-params POST /doctype/{type_id}/copy_from -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**请求体**

<!-- AUTOGEN:request-body POST /doctype/{type_id}/copy_from -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| source_type_id | string | 是 | — | 源类型 ID（必填，须 ≠ 目标 `type_id`）。 |
| field_ids | array[string] | 否 | — | 要复制的字段 ID 列表；不传或 `null` 表示全部字段，空数组 `[]` 表示不复制字段。 |
| rule_ids | array[string] | 否 | — | 要复制的规则 ID 列表；不传或 `null` 表示全部规则，空数组 `[]` 表示不复制规则。 |
| on_conflict | string | 否 | rename | 目标类型已有同源 ID 副本时的策略：`rename`（默认，生成下一个 `_000N` 副本 ID）或 `skip`（跳过）。字段名/规则名保持不变。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response POST /doctype/{type_id}/copy_from status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| copied_fields | integer | 是 | 复制字段数 |
| skipped_fields | integer | 是 | 跳过字段数 |
| copied_rules | integer | 是 | 复制规则数 |
| skipped_rules | integer | 是 | 跳过规则数 |
| missing_dependencies | array[string] | 是 | 丢失的依赖（格式 规则名::源field_id） |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | 源 = 目标 | ResponseWrapper |
| 404 | 源或目标不存在 | ResponseWrapper |

## 导出配置为 JSON 载荷

把目标类型字段 + 规则序列化为可跨实例迁移的 JSON（规则依赖以 `field_name` 列表表达）。前端「只读查看配置」也复用本接口。

- 方法路径：`GET /doctype/{type_id}/export`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params GET /doctype/{type_id}/export -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response GET /doctype/{type_id}/export status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| type_id | string | 否 | 源类型 ID（导入时若未指定 `target_type_id` 则用它）。 |
| type_name | string | 否 | 源类型名（自动创建目标类型时作为名称）。 |
| description | string | 是 | 源类型描述。 |
| max_parse_pages | integer | 是 |  |
| enable_embedding | integer | 是 |  |
| version | integer | 是 | 载荷版本号，当前为 1。 |
| fields | array[ExportFieldItem] | 是 | 字段项列表（`ExportFieldItem`）。 |
| rules | array[ExportRuleItem] | 是 | 规则项列表（`ExportRuleItem`）。 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 404 | 类型不存在 | ResponseWrapper |

## 从 JSON 载荷导入配置

把 export 载荷导入目标类型。字段始终生成**新 `field_id`**；规则按 `depend_field_names` 在目标类型按字段名重映射。

- 方法路径：`POST /doctype/import`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /doctype/import -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| payload | ExportPayload | 是 | — | 导出载荷（`ExportPayload`）。 |
| target_type_id | string | 否 | — | 目标类型 ID；为空时用 `payload.type_id`。 |
| create_type_if_missing | boolean | 否 | True | 目标类型不存在时是否自动创建（默认 `true`）；为 `false` 且不存在则 404。 |
| on_conflict | string | 否 | rename | 同名策略：`rename`（默认）或 `skip`。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response POST /doctype/import status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| target_type_id | string | 否 | 目标类型 ID |
| created_type | boolean | 是 | 是否自动创建了类型 |
| copied_fields | integer | 是 | 导入字段数 |
| skipped_fields | integer | 是 | 跳过字段数 |
| copied_rules | integer | 是 | 导入规则数 |
| skipped_rules | integer | 是 | 跳过规则数 |
| missing_dependencies | array[string] | 是 | 丢失的依赖 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | `target_type_id` 与 `payload.type_id` 均空 | ResponseWrapper |
| 404 | 目标类型不存在且 `create_type_if_missing=false` | ResponseWrapper |

## 标记为模板

把副本 / 普通类型标记为模板（`is_template=1`），保留 `parent_type_id` 血缘。

- 方法路径：`POST /doctype/{type_id}/promote`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params POST /doctype/{type_id}/promote -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response POST /doctype/{type_id}/promote status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 类型 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | 默认类型无需标记 | ResponseWrapper |
| 404 | 类型不存在 | ResponseWrapper |

## 取消模板标记

取消模板标记（`is_template=0`），不影响 `parent_type_id`。

- 方法路径：`POST /doctype/{type_id}/demote`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params POST /doctype/{type_id}/demote -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response POST /doctype/{type_id}/demote status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| type_id | string | 是 | 类型 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 400 | 默认类型不可操作 | ResponseWrapper |
| 404 | 类型不存在 | ResponseWrapper |

## 列出项目

列出所有项目，每项附 `type_count`（该项目下类型数）。项目对「模板 + 其血缘下游」分类。

- 方法路径：`GET /doctype/projects`
- 认证：无（内网部署）

**响应体**

<!-- AUTOGEN:response GET /doctype/projects status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| project_id | string | 否 | 项目 ID |
| project_name | string | 否 | 项目名 |
| description | string | 是 | 项目描述（可空） |
| type_count | integer | 是 | 该项目下类型数 |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |

## 新增/改名项目（upsert）

按 `project_id` upsert。

- 方法路径：`POST /doctype/projects`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /doctype/projects -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| project_id | string | 是 | — | 项目 ID，匹配 `^[a-zA-Z0-9_-]+$`（最长 64）；作为 upsert 主键。 |
| project_name | string | 是 | — | 项目显示名（最长 200）。 |
| description | string | 否 | — | 项目描述（可空）。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response POST /doctype/projects status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| project_id | string | 是 | 项目 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 422 | `project_id` 不合法 | Pydantic 错误体 |

## 删除项目

先把成员类型的 `project_id` 置空（变未分组），再删项目本身——**不删除任何类型**。

- 方法路径：`DELETE /doctype/projects/{project_id}`
- 认证：无（内网部署）

**路径参数**

<!-- AUTOGEN:path-params DELETE /doctype/projects/{project_id} -->
| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| project_id | string | 是 | 项目 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。 |
<!-- /AUTOGEN:path-params -->

**响应体**

<!-- AUTOGEN:response DELETE /doctype/projects/{project_id} status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| project_id | string | 是 | 被删项目 ID |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功 | ResponseWrapper |
| 404 | 项目不存在 | ResponseWrapper |

## 批量归类到项目（级联血缘）

把 `type_ids` 归入 `project_id`（`null` 表示移出）。**级联血缘**：每个类型的所有 `parent_type_id` 传递后代一并归入同一项目；默认类型跳过。

- 方法路径：`POST /doctype/batch_assign_project`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /doctype/batch_assign_project -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| type_ids | array[string] | 是 | — | 要归类的类型 ID 列表（各自的血缘后代会一并带入同一项目）。 |
| project_id | string | 否 | — | 目标项目 ID；`null` 表示移出（变未分组）。 |
<!-- /AUTOGEN:request-body -->

**响应体**

<!-- AUTOGEN:response POST /doctype/batch_assign_project status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| requested | integer | 是 | 入参类型数 |
| affected | integer | 是 | 实际写入数（含级联） |
| project_id | string | 是 | 目标项目 ID（null=移出） |
<!-- /AUTOGEN:response -->

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（`affected` 含级联带入数） | ResponseWrapper |
| 404 | `project_id` 非空但项目不存在 | ResponseWrapper |
