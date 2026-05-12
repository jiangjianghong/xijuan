# 字段提取与逻辑分析配置指南

> **版本**: 1.0.0
> **目标读者**: 前端/后端开发人员
> **用途**: 指导如何正确配置字段提取规则和逻辑分析规则

---

## 目录

1. [概述](#1-概述)
2. [字段提取配置](#2-字段提取配置)
   - [2.1 表格类字段](#21-表格类字段)
   - [2.2 文本类字段](#22-文本类字段)
3. [逻辑分析配置](#3-逻辑分析配置)
   - [3.1 判断类规则](#31-判断类规则)
   - [3.2 计算类规则](#32-计算类规则)
4. [占位符规范](#4-占位符规范)
5. [完整配置示例](#5-完整配置示例)
6. [常见错误与排查](#6-常见错误与排查)

---

## 1. 概述

### 1.1 处理流程

```
文件上传 → 文档解析 → 分块/向量化 → 字段提取 → 逻辑分析 → 完成
                                      ↑            ↑
                                 extraction_field  analysis_rule
                                   (本文档重点)     (本文档重点)
```

### 1.2 配置关系

```
extraction_field (字段提取配置)
    ↓ 提取出字段值
extraction_result (提取结果)
    ↓ 被逻辑分析引用
analysis_rule (逻辑分析配置)
    ↓ 计算/判断
analysis_result (分析结果)
```

### 1.3 API 端点

| 操作 | 端点 | 方法 |
|------|------|------|
| 创建/更新字段配置 | `/extraction/fields` | POST |
| 查询字段配置列表 | `/extraction/fields` | GET |
| 删除字段配置 | `/extraction/fields/{field_id}` | DELETE |
| 调试字段提取 | `/extraction/test` | POST |
| 创建/更新分析规则 | `/analysis/rules` | POST |
| 查询分析规则列表 | `/analysis/rules` | GET |
| 删除分析规则 | `/analysis/rules/{rule_id}` | DELETE |
| 调试逻辑分析 | `/analysis/test` | POST |

---

## 2. 字段提取配置

字段提取分为两大类：**表格类** 和 **文本类**。

### 2.1 表格类字段

从文档中的表格提取数据，适用于财务报表、数据统计表等结构化内容。

#### 基础结构

```json
{
  "field_id": "字段唯一标识",
  "field_name": "字段显示名称",
  "source_type": "table",
  "priority": 0,
  "table_name_pattern": "表格名称匹配模式",
  "table_match_type": "匹配方式",
  "table_extract_prompt": "提取提示词（必须包含占位符）"
}
```

#### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `field_id` | string | 是 | 字段唯一标识，仅允许字母、数字、下划线，最长100字符 |
| `field_name` | string | 是 | 字段显示名称，最长200字符 |
| `source_type` | string | 是 | 固定为 `"table"` |
| `enabled` | int | 否 | 是否启用，默认 `1`（启用） |
| `priority` | int | 否 | 优先级，数值越小越先执行，默认 `0` |
| `table_name_pattern` | string | 是 | 要匹配的表格名称或模式 |
| `table_match_type` | string | 是 | 匹配方式，见下表 |
| `table_extract_prompt` | string | 是 | LLM 提取提示词，**必须包含占位符** |

#### 表格匹配方式 (table_match_type)

| 值 | 说明 | 示例 |
|------|------|------|
| `exact` | 精确匹配，表格名称必须完全相同 | `"利润表"` 只匹配名为 `"利润表"` 的表格 |
| `fuzzy` | 模糊匹配，相似度 ≥ 80% 即匹配 | `"利润表"` 可匹配 `"合并利润表"`、`"利润表（续）"` |
| `contains` | 包含匹配，表格名称包含指定文字即匹配 | `"利润"` 可匹配所有名称含 `"利润"` 的表格 |
| `llm` | LLM 语义匹配，由 AI 判断是否相关 | `"收入相关"` 可匹配 `"营业收入明细表"` |

#### 配置示例

**示例 1：提取利润表中的营业总收入**

```json
{
  "field_id": "total_revenue",
  "field_name": "营业总收入",
  "source_type": "table",
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "以下是检索到的利润表：\n<search_result>利润表</search_result>\n\n请从表格中找到「营业总收入」或「营业收入」对应的金额，仅返回数值（单位：元）。"
}
```

**示例 2：提取资产负债表中的总资产**

```json
{
  "field_id": "total_assets",
  "field_name": "资产总计",
  "source_type": "table",
  "priority": 2,
  "table_name_pattern": "资产负债表",
  "table_match_type": "contains",
  "table_extract_prompt": "以下是资产负债表：\n<search_result>资产负债表</search_result>\n\n请提取「资产总计」或「资产合计」的金额，仅返回数值。"
}
```

**示例 3：同时匹配多个表格**

如果需要从多个表格中提取（如合并报表和母公司报表），使用 `contains` 或 `fuzzy` 匹配：

```json
{
  "field_id": "net_profit",
  "field_name": "净利润",
  "source_type": "table",
  "priority": 3,
  "table_name_pattern": "利润",
  "table_match_type": "contains",
  "table_extract_prompt": "以下是利润相关表格：\n<search_result>利润</search_result>\n\n请优先从「合并利润表」中提取「净利润」金额。如果没有合并报表，则从「利润表」中提取。仅返回数值。"
}
```

---

### 2.2 文本类字段

从文档正文中提取信息，适用于公司名称、地址、日期等非结构化内容。

#### 基础结构

```json
{
  "field_id": "字段唯一标识",
  "field_name": "字段显示名称",
  "source_type": "text",
  "priority": 0,
  "search_type": "检索方式",
  "search_config": { "检索配置参数" },
  "text_extract_prompt": "提取提示词（必须包含占位符）"
}
```

#### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `field_id` | string | 是 | 字段唯一标识 |
| `field_name` | string | 是 | 字段显示名称 |
| `source_type` | string | 是 | 固定为 `"text"` |
| `enabled` | int | 否 | 是否启用，默认 `1` |
| `priority` | int | 否 | 优先级，默认 `0` |
| `search_type` | string | 是 | 检索方式，见下文 |
| `search_config` | object | 是 | 检索配置，根据 search_type 不同而不同 |
| `text_extract_prompt` | string | 是 | LLM 提取提示词，**必须包含占位符** |

#### 检索方式 (search_type)

文本类字段支持 5 种检索方式：

| 值 | 名称 | 适用场景 |
|------|------|------|
| `context` | 上下文检索 | 根据关键词定位，提取前后文 |
| `section` | 章节检索 | 匹配章节标题，提取整个章节 |
| `rule` | 规则检索 | 关键词 + 停用词边界，精确提取 |
| `chunk_db` | 数据库检索 | 从预分块中按关键词过滤 |
| `vector_db` | 向量检索 | 语义相似度检索 |

---

#### 2.2.1 上下文检索 (context)

**原理**: 在全文中搜索关键词，提取关键词前后指定字符数的内容。

**search_config 参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keywords` | string[] | 是 | - | 关键词列表，支持多个 |
| `context_before` | int | 否 | 200 | 关键词前取的字符数 |
| `context_after` | int | 否 | 200 | 关键词后取的字符数 |
| `max_results` | int | 否 | 5 | 最多返回几条结果 |
| `sort_order` | string | 否 | "asc" | 排序方式：`asc`（按位置）/ `desc` |

**配置示例**:

```json
{
  "field_id": "company_name",
  "field_name": "公司名称",
  "source_type": "text",
  "priority": 0,
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "企业名称", "甲方"],
    "context_before": 50,
    "context_after": 100,
    "max_results": 3
  },
  "text_extract_prompt": "请从以下内容中提取公司全称：\n\n<search_result>公司名称</search_result>\n\n<search_result>企业名称</search_result>\n\n<search_result>甲方</search_result>\n\n请返回完整的公司名称，不要包含简称。"
}
```

**单关键词简化写法**:

```json
{
  "field_id": "legal_representative",
  "field_name": "法定代表人",
  "source_type": "text",
  "priority": 1,
  "search_type": "context",
  "search_config": {
    "keywords": ["法定代表人"],
    "context_before": 20,
    "context_after": 50
  },
  "text_extract_prompt": "从以下内容提取法定代表人姓名：\n<search_result>法定代表人</search_result>\n\n仅返回姓名。"
}
```

---

#### 2.2.2 章节检索 (section)

**原理**: 匹配 Markdown 格式的章节标题（`# 1.1 标题`），提取整个章节内容。

**search_config 参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `section_pattern` | string | 是 | - | 章节标题匹配模式 |
| `section_match_type` | string | 否 | "contains" | 匹配方式：`exact`/`fuzzy`/`contains`/`llm` |
| `threshold` | float | 否 | 0.8 | fuzzy 模式的相似度阈值 |
| `max_results` | int | 否 | 3 | 最多返回几个章节 |

**配置示例**:

```json
{
  "field_id": "business_scope",
  "field_name": "经营范围",
  "source_type": "text",
  "priority": 2,
  "search_type": "section",
  "search_config": {
    "section_pattern": "经营范围",
    "section_match_type": "contains",
    "max_results": 1
  },
  "text_extract_prompt": "以下是经营范围相关章节：\n<search_result>经营范围</search_result>\n\n请完整提取公司的经营范围描述。"
}
```

**注意**: 章节检索的占位符标签应使用 `section_pattern` 的值。

---

#### 2.2.3 规则检索 (rule)

**原理**: 找到关键词后，向前/向后扩展到停用词边界，精确提取一段完整内容。

**search_config 参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keywords` | string[] | 是 | - | 关键词列表 |
| `stop_words` | string[] | 否 | 见下方 | 停用词列表（边界标记） |
| `direction` | string | 否 | "forward" | 扩展方向：`forward`/`backward`/`both` |
| `min_length` | int | 否 | 2 | 提取的最小长度 |
| `max_length` | int | 否 | 200 | 提取的最大长度 |
| `max_results` | int | 否 | 5 | 最多返回几条结果 |

**默认停用词**: `["#", "##", "###", "\n\n", "\n", "。", ".", "；", ";"]`

**配置示例**:

```json
{
  "field_id": "registration_date",
  "field_name": "注册日期",
  "source_type": "text",
  "priority": 3,
  "search_type": "rule",
  "search_config": {
    "keywords": ["成立日期", "注册日期", "设立日期"],
    "direction": "forward",
    "stop_words": ["\n", "。", "；", "，"],
    "max_length": 50
  },
  "text_extract_prompt": "从以下内容中提取注册日期：\n<search_result>成立日期</search_result>\n<search_result>注册日期</search_result>\n<search_result>设立日期</search_result>\n\n请以 YYYY-MM-DD 格式返回日期。"
}
```

---

#### 2.2.4 数据库检索 (chunk_db)

**原理**: 从预先分块的文本中，按关键词过滤相关分块。

**search_config 参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keywords` | string[] | 是 | - | 过滤关键词列表 |
| `max_results` | int | 否 | 10 | 返回分块数量 |
| `sort_order` | string | 否 | "asc" | 按 chunk_index 排序 |

**配置示例**:

```json
{
  "field_id": "risk_factors",
  "field_name": "风险因素",
  "source_type": "text",
  "priority": 4,
  "search_type": "chunk_db",
  "search_config": {
    "keywords": ["风险", "不确定性"],
    "max_results": 5
  },
  "text_extract_prompt": "以下是包含风险相关内容的段落：\n<search_result>风险</search_result>\n<search_result>不确定性</search_result>\n\n请总结主要的风险因素（不超过3条）。"
}
```

---

#### 2.2.5 向量检索 (vector_db)

**原理**: 将查询文本向量化，通过语义相似度从 Milvus 中检索最相关的分块。

**search_config 参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query_text` | string | 是 | - | 查询文本（会被向量化） |
| `top_k` | int | 否 | 5 | 返回最相似的 k 条结果 |
| `score_threshold` | float | 否 | null | 分数阈值（L2距离，越小越相似） |

**配置示例**:

```json
{
  "field_id": "core_competitiveness",
  "field_name": "核心竞争力",
  "source_type": "text",
  "priority": 5,
  "search_type": "vector_db",
  "search_config": {
    "query_text": "公司的核心竞争力和竞争优势是什么",
    "top_k": 3,
    "score_threshold": 0.5
  },
  "text_extract_prompt": "以下是与核心竞争力相关的内容：\n<search_result>公司的核心竞争力和竞争优势是什么</search_result>\n\n请总结公司的核心竞争力（不超过5条）。"
}
```

**注意**: 向量检索的占位符标签应使用 `query_text` 的值。

---

### 2.3 VL 类字段

**用途**: 直接基于 PDF 视觉模型端到端抽取，**不**依赖 file_content / file_chunk / file_table，**不**走文本 LLM 二次抽取。由 VL 模型直接输出 `{value, reason}` JSON。

**适用场景**:
- 关键字段藏在扫描图、复杂版式或表格嵌套里，文本检索/表格匹配捞不全
- 跨页/分散信息，需要视觉模型按上下文综合判断
- PDF 已上传时，原始字节自动持久化在 `uploads/{file_id}.pdf`，VL 直接读取

**前置条件**:
- `configs/config.yaml` 的 `vl_model:` 节配置好 `base_url` / `api_key` / `model`（默认 dashscope qwen-vl-max）
- `vl_model.pdf_storage_dir`（默认 `uploads`）目录可写

**3 种方法对比：**

| vl_method | 思路 | VL 调用次数 | 是否并发 | 适用 |
|---|---|---|---|---|
| `vl_model` | 一把梭：指定页全部塞 VL | 1 次 | — | 短文档、相关页固定 |
| `vl_progressive` | 逐批 + 伪历史累积，模型自判相关性 | 页数 / batch_size + 1 次聚合 | 串行 | 长文档、相关页分散 |
| `vl_locate` | 两轮：缩略图网格并行定位 → 关键页高清提取 | (页数 / grid_pages) + 1 次提取 | 第一轮并行 | 长文档、要快速定位关键页 |

**共通字段**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `source_type` | string | 是 | - | 固定为 `"vl"` |
| `vl_method` | string | 是 | - | `vl_model` / `vl_progressive` / `vl_locate` |
| `vl_config` | object | 否 | `{}` | 方法的可调参数（见下方各子节） |
| `vl_system_prompt` | string | 否 | `null` | 系统提示词，留空即不发 |
| `vl_extract_prompt` | string | 是 | - | 最终提取提示词，**必须包含 `value` 与 `reason` 关键字**（大小写不敏感）。VL 直接输出 JSON，不再有第二段文本 LLM 渲染 |

> 后端 `service/vl_service/_defaults.py` 提供 `vl_extract_prompt` / `batch_prompt_template` / `locate_prompt_template` 的默认值；前端 UI 也已预填，用户保持默认即可跑通。

#### 2.3.1 vl_model（全量）

**vl_config 参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `page_range` | string | 否 | `"all"` | 渲染的页范围。`"all"` 或 `"1-3"` / `"1-3,5"`；前端 UI 用「从第 X 页到第 Y 页」两数字框收集 |
| `max_pixels` | int | 否 | `4000000` | 单图像素上限，超出按比例缩 |

**配置示例（首页提取企业名称）：**
```json
{
  "field_id": "company_name_vl",
  "field_name": "企业名称",
  "source_type": "vl",
  "vl_method": "vl_model",
  "vl_config": {
    "page_range": "1-1",
    "max_pixels": 4000000
  },
  "vl_extract_prompt": "请基于以上图片提取企业全称。\n请只返回 JSON：{\"value\": \"企业全称\", \"reason\": \"在哪一页/位置看到\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

#### 2.3.2 vl_progressive（逐批扫描）

**vl_config 参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `field_hints` | string | 是 | - | 人类语言提示要找的字段（如 `"投资金额、签署日期、股东姓名"`） |
| `batch_size` | int | 否 | `2` | 每批塞 VL 的页数 |
| `max_pixels` | int | 否 | `4000000` | 单图像素上限 |
| `batch_prompt_template` | string | 否 | 见 `_defaults.py:DEFAULT_BATCH_PROMPT` | 自定义批次 prompt，必须含占位符 `{field_hints}` `{page_label}` `{total_pages}` `{history}` |

**配置示例（合同关键信息扫描）：**
```json
{
  "field_id": "contract_summary",
  "field_name": "合同关键信息",
  "source_type": "vl",
  "vl_method": "vl_progressive",
  "vl_config": {
    "field_hints": "签署日期、签约方、合同金额、有效期",
    "batch_size": 2
  },
  "vl_extract_prompt": "基于以上累积摘要，请综合整理合同关键信息。\n请只返回 JSON：{\"value\": \"日期/签约方/金额/有效期，多项用分号分隔\", \"reason\": \"分别在哪些页看到\"}"
}
```

#### 2.3.3 vl_locate（缩略图定位 + 高清提取）

**vl_config 参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `field_hints` | string | 是 | - | 人类语言提示要找的字段 |
| `grid_pages` | int | 否 | `6` | 每张网格图包含的页数 |
| `grid_cols` | int | 否 | `3` | 网格列数（行数 = ceil(grid_pages/grid_cols)） |
| `max_concurrent` | int | 否 | `20` | 第一轮多网格并行上限（与全局 `vl_model.global_max_concurrency` 取小） |
| `thumb_scale` | float | 否 | `0.75` | 缩略图缩放系数 |
| `key_pages_limit` | int | 否 | `6` | 第一轮命中的关键页上限（去重排序后截断） |
| `fallback_pages` | int | 否 | `3` | 第一轮一页未命中时回退取前 N 页 |
| `max_pixels` | int | 否 | `4000000` | 第二轮高清重渲染像素上限 |
| `locate_prompt_template` | string | 否 | 见 `_defaults.py:DEFAULT_LOCATE_PROMPT` | 自定义定位 prompt，必须含占位符 `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}` |

> VL 模板里字面 `{ }` 必须写成 `{{ }}` 转义（后端 `str.format()` 渲染）。

**配置示例（财报多页定位资产负债）：**
```json
{
  "field_id": "total_assets_vl",
  "field_name": "资产总额",
  "source_type": "vl",
  "vl_method": "vl_locate",
  "vl_config": {
    "field_hints": "资产总额、负债总额、净利润",
    "grid_pages": 6,
    "grid_cols": 3,
    "max_concurrent": 20,
    "key_pages_limit": 6
  },
  "vl_extract_prompt": "请从以上高清财报页中提取「资产总额」金额。\n请只返回 JSON：{\"value\": \"金额（含单位）\", \"reason\": \"看到的页码与位置\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

---

## 3. 逻辑分析配置

逻辑分析基于字段提取的结果进行二次计算或判断，分为 **判断类** 和 **计算类**。

### 3.1 判断类规则 (judge)

**用途**: 根据提取的字段值进行条件判断，返回 `true` 或 `false`。

#### 基础结构

```json
{
  "rule_id": "规则唯一标识",
  "rule_name": "规则显示名称",
  "rule_type": "judge",
  "expression": "判断表达式（必须包含字段占位符）",
  "depend_fields": ["依赖的字段ID列表"]
}
```

#### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `rule_id` | string | 是 | 规则唯一标识，仅允许字母、数字、下划线 |
| `rule_name` | string | 是 | 规则显示名称 |
| `rule_type` | string | 是 | 固定为 `"judge"` |
| `expression` | string | 是 | 发送给 LLM 的判断提示词，**必须包含字段占位符** |
| `depend_fields` | string[] | 否 | 依赖的字段 ID 列表（用于记录输入值） |
| `enabled` | int | 否 | 是否启用，默认 `1` |
| `priority` | int | 否 | 优先级，默认 `0` |

#### 配置示例

**示例 1：判断营收是否达标**

```json
{
  "rule_id": "revenue_qualified",
  "rule_name": "营收达标判断",
  "rule_type": "judge",
  "expression": "公司的营业总收入为 <field_result>total_revenue</field_result> 元。\n\n请判断：该公司营业总收入是否超过 1000 万元（10000000元）？",
  "depend_fields": ["total_revenue"],
  "priority": 0
}
```

**示例 2：多字段综合判断**

```json
{
  "rule_id": "profit_positive",
  "rule_name": "盈利状态判断",
  "rule_type": "judge",
  "expression": "公司财务数据如下：\n- 营业总收入：<field_result>total_revenue</field_result> 元\n- 净利润：<field_result>net_profit</field_result> 元\n\n请判断：该公司是否处于盈利状态（净利润大于0）？",
  "depend_fields": ["total_revenue", "net_profit"],
  "priority": 1
}
```

**示例 3：文本内容判断**

```json
{
  "rule_id": "has_risk_warning",
  "rule_name": "是否有风险警示",
  "rule_type": "judge",
  "expression": "公司名称：<field_result>company_name</field_result>\n风险因素描述：<field_result>risk_factors</field_result>\n\n请判断：该公司是否存在重大风险警示？",
  "depend_fields": ["company_name", "risk_factors"],
  "priority": 2
}
```

---

### 3.2 计算类规则 (calc)

**用途**: 对提取的数值字段进行数学运算，返回计算结果。

#### 基础结构

```json
{
  "rule_id": "规则唯一标识",
  "rule_name": "规则显示名称",
  "rule_type": "calc",
  "expression": "数学表达式（必须包含字段占位符）",
  "depend_fields": ["依赖的字段ID列表"]
}
```

#### 支持的运算

| 运算符 | 说明 | 示例 |
|--------|------|------|
| `+` | 加法 | `A + B` |
| `-` | 减法 | `A - B` |
| `*` | 乘法 | `A * B` |
| `/` | 除法 | `A / B` |
| `()` | 括号 | `(A + B) * C` |

**注意**: 计算类规则使用 `numexpr` 库进行安全计算，结果默认保留 2 位小数。

#### 配置示例

**示例 1：计算利润率**

```json
{
  "rule_id": "profit_margin",
  "rule_name": "利润率",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"],
  "priority": 0
}
```

**示例 2：计算资产负债率**

```json
{
  "rule_id": "debt_ratio",
  "rule_name": "资产负债率",
  "rule_type": "calc",
  "expression": "<field_result>total_liabilities</field_result> / <field_result>total_assets</field_result> * 100",
  "depend_fields": ["total_liabilities", "total_assets"],
  "priority": 1
}
```

**示例 3：计算净资产**

```json
{
  "rule_id": "net_assets",
  "rule_name": "净资产",
  "rule_type": "calc",
  "expression": "<field_result>total_assets</field_result> - <field_result>total_liabilities</field_result>",
  "depend_fields": ["total_assets", "total_liabilities"],
  "priority": 2
}
```

---

## 4. 占位符规范

### 4.1 搜索结果占位符

**格式**: `<search_result>标签</search_result>`

**用于**: 字段提取配置中的 `text_extract_prompt` 和 `table_extract_prompt`

| 场景 | 标签内容 | 示例 |
|------|----------|------|
| 上下文检索 | keywords 中的关键词 | `<search_result>公司名称</search_result>` |
| 章节检索 | section_pattern 的值 | `<search_result>经营范围</search_result>` |
| 规则检索 | keywords 中的关键词 | `<search_result>成立日期</search_result>` |
| 数据库检索 | keywords 中的关键词 | `<search_result>风险</search_result>` |
| 向量检索 | query_text 的值 | `<search_result>核心竞争力</search_result>` |
| 表格类字段 | table_name_pattern 的值 | `<search_result>利润表</search_result>` |

**无匹配时的替换**: `（未找到 '标签' 的相关内容）`

### 4.2 字段结果占位符

**格式**: `<field_result>字段标识</field_result>`

**用于**: 逻辑分析配置中的 `expression`

| 场景 | 标签内容 | 示例 |
|------|----------|------|
| 引用提取字段 | field_id | `<field_result>total_revenue</field_result>` |

**无提取结果时的替换**: `（未找到字段 '字段标识' 的提取结果）`

### 4.3 占位符校验规则

| 配置类型 | 字段 | 校验规则 |
|----------|------|----------|
| 表格类字段 | `table_extract_prompt` | 必须包含至少一个 `<search_result>标签</search_result>` |
| 文本类字段 | `text_extract_prompt` | 必须包含至少一个 `<search_result>标签</search_result>` |
| VL 类字段 | `vl_extract_prompt` | 必须包含 `value` 与 `reason` 关键字（大小写不敏感，VL 直接输出 JSON） |
| VL 类字段 | `vl_config.batch_prompt_template`（仅 vl_progressive 自定义时） | 必须含占位符 `{field_hints}` `{page_label}` `{total_pages}` `{history}` |
| VL 类字段 | `vl_config.locate_prompt_template`（仅 vl_locate 自定义时） | 必须含占位符 `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}` |
| 分析规则 | `expression` | 必须包含至少一个 `<field_result>字段标识</field_result>` |

**不符合规则时**: API 返回 422 错误，提示占位符缺失。

---

## 5. 完整配置示例

### 5.1 财务报表分析场景

**目标**: 从年报中提取关键财务指标，并计算财务比率。

#### 步骤 1：配置字段提取

```json
[
  {
    "field_id": "company_name",
    "field_name": "公司名称",
    "source_type": "text",
    "priority": 0,
    "search_type": "context",
    "search_config": {
      "keywords": ["公司名称", "公司全称"],
      "context_before": 20,
      "context_after": 100
    },
    "text_extract_prompt": "从以下内容中提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>公司全称</search_result>\n\n仅返回公司名称，不含其他文字。"
  },
  {
    "field_id": "total_revenue",
    "field_name": "营业总收入",
    "source_type": "table",
    "priority": 1,
    "table_name_pattern": "利润表",
    "table_match_type": "fuzzy",
    "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取「营业总收入」或「营业收入」的金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "net_profit",
    "field_name": "净利润",
    "source_type": "table",
    "priority": 2,
    "table_name_pattern": "利润表",
    "table_match_type": "fuzzy",
    "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取「净利润」或「归属于母公司股东的净利润」的金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "total_assets",
    "field_name": "资产总计",
    "source_type": "table",
    "priority": 3,
    "table_name_pattern": "资产负债表",
    "table_match_type": "contains",
    "table_extract_prompt": "以下是资产负债表：\n<search_result>资产负债表</search_result>\n\n请提取「资产总计」或「资产合计」的金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "total_liabilities",
    "field_name": "负债合计",
    "source_type": "table",
    "priority": 4,
    "table_name_pattern": "资产负债表",
    "table_match_type": "contains",
    "table_extract_prompt": "以下是资产负债表：\n<search_result>资产负债表</search_result>\n\n请提取「负债合计」或「负债总计」的金额，仅返回数值（单位：元）。"
  }
]
```

#### 步骤 2：配置逻辑分析

```json
[
  {
    "rule_id": "profit_margin",
    "rule_name": "净利润率(%)",
    "rule_type": "calc",
    "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
    "depend_fields": ["net_profit", "total_revenue"],
    "priority": 0
  },
  {
    "rule_id": "debt_ratio",
    "rule_name": "资产负债率(%)",
    "rule_type": "calc",
    "expression": "<field_result>total_liabilities</field_result> / <field_result>total_assets</field_result> * 100",
    "depend_fields": ["total_liabilities", "total_assets"],
    "priority": 1
  },
  {
    "rule_id": "is_profitable",
    "rule_name": "是否盈利",
    "rule_type": "judge",
    "expression": "公司「<field_result>company_name</field_result>」的净利润为 <field_result>net_profit</field_result> 元。\n\n请判断该公司是否处于盈利状态（净利润大于0）？",
    "depend_fields": ["company_name", "net_profit"],
    "priority": 2
  },
  {
    "rule_id": "debt_risk",
    "rule_name": "负债风险判断",
    "rule_type": "judge",
    "expression": "公司「<field_result>company_name</field_result>」的财务数据：\n- 资产总计：<field_result>total_assets</field_result> 元\n- 负债合计：<field_result>total_liabilities</field_result> 元\n\n请判断该公司资产负债率是否超过 70%（高负债风险）？",
    "depend_fields": ["company_name", "total_assets", "total_liabilities"],
    "priority": 3
  }
]
```

---

## 6. 常见错误与排查

### 6.1 API 返回 422 错误

**原因**: 占位符格式不正确或缺失。

**排查**:
- 检查 `text_extract_prompt` / `table_extract_prompt` 是否包含 `<search_result>标签</search_result>`
- 检查 `expression` 是否包含 `<field_result>字段标识</field_result>`
- 确保标签内容非空

**错误示例**:
```json
// 错误：使用了旧格式
"expression": "{total_revenue} > 1000000"

// 正确：使用新格式
"expression": "营业总收入 <field_result>total_revenue</field_result> 是否大于 1000000"
```

### 6.2 提取结果为空

**可能原因**:
1. 表格/文本检索未找到匹配内容
2. 关键词设置不当
3. 表格名称匹配模式过于严格

**排查**:
1. 使用 `POST /extraction/test` 调试接口查看 `search_results`
2. 检查 `keywords` 是否在文档中存在
3. 尝试放宽匹配条件（如 `exact` → `contains`）

### 6.3 计算结果错误

**可能原因**:
1. 字段提取值包含非数字字符
2. 字段提取值为空

**排查**:
1. 使用 `POST /analysis/test` 调试接口查看 `input_values`
2. 检查依赖字段的提取结果是否为纯数字
3. 在 `text_extract_prompt` 中明确要求「仅返回数值」

### 6.4 判断结果不准确

**可能原因**:
1. `expression` 描述不够清晰
2. 字段值格式不一致

**排查**:
1. 优化 `expression` 的描述，使判断条件更明确
2. 在提取配置中统一数值单位和格式

---

## 附录：字段类型速查表

| source_type | 子类型 | 适用场景 | 关键配置 |
|-------------|--------|----------|----------|
| `table` | - | 表格数据 | `table_name_pattern`, `table_match_type` |
| `text` | `search_type=context` | 关键词上下文 | `keywords`, `context_before/after` |
| `text` | `search_type=section` | 章节内容 | `section_pattern`, `section_match_type` |
| `text` | `search_type=rule` | 精确边界提取 | `keywords`, `stop_words`, `direction` |
| `text` | `search_type=chunk_db` | 分块过滤 | `keywords`, `max_results` |
| `text` | `search_type=vector_db` | 语义检索 | `query_text`, `top_k` |
| `vl` | `vl_method=vl_model` | 短文档全量视觉抽取 | `page_range`, `max_pixels` |
| `vl` | `vl_method=vl_progressive` | 长文档逐批扫描 | `field_hints`, `batch_size` |
| `vl` | `vl_method=vl_locate` | 长文档先定位再高清抽取 | `field_hints`, `grid_pages`, `key_pages_limit` |

---

*文档版本: 1.0.0 | 最后更新: 2025-01*
