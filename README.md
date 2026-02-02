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
