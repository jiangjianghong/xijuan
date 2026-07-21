# 逻辑分析配置手册

> 对应服务版本 0.3.0

逻辑分析是六阶段管线的最后一环（`analyzing`），在字段提取（`extracting`）之后运行：它读取该文件已提取的字段值，按规则做**二次判断或计算**，把结论写入 `analysis_result`。本手册讲「怎么配规则」——按任务组织的配方、可直接套用的 JSON、以及排查清单。

- 接口签名、请求/响应字段、状态码：见 [analysis 接口参考](../api/analysis.md)。
- `source_refs` 溯源结构（含 `_web_search`）：见 [source-refs 指南](./source-refs.md)。
- 全局参数（`analysis` / `web_search` 节）：见 [configuration 指南](./configuration.md)。
- 字段提取怎么配（judge/calc 依赖的字段从哪来）：见 [extraction-config 指南](./extraction-config.md)。

---

## 目录

1. [概览：两类规则与心智模型](#1-概览两类规则与心智模型)
2. [占位符与依赖（核心机制）](#2-占位符与依赖核心机制)
3. [judge 判断规则](#3-judge-判断规则)
4. [calc 计算规则](#4-calc-计算规则)
5. [web_search 网络搜索（judge 专属）](#5-web_search-网络搜索judge-专属)
6. [两条执行路径：随管线 vs 独立分析](#6-两条执行路径随管线-vs-独立分析)
7. [调试规则](#7-调试规则)
8. [端到端配方：财务报表场景](#8-端到端配方财务报表场景)
9. [常见错误与排查](#9-常见错误与排查)
10. [跨类型复制的占位符重映射](#10-跨类型复制的占位符重映射)

---

## 1. 概览：两类规则与心智模型

一条分析规则（`analysis_rule`）= 一个 `expression` 表达式 + 若干依赖字段（`depend_fields`）。表达式里用 `<field_result>字段ID</field_result>` 占位符引用提取结果，执行前会被替换成真实值，再交给对应引擎处理。

| 规则类型 | 引擎 | 产出 | 典型用途 |
|---|---|---|---|
| `judge` | LLM 判断 | `"true"` / `"false"` 字符串 + 理由 | 是否达标、是否盈利、是否存在风险等条件判断 |
| `calc` | `numexpr` 数学计算 | 数值字符串（默认保留 2 位小数）+ 计算式 | 利润率、负债率、净资产等比率/差值 |

数据流：

```
extraction_field  →  extraction_result   （提取值，本手册的输入）
                          ↓ 被 <field_result> 引用
analysis_rule     →  analysis_result     （判断/计算结论，本手册的产出）
```

规则按 `type_id` 隔离——每个文件绑定一个文档类型，分析时只加载**同类型且 `enabled=1`** 的规则，按 `priority` 升序执行。不同类型的规则互不共享（跨类型复制见 [第 10 节](#10-跨类型复制的占位符重映射)）。

---

## 2. 占位符与依赖（核心机制）

### 2.1 `<field_result>` 字段占位符

**格式**：`<field_result>字段ID</field_result>`，`字段ID` 即某条 `extraction_field` 的 `field_id`。

**渲染规则**：执行前逐个替换——

- 命中且提取值非空 → 替换为该字段的提取值；
- 未命中或提取值为空 → 替换为提示文本 `（未找到字段 '字段ID' 的提取结果）`。

> 替换用的是**该文件全部提取结果**的映射，不限于 `depend_fields`。也就是说占位符「能不能取到值」只看提取结果里有没有这个 `field_id`，与是否写进 `depend_fields` 无关。但仍强烈建议把表达式里引用到的每个字段都列进 `depend_fields`（原因见 2.2）。

**校验**：`expression` **必须包含至少一个** `<field_result>…</field_result>`（judge / calc 都要求），否则保存时返回 **422**。

### 2.2 `depend_fields` 的作用

`depend_fields` 是一个字段 ID 列表，声明本规则依赖哪些提取字段。它不参与占位符替换，但决定四件事：

1. **取值与留痕**：结果里的 `input_values` 只记录 `depend_fields` 列出的字段值，便于回溯规则「吃进了什么」。
2. **依赖校验**（见 2.3）：只对 `depend_fields` 里的字段做「是否为空 / 是否为数字」检查。
3. **溯源收集**：把这些字段的 `source_refs`（命中页码、bbox 等）挂进分析结果，供前端定位。
4. **独立分析的门控**：`/analysis/run` 独立执行时，只有 `depend_fields` 被外部输入的键**完整覆盖**的规则才会跑（见 [6.2](#62-独立分析analysisrun)）。

**结论**：把 `expression`（以及 `web_search.query`）中引用到的每一个 `<field_result>` 字段都写进 `depend_fields`，否则校验、留痕、溯源、独立执行会漏掉它。

### 2.3 依赖值校验：规则何时被「跳过」

保存规则不校验依赖值，但**执行时**会对 `depend_fields` 逐个检查，决定是否跳过：

| 场景 | judge | calc |
|---|---|---|
| `depend_fields` 为空 | 直接通过 | 直接通过 |
| 所有依赖字段都为空 | **跳过**，理由「所有依赖字段均为空: …」 | **跳过**，同左 |
| 至少一个非空 | 通过 | 还需**至少一个是有效数字**，否则跳过（理由列出空字段/非数字字段） |

被跳过的规则仍会写一条空结果（`result_value=""`，`reason` 为失败原因），并照常触发 `rule_done` 回调，只是 `success=false`。校验较宽松：**只要有一个依赖非空就会执行**，缺失的字段在表达式里以「未找到…」提示文本参与判断/计算——所以 calc 里混入空字段可能让公式算错（见 [第 9 节](#9-常见错误与排查)）。

---

## 3. judge 判断规则

用 LLM 对表达式描述的条件做真/假判断，产出 `"true"` / `"false"` 及理由。

### 基础结构

```json
{
  "rule_id": "revenue_qualified",
  "type_id": "financial_report",
  "rule_name": "营收达标判断",
  "rule_type": "judge",
  "expression": "公司营业总收入为 <field_result>total_revenue</field_result> 元。\n\n请判断：该公司营业总收入是否超过 1000 万元（10000000 元）？",
  "depend_fields": ["total_revenue"],
  "priority": 0
}
```

| 字段 | 必填 | 说明 |
|---|:--:|---|
| `rule_id` | 是 | 唯一标识，`^[a-zA-Z0-9_]+$`，最长 100，**全局唯一** |
| `type_id` | 否 | 归属文档类型，默认 `default` |
| `rule_name` | 是 | 显示名，最长 200 |
| `rule_type` | 是 | 固定 `"judge"` |
| `expression` | 是 | 判断提示词，**须含至少一个 `<field_result>`** |
| `system_prompt` | 否 | judge 专属，作为 system message 调控判断口径；calc 忽略 |
| `depend_fields` | 否 | 依赖字段 ID 列表（见 2.2） |
| `web_search` | 否 | judge 专属，联网检索（见 [第 5 节](#5-web_search-网络搜索judge-专属)） |
| `enabled` | 否 | 1 启用 / 0 停用，默认 1 |
| `priority` | 否 | 升序执行，默认 0 |

### 工作原理（重要）

你只需在 `expression` 里用**自然语言**把「已知条件 + 要判断什么」写清楚。系统会在发给 LLM 前**自动追加**一段固定的 JSON 输出指令，要求模型返回：

```json
{"result": "true 或 false", "reason": "判断理由/依据"}
```

因此：

- **不要**自己在 `expression` 里再写「请返回 JSON」之类的格式要求，交给系统即可。
- 返回值会被**归一化**：模型答 `true` / `是` → 存 `"true"`；答 `false` / `否` → 存 `"false"`。下游按小写字符串 `"true"`/`"false"` 消费。
- `reason` 取模型给的理由；模型偶发吐裸英文双引号破坏 JSON 时，系统会做兜底抢救，不至于整条失败。
- 用 `system_prompt` 固化裁判口径（如「你是严谨的财务审计助手，只依据给定数据判断，信息不足时判 false」）。

### 配方

**单字段阈值判断**（上方基础结构即是）。

**多字段综合判断**：

```json
{
  "rule_id": "profit_positive",
  "type_id": "financial_report",
  "rule_name": "盈利状态判断",
  "rule_type": "judge",
  "expression": "公司财务数据如下：\n- 营业总收入：<field_result>total_revenue</field_result> 元\n- 净利润：<field_result>net_profit</field_result> 元\n\n请判断：该公司是否处于盈利状态（净利润大于 0）？",
  "depend_fields": ["total_revenue", "net_profit"],
  "priority": 1
}
```

**文本内容判断**（依赖文本类提取字段）：

```json
{
  "rule_id": "has_risk_warning",
  "type_id": "financial_report",
  "rule_name": "是否有风险警示",
  "rule_type": "judge",
  "expression": "公司名称：<field_result>company_name</field_result>\n风险因素描述：<field_result>risk_factors</field_result>\n\n请判断：该公司是否存在重大风险警示？",
  "depend_fields": ["company_name", "risk_factors"],
  "priority": 2
}
```

---

## 4. calc 计算规则

对提取到的**数值**字段做数学运算，用 `numexpr` 安全求值。

### 基础结构

```json
{
  "rule_id": "profit_margin",
  "type_id": "financial_report",
  "rule_name": "净利润率(%)",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"],
  "priority": 0
}
```

### 支持的运算与精度

| 运算符 | 说明 | 示例 |
|---|---|---|
| `+` `-` `*` `/` | 四则运算 | `A - B`、`A / B * 100` |
| `( )` | 括号分组 | `(A + B) * C` |

- 结果为整数则去掉小数位，否则按 `analysis.calc_precision`（默认 **2** 位）四舍五入。
- `reason` 自动生成，形如 `计算公式: 12.5/100*100 = 12.5`。

### 重要：calc 只做算术，不做比较

执行前系统会**只保留** `0-9 + - * / ( ) . e E` 和空格这些字符，其余一律剥离。这意味着：

- 表达式里混入的文字（如单位、说明）会被自动清掉，占位符**必须解析成纯数字**（含科学计数法如 `1.2e8`）。若某字段解析成「未找到…」提示或含逗号/货币符号，会算错或报错——请在提取阶段就要求「仅返回数值」。
- **比较与布尔（`>` `<` `>=` `==` 等）不被支持**：这些符号会被剥离掉。要「是否大于阈值」这类真/假结论，请用 **judge** 规则，不要用 calc。

### 配方

**资产负债率**：

```json
{
  "rule_id": "debt_ratio",
  "type_id": "financial_report",
  "rule_name": "资产负债率(%)",
  "rule_type": "calc",
  "expression": "<field_result>total_liabilities</field_result> / <field_result>total_assets</field_result> * 100",
  "depend_fields": ["total_liabilities", "total_assets"],
  "priority": 1
}
```

**净资产（差值）**：

```json
{
  "rule_id": "net_assets",
  "type_id": "financial_report",
  "rule_name": "净资产",
  "rule_type": "calc",
  "expression": "<field_result>total_assets</field_result> - <field_result>total_liabilities</field_result>",
  "depend_fields": ["total_assets", "total_liabilities"],
  "priority": 2
}
```

---

## 5. web_search 网络搜索（judge 专属）

judge 规则可在判断前先联网检索（博查 Bocha AI），把检索到的公开信息一并喂给 LLM，适合「文档内查不到、需要外部事实佐证」的判断（如「该公司当前是否为 A 股上市公司」）。**仅 judge 支持，calc 无效。**

### 规则级配置

在规则上加一个 `web_search` 对象：

```json
"web_search": {
  "enabled": true,
  "query": "<field_result>company_name</field_result> 是否A股上市公司 股票代码",
  "count": 5,
  "freshness": "oneYear"
}
```

| 键 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `enabled` | bool | 是 | `true` 才启用；`false`/缺省时整个 `web_search` 不生效 |
| `query` | string | 启用时必填 | 搜索词，**支持 `<field_result>字段ID</field_result>` 占位符**，会先用提取值拼接再检索 |
| `count` | int | 否 | 返回条数，缺省走全局 `web_search.count`（默认 5） |
| `freshness` | string | 否 | 时间范围 `noLimit`/`oneDay`/`oneWeek`/`oneMonth`/`oneYear`，缺省走全局配置 |

### 两个占位符协作

启用网络搜索后，一条 judge 规则里会同时出现两种占位符：

- `<field_result>…</field_result>`：字段值占位符，`expression` 和 `web_search.query` 里都能用。
- `<web_search_result/>`：**搜索结果占位符**（自闭合、无标签），只出现在 `expression` 里，执行时被替换为格式化后的检索文本（按条编号，含标题/来源/日期/摘要）。

### 校验规则（启用时，否则 422）

保存规则时，若 `web_search.enabled=true`：

1. `rule_type` 必须是 `judge` —— 否则「仅 judge 类型规则支持网络搜索」。
2. `query` 去空格后非空 —— 否则「启用网络搜索时 query 不能为空」。
3. `expression` **必须包含 `<web_search_result/>`** —— 否则「启用网络搜索时 expression 必须包含 `<web_search_result/>` 占位符」。

### 失败不致命 + 溯源

- 检索失败（网络/鉴权/超时）**不会**让整条规则失败：`<web_search_result/>` 会被替换为 `（网络搜索失败: …）`，判断照常进行。
- 检索留痕写入 `source_refs._web_search`：`{query, results: [{name,url,siteName,datePublished,summary}], error?}`，通过 `GET /file/{id}/analysis` 与 `rule_done` 回调透出；调试流会多推一个 `web_search` 事件。结构详见 [source-refs 指南](./source-refs.md)。

### 完整示例

```json
{
  "rule_id": "listing_status",
  "type_id": "financial_report",
  "rule_name": "是否A股上市公司",
  "rule_type": "judge",
  "expression": "公司名称：<field_result>company_name</field_result>\n\n以下是联网检索到的公开信息：\n<web_search_result/>\n\n请依据以上信息判断：该公司当前是否为 A 股上市公司？信息不足时判 false。",
  "system_prompt": "你是严谨的信息核查助手，只依据给定资料判断，不臆测。",
  "depend_fields": ["company_name"],
  "web_search": {
    "enabled": true,
    "query": "<field_result>company_name</field_result> A股 上市 股票代码",
    "count": 5,
    "freshness": "oneYear"
  },
  "priority": 5
}
```

### 全局参数（`configs/config.yaml` 的 `web_search` 节）

规则里没写的部分走全局默认：

| 参数 | 默认 | 说明 |
|---|---|---|
| `base_url` / `api_key` | — | 博查 Web Search API 地址与密钥 |
| `count` | 5 | 默认返回条数 |
| `summary` | true | 返回长摘要 |
| `freshness` | `noLimit` | 默认时间范围 |
| `timeout` | 10 | 请求超时（秒） |
| `retry_count` | 2 | 重试次数（4xx 除 429 不重试） |
| `max_result_length` | 4000 | 注入 prompt 的搜索文本上限（末尾截断） |

---

## 6. 两条执行路径：随管线 vs 独立分析

同一批规则有两种触发方式，取值来源与副作用**完全不同**：

| 维度 | 随管线执行（`analyzing` 阶段） | 独立分析 `POST /analysis/run` |
|---|---|---|
| 字段值来源 | 该文件的 `extraction_result`（库里已提取的） | 请求里外部传入的 `field_values` |
| 规则筛选 | `type_id` 匹配 + `enabled=1` | 同左 |
| 规则是否执行 | 依赖校验（≥1 非空即跑，见 2.3） | **先按 `depend_fields` 的键做覆盖门控**，再走同一套依赖校验 |
| 写库 | upsert `analysis_result` | **不写库** |
| 读提取结果 | 是 | **否** |
| 批量 | 单文件 | `items` 多组并发 |

### 6.1 随管线执行

`extracting` 完成后自动进入 `analyzing`：按 `type_id` + `enabled=1` 取规则、按 `priority` 升序，逐条解析占位符 → 判断/计算 → upsert 到 `analysis_result`（键为 `file_id + rule_id`）。**单条规则失败只写空结果并跳过，不影响其它规则**；全部跑完把 `files.progress` 置 `complete`。每条规则完成推 `rule_done`，阶段末推 `stage_done`（回调契约见 [callbacks 参考](../api/callbacks.md)）。失败的单文件可用 `POST /file/{file_id}/retry/analyzing` 重跑本阶段。

### 6.2 独立分析（`/analysis/run`）

不依赖文件、不落库，直接拿外部字段值跑规则，适合「已有字段值、只想要分析结论」的外部集成。

```jsonc
{
  "mode": "sync",                    // sync | async | stream
  "items": [
    { "type_id": "financial_report", "biz_id": "doc-001",
      "field_values": { "net_profit": "1200000", "total_revenue": "8000000" } }
  ]
}
```

关键语义：

- **覆盖门控（最易踩坑）**：某条规则只有当它的 `depend_fields` **每个键**都出现在该 item 的 `field_values` 里，才会被执行；否则**静默跳过**——不在结果中、不计入 `total`。注意门控只看**键是否存在**，值可以为空（空值会在后续依赖校验里被判无效）。所以想让一条规则跑起来，`field_values` 必须提供它 `depend_fields` 里的全部键。
- `items` 之间**并发**，单个 item 内按 `priority, rule_id` 顺序执行。
- judge 的 `web_search` 在这里**同样生效**。
- `async` 模式必须带 `callback_url`，用 `task_id` 推送 `rule_done` / `task_done` / `task_failed`；`stream` 走 SSE。字段签名与状态码见 [analysis 接口参考](../api/analysis.md)。

---

## 7. 调试规则

保存到管线前，用调试接口对**指定文件**试跑单条规则（依赖值取自该 `file_id` 已有的 `extraction_result`）：

- **同步** `POST /analysis/test`：传 `file_id` + （`rule_id` 用已存规则 / `config` 临时配置，二选一），返回 `input_values`、`expression_resolved`、`result_value`、`reason`。
- **流式** `POST /analysis/test/stream`（SSE）：分步观察每个环节。

judge 的事件序列（便于定位是哪一步出问题）：

```
input_values → resolved_expression → [web_search] → prompt → llm_response → result → done
```

calc 更短：`input_values → resolved_expression → result → done`。事件清单见 [sse 参考](../api/sse.md)。

排查建议：

- 看 `resolved_expression`：占位符是否都替换成了真实值？出现「未找到字段 '…' 的提取结果」说明该字段没提取到或没写进依赖。
- judge 看 `prompt` / `llm_response`：确认喂给模型的内容和模型原始回复。
- 启用了 web_search 就看 `web_search` 事件的 `query` 与 `results` 是否符合预期。

---

## 8. 端到端配方：财务报表场景

目标：从年报提取关键财务指标，再计算比率、判断盈利与上市状态。全部规则挂在 `type_id = financial_report`。

**前置——字段提取**（详见 [extraction-config 指南](./extraction-config.md)），需先配好这些 `field_id`：`company_name`、`total_revenue`、`net_profit`、`total_assets`、`total_liabilities`。

**分析规则**（`POST /analysis/rules` 逐条 upsert）：

```json
[
  {
    "rule_id": "profit_margin",
    "type_id": "financial_report",
    "rule_name": "净利润率(%)",
    "rule_type": "calc",
    "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
    "depend_fields": ["net_profit", "total_revenue"],
    "priority": 0
  },
  {
    "rule_id": "debt_ratio",
    "type_id": "financial_report",
    "rule_name": "资产负债率(%)",
    "rule_type": "calc",
    "expression": "<field_result>total_liabilities</field_result> / <field_result>total_assets</field_result> * 100",
    "depend_fields": ["total_liabilities", "total_assets"],
    "priority": 1
  },
  {
    "rule_id": "is_profitable",
    "type_id": "financial_report",
    "rule_name": "是否盈利",
    "rule_type": "judge",
    "expression": "公司「<field_result>company_name</field_result>」净利润为 <field_result>net_profit</field_result> 元。\n\n请判断该公司是否处于盈利状态（净利润大于 0）？",
    "depend_fields": ["company_name", "net_profit"],
    "priority": 2
  },
  {
    "rule_id": "debt_risk",
    "type_id": "financial_report",
    "rule_name": "高负债风险判断",
    "rule_type": "judge",
    "expression": "公司「<field_result>company_name</field_result>」财务数据：\n- 资产总计：<field_result>total_assets</field_result> 元\n- 负债合计：<field_result>total_liabilities</field_result> 元\n\n请判断该公司资产负债率是否超过 70%（高负债风险）？",
    "depend_fields": ["company_name", "total_assets", "total_liabilities"],
    "priority": 3
  },
  {
    "rule_id": "listing_status",
    "type_id": "financial_report",
    "rule_name": "是否A股上市公司",
    "rule_type": "judge",
    "expression": "公司名称：<field_result>company_name</field_result>\n\n联网检索信息：\n<web_search_result/>\n\n请依据以上信息判断该公司当前是否为 A 股上市公司？信息不足判 false。",
    "depend_fields": ["company_name"],
    "web_search": {
      "enabled": true,
      "query": "<field_result>company_name</field_result> A股 上市 股票代码",
      "freshness": "oneYear"
    },
    "priority": 4
  }
]
```

配好后，凡是该类型下提取完成的文件都会自动跑这 5 条规则；也可用 `/analysis/run` 传外部 `field_values` 直接得结论。

---

## 9. 常见错误与排查

| 现象 | 常见原因 | 处理 |
|---|---|---|
| 保存返回 **422**：缺 `<field_result>` | `expression` 一个字段占位符都没有 | 至少放一个 `<field_result>字段ID</field_result>` |
| 保存返回 **422**：web_search 相关 | 非 judge 却启用了搜索 / `query` 为空 / `expression` 缺 `<web_search_result/>` | 见 [5. 校验规则](#校验规则启用时否则-422) 逐条对照 |
| 保存返回 **409** | `rule_id` 已被别的 `type_id` 占用（全局唯一） | 换一个 `rule_id` |
| 结果为空，理由「所有依赖字段均为空」 | 依赖字段在该文件没提取到值 | 先用 `/extraction/test` 确认字段能提出值；核对 `depend_fields` 的 `field_id` 拼写 |
| calc 结果错误或报「计算失败」 | 占位符解析成非数字（含逗号/货币符/「未找到…」提示）；或用了 `>` `<` 等被剥离的比较符 | 提取时要求「仅返回数值」；比较判断改用 judge |
| judge 结果不稳定/不准 | 表达式描述不清、缺少判据 | 把已知条件和判断标准写明确；用 `system_prompt` 固化口径；必要时补 `web_search` |
| web_search 无结果或走了失败提示 | `api_key`/网络问题、`query` 拼接后为空、`freshness` 过窄 | 看调试流 `web_search` 事件的 `query`/`error`；核对全局 `web_search` 配置 |
| 独立分析 `/analysis/run` 某规则「没跑」 | 该规则 `depend_fields` 的键未被 item 的 `field_values` **完整覆盖**，被静默跳过 | 在 `field_values` 里补齐该规则依赖的全部键（见 [6.2](#62-独立分析analysisrun)） |
| 规则改了不生效 | `enabled=0`，或规则 `type_id` 与文件类型不一致 | 确认 `enabled=1` 且 `type_id` 与目标文件一致 |

通用调试顺序：`/analysis/test/stream` 看 `resolved_expression`（占位符替换对不对）→ judge 看 `prompt`/`llm_response`，calc 看 `result` 里的清洗后公式。

---

## 10. 跨类型复制的占位符重映射

用 `POST /doctype/{type_id}/copy_from`（或从类型派生/导入）把配置复制到新类型时，字段会生成**基于源 ID 的新 `field_id`**。分析规则里的占位符会随 `depend_fields` **自动重映射**：`expression` 与 `web_search.query` 中的 `<field_result>旧字段ID</field_result>` 会被改写为新副本的字段 ID；依赖的字段若没被一起复制，则该依赖会回报给调用方（不静默丢弃）。复制完成后两份配置完全独立，改一份不影响另一份。存量副本若缺 `parent_type_id` 血缘，需手工标模板，此后经复制/派生的类型会自动记录来源。

---

### 附录：字段速查

| 用途 | 字段 | 适用类型 |
|---|---|---|
| 引用提取值 | `expression` 内 `<field_result>字段ID</field_result>` | judge / calc |
| 注入搜索结果 | `expression` 内 `<web_search_result/>` | judge（启用 web_search 时必填） |
| 声明依赖/门控 | `depend_fields` | judge / calc |
| 裁判口径 | `system_prompt` | 仅 judge |
| 联网检索 | `web_search.{enabled,query,count,freshness}` | 仅 judge |
| 计算精度 | 全局 `analysis.calc_precision`（默认 2） | 仅 calc |

**相关文档**：[analysis 接口参考](../api/analysis.md) · [source-refs 指南](./source-refs.md) · [configuration 指南](./configuration.md) · [extraction-config 指南](./extraction-config.md) · [callbacks 参考](../api/callbacks.md) · [sse 参考](../api/sse.md)
