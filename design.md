## 算法流程

<aside>
💡

**minerU解析(yield)→存整个md到关系数据库→表格提取→分块→分块存数据库(yield)→向量化(yield)→提交milvus(yield)→字段提取(yield)→逻辑分析(yield)**

</aside>

**任务提交，支持异步或同步（同步支持流式及非流式）**

根据文件名计算file_id 确认重复性

```python
import hashlib
def generate_file_id(file_name: str) -> str:
    """仅根据文件名生成file_id，同名文件视为重复"""
    return hashlib.sha256(file_name.encode('utf-8')).hexdigest()[:32]
```

## 库表

关系数据库：

### **1. files表**

存储文件基础信息及状态信息

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) PK | 文件名哈希 |
| file_name | VARCHAR(512) | 原始文件名 |
| file_size | BIGINT | 文件大小(字节) |
| create_time | DATETIME | 任务创建时间 |
| start_parsing_time | DATETIME | 开始解析时间 |
| end_parsing_time | DATETIME | 解析完成时间 |
| start_chunking_time | DATETIME | 开始分块时间 |
| end_chunking_time | DATETIME | 分块完成时间 |
| start_embedding_time | DATETIME | 开始向量化时间 |
| end_embedding_time | DATETIME | 向量化完成时间 |
| end_extracting_time | DATETIME | 字段提取完成时间 |
| end_analyzing_time | DATETIME | 逻辑分析完成时间 |
| progress | VARCHAR(32) | 当前进度状态 |
| error | TEXT | 错误信息 |
| updated_at | DATETIME | 最后更新时间 |

其中progress包含**parsing, chunking, embedding, extracting, analyzing, complete, parsing_failed, chunking_failed, embedding_failed, extracting_failed, analyzing_failed**

### **2.file_content表**

存储文件整个解析结果的信息

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) PK | 文件id |
| file_content | LONGTEXT | 解析结果(Markdown) |

### 3.file_table表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) | 文件id |
| table_index | INT | 第几个表 |
| total_table | INT | 该文件表格总数 |
| table_name | VARCHAR(500) | 表格名称 |
| table_content | LONGTEXT | 表格内容（含完整`<table></table>`标签） |

PRIMARY KEY (file_id, table_index)

### **4.file_chunk表**

存储分块信息

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) | 文件id |
| chunk_id | VARCHAR(64) | 分块id |
| chunk_index | INT | 第几块 |
| total_chunks | INT | 该文件分块总数 |
| chunk_content | TEXT | 分块文本内容（表格块含完整`<table></table>`标签） |

PRIMARY KEY (file_id, chunk_id)

### 5. extraction_field 表（字段提取配置表）

删除字段时使用**软删除**（enabled=0），不物理删除记录，不影响已有提取结果。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| field_id | VARCHAR(100) PK | 字段ID（用户输入，字母数字下划线，唯一，用于逻辑分析引用） |
| field_name | VARCHAR(200) | 字段中文名（展示用） |
| source_type | ENUM('table','text') | 来源类型：表格/文本 |
| enabled | TINYINT DEFAULT 1 | 是否启用（0=软删除/禁用） |
| priority | INT DEFAULT 0 | 优先级（执行顺序） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |
| **表格类专用** | | |
| table_name_pattern | VARCHAR(500) | 预检索表格名 |
| table_match_type | ENUM('exact','fuzzy','contains','llm') | 表格匹配规则 |
| table_extract_prompt | TEXT | 表格字段提取提示词（使用 `<search_result>` 占位符表示匹配到的表格内容） |
| **文本类专用** | | |
| search_type | ENUM('context','section','rule','chunk_db','vector_db') | 检索类型（一个字段只配一种） |
| search_config | JSON | 检索配置（见下方说明） |
| text_extract_prompt | TEXT | 文本字段提取提示词（使用 `<search_result>` 占位符表示检索结果） |

**search_config JSON 结构：**

```json
// context（上下文检索）
{
  "keywords": ["关键词1", "关键词2"],
  "context_before": 200,
  "context_after": 200,
  "max_results": 5,
  "sort_order": "asc"
}

// section（章节检索）
{
  "section_pattern": "投资估算",
  "section_match_type": "contains",
  "max_results": 3,
  "sort_order": "asc"
}

// chunk_db（关系数据库检索）
{
  "keyword_filter": "投资",
  "max_results": 10,
  "sort_order": "asc"
}

// rule（规则检索：关键词+停用词边界）
{
  "keywords": ["总投资", "投资总额"],
  "stop_words": ["#", "##", "###", "\n\n", "\n", "。", ".", "；", ";"],
  "direction": "forward",
  "min_length": 2,
  "max_length": 200,
  "max_results": 5,
  "sort_order": "asc"
}

// vector_db（向量数据库检索）
{
  "query_text": "项目总投资金额",
  "top_k": 5,
  "score_threshold": 0.7
}
```

### 6. analysis_rule 表（逻辑分析配置表）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| rule_id | VARCHAR(100) PK | 规则ID（用户输入，字母数字下划线，唯一） |
| rule_name | VARCHAR(200) | 规则中文名 |
| rule_type | ENUM('judge','calc') | 规则类型：判断/计算 |
| expression | TEXT | 表达式（判断类为提示词，计算类为公式，使用 `<field_result>field_id</field_result>` 占位符引用字段值） |
| depend_fields | JSON | 依赖的字段列表（field_id 数组，系统据此从 extraction_result 获取值替换占位符） |
| enabled | TINYINT DEFAULT 1 | 是否启用（0=软删除/禁用） |
| priority | INT DEFAULT 0 | 执行优先级 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

**expression 占位符格式：** `<field_result>field_id</field_result>`

系统执行时，从 extraction_result 中获取对应 field_id 的 extracted_value，替换占位符后再发送给 LLM 或进行计算。

**expression 示例：**

判断类（rule_type='judge'）：
```
你是一个专业的判断系统，你需要根据以下内容判断该项目是否符合投资要求。
当前内容是：<field_result>total_investment</field_result>
请根据以上内容判断该项目是否符合投资要求，符合返回true，不符合返回false。
请只返回true或false，不要添加其他内容。
```

计算类（rule_type='calc'）：
```
<field_result>field1</field_result>+<field_result>field2</field_result>*0.2
```

向量数据库：

### 7.Milvus表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) | 文件id |
| chunk_id | VARCHAR(64) | 分块id |
| chunk_index | INT | 第几块 |
| total_chunks | INT | 该文件分块总数 |
| chunk_content | VARCHAR(65535) | 分块文本（含完整`<table></table>`标签） |
| embedding | FLOAT_VECTOR(dim) | 向量（维度由模型决定） |

### 8. extraction_result 表（字段提取结果表）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) | 文件ID |
| field_id | VARCHAR(100) | 字段ID（关联 extraction_field.field_id） |
| extracted_value | TEXT | 提取的值 |

PRIMARY KEY (file_id, field_id)
INDEX (file_id)

### 9. analysis_result 表（逻辑分析结果表）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file_id | VARCHAR(64) | 文件ID |
| rule_id | VARCHAR(100) | 规则ID（关联 analysis_rule.rule_id） |
| result_value | VARCHAR(500) | 结果值（判断类为true/false，计算类为数值） |
| input_values | JSON | 输入值快照 {"field1": "100", "field2": "200"} |

PRIMARY KEY (file_id, rule_id)
INDEX (file_id)

### 索引设计

**关系数据库索引：**
- files 表：PRIMARY KEY (file_id)
- file_content 表：PRIMARY KEY (file_id)
- file_table 表：PRIMARY KEY (file_id, table_index)，INDEX (file_id)
- file_chunk 表：PRIMARY KEY (file_id, chunk_id)，INDEX (file_id)
- extraction_field 表：PRIMARY KEY (field_id)
- analysis_rule 表：PRIMARY KEY (rule_id)
- extraction_result 表：PRIMARY KEY (file_id, field_id)，INDEX (file_id)
- analysis_result 表：PRIMARY KEY (file_id, rule_id)，INDEX (file_id)

**Milvus 索引：**
- embedding：向量索引 (IVF_FLAT 或 HNSW)
- file_id：标量索引 (用于按文件删除/查询)

---

### 0. 服务初始化

启动时执行以下初始化操作：

1. **数据库表检查**：检查所有表是否存在，不存在则创建
2. **异常状态恢复**：将所有 `*ing` 状态改为对应的 `*_failed`
   - parsing → parsing_failed
   - chunking → chunking_failed
   - embedding → embedding_failed
   - extracting → extracting_failed
   - analyzing → analyzing_failed
3. **垃圾数据清理**：根据失败状态执行对应清理（与错误恢复逻辑一致）
   - parsing_failed → 清理 file_content, file_table, file_chunk, Milvus
   - chunking_failed → 清理 file_chunk, Milvus
   - embedding_failed → 清理 Milvus 中 file_id 对应记录
   - extracting_failed → 清理 extraction_result 中 file_id 对应记录
   - analyzing_failed → 清理 analysis_result 中 file_id 对应记录

---

### 1.MinerU解析

队列式解析（后期直接使用minerU-center进行管理），队列宽度为1，超时设置可控，及时提交解析状态到数据库的files 表中。

这里的接口需要先生成file_id 然后去数据库进行检索，如果存在则检查其状态：
- 状态为 **parsing_failed** → 清理 file_content, file_table, file_chunk, Milvus，然后重新开始解析
- 状态为 **chunking_failed** → 清理 file_chunk, Milvus，然后从分块阶段重新开始
- 状态为 **embedding_failed** → 清理 Milvus 中 file_id 对应记录，然后从向量化阶段重新开始
- 状态为 **extracting_failed** → 清理 extraction_result 中 file_id 对应记录，然后从字段提取阶段重新开始
- 状态为 **analyzing_failed** → 清理 analysis_result 中 file_id 对应记录，然后从逻辑分析阶段重新开始
- 状态为 **parsing/chunking/embedding/extracting/analyzing**（处理中） → 拒绝重复提交，返回当前状态
- 状态为 **complete** → 返回已完成

### 2.存整个md到关系数据库

将解析结果存到file_content表中，解析结果的内容不从内存释放

### 3.规则式表格提取（未来可以考虑使用ai校验）

1. 正则匹配: 用 <table>.*?</table> 模式查找所有HTML表格
2. 表名识别: 取表格前面最后一个 \n\n 之后的文本作为表名

参考函数：

```python
def parse_tables(content: str, file_id: str) -> list[dict]:
    """解析Markdown中的表格"""
    tables = []
    table_pattern = re.compile(r'<table>.*?</table>', re.DOTALL | re.IGNORECASE)
    matches = list(table_pattern.finditer(content))
    total_table = len(matches)

    for table_index, match in enumerate(matches, 1):
        table_content = match.group(0)
        start_pos = match.start()
        preceding_text = content[:start_pos].rstrip()

        # 找到最后一个 \n\n，取其前面的最后一行作为表名
        last_double_newline = preceding_text.rfind("\n\n")

        if last_double_newline != -1:
            # 取 \n\n 后面的内容，然后取最后一行 (通常就是表名)
            after_double_newline = preceding_text[last_double_newline:].strip()
            lines = after_double_newline.split("\n")
            table_name = lines[-1].strip() if lines else ""
        else:
            # 没有 \n\n，取整个前面内容的最后一行
            lines = preceding_text.strip().split("\n")
            table_name = lines[-1].strip() if lines else ""

        # 清理 markdown 标记 (如 # ## 等)
        table_name = re.sub(r'^#+\s*', '', table_name)

        # 如果表名包含 table 标签，说明是无效的（两个表格挨着）
        if '<table>' in table_name.lower() or '</table>' in table_name.lower():
            table_name = ""

        # 如果表名为空或过长，使用默认名称
        if not table_name or len(table_name) > 200:
            table_name = f"表{table_index}"

        tables.append({
            "file_id": file_id,
            "table_index": table_index,
            "total_table": total_table,
            "table_name": table_name[:500],
            "table_content": table_content  # 保留完整<table></table>标签
        })

    return tables
```

### 4.分块及分块存数据库

在分块时需要考虑表格内容 对于表格是<table>开始 </table>结尾 需要把表格作为一整个块 而且为了能够囊括表格名称 需要复用表格提取步骤中识别的 table_name，将其拼接到表格块开头作为上下文

直接使用前面内存中的解析结果进行批量分块，分块完成后，批量提交到mysql数据库。

**分块参数（config.yaml 可配置）：**
- chunk_size: 512（默认）
- chunk_overlap: 50（默认）
- max_chunk_size: 2048（含表格的最大块大小）

**chunk_id 生成规则：**
```python
chunk_id = hashlib.sha256((file_id + str(chunk_index)).encode('utf-8')).hexdigest()[:32]
```

**分块策略：**
1. 先识别所有 `<table>...</table>` 位置
2. 对非表格文本按 chunk_size/overlap 分块，分隔符优先级：`["\n\n", "\n", "。", " "]`
3. 表格作为独立块，**保留完整 `<table></table>` 标签**
4. 表格块前拼接 `table_name\n` 作为上下文
5. 若表格内容超过 max_chunk_size(2048)，仍保持为单独一块

**边界情况：**
- 代码块不拆分
- 图片引用保留在所属块

**错误恢复：** 清空 file_chunk 表及 Milvus 中 file_id 对应记录，从 file_content 读取内容重新分块及后续操作

### 5.向量化

批量对前面内存中的块进行向量化。

**模型配置（config.yaml）：**
- 模型：OpenAI 兼容本地模型
  - base_url、model_name、api_key（可选）
- 向量维度：由模型决定，Milvus Collection 创建时根据配置设定
- batch_size：默认 32
- timeout：默认 60s
- retry_count：默认 3（指数退避）

**失败处理：** 全量重做，不支持断点续传。清理 Milvus 中 file_id 对应记录，从 file_chunk 表读取所有分块重新向量化及后续操作。

### 6.提交到Milvus

这里批量提交到Milvus中，支持配置批量大小

### 7. 字段提取

完成向量化后，按 priority 顺序执行启用的字段提取规则。

**执行流程：**
1. 更新 files.progress = 'extracting'
2. 获取所有 enabled=1 的 extraction_field，按 priority 排序
3. 对每个字段执行提取（详见下方各类型流程）
4. 结果写入 extraction_result 表（extracted_value 存 LLM 原始返回）
5. 全部字段处理完毕后，更新 end_extracting_time，进入逻辑分析阶段

**单字段失败处理：** 某个字段提取失败（LLM超时/返回空/检索无结果）时，**跳过继续**处理下一个字段，extraction_result 中写入空字符串（extracted_value=''），不阻塞整体流程。

**整体错误恢复：** 清理 extraction_result 中 file_id 对应的所有记录，重新执行全部提取。

---

#### 7.1 表格类提取流程（source_type='table'）

```
1. 从 file_table 表加载该 file_id 的所有表格
2. 按 table_match_type 匹配 table_name_pattern：
   - exact：table_name == table_name_pattern
   - fuzzy：模糊匹配（编辑距离或相似度）
   - contains：table_name 包含 table_name_pattern
   - llm：将所有 table_name 列表发给 LLM，让其选择最匹配的
3. 匹配到多个表格时：逐个表格发送给 LLM 提取，取第一个返回非空结果的值
4. 表格内容以原始 HTML 格式（<table>...</table>）发送给 LLM
5. 构造 LLM 输入：将 table_extract_prompt 中的 <search_result> 占位符替换为表格 HTML 内容
6. 发送给 LLM，extracted_value 存 LLM 原始返回文本
```

**table_extract_prompt 示例：**
```
请从以下表格中提取"总投资金额"字段的值，只返回数值，不要添加其他内容。

<search_result>
```

#### 7.2 文本类提取流程（source_type='text'）

```
1. 根据 search_type 执行对应检索（详见 7.3）
2. 检索返回多条结果时：按 sort_order 排序后全部拼接（用 \n---\n 分隔）
3. 构造 LLM 输入：将 text_extract_prompt 中的 <search_result> 占位符替换为拼接后的检索内容
4. 一次性发送给 LLM 提取
5. extracted_value 存 LLM 原始返回文本
```

**text_extract_prompt 示例：**
```
请从以下内容中提取"项目总投资金额"，只返回数值和单位，不要添加其他内容。

<search_result>
```

#### 7.3 检索类型详细说明

**context（上下文检索）：**
1. 加载 file_content 全文
2. 在全文中搜索 keywords 中的每个关键词
3. 对每个命中位置，取前 context_before 字节 + 关键词 + 后 context_after 字节
4. 按 sort_order 排序（asc=按出现位置顺序，desc=逆序）
5. 取前 max_results 条

**section（章节检索）：**
1. 加载 file_content，调用 parse_sections() 解析章节结构
2. 按 section_match_type 匹配 section_pattern：
   - exact：title == section_pattern
   - fuzzy：模糊匹配
   - contains：title 包含 section_pattern
   - llm：将所有 title 列表发给 LLM 选择
3. 取匹配章节的 content[start_pos:end_pos] 作为检索结果
4. 按 sort_order 排序，取前 max_results 条

**rule（规则检索：关键词+停用词边界）：**
1. 加载 file_content 全文
2. 搜索 keywords 中的每个关键词位置
3. 从关键词位置出发，按 direction 方向扩展，直到遇到 stop_words 中的任一停用词为止：
   - forward：关键词位置向后扩展到停用词
   - backward：关键词位置向前扩展到停用词
   - both：双向扩展
4. 结果文本长度 < min_length 则丢弃，> max_length 则截断
5. 按 sort_order 排序，取前 max_results 条

**chunk_db（关系数据库检索）：**
1. 从 file_chunk 表查询 file_id 对应的所有分块
2. 如果配置了 keyword_filter，过滤 chunk_content 包含该关键词的分块
3. 按 chunk_index 排序（sort_order），取前 max_results 条

**vector_db（向量数据库检索）：**
1. 将 query_text 向量化
2. 在 Milvus 中检索 file_id 对应的相似分块
3. 过滤 score < score_threshold 的结果
4. 取前 top_k 条

参考函数（章节解析）：

```python
import re
from dataclasses import dataclass

@dataclass
class SectionInfo:
    """章节信息"""
    index: int        # 章节索引
    number: str       # 章节号如 "6" 或 "7.2"
    title: str        # 标题如 "投资估算"
    start_pos: int    # 起始位置
    end_pos: int      # 结束位置


def parse_sections(content: str) -> list[SectionInfo]:
    """
    解析 Markdown 文档中所有章节

    Args:
        content: Markdown 文档内容

    Returns:
        章节信息列表
    """
    pattern = re.compile(
        r'^#\s+([\d.]+)\s+(.+?)(?:\s+\d+)?\s*$',
        re.MULTILINE
    )

    matches = list(pattern.finditer(content))
    sections = []

    for i, match in enumerate(matches):
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections.append(SectionInfo(
            index=i,
            number=match.group(1),
            title=match.group(2).strip(),
            start_pos=match.start(),
            end_pos=end_pos
        ))

    return sections
```

### 8. 逻辑分析

完成字段提取后，按 priority 顺序执行启用的逻辑分析规则。

**执行流程：**
1. 更新 files.progress = 'analyzing'
2. 获取所有 enabled=1 的 analysis_rule，按 priority 排序
3. 对每个规则：
   a. 解析 expression 中的 `<field_result>field_id</field_result>` 占位符
   b. 根据 depend_fields 从 extraction_result 获取对应字段值
   c. 将占位符替换为实际的 extracted_value
   d. 记录 input_values 快照（用于结果追溯）
   e. 按 rule_type 执行：
      - **judge**：将替换后的完整文本作为 prompt 发送给 LLM，期望返回 true/false
      - **calc**：使用安全解析库（如 numexpr）执行公式计算，结果保留 calc_precision 位小数
4. 结果写入 analysis_result 表

**单条规则失败处理：** 某条规则执行失败（依赖字段值为空、计算异常、LLM超时）时，**跳过继续**处理下一条规则，result_value 存空字符串，不阻塞整体流程。

5. 全部规则处理完毕后，更新 end_analyzing_time，更新 files.progress = 'complete'

**calc 安全执行：** 使用 Python 安全解析库（如 numexpr、simpleeval），**禁止**使用原生 eval。只允许基本数学运算（+、-、*、/、()、**），不允许函数调用、模块导入等。

**整体错误恢复：** 清理 analysis_result 中 file_id 对应的所有记录，重新执行全部分析。

---

### API 接口定义

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /file/parse | 提交文件解析（支持 sync/async/stream） |
| GET | /file/{file_id}/status | 查询文件处理进度 |
| DELETE | /file/{file_id} | 删除文件及所有关联数据 |
| POST | /file/{file_id}/retry/{stage} | 从指定阶段重试（stage: parsing/chunking/embedding/extracting/analyzing） |
| POST | /search | 向量检索 |
| GET | /file/{file_id}/tables | 获取文件表格列表 |
| GET | /file/{file_id}/chunks | 获取文件分块列表 |
| GET | /extraction/fields | 获取字段提取配置列表 |
| POST | /extraction/fields | 新增/更新字段提取配置（根据 field_id 判断 upsert） |
| DELETE | /extraction/fields/{field_id} | 软删除字段提取配置（enabled=0） |
| POST | /extraction/test | 字段提取调试接口（支持两种模式，返回中间过程） |
| GET | /extraction/fields/{field_id}/check | 检查 field_id 是否已存在 |
| GET | /analysis/rules | 获取逻辑分析配置列表 |
| POST | /analysis/rules | 新增/更新逻辑分析配置（根据 rule_id 判断 upsert） |
| DELETE | /analysis/rules/{rule_id} | 软删除逻辑分析配置（enabled=0） |
| POST | /analysis/test | 逻辑分析调试接口（支持两种模式，返回中间过程） |
| GET | /analysis/rules/{rule_id}/check | 检查 rule_id 是否已存在 |
| GET | /file/{file_id}/extraction | 获取文件字段提取结果 |
| GET | /file/{file_id}/analysis | 获取文件逻辑分析结果 |
| POST | /file/{file_id}/retry/extracting | 重试字段提取 |
| POST | /file/{file_id}/retry/analyzing | 重试逻辑分析 |

#### 调试接口详细说明

**POST /extraction/test - 字段提取调试**

支持两种模式：

模式一：全自动（传 field_id + file_id）
```json
// 请求
{ "field_id": "total_investment", "file_id": "abc123" }
// 响应：返回检索中间结果 + LLM提取结果
{
  "search_results": [      // 检索到的原始内容列表
    { "index": 0, "content": "...", "source": "context_match_pos_1234" }
  ],
  "llm_input": "...",      // 实际发送给LLM的完整输入
  "llm_output": "1500万元", // LLM原始返回
  "extracted_value": "1500万元"
}
```

模式二：传完整配置直接测试（不需要先入库）
```json
// 请求
{
  "file_id": "abc123",
  "config": {
    "source_type": "text",
    "search_type": "context",
    "search_config": { "keywords": ["总投资"], "context_before": 200, "context_after": 200, "max_results": 5, "sort_order": "asc" },
    "text_extract_prompt": "请从以下内容中提取总投资金额：\n<search_result>"
  }
}
// 响应：同上
```

**POST /analysis/test - 逻辑分析调试**

支持两种模式：

模式一：全自动（传 rule_id + file_id）
```json
// 请求
{ "rule_id": "check_investment", "file_id": "abc123" }
// 响应
{
  "input_values": { "total_investment": "1500万元" },
  "expression_resolved": "你是一个专业的判断系统...当前内容是：1500万元...",
  "result_value": "true"
}
```

模式二：传完整配置直接测试
```json
// 请求
{
  "file_id": "abc123",
  "config": {
    "rule_type": "calc",
    "expression": "<field_result>field1</field_result>+<field_result>field2</field_result>*0.2",
    "depend_fields": ["field1", "field2"]
  }
}
// 响应：同上
```

---

### 配置参数（config.yaml）

```yaml
# MinerU配置
mineru:
  queue_width: 1          # 并发解析数
  parse_timeout: 300      # 解析超时(秒)
  max_file_size: 104857600  # 最大文件大小(100MB)

# 分块配置
chunking:
  chunk_size: 512         # 分块大小
  chunk_overlap: 50       # 重叠大小
  max_chunk_size: 2048    # 最大块大小(含表格)
  separators: ["\n\n", "\n", "。", " "]

# 向量化配置
embedding:
  base_url: "http://localhost:8000/v1"
  model_name: "bge-large-zh"
  api_key: ""             # 可选
  embedding_dim: 1024     # 向量维度
  batch_size: 32
  timeout: 60
  retry_count: 3

# Milvus配置
milvus:
  host: "localhost"
  port: 19530
  collection_name: "file_chunks"
  index_type: "IVF_FLAT"
  metric_type: "L2"
  nlist: 1024
  search_topk: 10

# MySQL配置
mysql:
  host: "localhost"
  port: 3306
  database: "file_parser"
  pool_size: 10

# 字段提取配置
extraction:
  llm_base_url: "http://localhost:8000/v1"
  llm_model: "qwen-7b"
  llm_timeout: 60
  llm_retry_count: 3
  max_context_length: 4096  # LLM最大输入长度

# 逻辑分析配置
analysis:
  calc_precision: 2         # 计算结果小数位数
  judge_timeout: 30         # 判断类LLM超时
```

---

### 错误处理策略

**错误分类：**
- **可重试**：网络超时、API限流、临时故障
- **不可重试**：文件格式错误、权限问题

**重试策略：** 最大次数和间隔从 config.yaml 读取，指数退避

**各阶段清理逻辑：**
- parsing_failed → 清理 file_content, file_table, file_chunk, Milvus
- chunking_failed → 清理 file_chunk, Milvus
- embedding_failed → 清理 Milvus 中 file_id 对应记录
- extracting_failed → 清理 extraction_result 中 file_id 对应记录
- analyzing_failed → 清理 analysis_result 中 file_id 对应记录
