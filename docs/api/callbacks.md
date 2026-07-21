# 异步回调契约

> 对应服务版本 0.3.0

当 `POST /file/parse` / `POST /file/{file_id}/retry/{stage}`（`async` 或 `sync` 模式）携带 `callback_url` 时，管线在每阶段开始、每条 `field_done` / `rule_done`、每阶段 `stage_done` 都会向该地址 **POST** 通知。`stream` 模式忽略 `callback_url`，事件改走 [SSE](sse.md)。

> **契约要点**：每次回调超时 **2.5s**；失败仅记 warning、**绝不阻断主流程**。回调载荷**不在 openapi 里**，故本页字段表全部人工维护。老消费者只读 `status` 字段不受影响（新事件靠 `event` 字段区分）。

## 事件模板速览

| status | event | 触发 |
|---|---|---|
| `<stage>` | （无 event） | 每阶段开始各 1 次 |
| `extracting` | `field_done` | 单字段完成（仅 extracting 阶段） |
| `analyzing` | `rule_done` | 单规则完成（仅 analyzing 阶段） |
| `<stage>` | `stage_done` | 每阶段结束各 1 次（携带完整数据） |
| `<stage>_failed` | `stage_failed` | 阶段失败 1 次，替代 stage_done 及后续事件，序列终止 |

## 事件详情

#### 阶段入口

- 触发时机：每个阶段开始
- 通道：回调 POST body

```jsonc
{ "file_id": "a1b2...", "status": "extracting" }
```

#### field_done（单字段完成）

- 触发时机：extracting 阶段每个字段抽取完成
- 通道：回调 POST body

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| field_id | string | 否 | 字段 ID |
| field_name | string | 是 | 字段名（配置删除则 null） |
| value | string | 否 | 抽取值 |
| reason | string | 是 | 抽取理由 |
| source_refs | object | 是 | 溯源，详见 [source-refs](../guides/source-refs.md) |
| success | boolean | 否 | 是否成功 |
| index | integer | 否 | 序号（从 0/1 起） |
| total | integer | 否 | 字段总数 |

```jsonc
{
  "file_id": "a1b2...", "status": "extracting", "event": "field_done",
  "data": { "field_id": "company_name", "field_name": "公司名称",
    "value": "示例公司", "reason": "...", "source_refs": {}, "success": true, "index": 5, "total": 12 }
}
```

#### rule_done（单规则完成）

- 触发时机：analyzing 阶段每条规则完成
- 通道：回调 POST body

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| rule_id | string | 否 | 规则 ID |
| rule_name | string | 是 | 规则名 |
| rule_type | string | 否 | judge / calc |
| result | string | 否 | 结果 |
| reason | string | 是 | 理由 |
| input_values | object | 否 | 依赖字段取值 |
| source_refs | object | 是 | 溯源（judge 网络搜索时含 `_web_search`） |
| success | boolean | 否 | 是否成功 |
| index | integer | 否 | 序号 |
| total | integer | 否 | 规则总数 |

#### stage_done（阶段完整数据）

- 触发时机：每阶段结束
- 通道：回调 POST body；`data` 随阶段不同：

| stage | data |
|---|---|
| parsing | `{content, middle_json, page_mapping}`（完整 markdown，等价 `/file/{id}/content`） |
| tableing | `{total, tables: [{file_id, table_index, total_table, table_name, table_content, start_pos, end_pos, page_num}]}` |
| chunking | `{total, chunks: [{file_id, chunk_id, chunk_index, total_chunks, chunk_content, start_pos, end_pos, page_num}]}` |
| embedding | **不携带 data**（仅完成信号；向量数据过大不下发，需查 Milvus） |
| extracting | `{total, succeeded, failed, results: [field_done.data ...]}` |
| analyzing | `{total, succeeded, failed, results: [rule_done.data ...]}` |

#### stage_failed（阶段失败）

```jsonc
{ "file_id": "a1b2...", "status": "extracting_failed", "event": "stage_failed",
  "data": { "stage": "extracting", "error": "TimeoutError: ..." } }
```

## 完整事件序列（一次成功管线）

```
parsing                    → parsing + stage_done（完整 md）
tableing                   → tableing + stage_done（完整 tables）
chunking                   → chunking + stage_done（完整 chunks）
embedding                  → embedding + stage_done（无 data）
extracting + field_done×N  → extracting + stage_done（完整 results）
analyzing  + rule_done×N   → analyzing  + stage_done（完整 results）
complete
（任一阶段失败 → 该阶段 stage_failed，序列终止，无 complete）
```

## 独立逻辑分析任务回调（POST /analysis/run）

`async` 模式的 `POST /analysis/run` 用 `task_id` 通过 `callback_url` 推送：

- `rule_done` — 单条规则完成（结构同上，附 `item_index` / `biz_id`）
- `task_done` — 全部 item 完成
- `task_failed` — 任务失败

请求立即返回 `{task_id}`；后续结果全部经回调下发。详见 [analysis](analysis.md#独立逻辑分析执行)。
