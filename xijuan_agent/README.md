# 析卷智能体

`xijuan_agent` 是接入现有 FastAPI 服务的文档研究工作台。输入一个已处理完成的 `file_id` 和问题，Agent 会使用同一个 OpenAI 兼容模型流式回答、发起工具调用，并可将原始 PDF 页图交给模型视觉分析。页面事件只保留在当前浏览器内存中，刷新后清空。

## 配置

在系统设置的「智能体 Agent」分组配置模型地址、模型名称和 API Key。模型服务需兼容 `chat/completions` 流式输出、function calling，以及 OpenAI `image_url` data URL 图像输入。工具调用和 PDF 图片阅读共用该模型。

`agent.max_rounds` 默认为 25，可在设置页调整为 1 到 100。每次 `pdf_reader` 调用最多读取 8 页。原始 PDF 仍按现有 `vl_model.pdf_storage_dir` 配置存放，并受 PDF 保留策略管理。

## 工作台与 API

- `GET /agent`：独立工作台页面，复用现有管理员会话、API Key 或 IP 白名单鉴权。
- `POST /agent/stream`：SSE 智能体运行接口，请求体为 `{ "file_id": "...", "question": "..." }`。
- 事件包含 `status`、`context_ready`、`round_start`、`token`、`tool_call_delta`、`tool_call`、`tool_result`、`done` 和 `error`。客户端断开流即可中止当前运行。

## 工具

- 文本检索：`search_context`、`search_rule`、`search_chunk_db`、`search_bm25`、`search_vector_db`、`search_page`。
- 章节：`list_sections` 列出标题和层级，`read_section` 按章节索引读取完整章节。
- 表格：`list_tables` 列出表名和索引，`read_table` 按索引读取内容，不运行表名匹配模式。
- 外部与视觉：`web_search` 调用已配置的博查搜索；`pdf_reader` 按页码范围渲染页图，并将图像放回同一 Agent 模型上下文。

各工具的参数、边界和结果语义定义在 `tools.py` 的 OpenAI function schemas 中。
