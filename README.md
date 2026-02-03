# wanz_prase2_001

皖资二期项目

# SSE 事件流程

| 事件 | 说明 |
|------|------|
| `parsing_start` | 开始 MinerU 解析 |
| `parsing` | MinerU 解析完成 |
| `content_saved` | MD 内容已存储 |
| `tables_extracted` | 表格提取完成 |
| `chunking_start` | 开始分块 |
| `chunking` | 分块完成 |
| `chunks_saving` | 开始存储分块 |
| `chunks_saved` | 分块已存储 |
| `embedding_start` | 开始向量化 |
| `embedding` | 向量化完成 |
| `milvus_submitting` | 开始提交 Milvus |
| `milvus_submitted` | Milvus 提交完成 |
| `tasks_loading` | 开始获取任务 |
| `tasks_loaded` | 任务获取完成 |
| `extraction_start` | 开始关键词提取 |
| `extraction` | 关键词提取完成 |
| `analysis_start` | 开始逻辑分析 |
| `analysis` | 逻辑分析完成 |
| `complete` | 全部完成 |
| `error` | 发生错误 |

---

# 字段提取流程详细分析

## 1. 总体流程概览（调用链）

```
run_extraction(file_id, session)
    │
    ├── 获取所有 enabled=1 的 extraction_field，按 priority 排序
    │
    └── 遍历每个 field:
            │
            ├── source_type == "table"
            │       └── extract_table_field(file_id, field, session)
            │               ├── 查询 file_table 获取所有表格
            │               ├── 按 table_match_type 匹配表格
            │               ├── 构建 LLM prompt（table_extract_prompt + <search_result>）
            │               ├── 调用 chat_completion()
            │               └── parse_llm_json_response() 解析结果
            │
            └── source_type == "text"
                    └── extract_text_field(file_id, field, session)
                            ├── 查询 file_content 获取全文
                            ├── 根据 search_type 调用对应检索方法:
                            │       ├── search_context()    → 关键词上下文检索
                            │       ├── search_section()    → 章节检索
                            │       ├── search_rule()       → 规则检索
                            │       ├── search_chunk_db()   → 关系数据库检索
                            │       └── search_vector_db()  → 向量数据库检索
                            ├── 拼接检索结果
                            ├── 构建 LLM prompt（text_extract_prompt + <search_result>）
                            ├── 调用 chat_completion()
                            └── parse_llm_json_response() 解析结果
```

---

## 2. 入口函数 `run_extraction()` 逐行分析

**文件**: `service/extraction_service.py:532-607`

```python
async def run_extraction(file_id: str, session: AsyncSession) -> None:
    """执行文件的完整字段提取流程。"""

    # 1. 日志记录开始
    logger.info("开始字段提取: {}", file_id)

    # 2. 查询所有启用的字段配置，按优先级排序
    stmt = (
        select(ExtractionField)
        .where(ExtractionField.enabled == 1)
        .order_by(ExtractionField.priority)
    )
    result = await session.execute(stmt)
    fields = result.scalars().all()

    # 3. 遍历每个字段执行提取
    for field in fields:
        try:
            # 4. 根据 source_type 分发到不同的提取函数
            if field.source_type == "table":
                extracted_value, reason = await extract_table_field(file_id, field, session)
            else:
                extracted_value, reason = await extract_text_field(file_id, field, session)

            # 5. 查询是否已有结果记录（用于 upsert）
            stmt = select(ExtractionResult).where(
                ExtractionResult.file_id == file_id,
                ExtractionResult.field_id == field.field_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            # 6. 更新或插入结果
            if existing:
                existing.extracted_value = extracted_value
                existing.reason = reason
            else:
                extraction_result = ExtractionResult(
                    file_id=file_id,
                    field_id=field.field_id,
                    extracted_value=extracted_value,
                    reason=reason,
                )
                session.add(extraction_result)

            await session.commit()

        except Exception as e:
            # 7. 单字段失败时保存空值，继续处理下一个字段
            logger.error("字段提取失败: field_id={}, error={}", field.field_id, e)
            # ... 保存空值逻辑 ...
```

**关键设计点**：
- 单字段失败不影响其他字段，保证健壮性
- 支持 upsert 语义，可重复执行
- 按 priority 排序确保字段提取顺序可控

---

## 3. 表格类提取 `extract_table_field()` 完整分析

**文件**: `service/extraction_service.py:392-462`

### 3.1 流程步骤

```python
async def extract_table_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> Tuple[str, str]:

    # 1. 查询文件的所有表格
    stmt = select(FileTable).where(FileTable.file_id == file_id)
    result = await session.execute(stmt)
    tables = result.scalars().all()

    if not tables:
        return "", ""

    # 2. 表格匹配（4 种方式）
    match_type = field.table_match_type or "contains"  # 默认 contains
    pattern = field.table_name_pattern or ""

    matched_tables = []
    for table in tables:
        matched = False

        if match_type == "exact":
            matched = table.table_name == pattern
        elif match_type == "fuzzy":
            ratio = SequenceMatcher(None, table.table_name, pattern).ratio()
            matched = ratio >= 0.8  # 相似度阈值 0.8
        elif match_type == "contains":
            matched = pattern.lower() in table.table_name.lower()
        elif match_type == "llm":
            # 使用 LLM 判断是否匹配
            prompt = f"判断以下表格名称是否与查询匹配。\n\n查询: {pattern}\n表格名称: {table.table_name}\n\n只回答'是'或'否'。"
            response = await chat_completion(prompt)
            matched = "是" in response

        if matched:
            matched_tables.append(table)

    # 3. 逐个表格发送 LLM，取第一个非空结果
    prompt_template = field.table_extract_prompt or "从以下表格中提取信息：\n<search_result>\n请提取相关字段值。"

    for table in matched_tables:
        search_result_text = f"表格名称: {table.table_name}\n{table.table_content}"
        llm_input = prompt_template.replace("<search_result>", search_result_text)
        llm_input += JSON_OUTPUT_INSTRUCTION  # 附加 JSON 输出格式要求

        response = await chat_completion(llm_input)
        extracted_value, reason = parse_llm_json_response(response)

        if extracted_value:  # 非空则返回
            return extracted_value, reason

    return "", ""
```

### 3.2 四种表格匹配方式

| 匹配方式 | 说明 | 配置字段 |
|---------|------|---------|
| `exact` | 完全匹配表格名称 | `table_name_pattern` |
| `fuzzy` | 模糊匹配，相似度 ≥ 0.8 | `table_name_pattern` |
| `contains` | 包含匹配（不区分大小写） | `table_name_pattern` |
| `llm` | 使用 LLM 判断是否匹配 | `table_name_pattern` |

---

## 4. 文本类提取 `extract_text_field()` 完整分析

**文件**: `service/extraction_service.py:465-529`

### 4.1 流程步骤

```python
async def extract_text_field(
    file_id: str, field: ExtractionField, session: AsyncSession
) -> Tuple[str, str]:

    # 1. 获取文件内容
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content:
        return "", ""

    content = file_content.file_content
    search_type = field.search_type or "context"  # 默认 context
    search_config = field.search_config or {}

    # 2. 根据 search_type 调用对应检索方法
    search_results = []
    if search_type == "context":
        search_results = await search_context(content, search_config)
    elif search_type == "section":
        search_results = await search_section(content, search_config)
    elif search_type == "rule":
        search_results = await search_rule(content, search_config)
    elif search_type == "chunk_db":
        search_results = await search_chunk_db(file_id, search_config, session)
    elif search_type == "vector_db":
        search_results = await search_vector_db(file_id, search_config)

    if not search_results:
        return "", ""

    # 3. 拼接检索结果（不同检索方式提取不同字段）
    if search_type == "context":
        results_text = "\n---\n".join([r["context"] for r in search_results])
    elif search_type == "section":
        results_text = "\n---\n".join([r["content"] for r in search_results])
    elif search_type == "rule":
        results_text = "\n---\n".join([r["extracted_text"] for r in search_results])
    elif search_type in ("chunk_db", "vector_db"):
        results_text = "\n---\n".join([r["chunk_content"] for r in search_results])

    # 4. 构建 LLM 输入
    prompt_template = field.text_extract_prompt or "从以下内容中提取信息：\n<search_result>\n请提取相关字段值。"
    llm_input = prompt_template.replace("<search_result>", results_text)
    llm_input += JSON_OUTPUT_INSTRUCTION

    # 5. 调用 LLM 提取
    response = await chat_completion(llm_input)
    return parse_llm_json_response(response)
```

---

## 5. 五种检索方法详解

### 5.1 `search_context` - 关键词上下文检索

**文件**: `service/extraction_service.py:105-145`

根据关键词在全文中定位并提取上下文。

**search_config 配置格式**:
```json
{
    "keywords": ["关键词1", "关键词2"],
    "context_before": 200,    // 关键词前取的字节数（默认 200）
    "context_after": 200,     // 关键词后取的字节数（默认 200）
    "max_results": 5,         // 最大返回条数（默认 5）
    "sort_order": "asc"       // 排序方式 asc/desc（默认 asc，按出现位置）
}
```

**返回结果格式**:
```json
[
    {"keyword": "关键词", "position": 100, "context": "...上下文文本..."}
]
```

---

### 5.2 `search_section` - 章节检索

**文件**: `service/extraction_service.py:148-207`

匹配 Markdown 章节标题并返回章节内容。

**search_config 配置格式**:
```json
{
    "section_pattern": "项目概况",
    "section_match_type": "contains",  // 或 match_type
    "threshold": 0.8,         // 模糊匹配阈值（默认 0.8）
    "max_results": 3,         // 最大返回条数（默认 3）
    "sort_order": "asc"       // 排序方式（默认 asc，按章节顺序）
}
```

**章节匹配方式**:
| 匹配方式 | 说明 |
|---------|------|
| `exact` | 完全匹配章节标题 |
| `fuzzy` | 模糊匹配，相似度 ≥ threshold |
| `contains` | 包含匹配（不区分大小写） |
| `llm` | 使用 LLM 判断是否匹配 |

**返回结果格式**:
```json
[
    {"section_number": "1.1", "section_title": "项目概况", "section_index": 0, "content": "...章节内容..."}
]
```

**章节解析正则**: `^#\s+([\d.]+)\s+(.+?)(?:\s+\d+)?\s*$`
- 匹配格式：`# 1.1 章节标题` 或 `# 1.1 章节标题 123`（末尾数字为页码，会被忽略）

---

### 5.3 `search_rule` - 规则检索

**文件**: `service/extraction_service.py:210-283`

关键词 + 停用词边界扩展，从关键词位置向前/后扩展到停用词为止。

**search_config 配置格式**:
```json
{
    "keywords": ["关键词1", "关键词2"],
    "stop_words": ["#", "##", "###", "\n\n", "\n", "。", ".", "；", ";"],  // 默认值
    "direction": "forward",   // 扩展方向：forward/backward/both
    "min_length": 2,          // 最小提取长度（默认 2）
    "max_length": 200,        // 最大提取长度（默认 200）
    "max_results": 5,         // 最大返回条数（默认 5）
    "sort_order": "asc"       // 排序方式（默认 asc）
}
```

**扩展方向说明**:
- `forward`: 从关键词向后扩展到停用词
- `backward`: 从关键词向前扩展到停用词
- `both`: 双向扩展

**返回结果格式**:
```json
[
    {"keyword": "关键词", "position": 100, "extracted_text": "...提取的文本..."}
]
```

---

### 5.4 `search_chunk_db` - 关系数据库检索

**文件**: `service/extraction_service.py:286-336`

从 `file_chunk` 表按关键词过滤分块。

**search_config 配置格式**:
```json
{
    "keyword_filter": "关键词",       // 单个字符串
    // 或
    "keywords": ["关键词1", "关键词2"], // 列表形式
    "max_results": 10,                // 或 top_k（默认 10）
    "sort_order": "asc"               // 排序方式（默认 asc，按 chunk_index）
}
```

**返回结果格式**:
```json
[
    {"chunk_id": "xxx", "chunk_index": 0, "chunk_content": "...分块内容..."}
]
```

---

### 5.5 `search_vector_db` - 向量数据库检索

**文件**: `service/extraction_service.py:339-380`

将查询文本向量化后在 Milvus 中检索相似分块。

**search_config 配置格式**:
```json
{
    "query_text": "查询文本",
    "top_k": 5,                 // 返回条数（默认 5）
    "score_threshold": 0.8     // 分数阈值（可选）
}
```

**内部流程**:
1. 调用 `get_embeddings([query_text])` 获取查询向量
2. 调用 `MilvusClient.search()` 检索
3. 按 file_id 过滤，返回相似分块

**返回结果格式**:
```json
[
    {"chunk_id": "xxx", "chunk_index": 0, "chunk_content": "...", "score": 0.95}
]
```

---

## 6. LLM 客户端调用细节

**文件**: `utils/llm_client.py`

### 6.1 `chat_completion()` - 聊天补全

```python
async def chat_completion(
    prompt: str,
    *,
    base_url: Optional[str] = None,    # API 地址，默认从配置读取
    model: Optional[str] = None,        # 模型名称，默认从配置读取
    api_key: Optional[str] = None,      # API Key，默认从配置读取
    timeout: Optional[int] = None,      # 超时秒数，默认从配置读取
    messages: Optional[List[Dict[str, str]]] = None,  # 自定义 messages
) -> str:
```

**配置来源** (`configs/config.yaml` → `extraction`):
```yaml
extraction:
  llm_base_url: "http://localhost:8000/v1"
  llm_model: "qwen-7b"
  llm_api_key: ""
  llm_timeout: 60
```

**请求格式**:
```
POST {base_url}/chat/completions
Headers:
  Content-Type: application/json
  Authorization: Bearer {api_key}
Body:
  {
    "model": "qwen-7b",
    "messages": [{"role": "user", "content": "..."}]
  }
```

### 6.2 `get_embeddings()` - 向量化

```python
async def get_embeddings(
    texts: List[str],
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: Optional[int] = None,
    timeout: Optional[int] = None,
    max_retries: int = 3,
) -> List[List[float]]:
```

**配置来源** (`configs/config.yaml` → `embedding`):
```yaml
embedding:
  base_url: "http://localhost:8000/v1"
  model_name: "bge-large-zh"
  api_key: ""
  batch_size: 32
  timeout: 60
```

**特性**:
- 支持批量处理（按 batch_size 分批）
- 自动重试（指数退避：2^attempt 秒）
- 最大重试 3 次

---

## 7. LLM 响应解析策略

**文件**: `service/extraction_service.py:23-60`

### 7.1 `parse_llm_json_response()` 解析流程

```python
def parse_llm_json_response(response: str) -> Tuple[str, str]:
    """解析 LLM 返回的 JSON 响应，提取 value 和 reason。"""

    response = response.strip()

    # 策略 1: 提取 ```json ... ``` 代码块
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        response = json_match.group(1)

    # 策略 2: 直接解析 JSON
    try:
        data = json.loads(response)
        return str(data.get("value", "")).strip(), str(data.get("reason", "")).strip()
    except json.JSONDecodeError:
        pass

    # 策略 3: 正则提取 JSON 对象
    json_obj_match = re.search(r"\{[^{}]*\"value\"[^{}]*\}", response, re.DOTALL)
    if json_obj_match:
        try:
            data = json.loads(json_obj_match.group())
            return str(data.get("value", "")).strip(), str(data.get("reason", "")).strip()
        except json.JSONDecodeError:
            pass

    # 兜底: 返回原始响应作为 value
    return response.strip(), ""
```

### 7.2 JSON 输出格式要求

每次调用 LLM 时都会附加以下指令：

```python
JSON_OUTPUT_INSTRUCTION = """

请以 JSON 格式返回结果，包含 value（提取的值）和 reason（提取理由/依据）两个字段：
{"value": "提取的值", "reason": "说明从哪里提取、为什么这样提取"}"""
```

---

## 8. 数据库表结构

### 8.1 `extraction_field` - 字段配置表

| 字段 | 类型 | 说明 |
|-----|------|------|
| `field_id` | VARCHAR(100) | 主键 |
| `field_name` | VARCHAR(200) | 字段名称 |
| `source_type` | ENUM('table','text') | 来源类型 |
| `enabled` | TINYINT | 是否启用 (1=启用) |
| `priority` | INT | 优先级（数值小优先） |
| `table_name_pattern` | VARCHAR(500) | 表格名称模式 |
| `table_match_type` | ENUM | 表格匹配方式 |
| `table_extract_prompt` | TEXT | 表格提取 prompt |
| `search_type` | ENUM | 文本检索类型 |
| `search_config` | JSON | 检索配置 |
| `text_extract_prompt` | TEXT | 文本提取 prompt |

### 8.2 `extraction_result` - 提取结果表

| 字段 | 类型 | 说明 |
|-----|------|------|
| `file_id` | VARCHAR(64) | 文件 ID（联合主键） |
| `field_id` | VARCHAR(100) | 字段 ID（联合主键） |
| `extracted_value` | TEXT | 提取的值 |
| `reason` | TEXT | 提取理由 |

### 8.3 相关表

- `file_content`: 存储文件全文（`file_id`, `file_content`）
- `file_table`: 存储解析出的表格（`file_id`, `table_index`, `table_name`, `table_content`）
- `file_chunk`: 存储文本分块（`file_id`, `chunk_id`, `chunk_index`, `chunk_content`）

---

## 9. 提示词拼接完整示例

### 9.1 表格类提取示例

**配置**:
```json
{
    "field_id": "project_name",
    "source_type": "table",
    "table_name_pattern": "项目信息",
    "table_match_type": "contains",
    "table_extract_prompt": "从以下表格中提取项目名称：\n<search_result>\n请仅返回项目名称。"
}
```

**实际 LLM 输入**:
```
从以下表格中提取项目名称：
表格名称: 项目基本信息表
| 字段 | 值 |
|-----|-----|
| 项目名称 | XX市城市道路改造工程 |
| 建设单位 | XX市住建局 |
请仅返回项目名称。

请以 JSON 格式返回结果，包含 value（提取的值）和 reason（提取理由/依据）两个字段：
{"value": "提取的值", "reason": "说明从哪里提取、为什么这样提取"}
```

### 9.2 文本类提取示例（context 检索）

**配置**:
```json
{
    "field_id": "total_investment",
    "source_type": "text",
    "search_type": "context",
    "search_config": {
        "keywords": ["总投资", "投资估算"],
        "context_before": 100,
        "context_after": 200,
        "max_results": 3
    },
    "text_extract_prompt": "从以下内容中提取项目总投资金额：\n<search_result>\n请返回具体金额数值。"
}
```

**实际 LLM 输入**:
```
从以下内容中提取项目总投资金额：
...前文内容...总投资约 3.5 亿元，其中建安费用 2.8 亿元...后文内容...
---
...另一处匹配内容...
请返回具体金额数值。

请以 JSON 格式返回结果，包含 value（提取的值）和 reason（提取理由/依据）两个字段：
{"value": "提取的值", "reason": "说明从哪里提取、为什么这样提取"}
```

---

## 10. 调试接口说明

**端点**: `POST /extraction/test`

**文件**: `blue_print/extraction_router.py:131-231`

### 10.1 请求格式

**模式 1: 使用已保存的字段配置**
```json
{
    "file_id": "xxx",
    "field_id": "project_name"
}
```

**模式 2: 使用临时配置**
```json
{
    "file_id": "xxx",
    "config": {
        "field_name": "测试字段",
        "source_type": "text",
        "search_type": "context",
        "search_config": {
            "keywords": ["总投资"],
            "context_before": 100,
            "context_after": 200
        },
        "text_extract_prompt": "提取总投资金额：\n<search_result>"
    }
}
```

### 10.2 响应格式

```json
{
    "code": 0,
    "message": "success",
    "data": {
        "search_results": [
            {"keyword": "总投资", "position": 1234, "context": "..."}
        ],
        "llm_input": "提取总投资金额：\n...",
        "llm_output": "{\"value\": \"3.5亿元\", \"reason\": \"...\"}",
        "extracted_value": "3.5亿元",
        "reason": "从文档第1234字符处提取"
    }
}
```

### 10.3 调试流程

1. 调用 `/extraction/test` 传入 `config` 测试配置效果
2. 查看 `search_results` 确认检索是否准确
3. 查看 `llm_input` 确认 prompt 拼接是否正确
4. 查看 `extracted_value` 和 `reason` 确认提取结果
5. 调整 `search_config` 或 `text_extract_prompt` 优化效果
6. 效果满意后调用 `POST /extraction/fields` 保存配置

---

# 完整 Pipeline 流程详细分析

## 1. Pipeline 总体架构

### 1.1 完整调用链

```
POST /file/parse (文件上传入口)
    │
    ├── 文件大小检查 + 文件ID生成
    │
    └── 根据 mode 选择执行方式
        ├── async: 后台异步执行
        ├── stream: SSE 实时流
        └── sync: 同步阻塞执行
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 1: MinerU 解析                                         │
├─────────────────────────────────────────────────────────────┤
│ parse_file() → Markdown 内容                                │
│   ├── parse_pdf() → 调用 MinerU API                        │
│   ├── save_file_content() → FileContent 表                 │
│   ├── parse_tables() → 提取 HTML 表格                      │
│   └── save_tables() → FileTable 表                         │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 2: 分块                                                │
├─────────────────────────────────────────────────────────────┤
│ chunk_content() → 分块列表                                  │
│   ├── split_text() → 递归字符分割                          │
│   ├── 表格作为独立块（前缀拼接 table_name）                │
│   └── save_chunks() → FileChunk 表                         │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 3: 向量化 + Milvus 存储                                │
├─────────────────────────────────────────────────────────────┤
│ embed_chunks() → 向量列表                                   │
│   └── get_embeddings() → 批量调用 Embedding API            │
│ submit_to_milvus() → 向量数据库                             │
│   └── MilvusClient.insert() → Milvus Collection            │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 4: 字段提取                                            │
├─────────────────────────────────────────────────────────────┤
│ run_extraction() → 提取结果                                 │
│   ├── extract_table_field() → 表格类提取                   │
│   └── extract_text_field() → 文本类提取                    │
│       └── ExtractionResult 表                              │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│ 阶段 5: 逻辑分析                                            │
├─────────────────────────────────────────────────────────────┤
│ run_analysis() → 分析结果                                   │
│   ├── execute_judge() → 判断类规则（LLM）                  │
│   └── execute_calc() → 计算类规则（numexpr）               │
│       └── AnalysisResult 表                                │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
        progress = "complete"
```

### 1.2 状态转移图

```
parsing → chunking → embedding → extracting → analyzing → complete
   ↓          ↓          ↓           ↓            ↓
parsing_  chunking_  embedding_  extracting_  analyzing_
failed    failed     failed      failed       failed
```

### 1.3 三种执行模式

| 模式 | 说明 | 返回值 |
|------|------|--------|
| `async` | 后台异步执行，立即返回 | `{"file_id": "xxx"}` |
| `stream` | SSE 流式输出，实时推送事件 | `StreamingResponse(event-stream)` |
| `sync` | 同步阻塞执行，完全完成后返回 | `{"file_id": "xxx"}` |

---

## 2. 解析服务详细分析

**文件**: `service/parse_service.py`, `service/mineru_client.py`

### 2.1 入口函数 `parse_file()`

```python
async def parse_file(
    file_path: str,              # 文件路径/名称
    file_content_bytes: bytes,   # 文件二进制内容
    file_id: str,                # 文件 ID
    session: AsyncSession,       # 数据库会话
) -> str:                        # 返回 Markdown 内容
```

**执行流程**:
1. 更新 File 表：`progress="parsing"`, `start_parsing_time=now()`
2. 调用 `parse_pdf()` 向 MinerU 服务发送请求
3. 更新 File 表：`end_parsing_time=now()`
4. 异常时设置 `progress="parsing_failed"`, `error=str(e)`

### 2.2 MinerU API 调用 `parse_pdf()`

```python
async def parse_pdf(
    file_name: str,              # 文件名
    file_content: bytes,         # 文件二进制内容
    base_url: Optional[str],     # MinerU 服务地址
    timeout: Optional[int],      # 超时秒数
) -> str:                        # 返回 Markdown 内容
```

**HTTP 请求详情**:
```
POST {base_url}/file_parse
Content-Type: multipart/form-data

files: (filename, file_content, "application/pdf")
data:
  return_middle_json: "false"
  return_model_output: "false"
  return_md: "true"              # 核心：返回 Markdown
  return_images: "false"
  start_page_id: "0"
  end_page_id: "99999"           # 全部页面
  parse_method: "auto"
  lang_list: "ch"                # 中文
  backend: "vllm-async-engine"   # 推理引擎
```

**响应格式**:
```json
{
  "results": {
    "文件名(无后缀)": {
      "md_content": "解析后的 Markdown 文本"
    }
  }
}
```

### 2.3 表格提取 `parse_tables()`

```python
def parse_tables(content: str, file_id: str) -> List[Dict]:
```

**提取流程**:
1. 正则匹配：`r"<table>.*?</table>"` (DOTALL | IGNORECASE)
2. 对每个表格，提取表名：
   - 获取表格前的文本
   - 找最后一个 `\n\n` 的位置
   - 取最后一行作为表名
   - 移除 Markdown 标题符号 `^#+\s*`
   - 截断到 500 字符

**返回格式**:
```python
[
    {
        "file_id": "abc123...",
        "table_index": 1,          # 1-based
        "total_table": 3,
        "table_name": "表 1: 销售数据",
        "table_content": "<table>...</table>",
    },
    ...
]
```

### 2.4 MinerU 配置

```yaml
mineru:
  base_url: "http://124.222.125.54:7078"  # MinerU API 地址
  backend: "vllm-async-engine"            # 推理引擎
  queue_width: 1                          # 并发队列宽度
  parse_timeout: 1200                     # 超时秒数（20分钟）
  max_file_size: 104857600                # 最大文件 100MB
```

---

## 3. 分块服务详细分析

**文件**: `service/chunk_service.py`

### 3.1 入口函数 `chunk_content()`

```python
async def chunk_content(
    file_id: str,                # 文件 ID
    content: str,                # Markdown 全文
    tables: List[Dict],          # 表格信息列表
    session: AsyncSession,       # 数据库会话
) -> List[Dict]:                 # 返回分块列表
```

**分块策略（3 层）**:
```
第 1 层：表格识别
  ├── 识别所有 <table>...</table> 位置
  ├── 表格作为独立块保存
  └── 表格块前拼接 table_name 作为上下文

第 2 层：表格间文本分割
  ├── 处理表格前的文本
  ├── 处理表格后的文本
  └── 如无表格则对全文分割

第 3 层：递归字符分割（split_text）
  ├── 按分隔符优先级分割
  ├── 若单块超大则递归处理
  └── 添加块间重叠内容
```

### 3.2 核心算法 `split_text()`

```python
def split_text(
    text: str,                   # 待分割文本
    chunk_size: int,             # 目标块大小（字符数）
    chunk_overlap: int,          # 块间重叠大小（字符数）
    separators: List[str],       # 分隔符优先级列表
) -> List[str]:                  # 分块后的文本列表
```

**算法流程**:
```
1. 如果 len(text) ≤ chunk_size → 直接返回 [text]

2. 按分隔符优先级尝试分割：
   ["\n\n", "\n", "。", " "]

   对每个分隔符：
   a. 用该分隔符分割文本
   b. 贪心合并：
      - current_chunk 初始为空
      - 逐个添加分割段
      - 超过 chunk_size 时保存当前块，开始新块
      - 单个段 > chunk_size 时递归调用（降级到下一个分隔符）
   c. 添加重叠：
      - 每块前缀 = 上一块的最后 chunk_overlap 字符 + sep

3. 无分隔符可用时，强制按字符分割
```

**示例**:
```python
# 输入
text = "第一段内容\n\n第二段内容很长\n\n第三段"
chunk_size = 20
chunk_overlap = 5
separators = ["\n\n", "\n", "。", " "]

# 输出（添加重叠后）
[
    "第一段内容",
    "段内容\n\n第二段内容很长",
    "内容很长\n\n第三段"
]
```

### 3.3 返回数据结构

```python
[
    {
        "file_id": "abc123...",
        "chunk_id": "sha256_hash[:32]",  # 根据 file_id + chunk_index 生成
        "chunk_index": 0,                 # 0-based
        "total_chunks": 10,
        "chunk_content": "文本内容...",
    },
    ...
]
```

### 3.4 分块配置

```yaml
chunking:
  chunk_size: 512              # 目标块大小（字符数）
  chunk_overlap: 50            # 块间重叠（字符数）
  max_chunk_size: 2048         # 最大块大小（当前未使用）
  separators:                  # 分隔符优先级
    - "\n\n"                   # 1. 段落分隔（优先级最高）
    - "\n"                     # 2. 行分隔
    - "。"                     # 3. 中文句号
    - " "                      # 4. 空格（优先级最低）
```

---

## 4. 向量化服务详细分析

**文件**: `service/embedding_service.py`, `utils/milvus_client.py`

### 4.1 向量化函数 `embed_chunks()`

```python
async def embed_chunks(chunks: List[Dict]) -> List[List[float]]:
```

**执行流程**:
1. 提取所有 `chunk_content` → texts 列表
2. 调用 `get_embeddings(texts, ...)`
3. 返回与 chunks 等长的向量列表

### 4.2 批量向量化 `get_embeddings()`

```python
async def get_embeddings(
    texts: List[str],            # 待向量化文本列表
    base_url: Optional[str],     # API 服务地址
    model: Optional[str],        # 模型名称
    api_key: Optional[str],      # API 密钥
    batch_size: Optional[int],   # 批处理大小
    timeout: Optional[int],      # 请求超时秒数
    max_retries: int = 3,        # 最大重试次数
) -> List[List[float]]:          # 向量列表
```

**批处理实现**:
```python
for i in range(0, len(texts), batch_size):
    batch = texts[i : i + batch_size]

    for attempt in range(max_retries):
        try:
            resp = await client.post(
                f"{base_url}/embeddings",
                json={"model": model, "input": batch}
            )
            embeddings = [item["embedding"] for item in resp.json()["data"]]
            break
        except Exception:
            await asyncio.sleep(2 ** attempt)  # 指数退避
```

### 4.3 Milvus 客户端

**连接配置**:
```python
class MilvusClient:
    def connect(self):
        connections.connect(
            alias="default",
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
        )
```

**Collection Schema**:
```python
fields = [
    FieldSchema(name="chunk_id", dtype=VARCHAR, is_primary=True, max_length=64),
    FieldSchema(name="file_id", dtype=VARCHAR, max_length=64),
    FieldSchema(name="chunk_index", dtype=INT64),
    FieldSchema(name="total_chunks", dtype=INT64),
    FieldSchema(name="chunk_content", dtype=VARCHAR, max_length=65535),
    FieldSchema(name="embedding", dtype=FLOAT_VECTOR, dim=1024),
]
```

**索引配置**:
```python
index_params = {
    "index_type": "IVF_FLAT",    # 倒排索引
    "metric_type": "L2",          # 欧氏距离
    "params": {"nlist": 1024}     # 聚类数
}
```

**搜索方法**:
```python
def search(
    self,
    query_vector: List[float],   # 查询向量（1024 维）
    top_k: int = 10,             # 返回最相似的 k 条
    file_id: Optional[str],      # 按文件过滤
    score_threshold: Optional[float],  # 分数阈值
) -> List[Dict]:
    # 返回: [{"chunk_id", "file_id", "chunk_content", "score"}, ...]
```

### 4.4 向量化配置

```yaml
embedding:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model_name: "text-embedding-v4"
  api_key: "sk-xxx"
  embedding_dim: 1024            # 向量维度
  batch_size: 10                 # 每批处理文本数
  timeout: 60                    # 请求超时秒数
  retry_count: 3                 # 最大重试次数

milvus:
  host: "124.222.125.54"
  port: 7067
  user: "root"
  password: "Milvus"
  collection_name: "wanzi_prase2_001"
  index_type: "IVF_FLAT"
  metric_type: "L2"
  nlist: 1024
  search_topk: 10
```

---

## 5. 逻辑分析服务详细分析

**文件**: `service/analysis_service.py`, `blue_print/analysis_router.py`

### 5.1 入口函数 `run_analysis()`

```python
async def run_analysis(file_id: str, session: AsyncSession) -> None:
```

**执行流程**:
```
1. 获取所有 enabled=1 的 analysis_rule，按 priority 排序

2. 获取该文件的所有 ExtractionResult
   → 构建 {field_id: extracted_value} 映射

3. 遍历每条规则：
   a. 解析占位符 <field_result>field_id</field_result>
      → resolve_expression(rule.expression, field_values)

   b. 根据 rule_type 分支执行：
      ├── "judge" → execute_judge(resolved_expression)
      └── "calc"  → execute_calc(resolved_expression)

   c. 保存或更新 AnalysisResult

   d. 异常处理（保存空值继续）

4. 更新 File.progress = "complete"
```

### 5.2 判断类规则 `execute_judge()`

```python
async def execute_judge(resolved_expression: str) -> Tuple[str, str]:
    # 返回: (result, reason)
    # result: "true" 或 "false"
```

**LLM Prompt 格式**:
```
{resolved_expression}

请根据以上内容进行判断，以 JSON 格式返回结果：
{"result": "true 或 false", "reason": "判断理由/依据"}
```

**响应解析策略**:
1. 提取 `\`\`\`json {...}\`\`\`` 代码块
2. 直接解析 JSON
3. 正则提取 `{"result": ..., "reason": ...}`
4. 文本模糊匹配："true/是" → `"true"`，"false/否" → `"false"`

**规则示例**:
```yaml
rule_id: judge_growth
rule_name: 销售增长判断
rule_type: judge
expression: |
  基于以下数据进行判断：
  - 2023年销售额: <field_result>sales_2023</field_result>
  - 2024年销售额: <field_result>sales_2024</field_result>

  问题：2024年销售额相比2023年是否增长了10%以上？
depend_fields: ["sales_2023", "sales_2024"]
```

### 5.3 计算类规则 `execute_calc()`

```python
async def execute_calc(
    resolved_expression: str,    # 数学表达式
    precision: int = 2,          # 小数保留位数
) -> Tuple[str, str]:
    # 返回: (result_str, reason)
```

**执行流程**:
```
1. 清理表达式
   → 只保留: 0-9, +, -, *, /, (, ), ., e, E, 空格
   → 移除: 文字、符号、其他字符

2. 验证表达式有效性

3. 使用 numexpr 安全计算
   → numexpr.evaluate(cleaned_expr)

4. 格式化结果
   ├── 整数: 无小数点
   └── 浮点: 保留 precision 位小数

5. 生成 reason: "计算公式: {expr} = {result}"
```

**规则示例**:
```yaml
rule_id: calc_profit
rule_name: 利润计算
rule_type: calc
expression: |
  总营收: <field_result>revenue</field_result>
  减去成本: <field_result>cost</field_result>
  利润 = <field_result>revenue</field_result> - <field_result>cost</field_result>
depend_fields: ["revenue", "cost"]
```

**计算示例**:
| 原始表达式 | 清理后 | 结果 |
|----------|-------|------|
| `1000 + 300` | `1000+300` | `1300` |
| `5000 * 0.15` | `5000*0.15` | `750.00` |
| `计算利润: 5000 - 1000` | `5000-1000` | `4000` |

### 5.4 占位符替换 `resolve_expression()`

```python
def resolve_expression(
    expression: str,             # 原始表达式
    field_values: Dict[str, str] # {field_id: extracted_value}
) -> str:
```

**正则**:
```regex
<field_result>(\w+)</field_result>
```

**示例**:
```
原始: "销售额<field_result>sales</field_result>减去成本<field_result>cost</field_result>"
映射: {"sales": "10000", "cost": "3000"}
结果: "销售额10000减去成本3000"
```

### 5.5 分析配置

```yaml
analysis:
  calc_precision: 2              # 计算精度（小数位数）
  judge_timeout: 30              # 判断超时（秒）
```

### 5.6 调试接口

**端点**: `POST /analysis/test`

**模式 1: 使用已保存规则**
```json
{
  "file_id": "file_001",
  "rule_id": "calc_profit"
}
```

**模式 2: 使用临时配置**
```json
{
  "file_id": "file_001",
  "config": {
    "rule_type": "calc",
    "expression": "总收入<field_result>income</field_result> - 总支出<field_result>cost</field_result>",
    "depend_fields": ["income", "cost"]
  }
}
```

**响应格式**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "input_values": {"income": "1000", "cost": "300"},
    "expression_resolved": "总收入1000 - 总支出300",
    "result_value": "700",
    "reason": "计算公式: 1000-300 = 700"
  }
}
```

---

## 6. SSE 事件流实现

**文件**: `service/pipeline_service.py`

### 6.1 SSE 事件格式化

```python
def _sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

### 6.2 事件数据结构

```json
// 阶段完成事件
{
  "file_id": "xxx",
  "stage": "parsing",
  "message": "MinerU 解析完成",
  "content_length": 12345
}

// 错误事件
{
  "file_id": "xxx",
  "stage": "error",
  "message": "具体错误信息..."
}
```

### 6.3 SSE 响应头配置

```python
return StreamingResponse(
    generator,
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
    },
)
```

### 6.4 客户端接收示例

```javascript
const eventSource = new EventSource('/file/parse?mode=stream');

eventSource.addEventListener('parsing', (e) => {
    const data = JSON.parse(e.data);
    console.log('解析中:', data.message);
});

eventSource.addEventListener('complete', (e) => {
    console.log('完成！');
    eventSource.close();
});

eventSource.addEventListener('error', (e) => {
    const data = JSON.parse(e.data);
    console.error('失败:', data.message);
    eventSource.close();
});
```

---

## 7. 重试机制

### 7.1 重复提交检查

```
文件状态检查：
├── 文件不存在 → 新建记录 → 开始处理
├── processing → 409 错误（拒绝）
├── complete → 直接返回已完成
└── *_failed → 从对应阶段重试
```

### 7.2 从指定阶段重试 `run_from_stage()`

```python
# 清理策略：按阶段删除已生成的下游数据

if stage == "parsing":
    # 删除所有数据
    delete(FileContent, FileTable, FileChunk, ExtractionResult, AnalysisResult)
    milvus_client.delete_by_file_id(file_id)

elif stage == "chunking":
    # 保留: FileContent, FileTable
    # 删除: FileChunk, ExtractionResult, AnalysisResult, Milvus

elif stage == "embedding":
    # 保留: FileContent, FileTable, FileChunk
    # 删除: ExtractionResult, AnalysisResult, Milvus

elif stage == "extracting":
    # 删除: ExtractionResult, AnalysisResult

elif stage == "analyzing":
    # 删除: AnalysisResult
```

---

## 8. 完整数据库表结构

### 8.1 表结构汇总

| 表名 | 主键 | 关键字段 | 用途 |
|------|------|---------|------|
| `files` | file_id | progress, error, 时间戳 | 文件处理状态管理 |
| `file_content` | file_id | file_content (LONGTEXT) | 原始 Markdown 内容 |
| `file_table` | (file_id, table_index) | table_name, table_content | HTML 表格存储 |
| `file_chunk` | (file_id, chunk_id) | chunk_index, chunk_content | 分块文本存储 |
| `extraction_field` | field_id | source_type, search_type, priority | 字段提取配置 |
| `extraction_result` | (file_id, field_id) | extracted_value, reason | 提取结果 |
| `analysis_rule` | rule_id | rule_type, expression, depend_fields | 分析规则配置 |
| `analysis_result` | (file_id, rule_id) | result_value, input_values, reason | 分析结果 |

### 8.2 files 表

```python
class File(Base):
    __tablename__ = "files"

    file_id: str (VARCHAR(64), PRIMARY KEY)
    file_name: str (VARCHAR(512))
    file_size: int (BIGINT)
    create_time: datetime

    # 处理时间戳
    start_parsing_time: Optional[datetime]
    end_parsing_time: Optional[datetime]
    start_chunking_time: Optional[datetime]
    end_chunking_time: Optional[datetime]
    start_embedding_time: Optional[datetime]
    end_embedding_time: Optional[datetime]
    end_extracting_time: Optional[datetime]
    end_analyzing_time: Optional[datetime]

    # 状态
    progress: str  # parsing/chunking/embedding/extracting/analyzing/complete/*_failed
    error: Optional[str]
    updated_at: datetime
```

### 8.3 analysis_rule 表

```python
class AnalysisRule(Base):
    __tablename__ = "analysis_rule"

    rule_id: str (VARCHAR(100), PRIMARY KEY)
    rule_name: str (VARCHAR(200))
    rule_type: Enum("judge", "calc")
    expression: str (TEXT)
    depend_fields: Optional[List] (JSON)
    enabled: int (TINYINT, default=1)
    priority: int (INTEGER, default=0)
    created_at: datetime
    updated_at: datetime
```

### 8.4 analysis_result 表

```python
class AnalysisResult(Base):
    __tablename__ = "analysis_result"

    file_id: str (VARCHAR(64), PRIMARY KEY)
    rule_id: str (VARCHAR(100), PRIMARY KEY)
    result_value: str (VARCHAR(500))
    input_values: Optional[Dict] (JSON)  # 依赖字段值快照
    reason: Optional[str] (TEXT)
```

---

## 9. 完整配置参考

### 9.1 配置文件结构 (`configs/config.yaml`)

```yaml
# 服务器配置
server:
  host: "0.0.0.0"
  port: 8080

# MinerU 解析配置
mineru:
  base_url: "http://124.222.125.54:7078"
  backend: "vllm-async-engine"
  queue_width: 1
  parse_timeout: 1200
  max_file_size: 104857600

# 分块配置
chunking:
  chunk_size: 512
  chunk_overlap: 50
  max_chunk_size: 2048
  separators: ["\n\n", "\n", "。", " "]

# 向量化配置
embedding:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model_name: "text-embedding-v4"
  api_key: "sk-xxx"
  embedding_dim: 1024
  batch_size: 10
  timeout: 60
  retry_count: 3

# Milvus 配置
milvus:
  host: "124.222.125.54"
  port: 7067
  user: "root"
  password: "Milvus"
  collection_name: "wanzi_prase2_001"
  index_type: "IVF_FLAT"
  metric_type: "L2"
  nlist: 1024
  search_topk: 10

# MySQL 配置
mysql:
  host: "127.0.0.1"
  port: 3306
  database: "wanzi_prase2_001"
  username: "root"
  password: "1940"
  pool_size: 10

# 字段提取配置（LLM）
extraction:
  llm_base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  llm_model: "qwen-max"
  llm_api_key: "sk-xxx"
  llm_timeout: 60
  llm_retry_count: 3
  max_context_length: 4096

# 逻辑分析配置
analysis:
  calc_precision: 2
  judge_timeout: 30
```

### 9.2 配置加载

```python
from utils.config import get_config

# 全局配置单例（lru_cache 缓存）
cfg = get_config()

# 访问各模块配置
cfg.server.port          # 8080
cfg.chunking.chunk_size  # 512
cfg.embedding.batch_size # 10
cfg.analysis.calc_precision  # 2
```

---

## 10. API 端点汇总

### 10.1 文件处理

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/file/parse` | 上传并解析文件 |
| GET | `/file/{file_id}/status` | 查询处理状态 |
| DELETE | `/file/{file_id}` | 删除文件及相关数据 |
| POST | `/file/{file_id}/retry/{stage}` | 从指定阶段重试 |

### 10.2 查询结果

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/file/{file_id}/tables` | 获取表格列表 |
| GET | `/file/{file_id}/chunks` | 获取分块列表 |
| GET | `/file/{file_id}/extraction` | 获取提取结果 |
| GET | `/file/{file_id}/analysis` | 获取分析结果 |

### 10.3 字段提取配置

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/extraction/fields` | 获取字段配置列表 |
| POST | `/extraction/fields` | 新增/更新字段配置 |
| DELETE | `/extraction/fields/{field_id}` | 禁用字段配置 |
| POST | `/extraction/test` | 字段提取调试接口 |

### 10.4 逻辑分析配置

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/analysis/rules` | 获取规则列表 |
| POST | `/analysis/rules` | 新增/更新规则 |
| DELETE | `/analysis/rules/{rule_id}` | 禁用规则 |
| POST | `/analysis/test` | 规则分析调试接口 |
