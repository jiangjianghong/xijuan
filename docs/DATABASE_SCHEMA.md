# 数据库表结构文档

> **数据库**: MySQL 8.0+
> **字符集**: utf8mb4
> **排序规则**: utf8mb4_unicode_ci

---

## 目录

1. [表关系总览](#1-表关系总览)
2. [文件处理相关表](#2-文件处理相关表)
   - [2.1 files - 文件主表](#21-files---文件主表)
   - [2.2 file_content - 文件内容表](#22-file_content---文件内容表)
   - [2.3 file_table - 文件表格表](#23-file_table---文件表格表)
   - [2.4 file_chunk - 文件分块表](#24-file_chunk---文件分块表)
3. [配置相关表](#3-配置相关表)
   - [3.1 extraction_field - 字段提取配置表](#31-extraction_field---字段提取配置表)
   - [3.2 analysis_rule - 逻辑分析规则表](#32-analysis_rule---逻辑分析规则表)
4. [结果相关表](#4-结果相关表)
   - [4.1 extraction_result - 提取结果表](#41-extraction_result---提取结果表)
   - [4.2 analysis_result - 分析结果表](#42-analysis_result---分析结果表)
5. [向量数据库](#5-向量数据库-milvus)
6. [建表 SQL](#6-建表-sql)

---

## 1. 表关系总览

### 1.1 ER 图

```
┌─────────────────┐
│     files       │ ─────────────────────────────────────────────┐
│   (文件主表)     │                                              │
└────────┬────────┘                                              │
         │ 1:1                                                   │
         ▼                                                       │
┌─────────────────┐                                              │
│  file_content   │                                              │
│  (文件全文内容)  │                                              │
└─────────────────┘                                              │
         │                                                       │
         │ 1:N                                                   │
         ▼                                                       │
┌─────────────────┐     ┌─────────────────┐                     │
│   file_table    │     │   file_chunk    │                     │
│  (解析出的表格)  │     │  (文本分块)      │                     │
└─────────────────┘     └─────────────────┘                     │
                                                                 │
                                                                 │
┌─────────────────┐                        ┌─────────────────┐   │
│extraction_field │ ───────────────────▶   │extraction_result│◀──┤
│  (字段提取配置)  │      根据配置提取      │   (提取结果)     │   │
└─────────────────┘                        └────────┬────────┘   │
                                                    │            │
                                                    │ 被引用     │
                                                    ▼            │
┌─────────────────┐                        ┌─────────────────┐   │
│  analysis_rule  │ ───────────────────▶   │ analysis_result │◀──┘
│  (逻辑分析配置)  │      根据配置分析      │   (分析结果)     │
└─────────────────┘                        └─────────────────┘
```

### 1.2 表清单

| 序号 | 表名 | 说明 | 主键 |
|------|------|------|------|
| 1 | `files` | 文件主表，记录文件基本信息和处理状态 | `file_id` |
| 2 | `file_content` | 文件解析后的全文内容 | `file_id` |
| 3 | `file_table` | 文件中提取的表格数据 | `file_id` + `table_index` |
| 4 | `file_chunk` | 文件文本分块 | `file_id` + `chunk_id` |
| 5 | `extraction_field` | 字段提取配置 | `field_id` |
| 6 | `analysis_rule` | 逻辑分析规则配置 | `rule_id` |
| 7 | `extraction_result` | 字段提取结果 | `file_id` + `field_id` |
| 8 | `analysis_result` | 逻辑分析结果 | `file_id` + `rule_id` |

---

## 2. 文件处理相关表

### 2.1 files - 文件主表

记录上传文件的基本信息和处理进度状态。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件唯一标识（UUID 或哈希值） |
| `file_name` | VARCHAR(512) | NOT NULL | - | 原始文件名 |
| `file_size` | BIGINT | - | 0 | 文件大小（字节） |
| `create_time` | DATETIME | - | CURRENT_TIMESTAMP | 文件上传时间 |
| `start_parsing_time` | DATETIME | NULLABLE | NULL | 开始解析时间 |
| `end_parsing_time` | DATETIME | NULLABLE | NULL | 解析完成时间 |
| `start_chunking_time` | DATETIME | NULLABLE | NULL | 开始分块时间 |
| `end_chunking_time` | DATETIME | NULLABLE | NULL | 分块完成时间 |
| `start_embedding_time` | DATETIME | NULLABLE | NULL | 开始向量化时间 |
| `end_embedding_time` | DATETIME | NULLABLE | NULL | 向量化完成时间 |
| `end_extracting_time` | DATETIME | NULLABLE | NULL | 字段提取完成时间 |
| `end_analyzing_time` | DATETIME | NULLABLE | NULL | 逻辑分析完成时间 |
| `progress` | VARCHAR(32) | - | 'parsing' | 当前处理进度状态 |
| `error` | TEXT | NULLABLE | NULL | 错误信息（失败时记录） |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 最后更新时间（自动更新） |

#### progress 字段枚举值

| 值 | 说明 | 下一状态 |
|----|------|----------|
| `parsing` | 正在解析文件 | `chunking` / `parsing_failed` |
| `parsing_failed` | 文件解析失败 | - |
| `chunking` | 正在分块 | `embedding` / `chunking_failed` |
| `chunking_failed` | 分块失败 | - |
| `embedding` | 正在向量化 | `extracting` / `embedding_failed` |
| `embedding_failed` | 向量化失败 | - |
| `extracting` | 正在提取字段 | `analyzing` / `extracting_failed` |
| `extracting_failed` | 字段提取失败 | - |
| `analyzing` | 正在逻辑分析 | `complete` / `analyzing_failed` |
| `analyzing_failed` | 逻辑分析失败 | - |
| `complete` | 处理完成 | - |

#### 状态流转图

```
parsing ──▶ chunking ──▶ embedding ──▶ extracting ──▶ analyzing ──▶ complete
    │           │            │              │              │
    ▼           ▼            ▼              ▼              ▼
parsing_   chunking_   embedding_    extracting_    analyzing_
 failed     failed       failed        failed         failed
```

#### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "file_name": "2024年度财务报告.pdf",
  "file_size": 2048576,
  "create_time": "2025-01-15 10:30:00",
  "start_parsing_time": "2025-01-15 10:30:01",
  "end_parsing_time": "2025-01-15 10:31:15",
  "start_chunking_time": "2025-01-15 10:31:15",
  "end_chunking_time": "2025-01-15 10:31:20",
  "start_embedding_time": "2025-01-15 10:31:20",
  "end_embedding_time": "2025-01-15 10:32:00",
  "end_extracting_time": "2025-01-15 10:33:00",
  "end_analyzing_time": "2025-01-15 10:33:30",
  "progress": "complete",
  "error": null,
  "updated_at": "2025-01-15 10:33:30"
}
```

---

### 2.2 file_content - 文件内容表

存储文件解析后的完整 Markdown 格式文本内容。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID（关联 files 表） |
| `file_content` | LONGTEXT | NOT NULL | - | 解析后的全文内容（Markdown 格式） |

#### 说明

- 与 `files` 表是 **1:1** 关系
- 内容为 Markdown 格式，章节使用 `# 编号 标题` 格式
- 存储上限约 4GB（LONGTEXT）

#### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "file_content": "# 1 公司简介\n\n某某科技有限公司成立于2010年...\n\n# 2 财务报表\n\n## 2.1 资产负债表\n\n..."
}
```

---

### 2.3 file_table - 文件表格表

存储从文件中提取的表格数据。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `table_index` | INT | PK, NOT NULL | - | 表格序号（从 0 开始） |
| `total_table` | INT | - | 0 | 该文件的表格总数 |
| `table_name` | VARCHAR(500) | - | '' | 表格名称（从表头或上下文提取） |
| `table_content` | LONGTEXT | NOT NULL | - | 表格内容（Markdown 格式） |

#### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_file_table_file_id` | `file_id` | 普通索引 |

#### 说明

- 与 `files` 表是 **1:N** 关系（一个文件可有多个表格）
- 复合主键：`file_id` + `table_index`
- `table_content` 为 Markdown 表格格式

#### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "table_index": 0,
  "total_table": 3,
  "table_name": "合并资产负债表",
  "table_content": "| 项目 | 期末余额 | 期初余额 |\n|------|----------|----------|\n| 货币资金 | 1,234,567 | 987,654 |\n| 应收账款 | 456,789 | 321,456 |"
}
```

---

### 2.4 file_chunk - 文件分块表

存储文件文本分块，用于检索和向量化。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `chunk_id` | VARCHAR(64) | PK, NOT NULL | - | 分块唯一 ID |
| `chunk_index` | INT | - | 0 | 分块序号（从 0 开始） |
| `total_chunks` | INT | - | 0 | 该文件的分块总数 |
| `chunk_content` | TEXT | NOT NULL | - | 分块文本内容 |

#### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_file_chunk_file_id` | `file_id` | 普通索引 |

#### 说明

- 与 `files` 表是 **1:N** 关系
- 复合主键：`file_id` + `chunk_id`
- 分块大小通常为 500-1000 字符，有重叠
- `chunk_id` 格式通常为 `{file_id}_{chunk_index}`

#### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "chunk_id": "a1b2c3d4e5f6_0",
  "chunk_index": 0,
  "total_chunks": 15,
  "chunk_content": "# 1 公司简介\n\n某某科技有限公司（以下简称"公司"）成立于2010年，是一家专注于人工智能技术研发的高新技术企业..."
}
```

---

## 3. 配置相关表

### 3.1 extraction_field - 字段提取配置表

定义需要从文档中提取的字段及其提取方式。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `field_id` | VARCHAR(100) | PK, NOT NULL | - | 字段唯一标识 |
| `field_name` | VARCHAR(200) | NOT NULL | - | 字段显示名称 |
| `source_type` | ENUM | NOT NULL | - | 数据源类型 |
| `enabled` | TINYINT | - | 1 | 是否启用（1=启用, 0=禁用） |
| `priority` | INT | - | 0 | 执行优先级（越小越优先） |
| `created_at` | DATETIME | - | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 更新时间（自动更新） |
| `table_name_pattern` | VARCHAR(500) | NULLABLE | NULL | 【表格类】表格名称匹配模式 |
| `table_match_type` | ENUM | NULLABLE | NULL | 【表格类】表格匹配方式 |
| `table_extract_prompt` | TEXT | NULLABLE | NULL | 【表格类】LLM 提取提示词 |
| `search_type` | ENUM | NULLABLE | NULL | 【文本类】检索方式 |
| `search_config` | JSON | NULLABLE | NULL | 【文本类】检索配置参数 |
| `text_extract_prompt` | TEXT | NULLABLE | NULL | 【文本类】LLM 提取提示词 |

#### 枚举值说明

**source_type（数据源类型）**

| 值 | 说明 |
|----|------|
| `table` | 从表格中提取 |
| `text` | 从文本中提取 |

**table_match_type（表格匹配方式）**

| 值 | 说明 |
|----|------|
| `exact` | 精确匹配 |
| `fuzzy` | 模糊匹配（相似度≥80%） |
| `contains` | 包含匹配 |
| `llm` | LLM 语义匹配 |

**search_type（文本检索方式）**

| 值 | 说明 |
|----|------|
| `context` | 上下文检索 |
| `section` | 章节检索 |
| `rule` | 规则检索 |
| `chunk_db` | 数据库分块检索 |
| `vector_db` | 向量数据库检索 |

#### search_config JSON 结构

根据 `search_type` 不同，结构有所差异：

**context 模式**
```json
{
  "keywords": ["关键词1", "关键词2"],
  "context_before": 200,
  "context_after": 200,
  "max_results": 5,
  "sort_order": "asc"
}
```

**section 模式**
```json
{
  "section_pattern": "章节标题",
  "section_match_type": "contains",
  "threshold": 0.8,
  "max_results": 3
}
```

**rule 模式**
```json
{
  "keywords": ["关键词"],
  "stop_words": ["\n", "。"],
  "direction": "forward",
  "min_length": 2,
  "max_length": 200,
  "max_results": 5
}
```

**chunk_db 模式**
```json
{
  "keywords": ["关键词"],
  "max_results": 10,
  "sort_order": "asc"
}
```

**vector_db 模式**
```json
{
  "query_text": "查询文本",
  "top_k": 5,
  "score_threshold": 0.5
}
```

#### 示例数据

**表格类字段**
```json
{
  "field_id": "total_revenue",
  "field_name": "营业总收入",
  "source_type": "table",
  "enabled": 1,
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取营业总收入金额。",
  "search_type": null,
  "search_config": null,
  "text_extract_prompt": null
}
```

**文本类字段**
```json
{
  "field_id": "company_name",
  "field_name": "公司名称",
  "source_type": "text",
  "enabled": 1,
  "priority": 0,
  "table_name_pattern": null,
  "table_match_type": null,
  "table_extract_prompt": null,
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "企业名称"],
    "context_before": 50,
    "context_after": 100
  },
  "text_extract_prompt": "从以下内容提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>企业名称</search_result>"
}
```

---

### 3.2 analysis_rule - 逻辑分析规则表

定义基于提取字段进行二次计算或判断的规则。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `rule_id` | VARCHAR(100) | PK, NOT NULL | - | 规则唯一标识 |
| `rule_name` | VARCHAR(200) | NOT NULL | - | 规则显示名称 |
| `rule_type` | ENUM | NOT NULL | - | 规则类型 |
| `expression` | TEXT | NOT NULL | - | 表达式/提示词 |
| `depend_fields` | JSON | NULLABLE | NULL | 依赖的字段 ID 列表 |
| `enabled` | TINYINT | - | 1 | 是否启用 |
| `priority` | INT | - | 0 | 执行优先级 |
| `created_at` | DATETIME | - | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 更新时间 |

#### 枚举值说明

**rule_type（规则类型）**

| 值 | 说明 | expression 用途 |
|----|------|-----------------|
| `judge` | 判断类 | 发送给 LLM 进行判断的完整提示词 |
| `calc` | 计算类 | 数学表达式（支持 +、-、*、/、()） |

#### depend_fields JSON 结构

字符串数组，列出该规则依赖的所有 `field_id`：

```json
["total_revenue", "net_profit", "total_assets"]
```

#### 示例数据

**判断类规则**
```json
{
  "rule_id": "is_profitable",
  "rule_name": "是否盈利",
  "rule_type": "judge",
  "expression": "公司净利润为 <field_result>net_profit</field_result> 元。\n\n请判断该公司是否处于盈利状态？",
  "depend_fields": ["net_profit"],
  "enabled": 1,
  "priority": 0
}
```

**计算类规则**
```json
{
  "rule_id": "profit_margin",
  "rule_name": "净利润率",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"],
  "enabled": 1,
  "priority": 1
}
```

---

## 4. 结果相关表

### 4.1 extraction_result - 提取结果表

存储每个文件的字段提取结果。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `field_id` | VARCHAR(100) | PK, NOT NULL | - | 字段 ID（关联 extraction_field） |
| `extracted_value` | TEXT | - | '' | 提取的值 |
| `reason` | TEXT | NULLABLE | NULL | 提取理由/依据（LLM 返回） |

#### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_extraction_result_file_id` | `file_id` | 普通索引 |

#### 说明

- 复合主键：`file_id` + `field_id`
- 每个文件的每个字段只有一条记录
- 提取失败时 `extracted_value` 为空字符串

#### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "field_id": "total_revenue",
  "extracted_value": "150000000",
  "reason": "从合并利润表第3行「营业总收入」列提取，金额为1.5亿元"
}
```

---

### 4.2 analysis_result - 分析结果表

存储每个文件的逻辑分析结果。

#### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `rule_id` | VARCHAR(100) | PK, NOT NULL | - | 规则 ID（关联 analysis_rule） |
| `result_value` | VARCHAR(500) | - | '' | 分析结果值 |
| `input_values` | JSON | NULLABLE | NULL | 输入字段值快照 |
| `reason` | TEXT | NULLABLE | NULL | 分析理由/依据 |

#### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_analysis_result_file_id` | `file_id` | 普通索引 |

#### 说明

- 复合主键：`file_id` + `rule_id`
- `result_value` 对于 judge 类型为 `"true"` 或 `"false"`
- `result_value` 对于 calc 类型为计算结果（保留2位小数）
- `input_values` 记录分析时使用的字段值，便于追溯

#### input_values JSON 结构

```json
{
  "net_profit": "5000000",
  "total_revenue": "150000000"
}
```

#### 示例数据

**判断类结果**
```json
{
  "file_id": "a1b2c3d4e5f6",
  "rule_id": "is_profitable",
  "result_value": "true",
  "input_values": {
    "net_profit": "5000000"
  },
  "reason": "净利润5000000元大于0，公司处于盈利状态"
}
```

**计算类结果**
```json
{
  "file_id": "a1b2c3d4e5f6",
  "rule_id": "profit_margin",
  "result_value": "3.33",
  "input_values": {
    "net_profit": "5000000",
    "total_revenue": "150000000"
  },
  "reason": "计算公式: 5000000 / 150000000 * 100 = 3.33"
}
```

---

## 5. 向量数据库 (Milvus)

除 MySQL 外，系统还使用 Milvus 存储文本分块的向量表示。

### 5.1 Collection 结构

**Collection 名称**: `file_chunks`（可配置）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | INT64 | 自增主键 |
| `file_id` | VARCHAR(64) | 文件 ID |
| `chunk_id` | VARCHAR(64) | 分块 ID |
| `chunk_index` | INT32 | 分块序号 |
| `embedding` | FLOAT_VECTOR(1536) | 向量表示（维度取决于嵌入模型） |

### 5.2 索引配置

- **索引类型**: IVF_FLAT / HNSW
- **度量方式**: L2（欧氏距离）
- **nlist**: 1024（IVF_FLAT）

---

## 6. 建表 SQL

```sql
-- 创建数据库
CREATE DATABASE IF NOT EXISTS wanz_parse
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE wanz_parse;

-- 1. files 表
CREATE TABLE IF NOT EXISTS files (
  file_id VARCHAR(64) PRIMARY KEY,
  file_name VARCHAR(512) NOT NULL,
  file_size BIGINT DEFAULT 0,
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  start_parsing_time DATETIME NULL,
  end_parsing_time DATETIME NULL,
  start_chunking_time DATETIME NULL,
  end_chunking_time DATETIME NULL,
  start_embedding_time DATETIME NULL,
  end_embedding_time DATETIME NULL,
  end_extracting_time DATETIME NULL,
  end_analyzing_time DATETIME NULL,
  progress VARCHAR(32) DEFAULT 'parsing',
  error TEXT NULL,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. file_content 表
CREATE TABLE IF NOT EXISTS file_content (
  file_id VARCHAR(64) PRIMARY KEY,
  file_content LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. file_table 表
CREATE TABLE IF NOT EXISTS file_table (
  file_id VARCHAR(64) NOT NULL,
  table_index INT NOT NULL,
  total_table INT DEFAULT 0,
  table_name VARCHAR(500) DEFAULT '',
  table_content LONGTEXT NOT NULL,
  PRIMARY KEY (file_id, table_index),
  INDEX ix_file_table_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. file_chunk 表
CREATE TABLE IF NOT EXISTS file_chunk (
  file_id VARCHAR(64) NOT NULL,
  chunk_id VARCHAR(64) NOT NULL,
  chunk_index INT DEFAULT 0,
  total_chunks INT DEFAULT 0,
  chunk_content TEXT NOT NULL,
  PRIMARY KEY (file_id, chunk_id),
  INDEX ix_file_chunk_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. extraction_field 表
CREATE TABLE IF NOT EXISTS extraction_field (
  field_id VARCHAR(100) PRIMARY KEY,
  field_name VARCHAR(200) NOT NULL,
  source_type ENUM('table', 'text') NOT NULL,
  enabled TINYINT DEFAULT 1,
  priority INT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  table_name_pattern VARCHAR(500) NULL,
  table_match_type ENUM('exact', 'fuzzy', 'contains', 'llm') NULL,
  table_extract_prompt TEXT NULL,
  search_type ENUM('context', 'section', 'rule', 'chunk_db', 'vector_db') NULL,
  search_config JSON NULL,
  text_extract_prompt TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. analysis_rule 表
CREATE TABLE IF NOT EXISTS analysis_rule (
  rule_id VARCHAR(100) PRIMARY KEY,
  rule_name VARCHAR(200) NOT NULL,
  rule_type ENUM('judge', 'calc') NOT NULL,
  expression TEXT NOT NULL,
  depend_fields JSON NULL,
  enabled TINYINT DEFAULT 1,
  priority INT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7. extraction_result 表
CREATE TABLE IF NOT EXISTS extraction_result (
  file_id VARCHAR(64) NOT NULL,
  field_id VARCHAR(100) NOT NULL,
  extracted_value TEXT DEFAULT '',
  reason TEXT NULL,
  PRIMARY KEY (file_id, field_id),
  INDEX ix_extraction_result_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 8. analysis_result 表
CREATE TABLE IF NOT EXISTS analysis_result (
  file_id VARCHAR(64) NOT NULL,
  rule_id VARCHAR(100) NOT NULL,
  result_value VARCHAR(500) DEFAULT '',
  input_values JSON NULL,
  reason TEXT NULL,
  PRIMARY KEY (file_id, rule_id),
  INDEX ix_analysis_result_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

## 附录：字段类型速查

| MySQL 类型 | 存储范围 | 用途 |
|------------|----------|------|
| VARCHAR(64) | 最多 64 字符 | ID 类字段 |
| VARCHAR(100) | 最多 100 字符 | 配置 ID |
| VARCHAR(200) | 最多 200 字符 | 名称字段 |
| VARCHAR(500) | 最多 500 字符 | 模式/结果值 |
| VARCHAR(512) | 最多 512 字符 | 文件名 |
| TEXT | 最多 64KB | 中等文本 |
| LONGTEXT | 最多 4GB | 大文本（全文、表格内容） |
| JSON | 变长 | 结构化配置 |
| TINYINT | 0-255 | 布尔标志 |
| INT | ±21亿 | 序号、计数 |
| BIGINT | ±922亿亿 | 文件大小 |
| DATETIME | 日期时间 | 时间戳 |
| ENUM | 枚举值 | 固定选项 |

---

*文档版本: 1.0.0 | 最后更新: 2025-01*
