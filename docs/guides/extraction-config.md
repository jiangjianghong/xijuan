# 字段提取配置手册

> 对应服务版本 0.3.0

本手册讲**怎么配**字段提取（`extraction_field`）：三类来源（table / text / vl）如何选、
`search_config` / `vl_config` 各调什么、占位符怎么写、配错了怎么排查。以配方、可直接套用的
JSON 示例和排错清单为主。

- **字段全清单 / 类型 / 可空性**（`extraction_field` 的每一列）见 [data-model#extraction_field](../reference/data-model.md#extraction_field)，本页不重复。
- **枚举合法取值**（`source_type` / `table_match_type` / `search_type` / `vl_method`）见 [enums](../reference/enums.md)。
- **接口出入参 / 状态码**（`POST /extraction/fields`、调试接口）见 [api/extraction](../api/extraction.md)。
- **溯源结构**（`source_refs`、命中片段、bbox、模型自报页码）见 [source-refs](source-refs.md)。
- **逻辑分析**（`<field_result>` 占位符、judge / calc 规则）是另一份手册 [analysis-config](analysis-config.md)。

---

## 1. 先选来源类型

一个字段只走一种来源，由 `source_type` 决定，并激活对应的一组配置字段：

| source_type | 子类型 | 适用场景 | 关键配置 |
|---|---|---|---|
| `table` | — | 结构化表格（财报、统计表） | `table_name_pattern`、`table_match_type` |
| `text` | `search_type=context` | 关键词上下文 | `keywords`、`context_before/after` |
| `text` | `search_type=section` | 整段章节 | `section_pattern`、`section_match_type` |
| `text` | `search_type=rule` | 停用词边界精确切片 | `keywords`、`stop_words`、`direction` |
| `text` | `search_type=chunk_db` | 分块库关键词过滤 | `keywords`、`max_results` |
| `text` | `search_type=vector_db` | 语义相似检索 | `query_text`、`top_k` |
| `text` | `search_type=page` | 按页码整片喂 LLM | `page_range`、`max_length` |
| `vl` | `vl_method=vl_model` | 短文档 / 相关页固定，一次视觉抽取 | `page_range` |
| `vl` | `vl_method=vl_progressive` | 长文档、相关页分散，逐批扫描 | `field_hints`、`batch_size` |
| `vl` | `vl_method=vl_locate` | 长文档，先定位关键页再高清抽取 | `field_hints`、`grid_pages` |

**怎么选：**
- 数据在**规整表格**里 → `table`。
- 数据在**正文文字**里，且能被 MinerU 正确解析成 Markdown → `text`（按下面 6 种检索方式挑）。
- 数据藏在**扫描图 / 复杂版式 / 跨页**里，文本检索捞不全 → `vl`（直接读 PDF 原图，绕过 Markdown）。

`field_id`（`^[a-zA-Z0-9_]+$`，**全局唯一**）、`field_name`、`enabled`（默认 1）、`priority`
（默认 0，越小越先）等通用字段各类共用，含义见 [data-model#extraction_field](../reference/data-model.md#extraction_field)。

---

## 2. 表格类（table）

从解析出的 `<table>` 块里抽数，适合财务报表、数据统计表等结构化内容。

### 匹配方式 `table_match_type`

| 值 | 说明 | 示例 |
|---|---|---|
| `exact` | 表名完全相同才命中 | `"利润表"` 只匹配名为 `"利润表"` 的表 |
| `fuzzy` | 相似度 ≥ 80% 即命中 | `"利润表"` 可命中 `"合并利润表"`、`"利润表（续）"` |
| `contains` | 表名包含指定文字即命中 | `"利润"` 命中所有名字含「利润」的表 |
| `llm` | 由 LLM 语义判断是否相关 | `"收入相关"` 可命中 `"营业收入明细表"` |

从严到宽：`exact` < `fuzzy` < `contains` < `llm`。捞不到就往宽调，捞太多杂表就往严调，或改用 `table_match_keywords` / `table_match_max_results` 收敛命中集（见 [data-model#extraction_field](../reference/data-model.md#extraction_field)）。

### 配方

**① 从利润表取营业总收入**（表名可能带「合并」前缀 → 用 `fuzzy`）：

```json
{
  "field_id": "total_revenue",
  "field_name": "营业总收入",
  "source_type": "table",
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "以下是检索到的利润表：\n<search_result>利润表</search_result>\n\n请找到「营业总收入」或「营业收入」对应的金额，仅返回数值（单位：元）。"
}
```

**② 多表兜底**（合并报表优先，无则退母公司报表 → 用 `contains` 一网打尽，在 prompt 里排序）：

```json
{
  "field_id": "net_profit",
  "field_name": "净利润",
  "source_type": "table",
  "priority": 3,
  "table_name_pattern": "利润",
  "table_match_type": "contains",
  "table_extract_prompt": "以下是利润相关表格：\n<search_result>利润</search_result>\n\n请优先从「合并利润表」提取「净利润」金额；若无合并报表，则从「利润表」提取。仅返回数值。"
}
```

**占位符标签 = `table_name_pattern` 的值**：上面 `<search_result>利润表</search_result>` / `<search_result>利润</search_result>` 的标签必须与 `table_name_pattern` 一致，命中表格的内容才会被注入到该占位符处。

---

## 3. 文本类（text）

从正文抽取，`search_type` 决定「怎么把相关文字捞出来喂给 LLM」。六选一：

| search_type | 原理 | 占位符标签取值 |
|---|---|---|
| `context` | 全文找关键词，取其前后若干字符 | `keywords` 里每个关键词各一个占位符 |
| `section` | 匹配 Markdown 章节标题，取整段 | `section_pattern` 的值 |
| `rule` | 找到关键词后扩展到停用词边界，精确切片 | `keywords` 里的关键词 |
| `chunk_db` | 从预分块（MySQL）按关键词过滤 | `keywords` 里的关键词 |
| `vector_db` | 查询文本向量化，Milvus 语义召回 | `query_text` 的值 |
| `page` | 按页码直接切 Markdown 整片喂 LLM | 固定 `page_content` |

> 下面每种只列**常用可调项**；`search_config` 是自由 JSON，全部键与默认值见 [data-model#extraction_field](../reference/data-model.md#extraction_field)。

### 3.1 上下文检索 `context`

关键词命中点前取 `context_before`（默认 200）字符、后取 `context_after`（默认 200）字符。`max_results`（默认 5）限制条数。多关键词时**每个关键词一个占位符**。

```json
{
  "field_id": "company_name",
  "field_name": "公司名称",
  "source_type": "text",
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "企业名称", "甲方"],
    "context_before": 50,
    "context_after": 100,
    "max_results": 3
  },
  "text_extract_prompt": "请从以下内容提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>企业名称</search_result>\n<search_result>甲方</search_result>\n\n返回完整公司名称，不含简称。"
}
```

### 3.2 章节检索 `section`

匹配 Markdown 标题（如 `# 1.1 经营范围`）并取整段。`section_match_type`（`exact`/`fuzzy`/`contains`/`llm`，默认 `contains`）、`threshold`（fuzzy 阈值 0.8）、`max_results`（默认 3）。**占位符标签用 `section_pattern` 的值。**

```json
{
  "field_id": "business_scope",
  "field_name": "经营范围",
  "source_type": "text",
  "search_type": "section",
  "search_config": { "section_pattern": "经营范围", "section_match_type": "contains", "max_results": 1 },
  "text_extract_prompt": "以下是经营范围章节：\n<search_result>经营范围</search_result>\n\n请完整提取公司的经营范围描述。"
}
```

### 3.3 规则检索 `rule`

找到关键词后向 `direction`（`forward`/`backward`/`both`，默认 forward）扩展，止于最近的停用词，切出一段。默认停用词 `["#", "##", "###", "\n\n", "\n", "。", ".", "；", ";"]`；`min_length`（2）、`max_length`（200）兜底。适合「关键词 + 一小段值」的精确提取。

```json
{
  "field_id": "registration_date",
  "field_name": "注册日期",
  "source_type": "text",
  "search_type": "rule",
  "search_config": {
    "keywords": ["成立日期", "注册日期", "设立日期"],
    "direction": "forward",
    "stop_words": ["\n", "。", "；", "，"],
    "max_length": 50
  },
  "text_extract_prompt": "从以下内容提取注册日期：\n<search_result>成立日期</search_result>\n<search_result>注册日期</search_result>\n<search_result>设立日期</search_result>\n\n以 YYYY-MM-DD 格式返回。"
}
```

### 3.4 分块库检索 `chunk_db`

从入库的文本分块里按 `keywords` 过滤，`max_results`（默认 10，亦可写 `top_k`）限制分块数。适合「相关内容散落多段、要整段喂 LLM 归纳」的场景。

```json
{
  "field_id": "risk_factors",
  "field_name": "风险因素",
  "source_type": "text",
  "search_type": "chunk_db",
  "search_config": { "keywords": ["风险", "不确定性"], "max_results": 5 },
  "text_extract_prompt": "以下是含风险内容的段落：\n<search_result>风险</search_result>\n<search_result>不确定性</search_result>\n\n请总结主要风险因素（不超过 3 条）。"
}
```

### 3.5 向量检索 `vector_db`

`query_text` 向量化后从 Milvus 召回 `top_k`（默认 5）条最相似分块，`score_threshold`（L2 距离，越小越相似）可选过滤。适合关键词不固定、要「语义找」的抽象字段。**占位符标签用 `query_text` 的值。**

```json
{
  "field_id": "core_competitiveness",
  "field_name": "核心竞争力",
  "source_type": "text",
  "search_type": "vector_db",
  "search_config": { "query_text": "公司的核心竞争力和竞争优势是什么", "top_k": 3, "score_threshold": 0.5 },
  "text_extract_prompt": "以下是与核心竞争力相关的内容：\n<search_result>公司的核心竞争力和竞争优势是什么</search_result>\n\n请总结公司核心竞争力（不超过 5 条）。"
}
```

### 3.6 按页检索 `page`

不做关键词/语义检索，直接按 `page_range` 把 Markdown 整片切出来喂 LLM。`page_range` 支持 `"all"` / `"1-3"` / `"1-3,5"` / `"2"`；`max_length`（默认 30000）超长时末尾截断。**占位符标签固定为 `page_content`。** 适合「相关信息集中在已知页码」的定点提取。

```json
{
  "field_id": "cover_title",
  "field_name": "封面标题",
  "source_type": "text",
  "search_type": "page",
  "search_config": { "page_range": "1-1", "max_length": 5000 },
  "text_extract_prompt": "以下是文档首页内容：\n<search_result>page_content</search_result>\n\n请提取封面标题。"
}
```

---

## 4. VL 类（vl）

直接读 `uploads/{file_id}.pdf` 渲染成图给视觉模型，**不**依赖 MinerU 的 Markdown、**不**走文本 LLM 二次抽取，由 VL 直接输出 `{value, reason}` JSON。适合扫描图、复杂版式、跨页信息。

**前置条件：**
- `configs/config.yaml` 的 `vl_model:` 节配好 `base_url` / `api_key` / `model`（默认 dashscope qwen-vl-max），见 [configuration](configuration.md)。
- 文件上传时 PDF 已持久化到 `uploads/{file_id}.pdf`（被 storage 保留策略清掉的文件抽取会 404）。

**三法对比：**

| vl_method | 思路 | VL 调用次数 | 并发 | 适用 |
|---|---|---|---|---|
| `vl_model` | 一把梭：指定页全塞 VL | 1 | — | 短文档、相关页固定 |
| `vl_progressive` | 逐批 + 伪历史累积，模型自判相关性 | 页数/`batch_size` + 1 聚合 | 串行 | 长文档、相关页分散 |
| `vl_locate` | 两轮：缩略图网格并行定位 → 关键页高清提取 | 页数/`grid_pages` + 1 提取 | 第一轮并行 | 长文档、要快速定位关键页 |

**三法共通：** `vl_extract_prompt` 是最终提取 prompt，**必须含 `value` 与 `reason` 关键字**（大小写不敏感，因为要 VL 直接吐 JSON）；`vl_system_prompt` 可空。后端 `service/vl_service/_defaults.py` 与前端 UI 都预填了默认 prompt，保持默认即可跑通。全局并发上限 `vl_model.global_max_concurrency`（默认 8）。

### 4.1 vl_model（全量）

`vl_config`：`page_range`（默认 `"all"`，同 3.6 语法）、`max_pixels`（默认 4000000，单图像素上限，超出按比例缩）。

```json
{
  "field_id": "company_name_vl",
  "field_name": "企业名称",
  "source_type": "vl",
  "vl_method": "vl_model",
  "vl_config": { "page_range": "1-1", "max_pixels": 4000000 },
  "vl_extract_prompt": "请基于以上图片提取企业全称。\n只返回 JSON：{\"value\": \"企业全称\", \"reason\": \"在哪一页/位置看到\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

### 4.2 vl_progressive（逐批扫描）

`vl_config`：`field_hints`（必填，人话描述要找什么，如 `"投资金额、签署日期、股东姓名"`）、`batch_size`（每批页数，默认 2）、`max_pixels`。可选 `batch_prompt_template` 覆盖批次 prompt，**自定义时必须含占位符** `{history}` `{field_hints}` `{page_label}` `{total_pages}`。

```json
{
  "field_id": "contract_summary",
  "field_name": "合同关键信息",
  "source_type": "vl",
  "vl_method": "vl_progressive",
  "vl_config": { "field_hints": "签署日期、签约方、合同金额、有效期", "batch_size": 2 },
  "vl_extract_prompt": "基于以上累积摘要，综合整理合同关键信息。\n只返回 JSON：{\"value\": \"日期/签约方/金额/有效期，多项用分号分隔\", \"reason\": \"分别在哪些页看到\"}"
}
```

### 4.3 vl_locate（缩略图定位 + 高清提取）

`vl_config`：`field_hints`（必填）、`grid_pages`（每张网格图页数，默认 6）、`grid_cols`（列数，默认 3）、`max_concurrent`（第一轮并行上限，默认 20，与全局并发取小）、`key_pages_limit`（关键页上限，默认 6）、`fallback_pages`（一页未命中时回退取前 N 页，默认 3）、`max_pixels`。可选 `locate_prompt_template` 覆盖定位 prompt，**自定义时必须含占位符** `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}`。

```json
{
  "field_id": "total_assets_vl",
  "field_name": "资产总额",
  "source_type": "vl",
  "vl_method": "vl_locate",
  "vl_config": { "field_hints": "资产总额、负债总额、净利润", "grid_pages": 6, "grid_cols": 3, "key_pages_limit": 6 },
  "vl_extract_prompt": "请从以上高清财报页提取「资产总额」金额。\n只返回 JSON：{\"value\": \"金额（含单位）\", \"reason\": \"看到的页码与位置\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

> **模板转义坑：** 自定义 `batch_prompt_template` / `locate_prompt_template` 由后端 `str.format()` 渲染，模板里想输出**字面** `{` `}`（如 JSON 花括号）必须写成 `{{` `}}`，否则渲染报错。

---

## 5. use_llm 开关（text / table）

`use_llm`（默认 1）**只对 text / table 生效**，vl 恒需模型、不读此开关。置 `0` 时检索照常跑、`source_refs` 照常构建，但**跳过占位符校验与 LLM 调用**，直接把各标签检索原文用 `\n---\n` 拼成 `value`，`reason` 固定为「未启用 LLM，直接返回检索原文」。

用途：只想拿到「命中的原文片段」而不需要模型加工时（后续自己处理，或交给 [逻辑分析](analysis-config.md)）。此时提取 prompt 的占位符必填要求被放宽（可留空）。前端字段表单有「使用 LLM 提取」勾选框（VL 时隐藏）。

---

## 6. 占位符规范

### 6.1 `<search_result>标签</search_result>`（检索结果）

用于 `text_extract_prompt` / `table_extract_prompt`。**标签内容不是随便写的**，必须与检索配置对应，命中内容才会被注入到该占位符：

| 来源 | 标签取什么 |
|---|---|
| table | `table_name_pattern` 的值 |
| text · context / rule / chunk_db | `keywords` 里的每个关键词（各一个占位符） |
| text · section | `section_pattern` 的值 |
| text · vector_db | `query_text` 的值 |
| text · page | 固定 `page_content` |

无命中时占位符被替换为 `（未找到 '标签' 的相关内容）`，LLM 仍会执行、通常返回空值。

### 6.2 校验规则（配错即 422）

| 配置 | 规则 |
|---|---|
| `text_extract_prompt` | 含 ≥1 个 `<search_result>标签</search_result>`（`use_llm=0` 放宽） |
| `table_extract_prompt` | 含 ≥1 个 `<search_result>标签</search_result>`（`use_llm=0` 放宽） |
| `vl_extract_prompt` | 含 `value` 与 `reason` 关键字（大小写不敏感）；`source_type=vl` 时必填，`use_llm` **不**放宽 |
| `vl_method` | `source_type=vl` 时必填 |
| `batch_prompt_template`（vl_progressive 自定义时） | 含 `{history}` `{field_hints}` `{page_label}` `{total_pages}` |
| `locate_prompt_template`（vl_locate 自定义时） | 含 `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}` |

### 6.3 让模型自报参考页码（可选）

text / table 抽取时，可在 prompt 里要求 LLM 除 `value` / `reason` 外再返回 `pages`（参考到的页码整数数组）。后端会归一化并挂到 `source_refs._model_pages`，前端 PDF 定位（📍）优先跳这些页。这是模型自报，区别于程序算的 `ref.page_num`。细节见 [source-refs](source-refs.md)。

> `<field_result>字段标识</field_result>` 是**逻辑分析**的占位符，用于 `analysis_rule.expression` 引用提取结果，不在本手册范围，见 [analysis-config](analysis-config.md)。

---

## 7. 端到端范例（财报场景 · 提取部分）

一次配好一组字段，供后续逻辑分析引用（分析规则的配法见 [analysis-config](analysis-config.md)）：

```json
[
  {
    "field_id": "company_name",
    "field_name": "公司名称",
    "source_type": "text",
    "priority": 0,
    "search_type": "context",
    "search_config": { "keywords": ["公司名称", "公司全称"], "context_before": 20, "context_after": 100 },
    "text_extract_prompt": "从以下内容提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>公司全称</search_result>\n\n仅返回公司名称。"
  },
  {
    "field_id": "total_revenue",
    "field_name": "营业总收入",
    "source_type": "table",
    "priority": 1,
    "table_name_pattern": "利润表",
    "table_match_type": "fuzzy",
    "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取「营业总收入」或「营业收入」金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "net_profit",
    "field_name": "净利润",
    "source_type": "table",
    "priority": 2,
    "table_name_pattern": "利润表",
    "table_match_type": "fuzzy",
    "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取「净利润」或「归属于母公司股东的净利润」金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "total_assets",
    "field_name": "资产总计",
    "source_type": "table",
    "priority": 3,
    "table_name_pattern": "资产负债表",
    "table_match_type": "contains",
    "table_extract_prompt": "以下是资产负债表：\n<search_result>资产负债表</search_result>\n\n请提取「资产总计」或「资产合计」金额，仅返回数值（单位：元）。"
  }
]
```

保存用 `POST /extraction/fields`（逐条 upsert，按 `field_id` 全局唯一）。跨文档类型复用时用 `POST /doctype/{type_id}/copy_from` 复制，占位符会随字段自动重映射，见 [api/extraction](../api/extraction.md) 与 [api/doctype](../api/doctype.md)。

---

## 8. 常见错误与排查

### 8.1 保存报 422（占位符）

对照 §6.2。最常见：
- text/table 的提取 prompt 忘了写 `<search_result>标签</search_result>`，或标签与检索配置对不上（如 vector_db 标签没用 `query_text` 的值）。
- vl 的 `vl_extract_prompt` 里没有 `value` / `reason` 字样。
- 自定义 VL 模板缺占位符，或字面花括号没转义成 `{{ }}`（§4.3）。

### 8.2 提取结果为空

多半是**没检索到内容**，而非 LLM 出错。逐步排查：
1. 用 `POST /extraction/test`（同步）或 `POST /extraction/test/stream`（SSE）传 `file_id` + `field_id`/`config` 调试，看返回的 `search_results` / `llm_input` 是不是空——占位符被替换成了「未找到」就说明检索没命中。
2. table：`table_match_type` 从严放宽（`exact` → `fuzzy` → `contains`），或确认表名是否被 tableing 阶段正确识别（看文件详情的表格页）。
3. text：确认 `keywords` / `section_pattern` / `query_text` 在文档里确实存在；关键词太生僻或写法不一致时多给几个近义词。
4. 文档本身是扫描图、Markdown 里根本没有该文字 → 改用 `vl`。

### 8.3 VL 抽取报 404 / 无输出

- `uploads/{file_id}.pdf` 不存在：文件未成功上传，或被 `storage` 保留策略按容量/时长清理了。重新上传即可，保留策略见 [configuration](configuration.md)。
- 相关页没渲进去：检查 `page_range`（vl_model）或加大 `grid_pages` / `key_pages_limit`（vl_locate）、`batch_size`（vl_progressive）。

### 8.4 抽出来的值不是纯数字 / 后续计算出错

在提取 prompt 里明确要求「仅返回数值，不含单位和千分位」。数值字段被 [逻辑分析](analysis-config.md) 的 calc 规则引用时，非数字字符会导致计算失败——排查用 `POST /analysis/test` 看 `input_values`。

> 调试接口的完整出入参见 [api/extraction](../api/extraction.md)；`search_results` 的形态随 `source_type` / `search_type` 变化，VL 字段的 `llm_output` 即最终 `extracted_value`（直出 JSON）。
