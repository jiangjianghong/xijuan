# SSE 流式事件清单

> 对应服务版本 0.3.0

流式端点返回 `text/event-stream`，逐步推送事件（SSE `event:` + `data:` JSON）。`mode=stream` 时忽略 `callback_url`。

## 流式端点

| 端点 | 说明 |
|---|---|
| `POST /file/parse?mode=stream` | 提交解析，逐阶段推送 |
| `POST /file/{file_id}/retry/{stage}?mode=stream` | 从阶段重试，事件序列同上 |
| `POST /extraction/test/stream` | 字段提取调试 |
| `POST /analysis/test/stream` | 逻辑分析调试 |
| `POST /analysis/run`（`mode=stream`） | 独立逻辑分析 |

## 1. 解析 / 重试流（parse / retry）

逐阶段推送，事件序列：

```
parsing → tableing → chunking → embedding
        → extracting (+ field_extracted × N)
        → analyzing  (+ rule_evaluated × N)
        → complete
（失败推 error 事件后终止）
```

- `field_extracted` — 单字段完成，`data` 含 `field_id / field_name / extracted_value / reason / source_refs / current / total`
- `rule_evaluated` — 单规则完成，`data` 含 `rule_id / rule_name / result_value / reason / current / total`

## 2. 字段提取调试流（/extraction/test/stream）

事件：`search_result`（检索结果）→ `prompt`（渲染后提示词）→ `llm_response`（LLM/VL 输出）→ `result` → `done`。

VL 字段另有进度事件：`pdf_loaded` / `progressive_batch` / `locate_locate` / `locate_extract`（**仅** SSE 推送，不走异步回调）。

## 3. 逻辑分析调试流（/analysis/test/stream）

事件：`input_values` → `resolved_expression` →（judge：[`web_search`] → `prompt` → `llm_response`）→ `result` → `done`。calc 类型无 `prompt` / `llm_response` 步骤。`web_search` 事件 `data` 即溯源结构 `{query, results, error?}`。

## 4. 独立逻辑分析流（/analysis/run?mode=stream）

按 item + rule 推送 `rule_done`，最后 `task_done`；失败 `task_failed`。

## ⚠️ 回调事件 vs SSE 事件的字段词汇差异

同一逻辑事件，异步回调通道与 SSE 通道用的字段名**不同**。同时对接两者时务必区分：

| 语义 | 回调（[callbacks.md](callbacks.md)） | SSE |
|---|---|---|
| 字段完成事件名 | `field_done` | `field_extracted` |
| 规则完成事件名 | `rule_done` | `rule_evaluated` |
| 提取值 | `value` | `extracted_value` |
| 规则结果 | `result` | `result_value` |
| 进度序号 | `index` | `current` |
| 进度总数 | `total` | `total`（一致） |
