# 文件片段上下文查询接口文档

## 接口说明

根据文件 ID 和关键词 / 文本片段，在 MinerU 已解析的 Markdown 文本中查找命中位置，返回命中上下文、片段所在页码、PDF 块级坐标，并返回该文件的全部分块。

该接口的 `file_id` 放在请求体中，不放在 URL path 中。

## 请求

```http
POST /file/context_query
Content-Type: application/json
```

### 请求体

```json
{
  "file_id": "a1b2c3d4...",
  "query": "合同金额",
  "query_type": "keyword",
  "context_before": 200,
  "context_after": 200,
  "case_sensitive": false,
  "include_all_chunks": true
}
```

### 请求字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---:|---:|---:|---|
| `file_id` | string | 是 | 无 | 文件 ID，上传解析接口返回的 `file_id` |
| `query` | string | 是 | 无 | 要查找的关键词或 MinerU Markdown 中的文本片段 |
| `query_type` | string | 否 | `keyword` | 支持 `keyword`、`text_fragment`；当前两者均按精确文本查找 |
| `context_before` | int | 否 | `200` | 命中位置前返回的字符数，不能小于 0 |
| `context_after` | int | 否 | `200` | 命中位置后返回的字符数，不能小于 0 |
| `case_sensitive` | bool | 否 | `false` | 是否大小写敏感 |
| `include_all_chunks` | bool | 否 | `true` | 是否返回该文件全部分块 |

## 响应

### 成功响应

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "a1b2c3d4...",
    "query": "合同金额",
    "query_type": "keyword",
    "matched": true,
    "match_count": 2,
    "matches": [
      {
        "match_index": 1,
        "keyword": "合同金额",
        "position": 1234,
        "match_start_pos": 1234,
        "match_end_pos": 1238,
        "context_start_pos": 1034,
        "context_end_pos": 1438,
        "context": "...合同金额...",
        "page_num": "5",
        "bboxes": [
          {
            "page_num": 5,
            "bbox": [50, 120, 520, 160],
            "page_size": [612, 792]
          }
        ]
      }
    ],
    "chunks": [
      {
        "file_id": "a1b2c3d4...",
        "chunk_id": "chunk_001",
        "chunk_index": 0,
        "total_chunks": 15,
        "chunk_content": "...",
        "start_pos": 0,
        "end_pos": 512,
        "page_num": "1",
        "hit": false,
        "hit_count": 0
      }
    ]
  }
}
```

### 响应字段

| 字段 | 类型 | 说明 |
|---|---:|---|
| `data.file_id` | string | 请求中的文件 ID |
| `data.query` | string | 请求中的查询文本 |
| `data.query_type` | string | 请求中的查询类型 |
| `data.matched` | bool | 是否找到命中 |
| `data.match_count` | int | 命中数量 |
| `data.matches` | array | 命中片段列表 |
| `data.chunks` | array | 文件分块列表；默认返回全部分块 |

### `matches` 字段

| 字段 | 类型 | 说明 |
|---|---:|---|
| `match_index` | int | 命中序号，从 1 开始 |
| `keyword` | string | 命中的查询文本 |
| `position` | int | 命中在 MinerU Markdown 全文中的起始字符偏移 |
| `match_start_pos` | int | 命中片段起始字符偏移 |
| `match_end_pos` | int | 命中片段结束字符偏移，半开区间 `[start, end)` |
| `context_start_pos` | int | 返回上下文的起始字符偏移 |
| `context_end_pos` | int | 返回上下文的结束字符偏移，半开区间 `[start, end)` |
| `context` | string | 命中片段附近上下文 |
| `page_num` | string | 命中片段所在页码，如 `"5"` 或 `"5-6"`；无法定位时为空字符串 |
| `bboxes` | array | PDF 块级坐标；无坐标时为空数组 |

### `bboxes` 字段

| 字段 | 类型 | 说明 |
|---|---:|---|
| `page_num` | int | 坐标所在 PDF 页码 |
| `bbox` | array | 坐标框 `[x0, y0, x1, y1]` |
| `page_size` | array | PDF 页面尺寸 `[width, height]`；存量数据可能没有该字段 |

### `chunks` 字段

| 字段 | 类型 | 说明 |
|---|---:|---|
| `file_id` | string | 文件 ID |
| `chunk_id` | string | 分块 ID |
| `chunk_index` | int | 分块序号，从 0 开始 |
| `total_chunks` | int | 文件总分块数 |
| `chunk_content` | string | 分块内容 |
| `start_pos` | int | 分块在 MinerU Markdown 全文中的起始字符偏移 |
| `end_pos` | int | 分块在 MinerU Markdown 全文中的结束字符偏移，半开区间 `[start, end)` |
| `page_num` | string | 分块所在页码，如 `"1"` 或 `"1-2"` |
| `hit` | bool | 该分块是否包含命中片段 |
| `hit_count` | int | 该分块包含的命中数量 |

## 无命中响应

无命中时接口仍返回全部分块。

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "a1b2c3d4...",
    "query": "不存在的词",
    "query_type": "keyword",
    "matched": false,
    "match_count": 0,
    "matches": [],
    "chunks": [
      {
        "file_id": "a1b2c3d4...",
        "chunk_id": "chunk_001",
        "chunk_index": 0,
        "total_chunks": 15,
        "chunk_content": "...",
        "start_pos": 0,
        "end_pos": 512,
        "page_num": "1",
        "hit": false,
        "hit_count": 0
      }
    ]
  }
}
```

## 错误响应

### 文件内容不存在或尚未解析完成

```json
{
  "detail": "文件内容不存在或尚未解析完成"
}
```

HTTP 状态码：`404`

### 请求参数非法

例如 `query` 为空、`context_before` 小于 0、`query_type` 不是 `keyword` / `text_fragment`。

HTTP 状态码：`422`

## 对接注意事项

- `file_id` 使用上传解析接口返回的同名字段。
- 接口依赖 MinerU 解析结果，文件至少需要已有 `file_content` 数据。
- `chunks` 默认返回该文件全部分块，大文件响应体可能较大。
- `page_num` 按命中片段本身计算，不按上下文窗口计算。
- `bboxes` 是块级坐标，不是字符级或表格单元格级坐标。
- 所有字符偏移都是基于 MinerU Markdown 全文的 Python 字符偏移。
