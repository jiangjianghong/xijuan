# 解析接口异步回调说明

本文档说明 PDF 解析管线在 **异步模式**（`mode=async`）下，向调用方 `callback_url` 推送状态时的全部入参与返回格式。

> 实现位置：
> - 入口：`blue_print/file_router.py`（`/file/parse`、`/file/{file_id}/retry/{stage}`、`/file/{file_id}/retry/extracting`、`/file/{file_id}/retry/analyzing`）
> - 调度：`service/pipeline_service.py:run_pipeline` / `run_from_stage`
> - 单项事件：`service/extraction_service.py:run_extraction`、`service/analysis_service.py:run_analysis`
> - HTTP 推送：`utils/callback.py:notify_callback`

---

## 1. 触发回调的接口

| Method | Path | 关键 query 参数 |
|---|---|---|
| POST | `/file/parse` | `mode=async`、`type_id=default`、`callback_url=<https://...>` |
| POST | `/file/{file_id}/retry/{stage}` | `mode=async`、`callback_url=<...>` |
| POST | `/file/{file_id}/retry/extracting` | `mode=async`、`callback_url=<...>` |
| POST | `/file/{file_id}/retry/analyzing` | `mode=async`、`callback_url=<...>` |

### 入参

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `file` | form-data | UploadFile | 仅 `/file/parse` 必填 | PDF 文件二进制；最大尺寸由 `mineru.max_file_size` 限制 |
| `mode` | query | string | 否 | `async` / `sync` / `stream`，默认 `async`；**仅 `async` 模式下回调生效** |
| `type_id` | query | string | 否 | 文档类型 ID，默认 `default`；决定字段/规则配置作用域 |
| `callback_url` | query | string | 否 | 回调接收地址。**留空则不推送**（静默跳过，见 `utils/callback.py:36-37`） |
| `stage` | path | string | 否 | 仅重试接口使用，取值 `parsing` / `tableing` / `chunking` / `embedding` / `extracting` / `analyzing` |

### 接口返回（不是回调本身）

`mode=async` 时接口立即返回 `200`：

```json
{
  "code": 0,
  "message": "文件已提交处理（异步）",
  "data": { "file_id": "abc123..." }
}
```

随后管线在后台运行，按下文格式向 `callback_url` POST。

---

## 2. 回调通用约定

| 项 | 值 |
|---|---|
| HTTP 方法 | `POST` |
| Content-Type | `application/json` |
| 请求体编码 | UTF-8，`ensure_ascii=False`（保留中文原文） |
| 超时 | **2.5 秒**（`utils/callback.py:18`） |
| 重试 | **不重试**。任何异常仅写日志，不影响主管线 |
| 失败语义 | 接收端慢 / 4xx / 5xx / 网络错误，对管线零影响 |
| 调用顺序 | 同一阶段内严格按下文顺序串行；阶段间也严格串行 |

> 接收方应在 2.5s 内返回 `2xx`。任何重逻辑（落库、转发）都建议异步处理，避免阻塞后续回调。

---

## 3. Payload 三种基础形态

```jsonc
// 形态 A：阶段入口（每个阶段开始时各 1 次）
{ "file_id": "...", "status": "<stage>" }

// 形态 B：单项进度事件（仅 extracting / analyzing 阶段）
{ "file_id": "...", "status": "<stage>", "event": "field_done|rule_done", "data": { ... } }

// 形态 C：阶段完整结果（每个阶段结束时各 1 次）
{ "file_id": "...", "status": "<stage>", "event": "stage_done", "data": { ... } }
```

`status` 取值集合：`parsing`、`tableing`、`chunking`、`embedding`、`extracting`、`analyzing`、`complete`。

---

## 4. 完整事件序列

一次成功的端到端管线，回调按以下顺序推送：

```
1.  parsing                                                # 形态 A
2.  parsing   + event=stage_done                            # 形态 C
3.  tableing                                                # 形态 A
4.  tableing  + event=stage_done                            # 形态 C
5.  chunking                                                # 形态 A
6.  chunking  + event=stage_done                            # 形态 C
7.  embedding                                               # 形态 A
8.  embedding + event=stage_done   (data 为空)               # 形态 C（无 data）
9.  extracting                                              # 形态 A
10. extracting + event=field_done × N （每字段 1 次）          # 形态 B
11. extracting + event=stage_done                           # 形态 C
12. analyzing                                               # 形态 A
13. analyzing + event=rule_done × M （每规则 1 次）           # 形态 B
14. analyzing + event=stage_done                            # 形态 C
15. complete                                                # 形态 A
```

**重试场景**（`/file/{id}/retry/{stage}`）只会从指定阶段开始推送对应及之后阶段的事件，不会回放已完成阶段。

**失败场景**：阶段抛出异常时**不会**推送 `stage_done` 与 `complete`；接收方应配合轮询 `/file/{id}` 查 `progress`（会变成 `<stage>_failed`）与 `error` 字段。

---

## 5. 各阶段 `stage_done.data` 详细结构

### 5.1 `parsing`

来源：`pipeline_service.py:428-438`

```json
{
  "file_id": "abc...",
  "status": "parsing",
  "event": "stage_done",
  "data": {
    "content": "<完整 Markdown 文本>",
    "middle_json": "<MinerU 中间 JSON 字符串>",
    "page_mapping": [
      { "page_num": 1, "start_pos": 0, "end_pos": 1234 },
      { "page_num": 2, "start_pos": 1234, "end_pos": 2456 }
    ]
  }
}
```

`content` 等价于 `GET /file/{id}/content` 返回值。`middle_json` 可能为 `null`（MinerU 未返回时）。

### 5.2 `tableing`

来源：`pipeline_service.py:471-477`，元素结构来自 `service/table_service.py:217-225`

```json
{
  "file_id": "abc...",
  "status": "tableing",
  "event": "stage_done",
  "data": {
    "total": 3,
    "tables": [
      {
        "file_id": "abc...",
        "table_index": 1,
        "total_table": 3,
        "table_name": "投资估算表",
        "table_content": "<table>...</table>",
        "start_pos": 5120,
        "end_pos": 6890,
        "page_num": "12"
      }
    ]
  }
}
```

`table_name` 已截断到 30 字符；模型识别失败时回退为表前最后一行或 `"未知"`。

### 5.3 `chunking`

来源：`pipeline_service.py:510-516`，元素结构来自 `service/chunk_service.py:356-365`

```json
{
  "file_id": "abc...",
  "status": "chunking",
  "event": "stage_done",
  "data": {
    "total": 42,
    "chunks": [
      {
        "file_id": "abc...",
        "chunk_id": "deterministic-chunk-id",
        "chunk_index": 0,
        "total_chunks": 42,
        "chunk_content": "<chunk text>",
        "start_pos": 0,
        "end_pos": 512,
        "page_num": "1"
      }
    ]
  }
}
```

> 表格作为独立 chunk 保留，`chunk_content` 含 `table_name\n<table>...</table>` 前缀。超过 8192 字符的表格会按 `</tr>` / `</td>` / `\n` 边界拆分为多个 chunk。

### 5.4 `embedding`

来源：`pipeline_service.py:550`

```json
{
  "file_id": "abc...",
  "status": "embedding",
  "event": "stage_done"
}
```

> **不携带 `data`**——向量数据量大不下发。需要向量请直查 Milvus（collection 见 `configs/config.yaml` 的 `milvus.collection_name`）。

### 5.5 `extracting`

> **关于 VL 字段：** 当前阶段除 `field_done` + `stage_done` 外，VL 抽取在 `/extraction/test/stream` SSE 调试通道还会推 `pdf_loaded` / `progressive_batch` / `locate_locate` / `locate_extract` 进度事件。这些事件**仅** SSE 推送，**不会**经 `callback_url`。callback_url 消费者依然只收 `field_done` + `stage_done`，与现有契约一致。

#### 5.5.1 单字段进度 `event=field_done`

来源：`extraction_service.py:806-817`（成功）/ `:845-856`（失败）

```json
{
  "file_id": "abc...",
  "status": "extracting",
  "event": "field_done",
  "data": {
    "field_id": "uuid-field-1",
    "field_name": "项目总投资",
    "value": "12345.67 万元",
    "reason": "依据第 3 章投资估算表小计行",
    "source_refs": {
      "_tables": [
        {
          "type": "table",
          "table_index": 1,
          "table_name": "投资估算表",
          "start_pos": 5120,
          "end_pos": 6890,
          "page_num": "12",
          "text": "表格名称: 投资估算表\n<table>...</table>"
        }
      ],
      "_texts": {
        "投资估算表": "表格名称: 投资估算表\n<table>...</table>"
      }
    },
    "success": true,
    "index": 5,
    "total": 12
  }
}
```

字段失败时：

| 字段 | 失败时取值 |
|---|---|
| `value` | `""` |
| `reason` | 异常文本 `str(e)` |
| `source_refs` | `null` |
| `success` | `false` |

##### `source_refs` 形状随 `source_type` 不同

- **table / text 字段**：`source_refs` 是 **dict**，按占位符 label 分组——table 类固定用 `_tables` 键，text 类按检索关键词分组（page 检索固定为 `page_content`），每个 label 对应一个命中数组（上例所示）。
- **VL 字段（source_type=vl）**：`source_refs` 是 dict，形如 `{"_vl": {method, total_pages, key_pages, vl_total_tokens, batches_with_info?}}`，**无检索文本**（不含 `text`/`_texts`）。消费者应按 `source_refs` 是否为 `null` 以及是否含 `_vl` 键分类处理；建议先判 `isinstance(refs, dict) and "_vl" in refs` 走 VL 分支。

##### 检索原文：每条 ref 的 `text` 与顶层 `_texts`

table / text 字段的 `source_refs` 携带模型实际看到的检索原文：

- 每条 ref 含 `text` = 该条命中注入 prompt 的原始片段（table 类含 `表格名称: xxx\n` 前缀）；
- 顶层 `_texts` 键 = `{label: 拼接后实际注入占位符的完整文本}`（多条命中以 `\n---\n` 拼接，与替换进 `<search_result>label</search_result>` 占位符的内容完全一致）。

`GET /file/{id}/extraction` 与回调 `field_done` / `stage_done` 均透出完整 `source_refs`。

> **老数据容错**：存量历史数据的 `source_refs` 无 `text` / `_texts` 键，消费方读取时需容错（取不到时按缺省处理，勿强制解包）。

**VL 字段的 field_done 示例（vl_locate 方法）：**

```json
{
  "file_id": "abc...",
  "status": "extracting",
  "event": "field_done",
  "data": {
    "field_id": "total_assets_vl",
    "field_name": "资产总额",
    "value": "1,234,567,890 元",
    "reason": "见第 12 页资产负债表合计行",
    "source_refs": {
      "_vl": {
        "method": "vl_locate",
        "total_pages": 48,
        "key_pages": [12, 13, 15],
        "vl_total_tokens": 8421,
        "batches_with_info": null
      }
    },
    "success": true,
    "index": 6,
    "total": 12
  }
}
```

`source_refs._vl` 字段含义：

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | string | `vl_model` / `vl_progressive` / `vl_locate` |
| `total_pages` | int | PDF 总页数 |
| `key_pages` | int[] \| null | 真正发给 VL 的关键页（1-indexed）；vl_progressive 不适用，固定为 `null` |
| `vl_total_tokens` | int | 本次抽取累计的 VL token 用量（包含所有轮次） |
| `batches_with_info` | int? | 仅 vl_progressive 出现：累积到摘要的批次数 |

#### 5.5.2 阶段汇总 `event=stage_done`

来源：`extraction_service.py:858-869`

```json
{
  "file_id": "abc...",
  "status": "extracting",
  "event": "stage_done",
  "data": {
    "total": 12,
    "succeeded": 10,
    "failed": 2,
    "results": [ /* 上面所有 field_done.data 的有序聚合 */ ]
  }
}
```

### 5.6 `analyzing`

#### 5.6.1 单规则进度 `event=rule_done`

来源：`analysis_service.py:406-419`（成功）/ `:350-363`、`:445-466`（失败）

```json
{
  "file_id": "abc...",
  "status": "analyzing",
  "event": "rule_done",
  "data": {
    "rule_id": "uuid-rule-1",
    "rule_name": "总投资是否超 1 亿",
    "rule_type": "judge",
    "result": "true",
    "reason": "总投资 12345.67 万元 > 10000 万元",
    "input_values": { "uuid-field-1": "12345.67 万元" },
    "source_refs": [
      { "type": "field", "field_id": "uuid-field-1" }
    ],
    "success": true,
    "index": 3,
    "total": 8
  }
}
```

`rule_type` 取值 `judge`（LLM 判真假）或 `calc`（numexpr 计算）。

规则失败时与字段失败语义一致：`result=""`、`reason` 含错误文本、`source_refs=null`、`success=false`。

#### 5.6.2 阶段汇总 `event=stage_done`

来源：`analysis_service.py:477-488`

```json
{
  "file_id": "abc...",
  "status": "analyzing",
  "event": "stage_done",
  "data": {
    "total": 8,
    "succeeded": 7,
    "failed": 1,
    "results": [ /* 上面所有 rule_done.data 的有序聚合 */ ]
  }
}
```

### 5.7 `complete`

来源：`pipeline_service.py:612`

```json
{
  "file_id": "abc...",
  "status": "complete"
}
```

收到此包代表整条管线成功结束。

---

## 6. 失败行为速查

- 任一阶段抛错 → 数据库 `files.progress` 写为 `<stage>_failed`，`files.error` 写异常文本，**不再发送 `stage_done` 与 `complete`**。
- 阶段入口包（形态 A）已发出 → 接收方据此可判断"该阶段已开始但未到 stage_done"。
- 接收方应通过 `GET /file/{file_id}` 拉取 `progress` 与 `error` 兜底。
- `callback_url` 接收端的任何超时 / 5xx / 网络错误，主管线**不感知、不重试**。

---

## 7. 接收端最小实现示例

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/cb")
async def cb(req: Request):
    payload = await req.json()
    fid    = payload["file_id"]
    status = payload["status"]
    event  = payload.get("event")          # 可能为 None
    data   = payload.get("data")           # 可能为 None

    if event is None:
        print(f"[{fid}] 阶段开始: {status}")
    elif event == "field_done":
        print(f"[{fid}] 字段 {data['index']}/{data['total']} 完成: "
              f"{data['field_name']} -> {data['value']}")
    elif event == "rule_done":
        print(f"[{fid}] 规则 {data['index']}/{data['total']} 完成: "
              f"{data['rule_name']} -> {data['result']}")
    elif event == "stage_done":
        print(f"[{fid}] 阶段完整数据 {status}: keys={list(data) if data else []}")

    return {"ok": True}                    # 必须 2.5s 内返回 2xx
```

启动后用以下方式触发：

```bash
curl -X POST "http://localhost:5019/file/parse?mode=async&callback_url=http://127.0.0.1:8000/cb" \
  -F "file=@sample.pdf"
```

---

## 8. 兼容性提示

- 旧消费者只读 `status` 不受影响；新事件靠 `event` 字段区分，未识别的 `event` 应直接忽略而不是报错。
- 历史状态名 `table_name_validating` 已统一为 `tableing`，回调 `status` 不会再出现旧名。
- `embedding` 阶段的 `stage_done` **永不携带 `data`**，请勿对 `data` 做强制解包。
