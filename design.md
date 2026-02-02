## 算法流程

<aside>
💡

**minerU解析(yield)→存整个md到关系数据库→表格提取→分块→分块存数据库(yield)→向量化(yield)→提交milvus(yield)→获取关键词提取及逻辑分析任务(yield)→关键词提取(yield)→逻辑分析(yield)**

</aside>

**任务提交，支持异步或同步（同步支持流式及非流式）**

根据文件名计算file_id 确认重复性

## 库表

关系数据库：

### **1. files表**

存储文件基础信息及状态信息

| file_id | 文件id |
| --- | --- |
| file_name | 文件名 |
| create_time | 任务创建时间 |
| end_parsing_time | 解析完成时间 |
| 。。。其他几个进度时间 |  |
| progress | 文件进度 |
| error | 报错信息，一般为null |

其中process包含**parsing,chunking,embedding,complete,parsing_failed,chunking_failed,embedding_failed**

### **2.file_content表**

存储文件整个解析结果的信息

| file_id | 文件id |
| --- | --- |
| file_name | 文件名 |
| file_content | 解析结果 |

### 3.file_table表

| file_id | 文件id |
| --- | --- |
| file_name | 文件名 |
| table_index | 表格位置，也就是第几个表 |
| total_table | 表格数量 |
| table_name | 表格名称 |
| table_content | 表格内容 |

### **4.file_chunk表**

存储分块信息 ****

| file_id | 文件id |
| --- | --- |
| chunk_id | 分块id |
| chunk_index | 分块计数，也就是当前文件的第几块 |
| total_chunks | 分块总数 |

### 5.字段提取表

存储要提取的字段及规则

### 6.逻辑分析表

存储逻辑分析相关

向量数据库：

### 7.Milvus表

| file_id | 文件id |
| --- | --- |
| file_name | 文件名 |
| chunk_id | 分块id |
| chunk_index | 分块计数，也就是当前文件的第几块 |
| total_chunks | 分块总数 |
| embedding | 向量 |

### 1.MinerU解析

队列式解析（后期直接使用minerU-center进行管理），队列宽度为1，超时设置可控，及时提交解析状态到数据库的files 表中。

这里的接口需要先生成file_id 然后去数据库进行检索，如果存在则检查其状态，只要其状态不为完成则需要 删除数据库中所有表里面存在的相同file_id的记录 包括files表、file_content表、file_chunk表以及Milvus表中的记录。

### 2.存整个md到关系数据库

将解析结果存到file_content表中，解析结果的内容不从内存释放

### 3.规则式表格提取（未来可以考虑使用ai校验）

1. 正则匹配: 用 <table>.*?</table> 模式查找所有HTML表格
2. 表名识别: 取表格前面最后一个 \n\n 之后的文本作为表名

参考函数，返回字段需要调整

```python
def parse_tables(content: str, file_id: str, file_name: str) -> list[dict]:
    """解析Markdown中的表格"""
    tables = []
    table_pattern = re.compile(r'<table>.*?</table>', re.DOTALL | re.IGNORECASE)
    matches = list(table_pattern.finditer(content))

    for order, match in enumerate(matches, 1):
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
            table_name = f"表{order}"

        tables.append({
            "file_id": file_id,
            "file_name": file_name,
            "table_name": table_name[:500],  # 确保不超过数据库字段长度
            "table_order": order,
            "table_content": table_content
        })

    return tables
```

### 4.分块及分块存数据库

在分块时需要考虑表格内容 对于表格是<table>开始  <\table结尾>  需要把表格作为一整个块 而且为了能够囊括表格名称 需要包括表格前30个字符(或者其他策略)

直接使用前面内存中的解析结果进行批量分块，这里需要可配置分块大小，重叠大小，分块完成后，批量提交到mysql数据库。

这里需要提供一个解决分块失败的问题的接口 需要清空分块表（file_chunk）以及Milvus中file_id相同的记录后从数据库的file_content读取内容然后重新进行分块及后续操作

### 5.向量化

批量对前面内存中的块进行向量化，这里需要支持配置批量大小

有时候会存在向量化失败的问题，所以要提供从数据库中的分块表里面读取然后重新入库的接口：这个接口需要先检索向量库里面file_id为目标file_id的所有分块，然后删除他们，再读取Mysql中file_id 为目标文件的所有块放到pd里面进行向量化及后续操作

### 6.提交到Milvus

这里批量提交到Milvus中，支持配置批量大小

---

接下来是字段抽取的功能

这里只需要提供一个抽取失败时重新抽取的接口，然后给一个单次调用接口（用于测试），其他的应该是内部逻辑