## 算法流程

<aside>
💡

**minerU解析(yield)→存整个md到关系数据库→表格提取→分块→分块存数据库(yield)→向量化(yield)→提交milvus(yield)→获取关键词提取及逻辑分析任务(yield)→关键词提取(yield)→逻辑分析(yield)**

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
| progress | VARCHAR(32) | 当前进度状态 |
| error | TEXT | 错误信息 |
| updated_at | DATETIME | 最后更新时间 |

其中progress包含**parsing,chunking,embedding,complete,parsing_failed,chunking_failed,embedding_failed**

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

### 5.字段提取表

存储要提取的字段及规则

### 6.逻辑分析表

存储逻辑分析相关

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

### 索引设计

**关系数据库索引：**
- files 表：PRIMARY KEY (file_id)
- file_content 表：PRIMARY KEY (file_id)
- file_table 表：PRIMARY KEY (file_id, table_index)，INDEX (file_id)
- file_chunk 表：PRIMARY KEY (file_id, chunk_id)，INDEX (file_id)

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
3. **垃圾数据清理**：根据失败状态执行对应清理（与错误恢复逻辑一致）
   - parsing_failed → 清理 file_content, file_table, file_chunk, Milvus
   - chunking_failed → 清理 file_chunk, Milvus
   - embedding_failed → 清理 Milvus 中 file_id 对应记录

---

### 1.MinerU解析

队列式解析（后期直接使用minerU-center进行管理），队列宽度为1，超时设置可控，及时提交解析状态到数据库的files 表中。

这里的接口需要先生成file_id 然后去数据库进行检索，如果存在则检查其状态：
- 状态为 **parsing_failed** → 清理 file_content, file_table, file_chunk, Milvus，然后重新开始解析
- 状态为 **chunking_failed** → 清理 file_chunk, Milvus，然后从分块阶段重新开始
- 状态为 **embedding_failed** → 清理 Milvus 中 file_id 对应记录，然后从向量化阶段重新开始
- 状态为 **parsing/chunking/embedding**（处理中） → 拒绝重复提交，返回当前状态
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

---

### API 接口定义

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api/v1/files/parse | 提交文件解析（支持 sync/async/stream） |
| GET | /api/v1/files/{file_id}/status | 查询文件处理进度 |
| DELETE | /api/v1/files/{file_id} | 删除文件及所有关联数据 |
| POST | /api/v1/files/{file_id}/retry/{stage} | 从指定阶段重试（stage: parsing/chunking/embedding） |
| POST | /api/v1/search | 向量检索 |
| GET | /api/v1/files/{file_id}/tables | 获取文件表格列表 |
| GET | /api/v1/files/{file_id}/chunks | 获取文件分块列表 |

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

---

接下来是字段抽取的功能

这里只需要提供一个抽取失败时重新抽取的接口，然后给一个单次调用接口（用于测试），其他的应该是内部逻辑
