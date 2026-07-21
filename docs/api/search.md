# 向量检索接口 /search

> 对应服务版本 0.3.0

## 向量相似度检索

将 `query` 经 embedding 向量化后在 Milvus 中检索，返回命中分块及 COSINE 相似度 `score`（越大越相似，取值约 [-1, 1]）。

- 方法路径：`POST /search`
- 认证：无（内网部署）
- Content-Type：`application/json`

**请求体**

<!-- AUTOGEN:request-body POST /search -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| query | string | 是 | — | 检索文本（必填，空串返回空列表）。 |
| file_id | string | 否 | — | 限定检索的文件；省略则跨全部已向量化文件。 |
| top_k | integer | 否 | 10 | 返回条数，默认 10。 |
| score_threshold | number | 否 | — | 相似度下限，低于则过滤；省略不过滤。 |
<!-- /AUTOGEN:request-body -->

```jsonc
{
  "query": "公司注册资本是多少",
  "file_id": "a1b2c3...",   // 可选，限定检索的文件；省略则跨全部已向量化文件
  "top_k": 5,                // 可选，默认 10
  "score_threshold": 0.5     // 可选，相似度下限，低于则过滤；省略不过滤
}
```

**请求示例（curl）**

```bash
curl -X POST http://localhost:5019/search \
  -H "Content-Type: application/json" \
  -d '{"query": "公司注册资本", "top_k": 5}'
```

**响应体**

<!-- AUTOGEN:response POST /search status=200 -->
_data 为数组，每个元素：_

| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| chunk_id | string | 否 | 分块 ID |
| file_id | string | 否 | 文件唯一 ID |
| chunk_index | integer | 否 | 分块序号 |
| chunk_content | string | 否 | 分块正文 |
| score | number | 否 | COSINE 相似度（越大越相似） |
| page_num | string | 是 | 所在页（可空） |
<!-- /AUTOGEN:response -->

```jsonc
{
  "code": 200,
  "message": "success",
  "data": [
    { "chunk_id": "c_001", "file_id": "a1b2c3...", "chunk_index": 3,
      "chunk_content": "注册资本 1000 万元", "score": 0.87, "page_num": "2" }
  ]
}
```

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（`query` 为空串直接返回空数组） | ResponseWrapper |

> `query` 为空串时直接返回 `data: []`，不报错。相似度 `score` 越大越相似。
