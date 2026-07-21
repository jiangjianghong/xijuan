# 枚举值与状态机

> 对应服务版本 0.3.0

本页是「析卷 AI」全部枚举值与文件处理状态机的**唯一权威**。接口参考（`api/*`）与配置指南（`guides/*`）单向引用本页，不再各自重述，避免同一枚举散落多处漂移。

约定：下列枚举值均为**小写精确字符串**，直接出现在 API 请求/响应与数据库列中，匹配时大小写敏感。每个枚举给出「取值 + 含义 + 配置位置」。

## 枚举速览

| 枚举 | 配置位置 | 取值 |
|---|---|---|
| [SourceType](#sourcetype) | `extraction_field.source_type` | `table` · `text` · `vl` |
| [TableMatchType](#tablematchtype) | `table` 来源的 `table_match_type` | `exact` · `fuzzy` · `contains` · `llm` |
| [SearchType](#searchtype) | `text` 来源的 `search_type` | `context` · `section` · `rule` · `chunk_db` · `vector_db` · `page` |
| [VLMethod](#vlmethod) | `vl` 来源的 `vl_method` | `vl_model` · `vl_progressive` · `vl_locate` |
| [RuleType](#ruletype) | `analysis_rule.rule_type` | `judge` · `calc` |
| [progress](#progress) | `files.progress` | 见 [progress 状态机](#progress-状态机) |

---

<a id="sourcetype"></a>

## SourceType — 字段来源类型

配置位置：`extraction_field.source_type`。决定该字段走哪条抽取链路。

| 值 | 说明 |
|----|------|
| `table` | 从已提取的表格中匹配并 LLM 抽取 |
| `text` | 从 Markdown 文本检索后 LLM 抽取 |
| `vl` | 直接对原 PDF 走 VL 视觉模型抽取 |

<a id="tablematchtype"></a>

## TableMatchType — 表格匹配方式

配置位置：`table` 来源字段的 `table_match_type`。决定按表名定位目标表格的方式。

| 值 | 说明 |
|----|------|
| `exact` | 精确匹配 |
| `fuzzy` | 模糊匹配 |
| `contains` | 包含匹配 |
| `llm` | LLM 语义匹配（可携带 `table_match_keywords`） |

<a id="searchtype"></a>

## SearchType — 文本检索方式

配置位置：`text` 来源字段的 `search_type`。决定从 Markdown 中召回喂给 LLM 的上下文的方式。

| 值 | 说明 |
|----|------|
| `context` | 关键词命中 + 前后上下文 |
| `section` | 章节标题匹配 |
| `rule` | 关键词起点 + 停止词边界 |
| `chunk_db` | MySQL 内分块检索 |
| `vector_db` | Milvus 语义检索 |
| `page` | 按 `page_range` 直接切 Markdown 喂 LLM；占位符固定为 `<search_result>page_content</search_result>`，可配 `max_length` 末尾截断 |

<a id="vlmethod"></a>

## VLMethod — VL 抽取方法

配置位置：`vl` 来源字段的 `vl_method`。决定视觉模型端到端抽取的策略。

| 值 | 说明 |
|----|------|
| `vl_model` | 指定页全部塞 VL 一次出 JSON |
| `vl_progressive` | 分批扫描 + 伪历史累积 + 最后文本聚合 |
| `vl_locate` | 缩略图网格并行定位 + 关键页高清提取 |

<a id="ruletype"></a>

## RuleType — 分析规则类型

配置位置：`analysis_rule.rule_type`。决定逻辑分析阶段该规则如何求值。

| 值 | 说明 |
|----|------|
| `judge` | LLM 判断，返回 `true`/`false`（也可能是 LLM 自由文本判断结果） |
| `calc` | `numexpr` 计算表达式，按 `analysis.calc_precision`（默认 2 位）保留小数 |

---

<a id="progress"></a>

## progress 状态机

配置位置：`files.progress`。跟踪单个文件在六阶段管线中的处理进度。

**成功路径：** `parsing` → `tableing` → `chunking` → `embedding` → `extracting` → `analyzing` → `complete`

每个 `*ing` 状态都有对应的 `*_failed` 失败态；阶段失败时 `progress` 置为 `<stage>_failed` 并把错误写入 `files.error`。

```
parsing ──► tableing ──► chunking ──► embedding ──► extracting ──► analyzing ──► complete
   │           │            │             │             │             │
   ▼           ▼            ▼             ▼             ▼             ▼
 parsing_    tableing_    chunking_    embedding_    extracting_   analyzing_
  failed      failed       failed        failed        failed        failed
   │           │            │             │             │             │
   └───────────┴────────────┴─────┬───────┴─────────────┴─────────────┘
                                  │
                POST /file/{id}/retry/{stage}
        （清理该阶段及下游数据后，从对应 *ing 阶段重新进入管线）
```

**状态取值：**

| 值 | 说明 |
|----|------|
| `parsing` / `parsing_failed` | 解析（MinerU） |
| `tableing` / `tableing_failed` | 表格识别（LLM 命名） |
| `chunking` / `chunking_failed` | 分块 |
| `embedding` / `embedding_failed` | 向量化 + Milvus 写入 |
| `extracting` / `extracting_failed` | 字段提取 |
| `analyzing` / `analyzing_failed` | 逻辑分析 |
| `complete` | 处理完成 |

> 启动时 `init_service` 会把所有残留的 `*ing` 状态强制改为 `*_failed`（崩溃恢复），并清理对应的孤儿数据。兼容旧值 `table_name_validating` → `tableing`。

> 重试（`POST /file/{id}/retry/{stage}`）会重置目标阶段及所有下游阶段的开始/结束时间戳，清理下游数据后从该阶段重跑。阶段与接口详见 [api/file.md](../api/file.md)。
