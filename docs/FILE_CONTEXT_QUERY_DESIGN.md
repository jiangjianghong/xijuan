# 文件片段上下文查询设计方案

## 背景

当前系统已经在解析、分块、抽取链路中保存了从 MinerU Markdown 文本到 PDF 页码的映射能力，但这部分能力主要服务于字段抽取，没有独立接口支持调用方直接传入 `file_id` 和关键词或文本片段，返回片段上下文、页码以及文件块信息。

本方案只讨论后续开发设计，不包含代码实现。

## 现状确认

### 文件 ID 字段名

项目中目标文件 ID 字段确实叫 `file_id`。

主要依据：

- `files.file_id` 是文件主表主键。
- `file_content.file_id` 保存解析后的 Markdown、`middle_json`、`page_mapping`。
- `file_chunk.file_id` 保存分块归属。
- `file_table.file_id`、`extraction_result.file_id`、`analysis_result.file_id` 都沿用同一字段。
- 上传接口返回 `{ "file_id": "..." }`。

因此后续接口请求体中仍使用 `file_id`，不新增 `document_id`、`fileId` 等别名，避免破坏项目内已有命名一致性。

### 已有相关能力

1. MinerU 解析后，Markdown 全文保存在 `file_content.file_content`。
2. `middle_json` 会通过 `utils.page_mapping.build_page_mapping` 生成 `page_mapping`。
3. `utils.page_mapping.lookup_page_num(page_mapping, start_pos, end_pos)` 可以根据 Markdown 字符偏移返回页码字符串，如 `"1"` 或 `"1-3"`。
4. `file_chunk` 表保存了所有分块，每个分块包含 `chunk_content`、`start_pos`、`end_pos`、`page_num`。
5. `service.extraction_service.search_context` 已能根据关键词在全文中提取上下文，但它目前是抽取服务内部能力，不是独立业务 API。

## 需求理解

需要新增一个独立查询能力：

- 调用方通过请求体传入 `file_id`。
- 调用方传入关键词，或 MinerU 已解析文本中的某个片段。
- 服务在该文件的 MinerU Markdown 全文中查找命中位置。
- 返回命中片段附近上下文。
- 返回命中片段所在页码。
- 返回该文件的所有内容块，而不是按 `max_content`、`max_results` 或分页只返回一部分块。
- 后续开发必须在新分支进行，不能直接影响当前主分支。

## 推荐接口

推荐新增一个 POST 接口，避免把 `file_id` 放在 URL 中。

```http
POST /file/context_query
Content-Type: application/json
```

请求体：

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

字段说明：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---:|---:|---:|---|
| `file_id` | string | 是 | 无 | 文件 ID，项目内现有字段名 |
| `query` | string | 是 | 无 | 关键词或 MinerU Markdown 中的文本片段 |
| `query_type` | string | 否 | `keyword` | 暂支持 `keyword`、`text_fragment`，两者 MVP 都按精确文本查找 |
| `context_before` | int | 否 | `200` | 命中位置前返回的字符数 |
| `context_after` | int | 否 | `200` | 命中位置后返回的字符数 |
| `case_sensitive` | bool | 否 | `false` | 英文大小写是否敏感；中文无明显影响 |
| `include_all_chunks` | bool | 否 | `true` | 是否返回该文件所有 `file_chunk` 分块；按当前需求默认返回 |

不建议在 MVP 中加入 `max_content` 或 `max_results` 作为默认限制，因为当前明确需求是返回文件中所有块。如果未来遇到大文件响应过大问题，再增加可选分页或流式返回。

## 推荐响应

```json
{
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
      "bboxes": []
    }
  ],
  "chunks": [
    {
      "file_id": "a1b2c3d4...",
      "chunk_id": "c1...",
      "chunk_index": 0,
      "total_chunks": 42,
      "chunk_content": "...",
      "start_pos": 0,
      "end_pos": 512,
      "page_num": "1",
      "hit": false,
      "hit_count": 0
    }
  ]
}
```

响应设计要点：

- `matches` 返回命中片段级别结果，页码应按命中片段本身的 `match_start_pos/match_end_pos` 计算。
- `context_start_pos/context_end_pos` 表示上下文窗口范围。
- `context` 是给前端或调用方直接展示的上下文文本。
- `chunks` 返回文件所有块，来自 `file_chunk` 表。
- 每个 chunk 额外标记 `hit` 和 `hit_count`，方便前端高亮包含命中的块。
- `bboxes` 可复用 `lookup_bboxes`，有坐标时返回；存量无 bbox 的数据返回空数组即可。

## 服务逻辑规划

### 1. 请求校验

- `file_id` 不能为空。
- `query` 不能为空，去除首尾空白后仍为空则返回 400。
- `context_before/context_after` 不能为负数。
- `query_type` 只允许 `keyword`、`text_fragment`。

### 2. 查询文件内容

从 `file_content` 查询：

- `file_content.file_content`：MinerU Markdown 全文。
- `file_content.page_mapping`：页码映射。

如果没有记录：

- 文件不存在或解析尚未完成时返回 404 或业务错误。
- 建议错误文案区分“文件不存在”和“文件内容不存在”，但如果不额外查 `files` 表，MVP 可以统一返回“文件内容不存在或尚未解析完成”。

### 3. 全文命中检索

MVP 使用精确文本查找：

- `query_type=keyword`：按关键词查找所有出现位置。
- `query_type=text_fragment`：按完整片段查找所有出现位置。

实现上可以复用或改造 `search_context`，但建议新增更贴合该接口的服务函数，例如：

```python
find_context_matches(content, query, context_before, context_after, case_sensitive)
```

原因：

- 现有 `search_context` 默认有 `max_results=5`，不符合“所有命中/所有块返回”的要求。
- 新函数可以同时返回命中片段范围和上下文范围，字段更清晰。
- 避免修改抽取服务内部行为，降低对字段抽取链路的影响。

### 4. 页码计算

对每条 match：

- 用 `lookup_page_num(page_mapping, match_start_pos, match_end_pos)` 计算命中片段所在页码。
- 用 `lookup_bboxes(page_mapping, match_start_pos, match_end_pos)` 尝试获取 PDF 块坐标。

注意：页码建议按命中片段本身算，而不是按上下文窗口算。否则上下文跨页时可能返回更宽的页码范围，不利于定位“片段所在页”。

实现时还需注意 Python 文本切片采用半开区间 `[start, end)`。命中刚好结束在下一页锚点时，应使用 `end_pos - 1` 查页码和 bbox，避免把下一页误算进命中片段页码。

### 5. 返回所有分块

从 `file_chunk` 查询该文件所有分块：

```sql
select * from file_chunk where file_id = :file_id order by chunk_index
```

返回字段建议补齐：

- `file_id`
- `chunk_id`
- `chunk_index`
- `total_chunks`
- `chunk_content`
- `start_pos`
- `end_pos`
- `page_num`
- `hit`
- `hit_count`

当前 `GET /file/{file_id}/chunks` 的响应 schema 未暴露 `start_pos/end_pos`，新接口应暴露，便于前端定位、高亮和二次处理。

### 6. chunk 命中标记

对每个 chunk，根据 match 的位置判断是否命中：

```text
match_start_pos < chunk.end_pos and match_end_pos > chunk.start_pos
```

满足则说明命中片段与该 chunk 有交集。

如果 chunk 文本由重叠分块产生，同一个 match 可能落入多个 chunk，这是合理结果，不应强行去重。

## 推荐文件改动范围

后续实现建议控制在以下范围：

- `model/schemas.py`
  - 新增请求体 schema：`FileContextQueryRequest`
  - 新增响应 item schema：`FileContextMatchItem`、`FileContextChunkItem`
- `service/file_context_service.py`
  - 新增独立服务，避免继续膨胀 `extraction_service.py`
  - 负责查询全文、查找命中、计算页码、组装所有 chunks
- `blue_print/file_router.py`
  - 新增 `POST /file/context_query`
  - 保持现有 `GET /file/{file_id}/chunks`、`GET /file/{file_id}/outline` 不变
- `tests/test_file_context_query.py`
  - 覆盖请求校验、命中页码、所有 chunks 返回、无命中、无 page_mapping 等情况
- `docs/API_DOCUMENTATION.md`
  - 实现完成后补充正式 API 文档

## 分支策略

当前已为方案工作创建新分支：

```bash
feature/file-context-design
```

后续实际开发建议继续在该分支或基于它再切实现分支，例如：

```bash
feature/file-context-query
```

不要在 `master` 直接开发。

## 风险评估

### 响应体过大

当前需求要求返回文件所有块，遇到大 PDF 时响应可能很大。

缓解策略：

- MVP 严格按需求返回全部。
- 日志记录 `total_chunks`、响应构造耗时。
- 后续如有性能压力，再增加 `include_all_chunks=false`、分页或流式接口。

### 页码映射精度

`page_mapping` 是通过 MinerU `middle_json` 的 block 文本前缀在 Markdown 中定位生成的，不是逐字符原生页码。一般足够定位页码和块级 bbox，但在 Markdown 内容被 MinerU 改写、重复文本很多、表格结构复杂时，页码可能存在偏差。

缓解策略：

- 返回 `start_pos/end_pos`，便于排查。
- 保留 `page_num=""` 的容错结果。
- 对 bbox 为空不视为错误。

### 与现有抽取服务耦合

不要直接修改 `search_context` 默认行为，因为字段抽取依赖它的 `max_results=5` 等约定。

推荐新增独立 `file_context_service.py`，只复用 `lookup_page_num`、`lookup_bboxes` 这类纯工具函数。

### 表格命中

MinerU Markdown 中表格是 HTML `<table>` 文本，关键词可能命中表格 HTML 内部。页码计算仍可通过字符偏移工作，但 bbox 可能是整表块级 bbox，不是单元格级 bbox。

MVP 接受块级定位，不做单元格级定位。

## 测试计划

建议新增测试：

1. 请求体传 `file_id` 和 `query`，命中后返回 `matches`。
2. `file_id` 确认不在 URL 中。
3. 命中片段页码按 `lookup_page_num` 返回。
4. 上下文窗口不越界。
5. 返回该文件所有 chunks，不受命中数量限制。
6. chunk 中包含命中时 `hit=true`、`hit_count>0`。
7. 无命中时 `matched=false`、`match_count=0`，但仍返回所有 chunks。
8. `page_mapping` 为空时不报错，`page_num=""`、`bboxes=[]`。
9. 文件内容不存在时返回清晰错误。

## 结论

项目内文件 ID 字段名确认为 `file_id`。新能力应设计为请求体传入 `file_id`，新增独立 POST 接口，不沿用 URL path 参数风格。底层页码定位能力已经存在，主要开发工作是新增服务层和接口层，把“全文片段检索 + 页码定位 + 所有 chunks 返回”组合成稳定 API。
