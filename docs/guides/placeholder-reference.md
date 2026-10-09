# 配置占位符参考

本文说明字段抽取、进阶字段和逻辑分析配置中可使用的占位符，以及每类值的获取方式。

## 1. 占位符总览

| 占位符 | 主要使用位置 | 值的来源 |
|---|---|---|
| `<search_result>标签</search_result>` | `text_extract_prompt`、`table_extract_prompt` | 当前 PDF 中检索到的原文或表格 |
| `<field_result>字段ID</field_result>` | 进阶字段配置、分析规则 | 当前文档中其他字段的提取结果 |
| `<param>参数名</param>` | 字段配置、分析规则 | 调用方运行时传入的外部参数 |

这三类占位符的替换发生在执行阶段，不会把替换后的值永久写回字段配置。

## 2. `<search_result>`：文档检索结果

格式：

```text
<search_result>标签</search_result>
```

标签必须与检索配置中的值一致：

| 来源配置 | 标签取值 |
|---|---|
| `source_type=table` | `table_name_pattern` |
| `search_type=context` | `search_config.keywords` 中的关键词 |
| `search_type=rule` | `search_config.keywords` 中的关键词 |
| `search_type=chunk_db` | `search_config.keywords` 中的关键词 |
| `search_type=section` | `search_config.section_pattern` |
| `search_type=vector_db` | `search_config.query_text` 中的每一项 |
| `search_type=page` | 固定为 `page_content` |

示例：

```json
{
  "search_config": {
    "keywords": ["公司名称", "企业名称"]
  },
  "text_extract_prompt": "请提取公司名称：\n<search_result>公司名称</search_result>\n<search_result>企业名称</search_result>"
}
```

执行时，系统会把占位符替换为 PDF 中检索到的片段。没有命中时会替换为未找到提示。

### 常见配置

```text
text + context:
<search_result>公司名称</search_result>

text + page:
<search_result>page_content</search_result>

table:
<search_result>利润表</search_result>
```

当 `text` 或 `table` 字段启用 LLM（`use_llm=1`）时，抽取 Prompt 至少需要包含一个有效的
`<search_result>...</search_result>` 占位符。`use_llm=0` 时会直接返回检索原文，可不配置该占位符。

## 3. `<field_result>`：引用其他字段结果

格式：

```text
<field_result>字段ID</field_result>
```

其中 `字段ID` 是 `extraction_field.field_id`，可以通过以下方式获取：

- 调用 `GET /extraction/fields` 查询字段配置；
- 在前端字段配置页面的字段选择器中选择；
- 查看文档类型导出的配置 JSON。

### 进阶字段示例

先抽取甲方名称，再用甲方名称作为关键词检索注册资本：

```json
{
  "field_id": "registered_capital",
  "field_name": "注册资本",
  "source_type": "text",
  "is_advanced": 1,
  "search_type": "context",
  "search_config": {
    "keywords": ["<field_result>party_a</field_result>"],
    "context_after": 60,
    "max_results": 3
  },
  "text_extract_prompt": "从 <search_result><field_result>party_a</field_result></search_result> 中提取注册资本"
}
```

执行顺序是：

1. 先完成所有普通字段；
2. 用普通字段的提取值替换 `<field_result>`；
3. 再执行进阶字段。

进阶字段只能引用普通字段，不能引用另一个进阶字段。引用字段没有提取值时，占位符会替换为空，相关检索通常会失败。

`<field_result>` 还可以出现在进阶字段的以下位置：

- `search_config` 中的字符串或字符串列表；
- `table_match_keywords`；
- `text_extract_prompt`、`table_extract_prompt`、系统提示词；
- `vl_config.field_hints`、VL 自定义模板；
- `table_match_prompt` 和章节匹配 Prompt。

### 分析规则中的使用

逻辑分析规则同样通过字段 ID 获取提取结果：

```json
{
  "rule_type": "judge",
  "expression": "净利润为 <field_result>net_profit</field_result>，请判断公司是否盈利。"
}
```

分析规则的 `expression` 必须至少包含一个 `<field_result>...</field_result>` 占位符。

## 4. `<param>`：外部运行参数

格式：

```text
<param>参数名</param>
```

适合引用 PDF 中不存在、但上游系统知道的信息，例如当前日期、申报年度、审批批次号等。

### 第一步：定义参数

按文档类型配置参数：

```text
GET  /doctype/{type_id}/params
POST /doctype/{type_id}/params
```

参数定义包括参数标识、名称、说明、默认值和是否必填。占位符中的名称就是参数定义中的 `param_key`。

### 第二步：调用时传入参数

文件解析时，在 `POST /file/parse` 的 multipart form 中传入 `params` JSON 字符串：

```bash
curl -F 'params={"current_date":"2026-10-09","report_year":"2026"}' ...
```

配置中使用：

```text
今天是 <param>current_date</param>，请判断有效期是否已过期。
```

独立分析时，在 `POST /analysis/run` 的每个 item 中传入 `params`。

参数合并顺序是：

```text
参数默认值 <- 本次调用传入值
```

文件解析后的完整参数快照会保存到文件记录中，重试时继续沿用原参数。

未定义、未传入且没有默认值的参数会替换为空字符串并记录 warning。参数值只支持文本、数字、布尔值或 `null`，对象和数组不支持作为占位符值。

## 5. VL 模板变量

VL 自定义模板使用 `{变量}`，这是系统模板变量，不是字段引用占位符。

### `vl_progressive.batch_prompt_template`

必须包含：

```text
{history}
{field_hints}
{page_label}
{total_pages}
```

可选：

```text
{scan_scope}
```

### `vl_locate.locate_prompt_template`

必须包含：

```text
{field_hints}
{page_labels}
{position_map}
{grid_rows}
{grid_cols}
```

这些变量由系统根据当前 PDF、页码和 VL 配置自动生成，不需要调用方传入。

如果 VL 模板中需要输出 JSON 的字面量大括号，必须转义：

```text
{{"found_pages": []}}
```

## 6. LLM 匹配模板变量

表格匹配和章节匹配的自定义 Prompt 使用以下变量：

| 变量 | 含义 |
|---|---|
| `{table_list}` | 候选表格序号和名称 |
| `{section_list}` | 候选章节序号和标题 |
| `{query}` | 当前匹配词 |
| `{quantity_hint}` | 数量限制提示 |

其中：

- 表格匹配模板必须包含 `{table_list}`；
- 章节匹配模板必须包含 `{section_list}`；
- `{query}` 和 `{quantity_hint}` 是可选变量；
- 默认模板可通过 `GET /extraction/match-prompt-defaults` 获取。

与 VL 模板不同，这类模板使用字符串替换，字面量 `{}` 通常不需要写成 `{{}}`。

## 7. 不是占位符的相关字段

以下字段容易被误认为占位符：

- `pages`：模型返回的参考页码；
- `source_pages`：系统根据模型页码或检索命中页推导出的可用页码；
- `page_source_field`：页码联动时填写的来源字段 ID，不使用 `<field_result>` 格式。

它们属于结果字段或配置字段，不是 Prompt 中的占位符。
